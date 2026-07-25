"""The four-agent coordination scenario, run end to end.

Deterministic by construction: proposals are submitted in a fixed order through explicit barriers,
and the clock is injected. Nothing depends on thread timing, which ``AGENTS.md`` forbids for the
demo.

The narrative:

1. **A** proposes renaming ``gross_revenue``. High blast radius, so it needs approval.
2. **B** proposes a metric change. The coordinator finds a lineage-mediated conflict with A and
   blocks B, quoting the lineage path as evidence.
3. **C** proposes an unrelated support change. Lineage-disjoint, so it prepares and commits
   immediately rather than queueing behind A or B.
4. **A** is approved and commits. Its lease is released.
5. **B** is re-analysed now that A is terminal, and proceeds.
6. **D** submits a stale expected version and fails closed.

Run with ``gtc-demo``. Receipts land under ``APP_STATE_DIR/receipts``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from graph_traffic_control.config import Settings, get_settings
from graph_traffic_control.context.fixture import FixtureContextProvider
from graph_traffic_control.context.namespace import Namespace
from graph_traffic_control.demo.agents import proposal_a, proposal_b, proposal_c, proposal_d
from graph_traffic_control.demo.seed import ARTIFACT_BY_URN, seed
from graph_traffic_control.domain.clock import SystemClock
from graph_traffic_control.domain.models import ChangeProposal, TransactionState
from graph_traffic_control.execute.targets import ArtifactExecutor
from graph_traffic_control.receipts import ReceiptWriter
from graph_traffic_control.txn.coordinator import Coordinator, PrepareOutcome

APPROVER = "release-manager"


class ScenarioRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.namespace = Namespace.from_settings(settings)
        self.clock = SystemClock()
        self.provider = FixtureContextProvider(settings.fixture_root, self.namespace, self.clock)
        self.executor = ArtifactExecutor(settings.state_dir)
        self.receipts = ReceiptWriter(settings.state_dir, secrets=(settings.datahub_token,))

        from graph_traffic_control.txn.store import TransactionStore

        self.store = TransactionStore(settings.db_path)
        self.coordinator = Coordinator(
            store=self.store,
            provider=self.provider,
            namespace=self.namespace,
            clock=self.clock,
            executor=self.executor,
            downstream_artifacts=ARTIFACT_BY_URN,
        )
        self.steps: list[dict[str, Any]] = []

    def close(self) -> None:
        self.store.close()

    def current_versions(self) -> dict[str, str]:
        snapshot = self.provider.snapshot()
        return {urn: entity.version_fingerprint() for urn, entity in snapshot.entities.items()}

    def _record(self, step: str, detail: dict[str, Any]) -> None:
        self.steps.append({"step": step, **detail})

    def _write_proposal_receipt(self, proposal: ChangeProposal, outcome: PrepareOutcome) -> None:
        self.receipts.proposal_receipt(
            proposal=proposal,
            impact=outcome.impact,
            conflicts=outcome.conflicts,
            state=outcome.state,
            context_source=self.provider.source,
            snapshot_fingerprint=(
                outcome.token.snapshot_fingerprint if outcome.token else ""
            ),
        )
        if outcome.lease and outcome.token:
            self.receipts.lease_receipt(outcome.lease, outcome.token)

    def _commit(self, proposal: ChangeProposal, outcome: PrepareOutcome) -> Any:
        if outcome.token.approval_required:
            self.coordinator.approve(outcome.token, APPROVER)
        token = self.coordinator.token(outcome.token.token)
        result = self.coordinator.commit(proposal, token)
        receipt_path = self.receipts.commit_receipt(
            proposal=proposal,
            final_state=result.state,
            events=self.store.list_events(proposal.proposal_id),
            context_source=self.provider.source,
            prepare_fingerprint=result.prepare_fingerprint,
            commit_fingerprint=result.commit_fingerprint,
            artifact_diff=result.artifact_diff,
            validation=result.validation,
            writeback=result.writeback,
            verification=result.verification,
            abort_reason=result.reason or None,
        )
        result.verification.receipts.append(receipt_path.name)
        return result

    def run(self, echo=print) -> dict[str, Any]:  # noqa: T202 - CLI output is the point
        versions = self.current_versions()

        echo("\n=== Graph Traffic Control: four-agent coordination ===")
        echo(f"context source: {self.provider.source}   namespace: {self.namespace.urn_prefix}\n")

        # 1. Agent A
        a = proposal_a(versions)
        a_outcome = self.coordinator.prepare(a)
        self._write_proposal_receipt(a, a_outcome)
        echo(f"[A] {a.intent}")
        echo(f"    writes {a.write_set[0].split(',')[1]}")
        echo(f"    -> {a_outcome.state.value}  blast radius {a_outcome.impact.blast_radius}"
             f"  approval required: {a_outcome.token.approval_required}")
        self._record("prepare-a", {"state": a_outcome.state.value})

        # 2. Agent B: conflicts with A through lineage
        b = proposal_b(versions)
        b_outcome = self.coordinator.prepare(b)
        self._write_proposal_receipt(b, b_outcome)
        echo(f"\n[B] {b.intent}")
        echo(f"    writes {b.write_set[0].split(',')[1]}  (a different file from A)")
        echo(f"    -> {b_outcome.state.value}")
        for conflict in b_outcome.conflicts:
            if conflict.lineage_path:
                path = " -> ".join(p.split(",")[1] for p in conflict.lineage_path)
                echo(f"    CONFLICT [{conflict.decision.value}] via lineage: {path}")
                self._record(
                    "conflict",
                    {
                        "decision": conflict.decision.value,
                        "lineage_path": conflict.lineage_path,
                        "explanation": conflict.explanation,
                    },
                )

        # 3. Agent C: unrelated, proceeds in parallel
        c = proposal_c(versions)
        c_outcome = self.coordinator.prepare(c)
        self._write_proposal_receipt(c, c_outcome)
        echo(f"\n[C] {c.intent}")
        echo(f"    -> {c_outcome.state.value} (lineage-disjoint; not queued behind A or B)")
        c_result = self._commit(c, c_outcome)
        echo(f"    -> {c_result.state.value}  {c_result.artifact_diff}")
        self._record("commit-c", {"state": c_result.state.value})

        # 4. Agent A commits after approval
        echo(f"\n[A] approved by {APPROVER}")
        a_result = self._commit(a, a_outcome)
        echo(f"    -> {a_result.state.value}  {a_result.artifact_diff}")
        echo(f"    graph fingerprint at prepare {a_result.prepare_fingerprint}"
             f" / at commit {a_result.commit_fingerprint}")
        self._record("commit-a", {"state": a_result.state.value})

        # 5. Agent B re-analysed now that A is terminal
        if self.store.get_state(b.proposal_id) is TransactionState.BLOCKED:
            echo("\n[B] re-analysed now that A has committed")
            retry = self.coordinator.reanalyze(b)
            self._write_proposal_receipt(b, retry)
            echo(f"    -> {retry.state.value}"
                 + (f"  {retry.reason}" if retry.reason else ""))
            if retry.prepared:
                b_result = self._commit(b, retry)
                echo(f"    -> {b_result.state.value}")
                self._record("commit-b", {"state": b_result.state.value})
            else:
                self._record("reanalyze-b", {"state": retry.state.value})

        # 6. Agent D: stale expected version
        d = proposal_d()
        d_outcome = self.coordinator.prepare(d)
        self._write_proposal_receipt(d, d_outcome)
        echo(f"\n[D] {d.intent}")
        echo(f"    -> {d_outcome.state.value}  {d_outcome.reason}")
        self._record("prepare-d", {"state": d_outcome.state.value, "reason": d_outcome.reason})

        echo(f"\nreceipts: {self.receipts.directory}")
        echo(f"events:   {len(self.store.list_events())} audited transitions\n")

        return {
            "steps": self.steps,
            "events": [event.model_dump(mode="json") for event in self.store.list_events()],
        }


def export_examples(runner: ScenarioRunner, trace: dict[str, Any], target: Path) -> None:
    """Write the committed, judge-facing examples directory."""
    target.mkdir(parents=True, exist_ok=True)
    for name, factory in (("a", proposal_a), ("b", proposal_b), ("c", proposal_c)):
        payload = factory().model_dump(mode="json")
        (target / f"agent-{name}-proposal.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    (target / "transaction-trace.json").write_text(
        json.dumps(trace, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Graph Traffic Control demo scenario.")
    parser.add_argument(
        "--export-examples",
        metavar="DIR",
        help="Write proposals and the transaction trace to a directory (for examples/).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    seed(settings)

    runner = ScenarioRunner(settings)
    try:
        trace = runner.run()
        if args.export_examples:
            export_examples(runner, trace, Path(args.export_examples))
            print(f"examples written to {args.export_examples}")
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"Demo failed: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
