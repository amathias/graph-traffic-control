"""Downstream lineage: the empty variant, and which entities are asked at all.

A live run against the shared instance failed with, exactly:

    Could not read downstream lineage for urn:li:dashboard:(looker,traffic.dash_exec_revenue):
    get_lineage downstreams.searchResults is a NoneType; expected a list

Every allocated entity was present and individually readable. The seed had applied all 49
operations. What failed was one question that should not have been asked, answered in a shape the
reader did not accept.

Two separate things are fixed here, and they are tested separately because they are separate
claims:

1. **A dashboard is a lineage sink, so it is not asked for downstream lineage.** Its inbound edge
   is discovered from the dataset at the other end. The completeness claim — that skipping the
   call loses no edge — is *proved* below against the fixture's own edge set, not asserted.
2. **``searchResults: null`` is a valid empty answer**, and only ``null``. Any other non-list
   still raises, and a tool error still aborts the whole read.

Neither is a tolerated failure. The protocol double now answers ``null`` for lineage sinks exactly
as the live instance does — it previously returned ``[]`` unconditionally, which is precisely why
the suite was green while `/api/graph` was returning 503.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.datahub import (
    DOWNSTREAM_LINEAGE_URN_PREFIXES,
    TOOL_GET_LINEAGE,
    DataHubContextProvider,
    downstream_urns_from_lineage,
)
from graph_traffic_control.context.mcp_client import McpClient, McpContractError
from graph_traffic_control.context.provider import ContextReadError
from graph_traffic_control.demo.seed import collect_urns, load_fixture_graph
from tests.fake_mcp import FakeMcpServer, FakeMcpState

TOKEN = "test-token"  # noqa: S105 - fixture value

DASHBOARD = "urn:li:dashboard:(looker,traffic.dash_exec_revenue)"
METRIC = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.metric_net_revenue,PROD)"
FOREIGN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"

#: The live response, verbatim. `downstreams` present, `searchResults` null.
LIVE_EMPTY_DOWNSTREAMS = {"downstreams": {"searchResults": None}}


@pytest.fixture
def graph(seeded_settings):
    return load_fixture_graph(seeded_settings)


@pytest.fixture
def live_like(graph, seeded_settings):
    """The protocol double loaded with the seeded fixture graph."""
    state = FakeMcpState()
    for urn in collect_urns(graph):
        state.add_entity(
            urn,
            name=urn,
            description="d",
            owners=["urn:li:corpGroup:sales-eng"],
            tags=["urn:li:tag:project-graph-traffic-control"],
            domain="urn:li:domain:graph-traffic-control",
        )
    for edge in graph["edges"]:
        state.lineage.setdefault(edge["upstream"], []).append(edge["downstream"])
    return state


def _snapshot(state, namespace, urns):
    with FakeMcpServer(state) as server:
        client = McpClient(server.url, TOKEN)
        try:
            return DataHubContextProvider(client, namespace, urns).snapshot()
        finally:
            client.close()


class TestTheExactLiveResponse:
    def test_null_search_results_is_an_empty_downstream_set(self):
        """The exact payload that broke the live run."""
        assert downstream_urns_from_lineage(LIVE_EMPTY_DOWNSTREAMS) == []

    def test_an_empty_list_is_still_accepted(self):
        assert downstream_urns_from_lineage({"downstreams": {"searchResults": []}}) == []

    def test_results_still_read_normally(self):
        payload = {"downstreams": {"searchResults": [{"entity": {"urn": METRIC}}]}}
        assert downstream_urns_from_lineage(payload) == [METRIC]

    @pytest.mark.parametrize("value", ["", 0, False, {}, "null", 0.0])
    def test_only_null_is_accepted_never_merely_falsy(self, value):
        """`null` means no results. Nothing else does, or a failure becomes an empty graph."""
        with pytest.raises(McpContractError, match="expected a list or null"):
            downstream_urns_from_lineage({"downstreams": {"searchResults": value}})

    def test_a_missing_downstreams_key_still_raises(self):
        with pytest.raises(McpContractError, match="no 'downstreams' key"):
            downstream_urns_from_lineage({})

    def test_an_absent_search_results_key_with_no_total_is_refused(self):
        """An empty object says nothing about how many matches there were.

        A later live observation showed ``searchResults`` can be omitted legitimately, but only
        alongside a ``total`` that states the count — see :class:`TestTheFacetsOnlyEnvelope`. With
        neither, the response is simply unrecognised, and unrecognised must not become "no edges".
        """
        with pytest.raises(McpContractError, match="neither 'searchResults' nor an integer"):
            downstream_urns_from_lineage({"downstreams": {}})

    def test_a_malformed_entry_inside_the_list_still_raises(self):
        with pytest.raises(McpContractError):
            downstream_urns_from_lineage({"downstreams": {"searchResults": [{"entity": {}}]}})


class TestLineageSinksAreNotAsked:
    def test_the_dashboard_is_never_asked_for_downstream_lineage(
        self, live_like, namespace, graph
    ):
        urns = collect_urns(graph)
        _snapshot(live_like, namespace, urns)

        downstream_calls = [
            args
            for name, args in live_like.calls
            if name == TOOL_GET_LINEAGE and args.get("upstream") is False
        ]
        assert downstream_calls, "datasets must still be asked"
        assert all(args["urn"] != DASHBOARD for args in downstream_calls)

    def test_datasets_are_still_asked(self, live_like, namespace, graph):
        urns = collect_urns(graph)
        _snapshot(live_like, namespace, urns)
        asked = {
            args["urn"]
            for name, args in live_like.calls
            if name == TOOL_GET_LINEAGE and args.get("upstream") is False
        }
        datasets = {u for u in urns if u.startswith(DOWNSTREAM_LINEAGE_URN_PREFIXES)}
        assert asked == datasets

    def test_skipping_the_sink_loses_no_edge(self, live_like, namespace, graph):
        """The completeness claim, proved rather than asserted.

        The dashboard's inbound edge is found from the dataset side, so the snapshot's edge set
        must still equal the fixture's edge set exactly.
        """
        snapshot = _snapshot(live_like, namespace, collect_urns(graph))
        built = {(e.upstream, e.downstream) for e in snapshot.edges}
        expected = {(e["upstream"], e["downstream"]) for e in graph["edges"]}
        assert built == expected
        assert (METRIC, DASHBOARD) in built, "the edge into the sink is still present"

    def test_the_whole_snapshot_builds_against_a_live_shaped_server(
        self, live_like, namespace, graph
    ):
        """End to end: this is the run that returned 503."""
        snapshot = _snapshot(live_like, namespace, collect_urns(graph))
        assert len(snapshot.entities) == 9
        assert len(snapshot.edges) == 7
        assert snapshot.fingerprint()


class TestStillFailsClosed:
    def test_a_tool_error_still_aborts_the_read(self, live_like, namespace, graph):
        """Accepting null must not have become 'tolerate lineage failures'."""
        live_like.fail_tools.add(TOOL_GET_LINEAGE)
        with pytest.raises(ContextReadError, match="downstream lineage"):
            _snapshot(live_like, namespace, collect_urns(graph))

    def test_a_malformed_lineage_payload_still_aborts_the_read(
        self, live_like, namespace, graph
    ):
        live_like.malformed_tools[TOOL_GET_LINEAGE] = {"downstreams": {"searchResults": "nope"}}
        with pytest.raises(ContextReadError):
            _snapshot(live_like, namespace, collect_urns(graph))

    def test_a_dataset_answering_null_is_an_empty_edge_set_not_a_crash(
        self, live_like, namespace, graph
    ):
        """A leaf dataset may legitimately answer null; it must not abort, and adds no edge."""
        leaf = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_support_sla,PROD)"
        live_like.null_downstreams.add(leaf)
        snapshot = _snapshot(live_like, namespace, collect_urns(graph))
        assert not any(e.upstream == leaf for e in snapshot.edges)
        assert len(snapshot.edges) == 7

    def test_a_foreign_downstream_is_still_dropped(self, live_like, namespace, graph):
        """Namespace isolation is unchanged by any of this."""
        live_like.lineage.setdefault(METRIC, []).append(FOREIGN)
        snapshot = _snapshot(live_like, namespace, collect_urns(graph))
        assert all(FOREIGN not in (e.upstream, e.downstream) for e in snapshot.edges)
        assert len(snapshot.edges) == 7


#: The live envelope for the second failure: `facets` and `total`, no `searchResults` key.
#: Sanitized from the coordinator's live capture of the downstream call on traffic.fct_revenue.
LIVE_FACETS_ONLY = {
    "downstreams": {
        "total": 0,
        "facets": [
            {
                "displayName": "Degree",
                "aggregations": [
                    {"count": 0, "value": "1"},
                    {"count": 0, "value": "2"},
                    {"count": 0, "value": "3+"},
                ],
            }
        ],
    }
}


class TestTheFacetsOnlyEnvelope:
    """`searchResults` omitted, `facets` and `total` present.

    `total` is the server's own count of matches, so it decides whether this is an empty answer
    or a withheld one. The absence of a key decides nothing.
    """

    def test_total_zero_is_an_empty_downstream_set(self):
        assert downstream_urns_from_lineage(LIVE_FACETS_ONLY) == []

    def test_a_nonzero_total_without_results_is_refused(self):
        """Told there are matches and not given them: never read as 'no edges'."""
        payload = {"downstreams": {"total": 3, "facets": []}}
        with pytest.raises(McpContractError, match="total=3"):
            downstream_urns_from_lineage(payload)

    def test_no_search_results_and_no_total_is_refused(self):
        with pytest.raises(McpContractError, match="neither 'searchResults' nor an integer"):
            downstream_urns_from_lineage({"downstreams": {"facets": []}})

    @pytest.mark.parametrize("flag", [False, True])
    def test_a_boolean_total_is_never_a_count(self, flag):
        """The one that shipped.

        ``bool`` subclasses ``int`` and ``False == 0``, so a value-first ``total == 0`` check read
        a JSON ``false`` as "no downstream matches" — accepting a flag as a count and turning an
        unreadable graph into an empty one. ``True`` took a different branch and raised, which is
        exactly why testing only ``True`` missed it.

        Both are parametrised deliberately: this defect is invisible unless both are asserted.
        """
        with pytest.raises(McpContractError, match="A boolean is not a match count"):
            downstream_urns_from_lineage({"downstreams": {"total": flag, "facets": []}})

    @pytest.mark.parametrize(
        "total", [0.0, 0.5, "0", None, [], {}, [0]], ids=repr
    )
    def test_a_non_integer_total_is_refused(self, total):
        """Only a real integer is a count. A float is not, however round it looks."""
        with pytest.raises(McpContractError, match="neither 'searchResults' nor an integer"):
            downstream_urns_from_lineage({"downstreams": {"total": total, "facets": []}})

    @pytest.mark.parametrize("total", [-1, -7])
    def test_a_negative_total_is_refused(self, total):
        """Not a possible match count, so not an answer about edges."""
        with pytest.raises(McpContractError, match="not a possible"):
            downstream_urns_from_lineage({"downstreams": {"total": total, "facets": []}})

    def test_only_a_real_integer_zero_is_accepted(self):
        """The whole allowance, stated once: int 0 and nothing else that compares equal to it."""
        assert downstream_urns_from_lineage({"downstreams": {"total": 0, "facets": []}}) == []
        for impostor in (False, 0.0, "0", None):
            with pytest.raises(McpContractError):
                downstream_urns_from_lineage({"downstreams": {"total": impostor}})
