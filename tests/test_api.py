"""Shared-contract endpoint tests.

``../COORDINATOR_PLAN.md`` requires every project to expose /api/health and /api/readiness, and
requires readiness to verify state without mutating shared state.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from graph_traffic_control.api import app
from graph_traffic_control.config import get_settings
from graph_traffic_control.demo.seed import seed


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client(settings):
    """Client whose endpoints resolve to the supplied test settings."""
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


class TestHealth:
    def test_health_reports_alive(self, settings):
        client = _client(settings)
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["project"] == "graph-traffic-control"

    def test_health_does_not_require_seeded_state(self, settings):
        client = _client(settings)
        assert not settings.state_dir.exists()
        assert client.get("/api/health").status_code == 200


class TestReadiness:
    def test_not_ready_before_seed(self, settings):
        client = _client(settings)
        response = client.get("/api/readiness")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False
        assert body["checks"]["state_dir"]["ok"] is False

    def test_ready_after_seed(self, settings):
        seed(settings)
        client = _client(settings)
        response = client.get("/api/readiness")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["checks"]["fixture_graph"]["ok"] is True
        assert body["checks"]["state_dir"]["ok"] is True

    def test_readiness_reports_the_namespace_allocation(self, settings):
        seed(settings)
        client = _client(settings)
        body = client.get("/api/readiness").json()
        assert body["namespace"]["urn_prefix"] == "traffic."
        assert body["namespace"]["tag"] == "project-graph-traffic-control"

    def test_datahub_unconfigured_is_reported_not_failed(self, settings):
        """Phases 0-4 must stay runnable with no DataHub."""
        seed(settings)
        client = _client(settings)
        datahub = client.get("/api/readiness").json()["checks"]["datahub"]
        assert datahub["ok"] is True
        assert datahub["status"] == "not_configured"

    def test_readiness_does_not_mutate_state(self, settings):
        seed(settings)
        client = _client(settings)
        before = sorted(p.name for p in settings.state_dir.iterdir())
        client.get("/api/readiness")
        after = sorted(p.name for p in settings.state_dir.iterdir())
        assert before == after, "readiness left a probe file behind"

    def test_unreachable_datahub_makes_readiness_fail(self, settings):
        """A configured but unreachable DataHub must fail closed, not be quietly ignored."""
        seed(settings)
        configured = settings.model_copy(
            update={
                "datahub_gms_url": "http://127.0.0.1:1",  # nothing listens here
                "datahub_token": "not-a-real-token",
            }
        )
        client = _client(configured)
        response = client.get("/api/readiness")
        assert response.status_code == 503
        assert response.json()["checks"]["datahub"]["status"] == "unreachable"
