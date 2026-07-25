"""Proposal schema strictness and the transaction state machine.

``AGENTS.md`` requires malformed proposals to be rejected before graph analysis, and requires
every transition to be legal and audited.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graph_traffic_control.demo.agents import FCT_REVENUE, proposal_a
from graph_traffic_control.domain.models import (
    AgentIdentity,
    ChangeAction,
    ChangeProposal,
    EntityContext,
    SchemaField,
    TransactionState,
)
from graph_traffic_control.domain.states import (
    IllegalTransition,
    can_transition,
    is_terminal,
    require_transition,
)


def _minimal_action(**overrides):
    payload = {
        "kind": "update_model",
        "target_urn": FCT_REVENUE,
        "artifact_path": "fct_revenue.sql",
    }
    payload.update(overrides)
    return ChangeAction(**payload)


class TestProposalStrictness:
    def test_unknown_fields_are_rejected(self):
        with pytest.raises(ValidationError):
            ChangeProposal(
                proposal_id="x",
                agent=AgentIdentity(agent_id="a", display_name="A"),
                intent="test",
                write_set=[FCT_REVENUE],
                action=_minimal_action(),
                unexpected_field="boom",
            )

    def test_empty_write_set_is_rejected(self):
        with pytest.raises(ValidationError):
            ChangeProposal(
                proposal_id="x",
                agent=AgentIdentity(agent_id="a", display_name="A"),
                intent="test",
                write_set=[],
                action=_minimal_action(),
            )

    def test_action_target_must_be_declared_in_the_write_set(self):
        with pytest.raises(ValidationError, match="not in the declared write_set"):
            ChangeProposal(
                proposal_id="x",
                agent=AgentIdentity(agent_id="a", display_name="A"),
                intent="test",
                write_set=["urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.other,PROD)"],
                action=_minimal_action(),
            )

    def test_rename_requires_both_column_names(self):
        with pytest.raises(ValidationError, match="rename_column requires"):
            _minimal_action(kind="rename_column", field_path="a")

    @pytest.mark.parametrize(
        "bad_path",
        ["/etc/passwd", "../../secrets.sql", "C:/windows/system32.sql", "..\\escape.sql"],
    )
    def test_artifact_path_cannot_escape_the_state_root(self, bad_path):
        with pytest.raises(ValidationError):
            _minimal_action(artifact_path=bad_path)

    @pytest.mark.parametrize("bad_duration", [0, -1, 3601])
    def test_lease_duration_is_bounded(self, bad_duration):
        with pytest.raises(ValidationError):
            ChangeProposal(
                proposal_id="x",
                agent=AgentIdentity(agent_id="a", display_name="A"),
                intent="test",
                write_set=[FCT_REVENUE],
                action=_minimal_action(),
                requested_lease_seconds=bad_duration,
            )

    def test_all_urns_deduplicates_and_includes_every_set(self):
        proposal = proposal_a()
        urns = proposal.all_urns
        assert len(urns) == len(set(urns))
        assert FCT_REVENUE in urns


class TestVersionFingerprint:
    def test_fingerprint_is_stable(self):
        entity = EntityContext(
            urn=FCT_REVENUE, name="x", fields=[SchemaField(path="a", type="int")]
        )
        assert entity.version_fingerprint() == entity.version_fingerprint()

    def test_fingerprint_changes_when_a_column_changes(self):
        base = EntityContext(
            urn=FCT_REVENUE, name="x", fields=[SchemaField(path="a", type="int")]
        )
        renamed = EntityContext(
            urn=FCT_REVENUE, name="x", fields=[SchemaField(path="b", type="int")]
        )
        assert base.version_fingerprint() != renamed.version_fingerprint()

    def test_fingerprint_is_order_independent(self):
        one = EntityContext(
            urn=FCT_REVENUE,
            name="x",
            fields=[SchemaField(path="a"), SchemaField(path="b")],
        )
        two = EntityContext(
            urn=FCT_REVENUE,
            name="x",
            fields=[SchemaField(path="b"), SchemaField(path="a")],
        )
        assert one.version_fingerprint() == two.version_fingerprint()


class TestSnapshotFingerprint:
    def test_subgraph_fingerprint_ignores_unrelated_entities(self, snapshot):
        relevant = [FCT_REVENUE]
        before = snapshot.subgraph_fingerprint(relevant)

        support = next(u for u in snapshot.entities if "support_tickets" in u)
        snapshot.entities[support].fields.append(SchemaField(path="new_column"))

        assert snapshot.subgraph_fingerprint(relevant) == before
        assert snapshot.fingerprint() != before

    def test_subgraph_fingerprint_reacts_to_relevant_change(self, snapshot):
        before = snapshot.subgraph_fingerprint([FCT_REVENUE])
        snapshot.entities[FCT_REVENUE].fields.append(SchemaField(path="new_column"))
        assert snapshot.subgraph_fingerprint([FCT_REVENUE]) != before


class TestStateMachine:
    def test_happy_path_is_legal(self):
        path = [
            TransactionState.SUBMITTED,
            TransactionState.ANALYZING,
            TransactionState.PREPARED,
            TransactionState.EXECUTING,
            TransactionState.VALIDATING,
            TransactionState.COMMITTED,
        ]
        for current, following in zip(path, path[1:], strict=False):
            require_transition(current, following)

    def test_cannot_commit_directly_from_submitted(self):
        with pytest.raises(IllegalTransition):
            require_transition(TransactionState.SUBMITTED, TransactionState.COMMITTED)

    def test_cannot_leave_a_terminal_state(self):
        for terminal in (
            TransactionState.COMMITTED,
            TransactionState.ABORTED,
            TransactionState.EXPIRED,
        ):
            assert is_terminal(terminal)
            with pytest.raises(IllegalTransition):
                require_transition(terminal, TransactionState.ANALYZING)

    def test_terminal_self_transition_is_allowed_for_idempotency(self):
        require_transition(TransactionState.COMMITTED, TransactionState.COMMITTED)
        require_transition(TransactionState.ABORTED, TransactionState.ABORTED)

    def test_blocked_can_be_reanalyzed(self):
        assert can_transition(TransactionState.BLOCKED, TransactionState.ANALYZING)

    def test_prepared_can_expire(self):
        assert can_transition(TransactionState.PREPARED, TransactionState.EXPIRED)

    def test_executing_cannot_expire(self):
        """Expiry mid-execution would leave an artifact half-written with no owner."""
        assert not can_transition(TransactionState.EXECUTING, TransactionState.EXPIRED)

    def test_illegal_transition_message_lists_legal_targets(self):
        with pytest.raises(IllegalTransition, match="Legal targets"):
            require_transition(TransactionState.COMMITTED, TransactionState.EXECUTING)
