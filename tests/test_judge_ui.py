"""The project-owned judge console.

A judge must be able to open one page, press one button, and see the whole coordination story:
agents, proposals, conflicts with their lineage evidence, leases, approvals, commits, rollback,
and the receipts that evidence them. These tests assert the page is self-contained and that the
payload behind it actually contains each of those things.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from graph_traffic_control import api
from graph_traffic_control.api import UI_INDEX, app
from graph_traffic_control.config import get_settings


@pytest.fixture
def client(seeded_settings):
    app.dependency_overrides[get_settings] = lambda: seeded_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def payload(client):
    response = client.post("/api/demo/run")
    assert response.status_code == 200, response.text
    return response.json()


class TestPageIsSelfContained:
    """A locked-down reviewer machine must render the console identically to a connected one."""

    def test_the_root_route_serves_the_console(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Graph Traffic Control" in response.text

    def test_no_external_asset_is_referenced(self):
        body = UI_INDEX.read_text(encoding="utf-8")
        offenders = re.findall(r'(?:src|href)\s*=\s*["\'](https?:|//)', body)
        assert offenders == [], f"external assets break an offline judge: {offenders}"

    def test_no_remote_fetch_targets(self):
        body = UI_INDEX.read_text(encoding="utf-8")
        remote = re.findall(r'fetch\(\s*["\']https?://', body)
        assert remote == []

    def test_styles_and_scripts_are_inline(self):
        body = UI_INDEX.read_text(encoding="utf-8")
        assert "<style>" in body and "<script>" in body
        assert "<link rel=\"stylesheet\"" not in body

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_the_inline_script_parses(self, tmp_path):
        """A syntax error would leave the console blank with a green test suite otherwise.

        Skipped rather than required, so the suite still runs without a JS runtime installed.
        """
        body = UI_INDEX.read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", body, re.S)
        assert script, "the console has no inline script"
        path = tmp_path / "ui.js"
        path.write_text(script.group(1), encoding="utf-8")
        result = subprocess.run(  # noqa: S603 - fixed argv, path from tmp_path
            [shutil.which("node"), "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_every_element_the_script_looks_up_exists(self):
        """Catches the wiring typo a payload test cannot see: a renamed or missing element id."""
        body = UI_INDEX.read_text(encoding="utf-8")
        declared = set(re.findall(r'\bid="([^"]+)"', body))
        looked_up = set(re.findall(r'\$\("([^"]+)"\)', body))
        missing = sorted(looked_up - declared)
        assert missing == [], f"the script reads element ids that do not exist: {missing}"


class TestScenarioPayload:
    def test_every_agent_appears(self, payload):
        agents = {a["agent_id"] for a in payload["agents"]}
        assert {"agent-a", "agent-b", "agent-c", "agent-d"} <= agents

    def test_every_proposal_carries_its_declared_sets_and_state(self, payload):
        for proposal in payload["proposals"]:
            assert proposal["proposal_id"]
            assert proposal["state"]
            assert proposal["write_set"], "every proposal declares what it writes"

    def test_the_hidden_conflict_is_shown_with_its_lineage_path(self, payload):
        """The whole thesis: two proposals that share no declared URN still collide."""
        paths = [
            conflict["lineage_path"]
            for proposal in payload["proposals"]
            for conflict in proposal["conflicts"]
            if conflict["lineage_path"]
        ]
        assert paths, "a judge must be able to see the lineage path that proves the conflict"
        assert any(len(path) >= 2 for path in paths)

    def test_no_proposal_is_shown_conflicting_with_itself(self, payload):
        """The coordinator computes each pair both ways; the console must still name the other
        party, not the proposal being looked at."""
        for proposal in payload["proposals"]:
            for conflict in proposal["conflicts"]:
                assert conflict["counterpart"] != proposal["proposal_id"], (
                    f"{proposal['proposal_id']} is shown conflicting with itself"
                )

    def test_one_disagreement_is_reported_once(self, payload):
        """Both directions of a pair are one disagreement, not two.

        They differ only in which side's write target is recorded as the subject, so keying on
        the subject would let the duplicate through.
        """
        for proposal in payload["proposals"]:
            keys = [
                (c["kind"], c["decision"], c["counterpart"]) for c in proposal["conflicts"]
            ]
            assert len(keys) == len(set(keys)), (
                f"{proposal['proposal_id']} reports the same disagreement twice: {keys}"
            )

    def test_the_subject_shown_is_the_viewing_proposals_own_asset(self, payload):
        """When a proposal raised the conflict itself, its own write target is the subject."""
        for proposal in payload["proposals"]:
            for conflict in proposal["conflicts"]:
                if conflict["proposal_id"] == proposal["proposal_id"]:
                    assert conflict["subject_urn"] in (
                        proposal["write_set"] + proposal["read_set"]
                    )

    def test_an_unrelated_proposal_committed_in_parallel(self, payload):
        by_id = {p["proposal_id"]: p for p in payload["proposals"]}
        assert by_id["prop-c-support-sla"]["state"] == "COMMITTED"

    def test_a_proposal_was_blocked_or_failed_closed(self, payload):
        states = {p["state"] for p in payload["proposals"]}
        assert states & {"BLOCKED", "ABORTED", "EXPIRED"}

    def test_the_stale_proposal_failed_closed_with_a_reason(self, payload):
        by_id = {p["proposal_id"]: p for p in payload["proposals"]}
        stale = by_id["prop-d-stale"]
        assert stale["state"] in {"ABORTED", "EXPIRED"}
        assert "stale" in stale["reason"].lower()

    def test_approval_is_visible_for_the_high_blast_radius_change(self, payload):
        by_id = {p["proposal_id"]: p for p in payload["proposals"]}
        agent_a = by_id["prop-a-rename-revenue"]
        assert agent_a["approval_required"] is True
        assert agent_a["approved_by"], "the approver must be visible, not just the requirement"

    def test_commits_expose_every_verification_flag(self, payload):
        committed = [p for p in payload["proposals"] if p["commit"]]
        assert committed
        for proposal in committed:
            verification = proposal["commit"]["verification"]
            for key in (
                "mutation_applied",
                "mutation_reread_verified",
                "validation_passed",
                "writeback_attempted",
                "writeback_verified",
                "writeback_restored",
                "artifact_rolled_back",
            ):
                assert key in verification

    def test_leases_are_reported(self, payload):
        assert "active" in payload["leases"] and "expired" in payload["leases"]

    def test_the_audit_log_is_append_only_and_ordered(self, payload):
        sequences = [event["sequence"] for event in payload["events"]]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)

    def test_the_graph_and_its_fingerprint_are_included(self, payload):
        assert payload["graph"]["entities"]
        assert payload["graph"]["edges"]
        assert payload["graph"]["fingerprint"]

    def test_receipts_are_listed_as_evidence(self, payload):
        assert payload["receipts"], "commit evidence must be reachable from the console"

    def test_the_context_source_is_stated_not_implied(self, payload):
        assert payload["context_source"] in {"fixture", "datahub-mcp"}


class TestRepeatability:
    def test_running_twice_tells_the_same_story(self, client):
        """A judge may press the button more than once. AGENTS.md forbids timing-dependent demos."""
        first = client.post("/api/demo/run").json()
        second = client.post("/api/demo/run").json()
        assert [p["state"] for p in first["proposals"]] == [
            p["state"] for p in second["proposals"]
        ]
        assert first["graph"]["fingerprint"] == second["graph"]["fingerprint"]

    def test_the_scenario_does_not_disturb_the_live_transaction_store(self, client, payload):
        """The judge run is isolated, so it cannot delete proposals submitted through the API."""
        assert client.get("/api/proposals").json()["proposals"] == []


class TestReceiptEndpoint:
    def test_a_named_receipt_can_be_read(self, client, payload):
        name = payload["receipts"][0]
        body = client.get(f"/api/receipts/{name}").json()
        assert body["kind"] in {"proposal", "lease", "commit"}

    def test_the_index_matches_the_scenario_payload(self, client, payload):
        assert client.get("/api/receipts").json()["receipts"] == payload["receipts"]

    def test_an_unknown_receipt_is_a_404(self, client, payload):
        assert client.get("/api/receipts/nope.json").status_code == 404

    @pytest.mark.parametrize(
        "name", ["..%2f..%2ftransactions.sqlite", "..%2F.env", "%2Fetc%2Fpasswd"]
    )
    def test_a_traversing_name_cannot_escape_the_receipts_directory(
        self, client, payload, name
    ):
        """The endpoint serves files by name; without containment it would serve any file."""
        response = client.get(f"/api/receipts/{name}")
        assert response.status_code in {400, 404}

    def test_a_non_json_file_is_not_served(self, client, payload, seeded_settings):
        directory = seeded_settings.state_dir / api.JUDGE_STATE_DIRNAME / "receipts"
        secret = directory / "secret.txt"
        secret.write_text("not evidence", encoding="utf-8")
        assert client.get("/api/receipts/secret.txt").status_code == 404
