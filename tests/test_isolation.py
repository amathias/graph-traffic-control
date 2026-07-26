"""Cross-project isolation, asserted at every surface that can reach shared state.

Five submissions share one DataHub instance and one host. ``AGENTS.md`` treats a cross-project
namespace collision as a blocking defect, so this file exists to make isolation a property of the
whole system rather than a property of whichever module was written most carefully.

Each test names the *surface* it guards. If a new surface is added that can write to DataHub or
to the filesystem, it belongs here.

The four sibling prefixes are the other submissions' allocations. They are used as the attacker
in every case, because "some other string" is a weaker test than "the exact thing that would
actually be destroyed".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from graph_traffic_control.api import app
from graph_traffic_control.config import get_settings
from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.context.namespace import (
    Namespace,
    NamespaceViolation,
    require_contained_path,
)
from graph_traffic_control.demo.datahub_state import reset_plan, seed_plan
from graph_traffic_control.demo.reset import reset
from graph_traffic_control.demo.seed import load_fixture_graph
from graph_traffic_control.domain.clock import ManualClock
from graph_traffic_control.domain.models import (
    AgentIdentity,
    ChangeAction,
    ChangeProposal,
)
from graph_traffic_control.writeback.datahub import ReversibleDescriptionWriteback
from tests.fake_mcp import FakeMcpServer, FakeMcpState

TOKEN = "test-token"  # noqa: S105 - fixture value

#: The other four submissions' allocations on the shared instance.
SIBLING_PREFIXES = ["lifeboat.", "license.", "forgetme.", "fuzzer."]


def sibling_dataset(prefix: str) -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:duckdb,{prefix}critical,PROD)"


def sibling_proposal(urn: str) -> ChangeProposal:
    return ChangeProposal(
        proposal_id="cross-project",
        agent=AgentIdentity(agent_id="rogue", display_name="Rogue Agent"),
        intent="write into another submission's graph",
        write_set=[urn],
        action=ChangeAction(
            kind="update_model", target_urn=urn, artifact_path="fct_revenue.sql"
        ),
    )


@pytest.mark.parametrize("prefix", SIBLING_PREFIXES)
class TestEverySurfaceRefusesASiblingProject:
    def test_proposal_submission(self, coordinator, prefix):
        with pytest.raises(NamespaceViolation):
            coordinator.prepare(sibling_proposal(sibling_dataset(prefix)))

    def test_writeback_refuses_before_making_any_call(self, namespace, prefix):
        state = FakeMcpState()
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            writeback = ReversibleDescriptionWriteback(client, namespace, ManualClock())
            with pytest.raises(NamespaceViolation):
                writeback.apply(sibling_dataset(prefix), "note")
            client.close()
        assert state.calls == [], "not one MCP call may be made for a foreign target"

    def test_datahub_seed_plan(self, seeded_settings, namespace, prefix):
        graph = load_fixture_graph(seeded_settings)
        graph["datasets"].append(
            {
                "urn": sibling_dataset(prefix),
                "name": f"{prefix}critical",
                "owners": [],
                "fields": [],
            }
        )
        with pytest.raises(NamespaceViolation):
            seed_plan(graph, namespace, seeded_settings)

    def test_datahub_reset_plan(self, seeded_settings, namespace, prefix):
        with pytest.raises(NamespaceViolation):
            reset_plan([sibling_dataset(prefix)], namespace, seeded_settings)

    def test_context_read_allocation(self, namespace, prefix):
        from graph_traffic_control.context.datahub import DataHubContextProvider

        state = FakeMcpState()
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            with pytest.raises(NamespaceViolation):
                DataHubContextProvider(
                    client, namespace, [sibling_dataset(prefix)], ManualClock()
                )
            client.close()

    def test_a_sibling_downstream_edge_never_enters_our_graph(self, namespace, prefix):
        """DataHub may legitimately report a cross-project edge. We must not adopt it."""
        from graph_traffic_control.context.datahub import DataHubContextProvider
        from graph_traffic_control.demo.agents import FCT_REVENUE

        state = FakeMcpState()
        state.add_entity(FCT_REVENUE, name="traffic.fct_revenue")
        state.schema_fields[FCT_REVENUE] = []
        state.lineage[FCT_REVENUE] = [sibling_dataset(prefix)]

        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            provider = DataHubContextProvider(
                client, namespace, [FCT_REVENUE], ManualClock()
            )
            snapshot = provider.snapshot()
            client.close()

        assert snapshot.edges == []


class TestUnknownUrnShapesFailClosed:
    """A guard that guesses is worse than no guard: it would authorise a foreign write."""

    @pytest.mark.parametrize(
        "urn",
        [
            "",
            "not-a-urn",
            "urn:li:dataset:traffic.x",                       # tuple body expected
            "urn:li:tag:(traffic.x)",                         # flat body expected
            "urn:li:mysteryEntity:(a,traffic.x,PROD)",        # unknown entity type
            "urn:li:dataset:(urn:li:dataPlatform:duckdb,,PROD)",  # empty name
            "urn:li:dataset:(unbalanced,traffic.x",           # unbalanced parentheses
        ],
    )
    def test_an_unparseable_urn_is_refused(self, namespace, urn):
        with pytest.raises(NamespaceViolation):
            namespace.require(urn, operation="isolation test")

    def test_contains_never_raises_but_never_guesses(self, namespace):
        assert namespace.contains("not-a-urn") is False
        assert namespace.contains("urn:li:mysteryEntity:(a,traffic.x,PROD)") is False

    def test_a_schema_field_inherits_its_parents_allocation(self, namespace):
        foreign_parent = (
            "urn:li:schemaField:"
            "(urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD),amount)"
        )
        with pytest.raises(NamespaceViolation):
            namespace.require(foreign_parent, operation="isolation test")

    def test_a_sibling_tag_is_not_our_tag(self, namespace):
        with pytest.raises(NamespaceViolation):
            namespace.require_tag("urn:li:tag:project-lifeboat", operation="isolation test")


class TestFilesystemIsolation:
    def test_reset_refuses_to_delete_the_fixture_root(self, settings):
        """Fixtures are version-controlled inputs, not disposable state."""
        hijacked = settings.model_copy(update={"app_state_dir": settings.fixture_root})
        with pytest.raises(NamespaceViolation):
            reset(hijacked)

    def test_reset_only_touches_its_own_state_directory(self, seeded_settings, tmp_path):
        outsider = tmp_path / "someone-elses-file.txt"
        outsider.write_text("must survive", encoding="utf-8")
        reset(seeded_settings)
        assert outsider.read_text(encoding="utf-8") == "must survive"

    def test_a_path_outside_the_root_is_refused(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir()
        with pytest.raises(NamespaceViolation):
            require_contained_path(tmp_path / "elsewhere", root, operation="isolation test")

    @pytest.mark.parametrize(
        "artifact_path",
        ["../escape.sql", "../../etc/passwd", "/etc/passwd", "C:/Windows/system.ini"],
    )
    def test_an_escaping_artifact_path_is_rejected_at_the_model(self, artifact_path):
        """Rejected before any coordination begins, per PROJECT_BRIEF's proposal protocol."""
        urn = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_revenue,PROD)"
        with pytest.raises(ValueError, match="artifact_path"):
            ChangeProposal(
                proposal_id="escape",
                agent=AgentIdentity(agent_id="a", display_name="A"),
                intent="escape the state directory",
                write_set=[urn],
                action=ChangeAction(
                    kind="update_model", target_urn=urn, artifact_path=artifact_path
                ),
            )


