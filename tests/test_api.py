"""HTTP surface: shared contract endpoints and the coordination lifecycle."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from graph_traffic_control.api import app
from graph_traffic_control.config import get_settings
from graph_traffic_control.demo.agents import proposal_a, proposal_b, proposal_c


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client(settings) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


class TestHealth:
    def test_health_reports_alive(self, settings):
        body = _client(settings).get("/api/health").json()
        assert body["status"] == "ok"
        assert body["project"] == "graph-traffic-control"

    def test_health_does_not_require_seeded_state(self, settings):
        assert _client(settings).get("/api/health").status_code == 200


class TestReadiness:
    def test_not_ready_before_seed(self, settings):
        response = _client(settings).get("/api/readiness")
        assert response.status_code == 503
        assert response.json()["checks"]["state"]["ok"] is False

    def test_ready_after_seed_in_test_env(self, seeded_settings):
        response = _client(seeded_settings).get("/api/readiness")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["mode"] == "fixture"

    def test_reports_namespace_allocation(self, seeded_settings):
        body = _client(seeded_settings).get("/api/readiness").json()
        assert body["namespace"]["urn_prefix"] == "traffic."
        assert body["namespace"]["tag"] == "project-graph-traffic-control"

    def test_readiness_is_non_mutating(self, seeded_settings):
        client = _client(seeded_settings)
        before = sorted(p.name for p in seeded_settings.state_dir.rglob("*"))
        client.get("/api/readiness")
        after = sorted(p.name for p in seeded_settings.state_dir.rglob("*"))
        assert before == after

    def test_fixture_mode_is_not_ready_in_production(self, seeded_settings):
        deployed = seeded_settings.model_copy(update={"app_env": "production"})
        assert _client(deployed).get("/api/readiness").status_code == 503


class TestGraph:
    def test_graph_endpoint_returns_the_project_subgraph(self, seeded_settings):
        body = _client(seeded_settings).get("/api/graph").json()
        assert body["source"] == "fixture"
        assert body["fingerprint"]
        assert len(body["entities"]) == 9
        assert len(body["edges"]) == 7

    def test_every_returned_entity_is_in_namespace(self, seeded_settings):
        body = _client(seeded_settings).get("/api/graph").json()
        assert all("traffic." in entity["urn"] for entity in body["entities"])


class TestProposalLifecycle:
    def test_unrelated_proposal_prepares_and_commits(self, seeded_settings):
        client = _client(seeded_settings)
        response = client.post("/api/proposals", json=proposal_c().model_dump(mode="json"))
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "PREPARED"
        assert body["lease_id"]

        commit = client.post(
            f"/api/proposals/{body['proposal_id']}/commit",
            params={"token": body["prepared_token"]},
        )
        assert commit.status_code == 200
        assert commit.json()["state"] == "COMMITTED"

    def test_conflicting_proposal_is_blocked_with_lineage_evidence(self, seeded_settings):
        client = _client(seeded_settings)
        client.post("/api/proposals", json=proposal_a().model_dump(mode="json"))
        response = client.post("/api/proposals", json=proposal_b().model_dump(mode="json"))

        body = response.json()
        assert body["state"] == "BLOCKED"
        paths = [c["lineage_path"] for c in body["conflicts"] if c["lineage_path"]]
        assert paths, "blocked proposal must expose its lineage evidence"

    def test_high_blast_radius_requires_approval_before_commit(self, seeded_settings):
        client = _client(seeded_settings)
        prepared = client.post(
            "/api/proposals", json=proposal_a().model_dump(mode="json")
        ).json()
        assert prepared["approval_required"] is True

        rejected = client.post(
            f"/api/proposals/{prepared['proposal_id']}/commit",
            params={"token": prepared["prepared_token"]},
        ).json()
        assert rejected["state"] == "ABORTED"
        assert "approval" in rejected["reason"].lower()

    def test_approved_high_blast_radius_change_commits(self, seeded_settings):
        client = _client(seeded_settings)
        prepared = client.post(
            "/api/proposals", json=proposal_a().model_dump(mode="json")
        ).json()

        approved = client.post(
            f"/api/proposals/{prepared['proposal_id']}/approve",
            params={"token": prepared["prepared_token"], "approver": "release-manager"},
        )
        assert approved.status_code == 200
        assert approved.json()["approved_by"] == "release-manager"

        commit = client.post(
            f"/api/proposals/{prepared['proposal_id']}/commit",
            params={"token": prepared["prepared_token"]},
        ).json()
        assert commit["state"] == "COMMITTED"
        assert commit["drift_detected"] is False

    def test_prepared_token_survives_across_requests(self, seeded_settings):
        """Each request builds a fresh runtime, so tokens must be persisted, not in memory."""
        client = _client(seeded_settings)
        prepared = client.post(
            "/api/proposals", json=proposal_c().model_dump(mode="json")
        ).json()
        # A completely separate request must still resolve the token.
        commit = client.post(
            f"/api/proposals/{prepared['proposal_id']}/commit",
            params={"token": prepared["prepared_token"]},
        )
        assert commit.status_code == 200
        assert commit.json()["state"] == "COMMITTED"

    def test_malformed_proposal_is_rejected_with_422(self, seeded_settings):
        client = _client(seeded_settings)
        response = client.post("/api/proposals", json={"proposal_id": "broken"})
        assert response.status_code == 422

    def test_foreign_namespace_proposal_is_rejected(self, seeded_settings):
        foreign = "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"
        payload = proposal_c().model_dump(mode="json")
        payload["write_set"] = [foreign]
        payload["action"]["target_urn"] = foreign
        response = _client(seeded_settings).post("/api/proposals", json=payload)
        assert response.status_code == 422

    def test_commit_with_an_unknown_token_is_404(self, seeded_settings):
        client = _client(seeded_settings)
        prepared = client.post(
            "/api/proposals", json=proposal_c().model_dump(mode="json")
        ).json()
        response = client.post(
            f"/api/proposals/{prepared['proposal_id']}/commit",
            params={"token": "prepared-nonsense"},
        )
        assert response.status_code == 404

    def test_resubmitting_a_terminal_proposal_id_is_409_not_500(self, seeded_settings):
        """Proposal ids are the client's idempotency key; reuse is a conflict, not a crash."""
        client = _client(seeded_settings)
        prepared = client.post(
            "/api/proposals", json=proposal_c().model_dump(mode="json")
        ).json()
        client.post(
            f"/api/proposals/{prepared['proposal_id']}/commit",
            params={"token": prepared["prepared_token"]},
        )
        again = client.post("/api/proposals", json=proposal_c().model_dump(mode="json"))
        assert again.status_code == 409
        assert "already reached" in again.json()["detail"]

    def test_commit_for_an_unknown_proposal_is_404(self, seeded_settings):
        response = _client(seeded_settings).post(
            "/api/proposals/does-not-exist/commit", params={"token": "x"}
        )
        assert response.status_code == 404


