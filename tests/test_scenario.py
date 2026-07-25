"""The end-to-end demo narrative.

``AGENTS.md`` defines done as: a reviewer can launch the demo agents, watch one unrelated change
commit while two semantically conflicting changes are sequenced, observe a stale proposal fail,
and confirm verification. This locks that narrative so a refactor cannot quietly break it.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.demo.scenario import ScenarioRunner, export_examples
from graph_traffic_control.domain.models import TransactionState


@pytest.fixture
def runner(seeded_settings):
    runner = ScenarioRunner(seeded_settings)
    yield runner
    runner.close()


@pytest.fixture
def result(runner):
    return runner.run(echo=lambda *args, **kwargs: None)


class TestNarrative:
    def test_unrelated_change_commits(self, runner, result):
        assert runner.store.get_state("prop-c-support-sla") is TransactionState.COMMITTED

    def test_conflicting_pair_is_sequenced_not_run_together(self, runner, result):
        steps = {step["step"] for step in result["steps"]}
        assert "conflict" in steps, "B was never blocked by A"
        assert runner.store.get_state("prop-a-rename-revenue") is TransactionState.COMMITTED

    def test_conflict_evidence_is_a_lineage_path(self, result):
        conflicts = [step for step in result["steps"] if step["step"] == "conflict"]
        assert conflicts
        path = conflicts[0]["lineage_path"]
        assert len(path) >= 2
        assert "fct_revenue" in path[0]
        assert "metric_net_revenue" in path[-1]
        assert conflicts[0]["decision"] == "REBASE"

    def test_blocked_proposal_proceeds_after_the_other_commits(self, runner, result):
        assert runner.store.get_state("prop-b-net-revenue-metric") in {
            TransactionState.COMMITTED,
            TransactionState.PREPARED,
        }

    def test_stale_proposal_fails_closed(self, runner, result):
        assert runner.store.get_state("prop-d-stale") is TransactionState.ABORTED

    def test_artifact_was_really_rewritten(self, runner, result):
        content = runner.executor.read("fct_revenue.sql")
        assert "recognized_revenue" in content
        assert "gross_revenue" not in content

    def test_every_transition_is_audited(self, result):
        assert len(result["events"]) >= 20

    def test_no_lease_is_left_active_at_the_end(self, runner, result):
        assert runner.coordinator.leases.active_leases() == []


class TestEvidence:
    def test_receipts_are_written_for_every_agent(self, runner, result, seeded_settings):
        names = sorted(p.name for p in (seeded_settings.state_dir / "receipts").iterdir())
        for proposal_id in (
            "prop-a-rename-revenue",
            "prop-b-net-revenue-metric",
            "prop-c-support-sla",
            "prop-d-stale",
        ):
            assert any(name == f"proposal-{proposal_id}.json" for name in names)

    def test_receipts_contain_no_raw_prepared_token(self, runner, result, seeded_settings):
        for receipt in (seeded_settings.state_dir / "receipts").iterdir():
            assert "prepared-" not in receipt.read_text(encoding="utf-8")

    def test_export_writes_the_judge_facing_examples(self, runner, result, tmp_path):
        target = tmp_path / "examples"
        export_examples(runner, result, target)
        names = sorted(p.name for p in target.iterdir())
        assert names == [
            "agent-a-proposal.json",
            "agent-b-proposal.json",
            "agent-c-proposal.json",
            "transaction-trace.json",
        ]

    def test_context_source_is_labelled_fixture(self, runner, result):
        """Evidence must always say where its context came from."""
        assert runner.provider.source == "fixture"