class TestApiIsolation:
    @pytest.fixture
    def client(self, seeded_settings):
        app.dependency_overrides[get_settings] = lambda: seeded_settings
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    @pytest.mark.parametrize("prefix", SIBLING_PREFIXES)
    def test_the_proposal_endpoint_refuses_a_sibling_target(self, client, prefix):
        response = client.post(
            "/api/proposals", json=sibling_proposal(sibling_dataset(prefix)).model_dump(mode="json")
        )
        assert response.status_code == 422
        assert "outside" in response.json()["detail"]

    def test_an_undeclared_write_target_is_rejected(self, client, namespace):
        """A proposal may not mutate an asset it did not declare, even its own."""
        declared = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_revenue,PROD)"
        undeclared = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.stg_sales,PROD)"
        response = client.post(
            "/api/proposals",
            json={
                "proposal_id": "undeclared",
                "agent": {"agent_id": "a", "display_name": "A"},
                "intent": "write something it never declared",
                "write_set": [declared],
                "action": {
                    "kind": "update_model",
                    "target_urn": undeclared,
                    "artifact_path": "stg_sales.sql",
                },
            },
        )
        assert response.status_code == 422


class TestNamespaceAllocationMatchesTheCoordinatorRecord:
    """A silent drift in the allocation is a cross-project incident waiting to happen."""

    def test_the_allocation_is_the_one_the_coordinator_assigned(self, settings):
        namespace = Namespace.from_settings(settings)
        assert namespace.urn_prefix == "traffic."
        assert namespace.project_tag == "project-graph-traffic-control"
        assert namespace.domain == "Demo / Graph Traffic Control"

    def test_every_fixture_entity_is_inside_the_allocation(self, seeded_settings, namespace):
        graph = load_fixture_graph(seeded_settings)
        for entity in [*graph["datasets"], *graph["dashboards"]]:
            namespace.require(entity["urn"], operation="fixture audit")
        for edge in graph["edges"]:
            namespace.require(edge["upstream"], operation="fixture audit")
            namespace.require(edge["downstream"], operation="fixture audit")
