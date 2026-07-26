"""Readiness must answer for the same snapshot ``/api/graph`` serves.

On the live instance every readiness check passed and readiness returned **200**, while
`/api/graph` returned **503**. Both statements were true at once: all nine allocated entities were
present and individually readable, and the graph could not be built, because nothing readiness did
had ever read lineage.

That is the worst possible failure for a readiness endpoint. Missing a problem is one thing;
returning 200 *because* it only checked the things that were fine is a check that certifies the
outage. The coordinator's gate depends on readiness meaning something.

So readiness now builds the real snapshot through the same provider the API uses, in both modes.
These tests pin that: whenever `/api/graph` would fail, readiness must not be ready — and the two
must not be able to disagree.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from graph_traffic_control import readiness
from graph_traffic_control.api import app
from graph_traffic_control.config import get_settings
from graph_traffic_control.context.datahub import TOOL_GET_LINEAGE
from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.demo.seed import load_manifest
from tests.fake_mcp import FakeMcpServer, FakeMcpState


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()

TOKEN = "test-token"  # noqa: S105 - fixture value
PROJECT_TAG_URN = "urn:li:tag:project-graph-traffic-control"
DOMAIN_URN = "urn:li:domain:graph-traffic-control"
DASHBOARD = "urn:li:dashboard:(looker,traffic.dash_exec_revenue)"


@pytest.fixture
def allocated(seeded_settings) -> list[str]:
    manifest = load_manifest(seeded_settings)
    assert manifest is not None
    return manifest["entities"]


@pytest.fixture
def live_state(allocated, seeded_settings) -> FakeMcpState:
    """Everything present and readable, with the fixture's real lineage."""
    from graph_traffic_control.demo.seed import load_fixture_graph

    state = FakeMcpState()
    state.add_entity(PROJECT_TAG_URN, name="project-graph-traffic-control")
    state.add_entity(DOMAIN_URN, name="Demo / Graph Traffic Control")
    for urn in allocated:
        state.add_entity(
            urn,
            name=urn,
            description="d",
            owners=["urn:li:corpGroup:sales-eng"],
            tags=[PROJECT_TAG_URN],
            domain=DOMAIN_URN,
        )
    for edge in load_fixture_graph(seeded_settings)["edges"]:
        state.lineage.setdefault(edge["upstream"], []).append(edge["downstream"])
    return state


def _live_settings(settings, url):
    return settings.model_copy(
        update={"datahub_mcp_url": url, "datahub_token": TOKEN, "app_env": "production"}
    )


def _factory(url):
    return lambda _settings: McpClient(url, TOKEN)


def _check(state, settings, namespace, allocated):
    with FakeMcpServer(state) as server:
        return readiness.check_datahub(
            _live_settings(settings, server.url), namespace, allocated, _factory(server.url)
        )


class TestReadinessBuildsTheSnapshot:
    def test_a_healthy_live_instance_reports_the_graph_it_built(
        self, live_state, seeded_settings, namespace, allocated
    ):
        check = _check(live_state, seeded_settings, namespace, allocated)
        assert check["ok"] is True
        assert check["status"] == "verified"
        assert check["graph_entities"] == 9
        assert check["graph_edges"] == 7
        assert check["graph_fingerprint"]

    def test_the_dashboard_null_lineage_no_longer_breaks_readiness(
        self, live_state, seeded_settings, namespace, allocated
    ):
        """The exact live condition. The double answers null for the dashboard by default."""
        check = _check(live_state, seeded_settings, namespace, allocated)
        assert check["ok"] is True


