"""Readiness must be strong and non-mutating.

The two rules under test: readiness never writes anything, and in live mode a bare liveness
response is never enough — tools, this project's tag, and its allocated entities must all be
verified with an authenticated call.
"""

from __future__ import annotations

import pytest

from graph_traffic_control import readiness
from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.demo.seed import load_manifest
from tests.fake_mcp import FakeMcpServer, FakeMcpState

TOKEN = "test-token"  # noqa: S105 - fixture value


@pytest.fixture
def allocated(seeded_settings) -> list[str]:
    manifest = load_manifest(seeded_settings)
    assert manifest is not None
    return manifest["entities"]


PROJECT_TAG_URN = "urn:li:tag:project-graph-traffic-control"
DOMAIN_URN = "urn:li:domain:graph-traffic-control"


@pytest.fixture
def live_state(allocated) -> FakeMcpState:
    state = FakeMcpState()
    state.add_entity(PROJECT_TAG_URN, name="project-graph-traffic-control")
    state.add_entity(DOMAIN_URN, name="Demo / Graph Traffic Control")
    for urn in allocated:
        state.add_entity(urn, name=urn)
    return state


def _live_settings(settings, url):
    return settings.model_copy(
        update={"datahub_mcp_url": url, "datahub_token": TOKEN, "app_env": "production"}
    )


def _factory(url):
    return lambda _settings: McpClient(url, TOKEN)


class TestNonMutating:
    def test_readiness_writes_nothing_to_the_state_directory(self, seeded_settings, namespace):
        before = sorted(p.name for p in seeded_settings.state_dir.rglob("*"))
        readiness.evaluate(seeded_settings, namespace, [])
        after = sorted(p.name for p in seeded_settings.state_dir.rglob("*"))
        assert before == after

    def test_live_readiness_calls_no_mutation_tool(
        self, seeded_settings, namespace, allocated, live_state
    ):
        with FakeMcpServer(live_state) as server:
            readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        called = {name for name, _ in live_state.calls}
        assert "update_description" not in called
        assert "add_tags" not in called


class TestFixtureMode:
    def test_fixture_mode_is_ready_in_a_test_environment(self, seeded_settings, namespace):
        result = readiness.evaluate(seeded_settings, namespace, [])
        assert result["ready"] is True
        assert result["mode"] == "fixture"

    def test_fixture_mode_fails_closed_in_production(self, seeded_settings, namespace):
        deployed = seeded_settings.model_copy(update={"app_env": "production"})
        result = readiness.evaluate(deployed, namespace, [])
        assert result["ready"] is False
        assert result["checks"]["datahub"]["status"] == "not_configured"

    def test_endpoint_without_a_token_is_not_live_mode(self, seeded_settings, namespace):
        half = seeded_settings.model_copy(
            update={"datahub_mcp_url": "http://127.0.0.1:8000/mcp", "datahub_token": ""}
        )
        assert half.live_mode is False
        assert readiness.check_datahub(half, namespace, [])["mode"] == "fixture"

    def test_unseeded_state_is_not_ready(self, settings, namespace):
        result = readiness.evaluate(settings, namespace, [])
        assert result["ready"] is False
        assert result["checks"]["state"]["ok"] is False

    def test_missing_artifacts_are_detected(self, seeded_settings, namespace):
        for artifact in (seeded_settings.state_dir / "artifacts").glob("*.sql"):
            artifact.unlink()
        assert readiness.check_state(seeded_settings)["ok"] is False


class TestLiveMode:
    def test_verified_when_tools_tag_and_entities_all_resolve(
        self, seeded_settings, namespace, allocated, live_state
    ):
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        assert check["ok"] is True
        assert check["status"] == "verified"
        assert check["tag_verified"] == "project-graph-traffic-control"
        assert check["domain_verified"] == DOMAIN_URN
        assert check["allocated_entities_found"] == check["allocated_entities_expected"]

    def test_missing_read_tool_fails_closed(
        self, seeded_settings, namespace, allocated, live_state
    ):
        live_state.tools.remove("get_lineage")
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        assert check["ok"] is False
        assert check["status"] == "missing_tools"
        assert "get_lineage" in check["missing_read_tools"]

    def test_missing_write_tool_fails_closed(
        self, seeded_settings, namespace, allocated, live_state
    ):
        """Without a mutation tool the project cannot perform its writeback, so it is not ready."""
        live_state.tools.remove("update_description")
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        assert check["ok"] is False
        assert "update_description" in check["missing_write_tools"]

    def test_missing_project_tag_fails_closed(
        self, seeded_settings, namespace, allocated, live_state
    ):
        del live_state.entities["urn:li:tag:project-graph-traffic-control"]
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        assert check["ok"] is False
        assert check["status"] == "tag_missing"

    def test_missing_allocated_entities_fails_closed(
        self, seeded_settings, namespace, allocated, live_state
    ):
        """A reachable, authenticated MCP server with an empty graph is not ready."""
        for urn in allocated:
            live_state.entities.pop(urn, None)
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        assert check["ok"] is False
        assert check["status"] == "entities_missing"

    def test_a_partially_ingested_catalogue_is_not_ready(
        self, seeded_settings, namespace, allocated, live_state
    ):
        """The complete allocation is required. Sampling would pass a half-ingested catalogue,
        and a partial graph reports fewer conflicts than really exist."""
        dropped = sorted(allocated)[-1]
        live_state.entities.pop(dropped)
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        assert check["ok"] is False
        assert check["status"] == "entities_missing"
        assert check["missing"] == [dropped]
        assert check["allocated_entities_expected"] == len(set(allocated))

    def test_missing_domain_fails_closed(
        self, seeded_settings, namespace, allocated, live_state
    ):
        live_state.entities.pop(DOMAIN_URN)
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        assert check["ok"] is False
        assert check["status"] == "domain_missing"

    def test_unreachable_endpoint_fails_closed(self, seeded_settings, namespace, allocated):
        settings = _live_settings(seeded_settings, "http://127.0.0.1:1/mcp")
        check = readiness.check_datahub(
            settings, namespace, allocated, lambda _s: McpClient(settings.datahub_mcp_url, TOKEN)
        )
        assert check["ok"] is False
        assert check["status"] == "unreachable"

    def test_empty_allocation_fails_closed(self, seeded_settings, namespace, live_state):
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url), namespace, [], _factory(server.url)
            )
        assert check["ok"] is False
        assert check["status"] == "no_allocated_entities"

    def test_foreign_allocated_urn_is_refused(self, seeded_settings, namespace, live_state):
        foreign = ["urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"]
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                foreign,
                _factory(server.url),
            )
        assert check["ok"] is False
        assert check["status"] == "namespace_violation"


class TestAuthenticationIsRequired:
    def test_authenticated_calls_are_used_not_a_bare_health_ping(
        self, seeded_settings, namespace, allocated, live_state
    ):
        """Every readiness signal must come from a token-bearing MCP call."""
        with FakeMcpServer(live_state) as server:
            readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                _factory(server.url),
            )
        assert live_state.auth_headers, "readiness made no authenticated call"
        assert all(header == f"Bearer {TOKEN}" for header in live_state.auth_headers)

    def test_server_rejecting_the_token_is_not_ready(
        self, seeded_settings, namespace, allocated, live_state
    ):
        live_state.reject_unauthenticated = True
        with FakeMcpServer(live_state) as server:
            check = readiness.check_datahub(
                _live_settings(seeded_settings, server.url),
                namespace,
                allocated,
                lambda _s: McpClient(server.url, ""),
            )
        assert check["ok"] is False
