"""Lineage expansion and shortest-path evidence.

This is where the project's central claim is made concrete: two proposals whose declared write
sets and read sets do not intersect can still conflict, because DataHub lineage connects them.
The shortest path between the two is the evidence, and it is what the demo shows.

Expansion is bounded by ``max_depth``. An unbounded expansion would eventually mark every asset
in a connected graph as impacted, which would make the coordinator behave like a global lock and
destroy the project's differentiation (``PROJECT_BRIEF.md`` conflict matrix row 6).
"""

from __future__ import annotations

import networkx as nx

from graph_traffic_control.domain.models import Criticality, GraphSnapshot, ImpactSet

DEFAULT_MAX_DEPTH = 3

_CRITICALITY_ORDER = {
    Criticality.UNKNOWN: 0,
    Criticality.TIER_3: 1,
    Criticality.TIER_2: 2,
    Criticality.TIER_1: 3,
}


def build_graph(snapshot: GraphSnapshot) -> nx.DiGraph:
    """Directed graph, edges pointing upstream -> downstream."""
    graph = nx.DiGraph()
    for urn in snapshot.entities:
        graph.add_node(urn)
    for edge in snapshot.edges:
        graph.add_node(edge.upstream)
        graph.add_node(edge.downstream)
        graph.add_edge(edge.upstream, edge.downstream)
    return graph


def descendants_within(graph: nx.DiGraph, source: str, max_depth: int) -> set[str]:
    """Downstream nodes reachable within ``max_depth`` hops."""
    if source not in graph:
        return set()
    lengths = nx.single_source_shortest_path_length(graph, source, cutoff=max_depth)
    return {node for node, distance in lengths.items() if distance > 0}


def ancestors_within(graph: nx.DiGraph, source: str, max_depth: int) -> set[str]:
    """Upstream nodes reachable within ``max_depth`` hops."""
    if source not in graph:
        return set()
    reversed_graph = graph.reverse(copy=False)
    lengths = nx.single_source_shortest_path_length(reversed_graph, source, cutoff=max_depth)
    return {node for node, distance in lengths.items() if distance > 0}


def shortest_lineage_path(graph: nx.DiGraph, source: str, target: str) -> list[str]:
    """Shortest directed path source -> target, or [] when none exists.

    This is the conflict evidence. It is deliberately directed: a path from A's write target down
    to B's read target proves that A's change can reach B. The reverse would not.
    """
    if source not in graph or target not in graph:
        return []
    try:
        return list(nx.shortest_path(graph, source=source, target=target))
    except nx.NetworkXNoPath:
        return []


def expand_impact(
    snapshot: GraphSnapshot,
    declared_reads: list[str],
    declared_writes: list[str],
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> ImpactSet:
    """Expand declared sets through lineage within the policy depth."""
    graph = build_graph(snapshot)

    downstream: set[str] = set()
    upstream: set[str] = set()
    for urn in declared_writes:
        downstream |= descendants_within(graph, urn, max_depth)
        upstream |= ancestors_within(graph, urn, max_depth)

    declared = set(declared_reads) | set(declared_writes)
    downstream -= declared
    upstream -= declared

    impacted = declared | downstream
    max_criticality = Criticality.UNKNOWN
    for urn in impacted:
        entity = snapshot.entities.get(urn)
        if entity is None:
            continue
        if _CRITICALITY_ORDER[entity.criticality] > _CRITICALITY_ORDER[max_criticality]:
            max_criticality = entity.criticality

    return ImpactSet(
        declared_reads=sorted(declared_reads),
        declared_writes=sorted(declared_writes),
        expanded_downstream=sorted(downstream),
        expanded_upstream=sorted(upstream),
        blast_radius=len(downstream),
        max_criticality=max_criticality,
    )
