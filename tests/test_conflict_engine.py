"""The conflict matrix, row by row.

The central claim of this project lives in row 3: two proposals whose declared read and write
sets do not intersect still conflict, and only DataHub lineage reveals it. If
``test_row3_*`` ever fails, the submission's thesis is broken.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.conflict.engine import (
    blocking_conflicts,
    detect_conflicts,
    requires_approval,
)
from graph_traffic_control.demo.agents import (
    FCT_REVENUE,
    FCT_SUPPORT_SLA,
    METRIC_NET_REVENUE,
    STG_SALES,
    proposal_a,
    proposal_b,
    proposal_c,
)
from graph_traffic_control.domain.models import (
    AgentIdentity,
    ChangeAction,
    ChangeProposal,
    ConflictDecision,
    ConflictKind,
    Criticality,
    FieldRef,
)


def _proposal(pid: str, *, reads: list[str], writes: list[str], field: str | None = None,
              new_field: str | None = None, artifact: str = "fct_revenue.sql") -> ChangeProposal:
    kind = "rename_column" if field and new_field else "update_model"
    return ChangeProposal(
        proposal_id=pid,
        agent=AgentIdentity(agent_id=f"agent-{pid}", display_name=pid),
        intent=f"test proposal {pid}",
        read_set=reads,
        write_set=writes,
        write_fields=[FieldRef(urn=writes[0], field_path=field)] if field else [],
        action=ChangeAction(
            kind=kind,
            target_urn=writes[0],
            artifact_path=artifact,
            field_path=field,
            new_field_path=new_field,
        ),
    )


class TestRow1WriteWrite:
    def test_same_target_blocks(self, snapshot):
        one = _proposal("p1", reads=[], writes=[FCT_REVENUE])
        two = _proposal("p2", reads=[], writes=[FCT_REVENUE])
        conflicts = detect_conflicts(one, two, snapshot)
        assert conflicts[0].kind is ConflictKind.WRITE_WRITE
        assert conflicts[0].decision is ConflictDecision.BLOCK
        assert blocking_conflicts(conflicts)

    def test_same_column_blocks(self, snapshot):
        one = _proposal("p1", reads=[], writes=[FCT_REVENUE], field="gross_revenue",
                        new_field="a")
        two = _proposal("p2", reads=[], writes=[FCT_REVENUE], field="gross_revenue",
                        new_field="b")
        conflicts = detect_conflicts(one, two, snapshot)
        assert conflicts[0].decision is ConflictDecision.BLOCK
        assert conflicts[0].field_path == "gross_revenue"

    def test_disjoint_columns_on_same_table_only_warn(self, snapshot):
        """Column-level refinement: distinct columns are not a hard collision."""
        one = _proposal("p1", reads=[], writes=[FCT_REVENUE], field="gross_revenue",
                        new_field="recognized_revenue")
        two = _proposal("p2", reads=[], writes=[FCT_REVENUE], field="refund_amount",
                        new_field="refunds")
        conflicts = detect_conflicts(one, two, snapshot)
        assert conflicts[0].decision is ConflictDecision.WARN
        assert not blocking_conflicts(conflicts)


class TestRow2WriteRead:
    def test_write_of_directly_read_asset_orders(self, snapshot):
        writer = _proposal("w", reads=[], writes=[FCT_REVENUE])
        reader = _proposal("r", reads=[FCT_REVENUE], writes=[FCT_SUPPORT_SLA],
                           artifact="fct_support_sla.sql")
        conflicts = detect_conflicts(writer, reader, snapshot)
        kinds = {c.kind for c in conflicts}
        assert ConflictKind.WRITE_READ in kinds
        assert any(c.decision is ConflictDecision.ORDER for c in conflicts)


class TestRow3LineageMediated:
    """The demo's central proof."""

    def test_a_and_b_write_different_assets_and_different_files(self):
        """What file-level coordination sees: two unrelated edits."""
        a, b = proposal_a(), proposal_b()
        assert set(a.write_set).isdisjoint(set(b.write_set))
        assert a.action.artifact_path != b.action.artifact_path
        assert a.action.target_urn != b.action.target_urn

    def test_conflict_holds_with_no_declared_urn_overlap_at_all(self, snapshot):
        """The strongest form of the claim.

        Here the two proposals share no URN in any set: not writes, not reads. The upstream
        proposal writes ``stg_sales``; the downstream one reads and writes assets two and three
        hops away. Only the lineage graph connects them.
        """
        upstream = _proposal(
            "p-upstream",
            reads=[],
            writes=[STG_SALES],
            field="line_amount",
            new_field="net_line_amount",
            artifact="stg_sales.sql",
        )
        downstream = _proposal(
            "p-downstream",
            reads=[METRIC_NET_REVENUE],
            writes=[FCT_SUPPORT_SLA],
            artifact="fct_support_sla.sql",
        )
        assert set(upstream.write_set + upstream.read_set).isdisjoint(
            set(downstream.write_set + downstream.read_set)
        ), "the premise of this test is zero declared overlap"

        conflicts = detect_conflicts(upstream, downstream, snapshot)
        lineage = [c for c in conflicts if c.kind is ConflictKind.UPSTREAM_SCHEMA]
        assert lineage, "lineage-mediated conflict missed despite a real graph path"
        assert lineage[0].lineage_path[0] == STG_SALES
        assert lineage[0].lineage_path[-1] == METRIC_NET_REVENUE
        assert len(lineage[0].lineage_path) == 3  # stg_sales -> fct_revenue -> metric

    def test_rename_reaches_downstream_metric_through_lineage(self, snapshot):
        """A renames a column on fct_revenue; B's metric sits downstream via lineage."""
        a, b = proposal_a(), proposal_b()
        conflicts = detect_conflicts(a, b, snapshot)
        lineage = [c for c in conflicts if c.kind is ConflictKind.UPSTREAM_SCHEMA]
        assert lineage, "lineage-mediated conflict was not detected"

        conflict = lineage[0]
        assert conflict.decision is ConflictDecision.REBASE
        assert conflict.lineage_path[0] == FCT_REVENUE
        assert conflict.lineage_path[-1] == METRIC_NET_REVENUE
        assert conflict.field_path == "gross_revenue"
        assert "lineage hop" in conflict.explanation

    def test_lineage_evidence_is_the_shortest_path(self, snapshot):
        conflicts = detect_conflicts(proposal_a(), proposal_b(), snapshot)
        lineage = [c for c in conflicts if c.kind is ConflictKind.UPSTREAM_SCHEMA][0]
        # fct_revenue -> metric_net_revenue is one hop; nothing shorter exists.
        assert len(lineage.lineage_path) == 2

    def test_conflict_is_blocking(self, snapshot):
        conflicts = detect_conflicts(proposal_a(), proposal_b(), snapshot)
        assert blocking_conflicts(conflicts)


