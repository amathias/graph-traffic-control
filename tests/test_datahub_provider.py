"""DataHub-backed provider.

The extractors must be tolerant of payload-shape variation and must never admit a foreign
entity into this project's graph. Exercised against the localhost MCP test double.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.datahub import (
    DataHubContextProvider,
    extract_criticality,
    extract_description,
    extract_fields,
    extract_owners,
)
from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.demo.agents import FCT_REVENUE, METRIC_NET_REVENUE
from graph_traffic_control.domain.clock import ManualClock
from graph_traffic_control.domain.models import Criticality
from tests.fake_mcp import FakeMcpServer, FakeMcpState

TOKEN = "test-token"  # noqa: S105 - fixture value

NAMESPACE = Namespace(
    urn_prefix="traffic.",
    project_tag="project-graph-traffic-control",
    domain="Demo / Graph Traffic Control",
)

FOREIGN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"


class TestExtractors:
    @pytest.mark.parametrize(
        "payload",
        [
            {"description": "hello"},
            {"editableDescription": "hello"},
            {"properties": {"description": "hello"}},
            {"editableProperties": {"description": "hello"}},
        ],
    )
    def test_description_shapes(self, payload):
        assert extract_description(payload) == "hello"

    def test_description_absent_returns_none(self):
        assert extract_description({"name": "x"}) is None
        assert extract_description("not a dict") is None

    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"criticality": "TIER_1"}, Criticality.TIER_1),
            ({"tier": "tier-2"}, Criticality.TIER_2),
            ({"tags": ["urn:li:tag:Tier_3"]}, Criticality.TIER_3),
            ({"name": "x"}, Criticality.UNKNOWN),
            ({"criticality": "nonsense"}, Criticality.UNKNOWN),
        ],
    )
    def test_criticality_shapes(self, payload, expected):
        assert extract_criticality(payload) is expected

    def test_owners_from_strings_and_objects(self):
        assert extract_owners({"owners": ["alice"]}) == ["alice"]
        assert extract_owners({"owners": [{"owner": "urn:li:corpuser:bob"}]}) == [
            "urn:li:corpuser:bob"
        ]

    def test_fields_from_several_shapes(self):
        assert extract_fields(["a", "b"]) == extract_fields(
            [{"fieldPath": "a"}, {"path": "b"}]
        )
        typed = extract_fields([{"fieldPath": "a", "nativeDataType": "DOUBLE"}])
        assert typed[0].type == "DOUBLE"

    def test_fields_without_a_path_are_skipped(self):
        assert extract_fields([{"type": "int"}]) == []


class TestSnapshot:
    @pytest.fixture
    def state(self) -> FakeMcpState:
        state = FakeMcpState()
        state.entities[FCT_REVENUE] = {
            "urn": FCT_REVENUE,
            "name": "traffic.fct_revenue",
            "description": "Revenue fact",
            "criticality": "TIER_1",
            "owners": ["finance-data"],
        }
        state.entities[METRIC_NET_REVENUE] = {
            "urn": METRIC_NET_REVENUE,
            "name": "traffic.metric_net_revenue",
        }
        state.schema_fields[FCT_REVENUE] = [
            {"fieldPath": "gross_revenue", "nativeDataType": "DOUBLE"}
        ]
        state.lineage[FCT_REVENUE] = [METRIC_NET_REVENUE]
        return state

    def test_snapshot_reads_entities_and_lineage(self, state):
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            provider = DataHubContextProvider(
                client, NAMESPACE, [FCT_REVENUE, METRIC_NET_REVENUE], ManualClock()
            )
            snapshot = provider.snapshot()
            client.close()

        assert provider.source == "datahub-mcp"
        assert snapshot.entities[FCT_REVENUE].criticality is Criticality.TIER_1
        assert snapshot.entities[FCT_REVENUE].fields[0].path == "gross_revenue"
        assert (FCT_REVENUE, METRIC_NET_REVENUE) in {
            (edge.upstream, edge.downstream) for edge in snapshot.edges
        }

    def test_foreign_downstream_edges_are_dropped(self, state):
        state.lineage[FCT_REVENUE] = [METRIC_NET_REVENUE, FOREIGN]
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            provider = DataHubContextProvider(
                client, NAMESPACE, [FCT_REVENUE, METRIC_NET_REVENUE], ManualClock()
            )
            snapshot = provider.snapshot()
            client.close()

        downstreams = {edge.downstream for edge in snapshot.edges}
        assert FOREIGN not in downstreams, "another project's entity entered this graph"

    def test_foreign_allocated_urn_is_refused_at_construction(self, state):
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            with pytest.raises(NamespaceViolation):
                DataHubContextProvider(client, NAMESPACE, [FOREIGN], ManualClock())
            client.close()

    def test_missing_schema_fields_do_not_fail_the_snapshot(self, state):
        """A dashboard has no columns; that must not abort the whole read."""
        state.fail_tools.add("list_schema_fields")
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            provider = DataHubContextProvider(client, NAMESPACE, [FCT_REVENUE], ManualClock())
            snapshot = provider.snapshot()
            client.close()
        assert snapshot.entities[FCT_REVENUE].fields == []

    def test_lineage_failure_degrades_to_no_edges(self, state):
        state.fail_tools.add("get_lineage")
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            provider = DataHubContextProvider(client, NAMESPACE, [FCT_REVENUE], ManualClock())
            snapshot = provider.snapshot()
            client.close()
        assert snapshot.edges == []

    def test_snapshot_is_deterministic_for_the_same_state(self, state):
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            provider = DataHubContextProvider(
                client, NAMESPACE, [FCT_REVENUE, METRIC_NET_REVENUE], ManualClock()
            )
            first = provider.snapshot().fingerprint()
            second = provider.snapshot().fingerprint()
            client.close()
        assert first == second
