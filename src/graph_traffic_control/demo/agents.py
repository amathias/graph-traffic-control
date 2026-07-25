"""Deterministic demo agent clients.

Four agents, matching ``PROJECT_BRIEF.md``:

- **A** renames ``gross_revenue`` on the revenue fact. High blast radius, needs approval.
- **B** redefines the net-revenue metric, reading the column A is renaming. A and B declare
  **no overlapping URNs**; only DataHub lineage reveals their conflict.
- **C** updates the support SLA model. Lineage-disjoint, so it proceeds in parallel.
- **D** submits a stale expected version and must fail closed.

These are deterministic clients, not model-driven. ``AGENTS.md`` permits optional LLM-generated
explanations, but the coordination behaviour must be real, so nothing here is generated.
"""

from __future__ import annotations

from graph_traffic_control.demo.seed import dataset_urn
from graph_traffic_control.domain.models import (
    AgentIdentity,
    ChangeAction,
    ChangeProposal,
    FieldRef,
    RiskDeclaration,
    RiskLevel,
    ValidationPlan,
)

STG_SALES = dataset_urn("traffic.stg_sales")
FCT_REVENUE = dataset_urn("traffic.fct_revenue")
METRIC_NET_REVENUE = dataset_urn("traffic.metric_net_revenue")
STG_SUPPORT = dataset_urn("traffic.stg_support")
FCT_SUPPORT_SLA = dataset_urn("traffic.fct_support_sla")

AGENT_A = AgentIdentity(
    agent_id="agent-a",
    display_name="Revenue Schema Agent",
    capabilities=["rename_column"],
)
AGENT_B = AgentIdentity(
    agent_id="agent-b",
    display_name="Semantic Metric Agent",
    capabilities=["redefine_metric"],
)
AGENT_C = AgentIdentity(
    agent_id="agent-c",
    display_name="Support Pipeline Agent",
    capabilities=["update_model"],
)
AGENT_D = AgentIdentity(
    agent_id="agent-d",
    display_name="Stale Context Agent",
    capabilities=["update_model"],
)


def proposal_a(expected_versions: dict[str, str] | None = None) -> ChangeProposal:
    """Rename ``gross_revenue`` to ``recognized_revenue`` on the revenue fact."""
    return ChangeProposal(
        proposal_id="prop-a-rename-revenue",
        agent=AGENT_A,
        intent="Rename gross_revenue to recognized_revenue for ASC 606 alignment",
        read_set=[STG_SALES],
        write_set=[FCT_REVENUE],
        write_fields=[FieldRef(urn=FCT_REVENUE, field_path="gross_revenue")],
        expected_versions=expected_versions or {},
        action=ChangeAction(
            kind="rename_column",
            target_urn=FCT_REVENUE,
            artifact_path="fct_revenue.sql",
            field_path="gross_revenue",
            new_field_path="recognized_revenue",
        ),
        validation_plan=ValidationPlan(
            expect_artifact_contains=["recognized_revenue"],
            expect_artifact_absent=["gross_revenue"],
            expect_downstream_resolvable=True,
        ),
        requested_lease_seconds=120,
        risk=RiskDeclaration(level=RiskLevel.HIGH, blast_radius_hint=2, requires_approval=True),
        evidence=["ASC 606 alignment ticket FIN-4821"],
    )


def proposal_b(expected_versions: dict[str, str] | None = None) -> ChangeProposal:
    """Redefine the net-revenue metric. Reads the column A is renaming.

    Note the sets: B writes only ``metric_net_revenue`` and reads only ``fct_revenue``. It shares
    no write target with A. File-level coordination sees nothing.
    """
    return ChangeProposal(
        proposal_id="prop-b-net-revenue-metric",
        agent=AGENT_B,
        intent="Publish net revenue metric derived from gross_revenue",
        read_set=[FCT_REVENUE],
        write_set=[METRIC_NET_REVENUE],
        read_fields=[FieldRef(urn=FCT_REVENUE, field_path="gross_revenue")],
        expected_versions=expected_versions or {},
        action=ChangeAction(
            kind="redefine_metric",
            target_urn=METRIC_NET_REVENUE,
            artifact_path="metric_net_revenue.sql",
        ),
        validation_plan=ValidationPlan(
            expect_artifact_contains=["net_revenue"], expect_downstream_resolvable=False
        ),
        requested_lease_seconds=120,
        risk=RiskDeclaration(level=RiskLevel.MEDIUM, blast_radius_hint=1),
        evidence=["Metric request ANALYTICS-338"],
    )


def proposal_c(expected_versions: dict[str, str] | None = None) -> ChangeProposal:
    """Update the support SLA model. Lineage-disjoint from A and B."""
    return ChangeProposal(
        proposal_id="prop-c-support-sla",
        agent=AGENT_C,
        intent="Recalculate support SLA attainment with the four-hour target",
        read_set=[STG_SUPPORT],
        write_set=[FCT_SUPPORT_SLA],
        expected_versions=expected_versions or {},
        action=ChangeAction(
            kind="update_model",
            target_urn=FCT_SUPPORT_SLA,
            artifact_path="fct_support_sla.sql",
        ),
        validation_plan=ValidationPlan(
            expect_artifact_contains=["sla_attainment"], expect_downstream_resolvable=False
        ),
        requested_lease_seconds=120,
        risk=RiskDeclaration(level=RiskLevel.LOW, blast_radius_hint=0),
        evidence=["Support ops request SUP-91"],
    )


def proposal_d() -> ChangeProposal:
    """Deliberately stale: declares an expected version that cannot match the live graph."""
    return ChangeProposal(
        proposal_id="prop-d-stale",
        agent=AGENT_D,
        intent="Adjust revenue rounding using stale graph context",
        read_set=[STG_SALES],
        write_set=[FCT_REVENUE],
        expected_versions={FCT_REVENUE: "0000000000000000"},
        action=ChangeAction(
            kind="update_model",
            target_urn=FCT_REVENUE,
            artifact_path="fct_revenue.sql",
        ),
        requested_lease_seconds=60,
        risk=RiskDeclaration(level=RiskLevel.MEDIUM),
        evidence=["Stale snapshot captured before A committed"],
    )


ALL_PROPOSALS = {
    "a": proposal_a,
    "b": proposal_b,
    "c": proposal_c,
    "d": proposal_d,
}
