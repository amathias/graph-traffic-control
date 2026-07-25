"""The proposal -> lease -> commit vertical slice.

Covers the behaviours ``AGENTS.md`` lists as non-negotiable: prepare/commit lifecycle, safe
parallel work, graph recheck before commit with fail-closed drift handling, validation, abort,
idempotency, and namespace refusal.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.namespace import NamespaceViolation
from graph_traffic_control.demo.agents import (
    FCT_REVENUE,
    METRIC_NET_REVENUE,
    proposal_a,
    proposal_b,
    proposal_c,
    proposal_d,
)
from graph_traffic_control.domain.models import (
    AgentIdentity,
    ChangeAction,
    ChangeProposal,
    TransactionState,
)
from graph_traffic_control.txn.coordinator import CoordinatorError


def _approve_and_commit(coordinator, proposal, outcome, writeback=None):
    if outcome.token.approval_required:
        coordinator.approve(outcome.token, "release-manager")
    return coordinator.commit(proposal, outcome.token, writeback)


class TestPrepare:
    def test_unrelated_proposal_prepares_and_commits(self, coordinator, versions):
        """Agent C must never wait for the revenue branch."""
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        assert outcome.prepared
        assert outcome.lease is not None
        assert outcome.impact.blast_radius == 0

        commit = _approve_and_commit(coordinator, c, outcome)
        assert commit.committed

    def test_high_blast_radius_requires_approval(self, coordinator, versions):
        outcome = coordinator.prepare(proposal_a(versions))
        assert outcome.prepared
        assert outcome.token.approval_required is True

    def test_commit_without_approval_aborts(self, coordinator, versions):
        a = proposal_a(versions)
        outcome = coordinator.prepare(a)
        result = coordinator.commit(a, outcome.token)
        assert result.state is TransactionState.ABORTED
        assert "approval" in result.reason.lower()

    def test_impact_expands_downstream_through_lineage(self, coordinator, versions):
        outcome = coordinator.prepare(proposal_a(versions))
        # fct_revenue -> metric_net_revenue -> dashboard
        assert outcome.impact.blast_radius >= 2
        assert any("metric_net_revenue" in urn for urn in outcome.impact.expanded_downstream)

    def test_stale_expected_version_aborts_at_prepare(self, coordinator):
        outcome = coordinator.prepare(proposal_d())
        assert outcome.state is TransactionState.ABORTED
        assert "stale" in outcome.reason.lower()

    def test_foreign_namespace_proposal_is_refused(self, coordinator):
        rogue = ChangeProposal(
            proposal_id="rogue",
            agent=AgentIdentity(agent_id="rogue", display_name="Rogue"),
            intent="write into another submission's graph",
            write_set=["urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"],
            action=ChangeAction(
                kind="update_model",
                target_urn="urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)",
                artifact_path="fct_revenue.sql",
            ),
        )
        with pytest.raises(NamespaceViolation):
            coordinator.prepare(rogue)
        assert coordinator._store.get_state("rogue") is TransactionState.ABORTED  # noqa: SLF001


class TestConcurrentSafety:
    def test_conflicting_proposal_is_blocked_while_unrelated_one_proceeds(
        self, coordinator, versions
    ):
        a, b, c = proposal_a(versions), proposal_b(versions), proposal_c(versions)

        a_outcome = coordinator.prepare(a)
        assert a_outcome.prepared

        b_outcome = coordinator.prepare(b)
        assert b_outcome.state is TransactionState.BLOCKED
        assert b_outcome.conflicts

        c_outcome = coordinator.prepare(c)
        assert c_outcome.prepared, "unrelated work must not be serialised behind the conflict"

        assert _approve_and_commit(coordinator, c, c_outcome).committed

    def test_blocked_proposal_reports_the_lineage_evidence(self, coordinator, versions):
        coordinator.prepare(proposal_a(versions))
        outcome = coordinator.prepare(proposal_b(versions))
        paths = [c.lineage_path for c in outcome.conflicts if c.lineage_path]
        assert paths, "a blocked proposal must carry its lineage evidence"
        assert any(FCT_REVENUE in path for path in paths)

    def test_blocked_proposal_can_be_reanalyzed_after_the_other_commits(
        self, coordinator, versions
    ):
        a, b = proposal_a(versions), proposal_b(versions)
        a_outcome = coordinator.prepare(a)
        b_outcome = coordinator.prepare(b)
        assert b_outcome.state is TransactionState.BLOCKED

        _approve_and_commit(coordinator, a, a_outcome)

        retry = coordinator.reanalyze(b)
        assert retry.state in {TransactionState.PREPARED, TransactionState.ABORTED}

    def test_reanalyze_rejects_non_blocked_proposals(self, coordinator, versions):
        c = proposal_c(versions)
        coordinator.prepare(c)
        with pytest.raises(CoordinatorError):
            coordinator.reanalyze(c)


class TestCommitRecheck:
    def test_commit_rereads_the_graph_and_fails_closed_on_drift(
        self, coordinator, versions, monkeypatch, provider
    ):
        """The graph changing between prepare and commit must abort the commit."""
        a = proposal_a(versions)
        outcome = coordinator.prepare(a)
        coordinator.approve(outcome.token, "release-manager")

        original = provider.snapshot

        def drifted():
            snapshot = original()
            entity = snapshot.entities[FCT_REVENUE]
            entity.fields = [f for f in entity.fields if f.path != "refund_amount"]
            return snapshot

        monkeypatch.setattr(provider, "snapshot", drifted)

        result = coordinator.commit(a, outcome.token)
        assert result.state is TransactionState.ABORTED
        assert result.drift_detected
        assert "drift" in result.reason.lower()

    def test_no_drift_permits_commit(self, coordinator, versions):
        a = proposal_a(versions)
        outcome = coordinator.prepare(a)
        result = _approve_and_commit(coordinator, a, outcome)
        assert result.committed
        assert not result.drift_detected

    def test_expired_lease_before_commit_expires_the_transaction(
        self, coordinator, versions, clock
    ):
        a = proposal_a(versions)
        outcome = coordinator.prepare(a)
        coordinator.approve(outcome.token, "release-manager")

        clock.advance(121)  # lease was 120s

        result = coordinator.commit(a, outcome.token)
        assert result.state is TransactionState.EXPIRED
        assert "lease" in result.reason.lower()

    def test_unknown_token_aborts(self, coordinator, versions):
        a = proposal_a(versions)
        outcome = coordinator.prepare(a)
        forged = outcome.token.model_copy(update={"token": "prepared-forged"})
        result = coordinator.commit(a, forged)
        assert result.state is TransactionState.ABORTED
        assert "token" in result.reason.lower()


class TestExecutionAndValidation:
    def test_commit_really_rewrites_the_artifact(self, coordinator, versions, executor):
        before = executor.read("fct_revenue.sql")
        assert "gross_revenue" in before

        a = proposal_a(versions)
        outcome = coordinator.prepare(a)
        result = _approve_and_commit(coordinator, a, outcome)
        assert result.committed

        after = executor.read("fct_revenue.sql")
        assert "recognized_revenue" in after
        assert "gross_revenue" not in after

    def test_validation_failure_rolls_the_artifact_back_and_aborts(
        self, coordinator, versions, executor
    ):
        a = proposal_a(versions)
        # Demand something the change cannot satisfy.
        a.validation_plan.expect_artifact_contains = ["a_column_that_will_never_exist"]
        before = executor.read("fct_revenue.sql")

        outcome = coordinator.prepare(a)
        result = _approve_and_commit(coordinator, a, outcome)

        assert result.state is TransactionState.ABORTED
        assert executor.read("fct_revenue.sql") == before, "artifact was not rolled back"

    def test_another_agents_downstream_artifact_does_not_veto_the_upstream_commit(
        self, coordinator, versions, executor
    ):
        """A's rename leaves B's metric referencing the old column, and A still commits.

        Failing A here would be a deadlock: B cannot rebase until A lands, so an upstream
        schema change could never be made. Cross-owner breakage is handled at prepare time by
        the conflict engine ordering the two proposals, not by aborting the upstream one.
        """
        a = proposal_a(versions)
        assert "gross_revenue" in executor.read("metric_net_revenue.sql")

        outcome = coordinator.prepare(a)
        result = _approve_and_commit(coordinator, a, outcome)

        assert result.committed
        # B's artifact is untouched and still stale; that is precisely why B must rebase.
        assert "gross_revenue" in executor.read("metric_net_revenue.sql")

    def test_self_inconsistent_rename_fails_validation(self, coordinator, versions, executor):
        """When a proposal owns both ends, a broken downstream reference does fail it."""
        a = proposal_a(versions)
        # Declare ownership of the downstream metric too, without fixing its SQL.
        a.write_set = [*a.write_set, METRIC_NET_REVENUE]

        outcome = coordinator.prepare(a)
        result = _approve_and_commit(coordinator, a, outcome)

        assert result.state is TransactionState.ABORTED
        assert "downstream" in result.reason.lower()
        assert "gross_revenue" in executor.read("fct_revenue.sql"), "rollback did not restore"


class TestIdempotencyAndAbort:
    def test_committing_twice_returns_the_same_terminal_state(self, coordinator, versions):
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        first = _approve_and_commit(coordinator, c, outcome)
        second = coordinator.commit(c, outcome.token)
        assert first.committed and second.committed
        assert second.reason == "already committed"

    def test_abort_is_idempotent(self, coordinator, versions):
        c = proposal_c(versions)
        coordinator.prepare(c)
        first = coordinator.abort(c, "operator cancelled")
        second = coordinator.abort(c, "operator cancelled again")
        assert first.state is TransactionState.ABORTED
        assert second.state is TransactionState.ABORTED

    def test_abort_releases_the_lease(self, coordinator, versions):
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        coordinator.abort(c, "operator cancelled")
        assert not coordinator.leases.is_live(outcome.lease.lease_id)

    def test_commit_after_abort_does_not_resurrect(self, coordinator, versions):
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        coordinator.abort(c, "operator cancelled")
        result = coordinator.commit(c, outcome.token)
        assert result.state is TransactionState.ABORTED

    def test_preparing_a_terminal_proposal_id_raises_a_clear_error(self, coordinator, versions):
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        _approve_and_commit(coordinator, c, outcome)
        with pytest.raises(CoordinatorError, match="already reached COMMITTED"):
            coordinator.prepare(proposal_c(versions))

    def test_commit_from_blocked_state_raises(self, coordinator, versions):
        a, b = proposal_a(versions), proposal_b(versions)
        a_outcome = coordinator.prepare(a)
        coordinator.prepare(b)
        with pytest.raises(CoordinatorError):
            coordinator.commit(b, a_outcome.token)


class TestAuditTrail:
    def test_every_transition_is_recorded(self, coordinator, versions, store):
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        _approve_and_commit(coordinator, c, outcome)

        states = [event.to_state for event in store.list_events(c.proposal_id)]
        assert states == [
            TransactionState.SUBMITTED,
            TransactionState.ANALYZING,
            TransactionState.PREPARED,
            TransactionState.EXECUTING,
            TransactionState.VALIDATING,
            TransactionState.COMMITTED,
        ]

    def test_events_have_monotonic_sequence_numbers(self, coordinator, versions, store):
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        _approve_and_commit(coordinator, c, outcome)
        sequences = [event.sequence for event in store.list_events()]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)

    def test_commit_event_carries_context_source(self, coordinator, versions, store):
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        _approve_and_commit(coordinator, c, outcome)
        committed = [
            e for e in store.list_events(c.proposal_id) if e.to_state is TransactionState.COMMITTED
        ][0]
        assert committed.evidence["context_source"] == "fixture"
