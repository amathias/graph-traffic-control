"""DataHub-backed provider, against the coordinator-observed MCP contracts.

Two things are being proven here:

1. The provider speaks the real contract — argument names and payload envelopes. The protocol
   double rejects any other argument set, so a regression fails rather than silently passing.
2. The provider **fails closed**. An MCP error, or a payload in a shape the contract does not
   describe, must raise. It must never degrade into an empty or partial graph, because an empty
   graph is indistinguishable from a graph with no conflicts.

Everything here runs against a localhost protocol double. **All evidence produced by these tests
is simulated**, not live DataHub behaviour.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.datahub import (
    LINEAGE_MAX_HOPS,
    LINEAGE_MAX_RESULTS,
    SCHEMA_FIELD_LIMIT,
    DataHubContextProvider,
    downstream_urns_from_lineage,
    entities_from_result,
    extract_criticality,
    extract_description,
    extract_domain,
    extract_owners,
    extract_tags,
    fields_from_payload,
)
from graph_traffic_control.context.mcp_client import McpClient, McpContractError
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.context.provider import ContextReadError
from graph_traffic_control.demo.agents import FCT_REVENUE, METRIC_NET_REVENUE
from graph_traffic_control.domain.clock import ManualClock
from graph_traffic_control.domain.models import Criticality
from tests.fake_mcp import FakeMcpServer, FakeMcpState, entity_payload

TOKEN = "test-token"  # noqa: S105 - fixture value

NAMESPACE = Namespace(
    urn_prefix="traffic.",
    project_tag="project-graph-traffic-control",
    domain="Demo / Graph Traffic Control",
)

DOMAIN_URN = "urn:li:domain:graph-traffic-control"
PROJECT_TAG_URN = "urn:li:tag:project-graph-traffic-control"
DASHBOARD = "urn:li:dashboard:(looker,traffic.dash_exec_revenue)"
FOREIGN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"


class TestEnvelopeContract:
    """``structuredContent`` envelopes are read exactly; anything else raises."""

    def test_entities_are_read_from_result(self):
        payload = {"result": [entity_payload(FCT_REVENUE, name="traffic.fct_revenue")]}
        assert entities_from_result(payload, [FCT_REVENUE])[FCT_REVENUE]["urn"] == FCT_REVENUE

    def test_entities_accept_a_urn_keyed_mapping(self):
        payload = {"result": {FCT_REVENUE: entity_payload(FCT_REVENUE)}}
        assert set(entities_from_result(payload, [FCT_REVENUE])) == {FCT_REVENUE}

    @pytest.mark.parametrize(
        "payload",
        [
            {},                                   # no 'result' key at all
            {"entities": []},                     # the shape an earlier revision guessed at
            {"result": "a string"},               # scalar where a collection belongs
            {"result": [{"name": "no urn"}]},     # entity without a urn
            {"result": ["not an object"]},        # list of scalars
        ],
    )
    def test_unknown_entity_shapes_raise(self, payload):
        with pytest.raises(McpContractError):
            entities_from_result(payload, [])

    def test_a_requested_entity_that_is_absent_raises(self):
        with pytest.raises(McpContractError, match="did not return"):
            entities_from_result({"result": []}, [FCT_REVENUE])

    def test_lineage_is_read_from_downstreams_search_results(self):
        payload = {"downstreams": {"searchResults": [{"entity": {"urn": METRIC_NET_REVENUE}}]}}
        assert downstream_urns_from_lineage(payload) == [METRIC_NET_REVENUE]

    @pytest.mark.parametrize(
        "payload",
        [
            {},                                                    # no 'downstreams'
            {"downstreams": []},                                   # not an object
            {"downstreams": {}},                                   # no 'searchResults'
            {"downstreams": {"searchResults": {}}},                # not a list
            {"downstreams": {"searchResults": [{"urn": "x"}]}},    # no nested 'entity'
            {"downstreams": {"searchResults": [{"entity": {}}]}},  # entity without a urn
        ],
    )
    def test_unknown_lineage_shapes_raise(self, payload):
        with pytest.raises(McpContractError):
            downstream_urns_from_lineage(payload)

    def test_fields_are_read_from_fields(self):
        payload = {"fields": [{"fieldPath": "gross_revenue", "nativeDataType": "DOUBLE"}]}
        fields = fields_from_payload(payload)
        assert (fields[0].path, fields[0].type) == ("gross_revenue", "DOUBLE")

    def test_an_entity_with_no_columns_is_an_empty_list_not_an_error(self):
        assert fields_from_payload({"fields": []}) == []

    @pytest.mark.parametrize(
        "payload",
        [{}, {"fields": {}}, {"schemaFields": []}, {"fields": [{"type": "int"}]}],
    )
    def test_unknown_field_shapes_raise(self, payload):
        with pytest.raises(McpContractError):
            fields_from_payload(payload)


class TestGovernanceExtraction:
    """Governance values are nested under properties / ownership / tags / domain."""

    def test_description_comes_from_properties(self):
        assert extract_description(entity_payload(FCT_REVENUE, description="hi")) == "hi"

    def test_editable_description_wins_over_properties(self):
        entity = entity_payload(FCT_REVENUE, description="original")
        entity["editableProperties"] = {"description": "edited"}
        assert extract_description(entity) == "edited"

    def test_absent_description_is_none_not_an_error(self):
        assert extract_description(entity_payload(FCT_REVENUE)) is None

    def test_owners_come_from_ownership_owners(self):
        entity = entity_payload(FCT_REVENUE, owners=["urn:li:corpuser:finance-data"])
        assert extract_owners(entity) == ["urn:li:corpuser:finance-data"]

    def test_owner_objects_are_unwrapped(self):
        entity = {"ownership": {"owners": [{"owner": {"urn": "urn:li:corpuser:bob"}}]}}
        assert extract_owners(entity) == ["urn:li:corpuser:bob"]

    def test_tags_come_from_tags_tags(self):
        entity = entity_payload(FCT_REVENUE, tags=[PROJECT_TAG_URN, "urn:li:tag:TIER_1"])
        assert extract_tags(entity) == sorted([PROJECT_TAG_URN, "urn:li:tag:TIER_1"])

    def test_domain_comes_from_domain_domain(self):
        assert extract_domain(entity_payload(FCT_REVENUE, domain=DOMAIN_URN)) == DOMAIN_URN

    def test_domain_accepts_a_domains_list(self):
        assert extract_domain({"domain": {"domains": [DOMAIN_URN]}}) == DOMAIN_URN

    def test_criticality_is_derived_from_a_tier_tag(self):
        entity = entity_payload(FCT_REVENUE, tags=[PROJECT_TAG_URN, "urn:li:tag:TIER_1"])
        assert extract_criticality(entity) is Criticality.TIER_1

    def test_criticality_without_a_tier_signal_is_unknown(self):
        entity = entity_payload(FCT_REVENUE, tags=[PROJECT_TAG_URN])
        assert extract_criticality(entity) is Criticality.UNKNOWN


@pytest.fixture
def state() -> FakeMcpState:
    state = FakeMcpState()
    state.add_entity(
        FCT_REVENUE,
        name="traffic.fct_revenue",
        description="Revenue fact",
        owners=["urn:li:corpuser:finance-data"],
        tags=[PROJECT_TAG_URN, "urn:li:tag:TIER_1"],
        domain=DOMAIN_URN,
    )
    state.add_entity(
        METRIC_NET_REVENUE,
        name="traffic.metric_net_revenue",
        tags=[PROJECT_TAG_URN],
        domain=DOMAIN_URN,
    )
    state.add_entity(DASHBOARD, name="traffic.dash_exec_revenue", domain=DOMAIN_URN)
    state.schema_fields[FCT_REVENUE] = [
        {"fieldPath": "gross_revenue", "nativeDataType": "DOUBLE"}
    ]
    state.schema_fields[METRIC_NET_REVENUE] = [{"fieldPath": "net_revenue", "type": "DOUBLE"}]
    state.lineage[FCT_REVENUE] = [METRIC_NET_REVENUE]
    return state


def _provider(server, urns, state=None) -> tuple[DataHubContextProvider, McpClient]:
    client = McpClient(server.url, TOKEN)
    return DataHubContextProvider(client, NAMESPACE, urns, ManualClock()), client


class TestSnapshot:
    def test_snapshot_reads_entities_lineage_and_governance(self, state):
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE, METRIC_NET_REVENUE])
            snapshot = provider.snapshot()
            client.close()

        entity = snapshot.entities[FCT_REVENUE]
        assert provider.source == "datahub-mcp"
        assert entity.criticality is Criticality.TIER_1
        assert entity.owners == ["urn:li:corpuser:finance-data"]
        assert entity.domain == DOMAIN_URN
        assert PROJECT_TAG_URN in entity.tags
        assert entity.fields[0].path == "gross_revenue"
        assert (FCT_REVENUE, METRIC_NET_REVENUE) in {
            (edge.upstream, edge.downstream) for edge in snapshot.edges
        }

    def test_tool_arguments_match_the_coordinator_contract(self, state):
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE])
            provider.snapshot()
            client.close()

        by_tool = {name: args for name, args in state.calls}
        assert by_tool["get_entities"] == {"urns": [FCT_REVENUE]}
        assert by_tool["list_schema_fields"] == {
            "urn": FCT_REVENUE,
            "limit": SCHEMA_FIELD_LIMIT,
        }
        assert by_tool["get_lineage"] == {
            "urn": FCT_REVENUE,
            "upstream": False,
            "max_hops": LINEAGE_MAX_HOPS,
            "max_results": LINEAGE_MAX_RESULTS,
        }

    def test_a_dashboard_is_never_asked_for_schema_fields(self, state):
        """Not asking is the fix. Asking and tolerating the error is the bug."""
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [DASHBOARD])
            snapshot = provider.snapshot()
            client.close()

        assert snapshot.entities[DASHBOARD].fields == []
        assert [args["urn"] for name, args in state.calls if name == "list_schema_fields"] == []

    def test_foreign_downstream_edges_are_dropped(self, state):
        state.lineage[FCT_REVENUE] = [METRIC_NET_REVENUE, FOREIGN]
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE, METRIC_NET_REVENUE])
            snapshot = provider.snapshot()
            client.close()

        downstreams = {edge.downstream for edge in snapshot.edges}
        assert FOREIGN not in downstreams, "another project's entity entered this graph"
        assert METRIC_NET_REVENUE in downstreams, "a real edge was dropped with the foreign one"

    def test_foreign_allocated_urn_is_refused_at_construction(self, state):
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            with pytest.raises(NamespaceViolation):
                DataHubContextProvider(client, NAMESPACE, [FOREIGN], ManualClock())
            client.close()

    def test_snapshot_is_deterministic_for_the_same_state(self, state):
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE, METRIC_NET_REVENUE])
            first = provider.snapshot().fingerprint()
            second = provider.snapshot().fingerprint()
            client.close()
        assert first == second


class TestFailsClosed:
    """The behaviour the coordinator rejected the previous candidate for."""

    @pytest.mark.parametrize(
        "failing_tool", ["get_entities", "get_lineage", "list_schema_fields"]
    )
    def test_an_mcp_error_aborts_the_snapshot(self, state, failing_tool):
        state.fail_tools.add(failing_tool)
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE])
            with pytest.raises(ContextReadError):
                provider.snapshot()
            client.close()

    def test_a_lineage_failure_never_degrades_to_an_empty_edge_list(self, state):
        """The specific regression: 'no lineage readable' must not read as 'no lineage'."""
        state.fail_tools.add("get_lineage")
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE, METRIC_NET_REVENUE])
            with pytest.raises(ContextReadError, match="incomplete graph"):
                provider.snapshot()
            client.close()

    def test_a_missing_schema_response_aborts_rather_than_emptying_the_schema(self, state):
        state.malformed_tools["list_schema_fields"] = {"schemaFields": []}
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE])
            with pytest.raises(ContextReadError):
                provider.snapshot()
            client.close()

    @pytest.mark.parametrize(
        "tool,payload",
        [
            ("get_entities", {"entities": []}),
            ("get_lineage", {"relationships": []}),
            ("list_schema_fields", {"fields": "not a list"}),
        ],
    )
    def test_unknown_response_shapes_abort_the_snapshot(self, state, tool, payload):
        state.malformed_tools[tool] = payload
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE])
            with pytest.raises(ContextReadError):
                provider.snapshot()
            client.close()

    def test_an_allocated_entity_missing_from_datahub_aborts(self, state):
        """A URN in the manifest that DataHub does not have is a seeding failure, not a gap."""
        del state.entities[METRIC_NET_REVENUE]
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [FCT_REVENUE, METRIC_NET_REVENUE])
            with pytest.raises(ContextReadError):
                provider.snapshot()
            client.close()

    def test_an_empty_allocation_is_refused(self, state):
        with FakeMcpServer(state) as server:
            provider, client = _provider(server, [])
            with pytest.raises(ContextReadError, match="No allocated entities"):
                provider.snapshot()
            client.close()