class TestRow4ReadOnly:
    def test_two_read_only_proposals_do_not_conflict(self, snapshot):
        # A proposal must declare a write set, so "read only" is modelled as writing an asset
        # nobody else touches while both read the same upstream.
        one = _proposal("p1", reads=[FCT_REVENUE], writes=[METRIC_NET_REVENUE],
                        artifact="metric_net_revenue.sql")
        two = _proposal("p2", reads=[FCT_REVENUE], writes=[FCT_SUPPORT_SLA],
                        artifact="fct_support_sla.sql")
        conflicts = detect_conflicts(one, two, snapshot)
        assert not blocking_conflicts(conflicts)


class TestRow5DisjointLineage:
    def test_support_branch_never_blocks_revenue_branch(self, snapshot):
        """Agent C's parallel commit depends on this."""
        for other in (proposal_a(), proposal_b()):
            assert not blocking_conflicts(detect_conflicts(proposal_c(), other, snapshot))
            assert not blocking_conflicts(detect_conflicts(other, proposal_c(), snapshot))

    def test_c_against_a_produces_no_lineage_conflict(self, snapshot):
        conflicts = detect_conflicts(proposal_c(), proposal_a(), snapshot)
        assert not [c for c in conflicts if c.kind is ConflictKind.UPSTREAM_SCHEMA]


class TestRow6SharedDomainFalsePositiveGuard:
    """Without this the coordinator degenerates into a global lock."""

    def test_shared_domain_without_lineage_warns_but_does_not_block(self, snapshot):
        conflicts = detect_conflicts(proposal_c(), proposal_a(), snapshot)
        assert conflicts, "expected at least a warning"
        assert all(c.decision is ConflictDecision.WARN for c in conflicts)
        assert not blocking_conflicts(conflicts)

    def test_warning_names_both_proposals(self, snapshot):
        conflicts = detect_conflicts(proposal_c(), proposal_a(), snapshot)
        shared = [c for c in conflicts if c.kind is ConflictKind.SHARED_DOMAIN][0]
        assert "prop-c-support-sla" in shared.explanation
        assert "prop-a-rename-revenue" in shared.explanation


class TestRow8Approval:
    def test_high_blast_radius_requires_approval(self):
        assert requires_approval(3, Criticality.TIER_3) is True

    def test_tier_one_asset_requires_approval(self):
        assert requires_approval(0, Criticality.TIER_1) is True

    def test_small_low_tier_change_does_not(self):
        assert requires_approval(0, Criticality.TIER_3) is False


class TestEngineHygiene:
    def test_proposal_never_conflicts_with_itself(self, snapshot):
        assert detect_conflicts(proposal_a(), proposal_a(), snapshot) == []

    def test_depth_bound_prevents_unbounded_expansion(self, snapshot):
        """A zero-hop bound must not report lineage conflicts."""
        conflicts = detect_conflicts(proposal_a(), proposal_b(), snapshot, max_depth=0)
        assert not [c for c in conflicts if c.kind is ConflictKind.UPSTREAM_SCHEMA]

    @pytest.mark.parametrize("decision", list(ConflictDecision))
    def test_blocking_classification_is_explicit(self, decision):
        expected = decision is not ConflictDecision.WARN
        from graph_traffic_control.domain.models import Conflict

        conflict = Conflict(
            kind=ConflictKind.WRITE_WRITE,
            decision=decision,
            proposal_id="x",
            other_proposal_id="y",
            subject_urn=STG_SALES,
            explanation="",
        )
        assert conflict.blocking is expected
