"""Post-execution validation.

Checks the changed artifact against the proposal's declared validation plan, and checks that
downstream artifacts still resolve against the new schema. The downstream check is what catches
Agent B's metric still referencing a column Agent A renamed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graph_traffic_control.conflict.lineage import build_graph, descendants_within
from graph_traffic_control.domain.models import ChangeProposal, GraphSnapshot
from graph_traffic_control.execute.targets import ArtifactExecutor


@dataclass
class ValidationResult:
    passed: bool
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def as_evidence(self) -> dict[str, str]:
        return {
            "checks_run": str(len(self.checks)),
            "failures": "; ".join(self.failures) if self.failures else "none",
        }


class Validator:
    def __init__(self, executor: ArtifactExecutor) -> None:
        self._executor = executor

    def validate(
        self,
        proposal: ChangeProposal,
        snapshot: GraphSnapshot,
        downstream_artifacts: dict[str, str] | None = None,
    ) -> ValidationResult:
        result = ValidationResult(passed=True)
        plan = proposal.validation_plan

        try:
            content = self._executor.read(proposal.action.artifact_path)
        except Exception as exc:  # noqa: BLE001 - surfaced as a validation failure
            result.passed = False
            result.failures.append(f"could not read changed artifact: {exc}")
            return result

        for needle in plan.expect_artifact_contains:
            result.checks.append(f"artifact contains {needle!r}")
            if needle not in content:
                result.passed = False
                result.failures.append(f"expected {needle!r} in changed artifact")

        for needle in plan.expect_artifact_absent:
            result.checks.append(f"artifact no longer contains {needle!r}")
            if needle in content:
                result.passed = False
                result.failures.append(f"expected {needle!r} to be absent from changed artifact")

        if plan.expect_downstream_resolvable and proposal.action.kind == "rename_column":
            self._check_downstream(proposal, snapshot, downstream_artifacts or {}, result)

        return result

    def _check_downstream(
        self,
        proposal: ChangeProposal,
        snapshot: GraphSnapshot,
        downstream_artifacts: dict[str, str],
        result: ValidationResult,
    ) -> None:
        """A renamed column must not still be referenced by a downstream artifact *this
        proposal owns*.

        The scope is deliberately limited to the proposal's own write set. A downstream asset
        belonging to another agent can also break, but unilaterally failing the upstream
        proposal for that would be the wrong remedy: it would make it impossible to ever land
        an upstream schema change, since the downstream owner cannot rebase until the upstream
        one commits. Cross-owner breakage is detected at prepare time by the conflict engine,
        which orders the two proposals and requires the downstream one to rebase. That
        sequencing is the product; this check is only a self-consistency guard.
        """
        old_name = proposal.action.field_path
        if not old_name:
            return

        owned = set(proposal.write_set)
        graph = build_graph(snapshot)
        for downstream_urn in sorted(descendants_within(graph, proposal.action.target_urn, 3)):
            if downstream_urn not in owned:
                continue
            artifact = downstream_artifacts.get(downstream_urn)
            if artifact is None:
                continue
            result.checks.append(f"downstream {downstream_urn} resolves")
            try:
                text = self._executor.read(artifact)
            except Exception:  # noqa: BLE001 - missing downstream artifact is not a failure
                continue
            if old_name in text:
                result.passed = False
                result.failures.append(
                    f"downstream artifact {artifact} still references renamed column "
                    f"{old_name!r} ({downstream_urn})"
                )