class TestObservability:
    def test_events_are_exposed_in_order(self, seeded_settings):
        client = _client(seeded_settings)
        prepared = client.post(
            "/api/proposals", json=proposal_c().model_dump(mode="json")
        ).json()
        client.post(
            f"/api/proposals/{prepared['proposal_id']}/commit",
            params={"token": prepared["prepared_token"]},
        )
        events = client.get("/api/events").json()["events"]
        assert [event["to_state"] for event in events][:2] == ["SUBMITTED", "ANALYZING"]
        assert events[-1]["to_state"] == "COMMITTED"

    def test_leases_endpoint_reports_remaining_time(self, seeded_settings):
        client = _client(seeded_settings)
        client.post("/api/proposals", json=proposal_c().model_dump(mode="json"))
        body = client.get("/api/leases").json()
        assert body["active"]
        assert body["active"][0]["seconds_remaining"] > 0

    def test_proposals_endpoint_lists_states(self, seeded_settings):
        client = _client(seeded_settings)
        client.post("/api/proposals", json=proposal_c().model_dump(mode="json"))
        proposals = client.get("/api/proposals").json()["proposals"]
        assert proposals[0]["state"] == "PREPARED"


class TestReceiptsWritten:
    def test_proposal_and_commit_receipts_land_on_disk(self, seeded_settings):
        client = _client(seeded_settings)
        prepared = client.post(
            "/api/proposals", json=proposal_c().model_dump(mode="json")
        ).json()
        client.post(
            f"/api/proposals/{prepared['proposal_id']}/commit",
            params={"token": prepared["prepared_token"]},
        )
        receipts = sorted(p.name for p in (seeded_settings.state_dir / "receipts").iterdir())
        assert any(name.startswith("proposal-") for name in receipts)
        assert any(name.startswith("lease-") for name in receipts)
        assert any(name.startswith("commit-") for name in receipts)