class TestReadinessCannotCertifyAnOutage:
    """Entities all present, graph unbuildable. Readiness must say no."""

    def test_unreadable_lineage_is_not_ready_even_with_every_entity_present(
        self, live_state, seeded_settings, namespace, allocated
    ):
        live_state.fail_tools.add(TOOL_GET_LINEAGE)
        check = _check(live_state, seeded_settings, namespace, allocated)

        assert check["ok"] is False
        assert check["status"] == "graph_unreadable"
        # It must still confirm the entities were there, or the operator will chase the wrong bug.
        assert check["allocated_entities_found"] == check["allocated_entities_expected"]
        assert "/api/graph" in check["detail"]

    def test_a_malformed_lineage_shape_is_not_ready(
        self, live_state, seeded_settings, namespace, allocated
    ):
        live_state.malformed_tools[TOOL_GET_LINEAGE] = {"downstreams": {"searchResults": 12}}
        check = _check(live_state, seeded_settings, namespace, allocated)
        assert check["ok"] is False
        assert check["status"] == "graph_unreadable"

    def test_unreadable_schema_fields_are_not_ready(
        self, live_state, seeded_settings, namespace, allocated
    ):
        live_state.fail_tools.add("list_schema_fields")
        check = _check(live_state, seeded_settings, namespace, allocated)
        assert check["ok"] is False
        assert check["status"] == "graph_unreadable"

    def test_readiness_and_the_graph_endpoint_agree(
        self, live_state, seeded_settings, namespace, allocated
    ):
        """The invariant the live run violated: never ready while /api/graph is 503."""
        live_state.fail_tools.add(TOOL_GET_LINEAGE)
        with FakeMcpServer(live_state) as server:
            settings = _live_settings(seeded_settings, server.url)
            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                graph_status = client.get("/api/graph").status_code
                readiness_status = client.get("/api/readiness").status_code
            finally:
                app.dependency_overrides.clear()

        assert graph_status == 503
        assert readiness_status == 503, "readiness must not be 200 while /api/graph is 503"

    def test_both_endpoints_succeed_on_a_healthy_live_instance(
        self, live_state, seeded_settings, namespace, allocated
    ):
        """The other half of the invariant: they agree when things work, too."""
        with FakeMcpServer(live_state) as server:
            settings = _live_settings(seeded_settings, server.url)
            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                graph = client.get("/api/graph")
                readiness_status = client.get("/api/readiness").status_code
            finally:
                app.dependency_overrides.clear()

        assert graph.status_code == 200
        assert graph.json()["source"].startswith("datahub")
        assert len(graph.json()["edges"]) == 7
        assert readiness_status == 200


class TestFixtureModeToo:
    def test_fixture_readiness_reports_the_snapshot_it_built(self, seeded_settings, namespace):
        check = readiness.check_fixture_graph(seeded_settings, namespace)
        assert check["ok"] is True
        assert check["snapshot_entities"] == 9
        assert check["snapshot_edges"] == 7

    def test_an_unbuildable_fixture_is_not_ready(self, seeded_settings, namespace, tmp_path):
        """A fixture that parses and is in-namespace can still fail to build a snapshot.

        Written into a temporary fixture root. The real ``demo/fixtures`` tree is version
        controlled and shared by every other test, so a test must never write into it.
        """
        root = tmp_path / "fixtures" / "graph-traffic-control"
        root.mkdir(parents=True)
        (root / "graph.json").write_text(
            json.dumps({"datasets": [{"urn": "traffic.x"}], "dashboards": [], "edges": []}),
            encoding="utf-8",
        )
        broken = seeded_settings.model_copy(
            update={"demo_fixture_root": tmp_path / "fixtures"}
        )

        check = readiness.check_fixture_graph(broken, namespace)
        assert check["ok"] is False

    def test_the_real_fixture_root_is_never_written_to_by_these_tests(self, seeded_settings):
        """Guards the guard: the shipped fixture must still be the shipped fixture."""
        graph = json.loads(
            (seeded_settings.fixture_root / "graph.json").read_text(encoding="utf-8")
        )
        assert len(graph["datasets"]) == 8
        assert len(graph["dashboards"]) == 1
        assert len(graph["edges"]) == 7


class TestStillNonMutating:
    def test_the_snapshot_check_calls_no_mutation_tool(
        self, live_state, seeded_settings, namespace, allocated
    ):
        _check(live_state, seeded_settings, namespace, allocated)
        called = {name for name, _ in live_state.calls}
        assert "update_description" not in called


FCT_REVENUE = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_revenue,PROD)"
METRIC_NET_REVENUE = (
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.metric_net_revenue,PROD)"
)


class TestSeededLineageMustReadBack:
    """An edgeless live graph must never be reported ready.

    The live instance returned every allocated entity and **zero** downstream matches for
    ``traffic.fct_revenue`` — a dataset whose ``upstreamLineage`` edge to
    ``traffic.metric_net_revenue`` had been applied and accepted. Entities present, lineage
    unreadable: a graph index problem, not a seeding problem.

    Reading that as "no edges" would make `/api/graph` answer 200 with nine entities and no
    lineage, and a graph with no lineage answers "nothing conflicts" to every question. The
    project's central claim is an edge, so an edge that will not read back is a hard stop.
    """

    def test_an_unindexed_lineage_graph_is_not_ready(
        self, live_state, seeded_settings, namespace, allocated
    ):
        live_state.facet_only_downstreams.add(FCT_REVENUE)
        check = _check(live_state, seeded_settings, namespace, allocated)

        assert check["ok"] is False
        assert check["status"] == "lineage_incomplete"
        assert [FCT_REVENUE, METRIC_NET_REVENUE] in check["missing_edges"]

    def test_the_failure_says_reindex_and_not_reseed(
        self, live_state, seeded_settings, namespace, allocated
    ):
        """Nobody must be sent to re-seed a correctly seeded shared instance."""
        live_state.facet_only_downstreams.add(FCT_REVENUE)
        detail = _check(live_state, seeded_settings, namespace, allocated)["detail"]

        assert "do NOT re-seed" in detail
        assert "Reindex" in detail
        assert "graph index" in detail

    def test_it_still_confirms_the_entities_were_present(
        self, live_state, seeded_settings, namespace, allocated
    ):
        """Otherwise the operator debugs the wrong layer."""
        live_state.facet_only_downstreams.add(FCT_REVENUE)
        check = _check(live_state, seeded_settings, namespace, allocated)
        assert check["allocated_entities_found"] == check["allocated_entities_expected"] == 9

    def test_a_totally_empty_lineage_index_is_not_ready(
        self, live_state, seeded_settings, namespace, allocated
    ):
        """The worst case: every edge missing, every entity present."""
        live_state.lineage.clear()
        check = _check(live_state, seeded_settings, namespace, allocated)

        assert check["ok"] is False
        assert check["status"] == "lineage_incomplete"
        assert check["graph_entities"] == 9
        assert check["graph_edges"] == 0
        assert len(check["missing_edges"]) == 7

    def test_a_single_missing_edge_is_enough_to_refuse(
        self, live_state, seeded_settings, namespace, allocated
    ):
        """No 'mostly complete' tolerance: the hidden conflict rides on one edge."""
        live_state.lineage[FCT_REVENUE] = []
        check = _check(live_state, seeded_settings, namespace, allocated)
        assert check["ok"] is False
        assert check["status"] == "lineage_incomplete"
        assert check["missing_edges"] == [[FCT_REVENUE, METRIC_NET_REVENUE]]

    def test_a_healthy_instance_reports_the_edges_it_verified(
        self, live_state, seeded_settings, namespace, allocated
    ):
        check = _check(live_state, seeded_settings, namespace, allocated)
        assert check["ok"] is True
        assert check["status"] == "verified"
        assert check["lineage_edges_verified"] == 7

    def test_extra_live_lineage_does_not_fail_readiness(
        self, live_state, seeded_settings, namespace, allocated
    ):
        """The guard is about missing seeded edges, not about forbidding new ones."""
        live_state.lineage.setdefault(METRIC_NET_REVENUE, []).append(FCT_REVENUE)
        check = _check(live_state, seeded_settings, namespace, allocated)
        assert check["ok"] is True
