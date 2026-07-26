"""Readiness evaluation.

Two hard rules, both from the coordinator's live-milestone instruction:

1. **Non-mutating.** Readiness may not write anything, anywhere — not a probe file, not a tag,
   not a description. Writability is tested with ``os.access``, never with a real write.
2. **A basic GMS health response is never sufficient.** In live mode the project is ready only
   after an *authenticated* check that every required MCP tool exists, that this project's tag and
   domain both resolve, that the **complete** allocated ``traffic.`` catalogue is present — not a
   sample of it — and that **the graph snapshot actually builds**. An unauthenticated liveness
   ping proves a container is up, not that the coordinator can do its job, and a partially
   ingested catalogue yields a partial graph that reports fewer conflicts than really exist.
3. **Readiness answers for the same snapshot ``/api/graph`` serves.** Presence checks alone once
   reported 200 on a live instance whose ``/api/graph`` returned 503, because nothing in readiness
   had ever read lineage. A readiness check that passes while the endpoint it vouches for fails
   does not merely miss the problem — it certifies it. Both modes therefore build the real
   snapshot through the same provider the API uses.
4. **The seeded lineage must read back.** Entity completeness is not enough. This project's
   central claim is an *edge* — the conflict between two proposals sharing no declared URN — so a
   snapshot with all nine entities and no edges answers "nothing conflicts" to every question,
   with HTTP 200. Readiness therefore verifies the edges too, and distinguishes a missing seed
   from an unindexed graph so nobody re-seeds a correctly seeded instance.

Mode is derived, not configured: live mode requires both ``DATAHUB_MCP_URL`` and ``DATAHUB_TOKEN``.
In fixture mode the service is ready only in a local or test environment, so a deployed instance
missing its credentials fails closed instead of quietly serving fixture data.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from graph_traffic_control.config import Settings
from graph_traffic_control.context.datahub import (
    REQUIRED_READ_TOOLS,
    REQUIRED_WRITE_TOOLS,
    TOOL_GET_ENTITIES,
    DataHubContextProvider,
    present_urns_from_result,
)
from graph_traffic_control.context.fixture import FixtureContextProvider
from graph_traffic_control.context.mcp_client import McpClient, McpError
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.context.provider import ContextReadError
from graph_traffic_control.demo.seed import (
    SEED_MANIFEST,
    collect_urns,
    load_fixture_graph,
)
from graph_traffic_control.execute.targets import ARTIFACTS_DIRNAME

#: Environments where running fixture-backed is a legitimate ready state.
FIXTURE_OK_ENVIRONMENTS = frozenset({"local", "test", "dev"})

#: Allocated URNs verified per ``get_entities`` call. Bounded so a large allocation cannot turn
#: readiness into one enormous request, while still verifying the catalogue in full.
ENTITY_PROBE_BATCH = 20

ClientFactory = Callable[[Settings], McpClient]


def _default_client_factory(settings: Settings) -> McpClient:
    return McpClient(settings.datahub_mcp_url, settings.datahub_token)


def check_fixture_graph(settings: Settings, namespace: Namespace) -> dict[str, Any]:
    try:
        graph = load_fixture_graph(settings)
    except FileNotFoundError as exc:
        return {"ok": False, "detail": str(exc)}
    except json.JSONDecodeError as exc:
        return {"ok": False, "detail": f"Fixture graph is not valid JSON: {exc}"}

    try:
        urns = collect_urns(graph)
    except KeyError as exc:
        return {"ok": False, "detail": f"Fixture graph is missing key {exc}"}

    try:
        namespace.require_all(urns, operation="Readiness check")
    except NamespaceViolation as exc:
        return {"ok": False, "detail": str(exc)}

    # Same reasoning as the live snapshot check below: validating that the fixture file parses
    # and stays in-namespace is not the same as proving a snapshot can be built from it, and
    # `/api/graph` serves the snapshot, not the file.
    try:
        snapshot = FixtureContextProvider(settings.fixture_root, namespace).snapshot()
    except (ContextReadError, NamespaceViolation) as exc:
        return {"ok": False, "detail": f"Fixture graph is not buildable into a snapshot: {exc}"}

    return {
        "ok": True,
        "entities": len(urns),
        "edges": len(graph.get("edges", [])),
        "snapshot_entities": len(snapshot.entities),
        "snapshot_edges": len(snapshot.edges),
    }


def check_state(settings: Settings) -> dict[str, Any]:
    """State directory, manifest, and artifacts. Writability is probed without writing."""
    state_dir = settings.state_dir
    if not state_dir.is_dir():
        return {"ok": False, "detail": f"{state_dir} does not exist. Run `gtc-seed` first."}
    if not os.access(state_dir, os.W_OK):
        return {"ok": False, "detail": f"{state_dir} is not writable."}

    manifest = state_dir / SEED_MANIFEST
    if not manifest.is_file():
        return {"ok": False, "detail": "Seed manifest missing. Run `gtc-seed`."}

    artifacts = state_dir / ARTIFACTS_DIRNAME
    if not artifacts.is_dir():
        return {"ok": False, "detail": "Artifact directory missing. Run `gtc-seed`."}
    sql_files = sorted(p.name for p in artifacts.glob("*.sql"))
    if not sql_files:
        return {"ok": False, "detail": "No SQL artifacts present. Run `gtc-seed`."}

    return {"ok": True, "path": str(state_dir), "artifacts": len(sql_files)}


def check_datahub(
    settings: Settings,
    namespace: Namespace,
    allocated_urns: list[str],
    client_factory: ClientFactory | None = None,
) -> dict[str, Any]:
    """Authenticated, non-mutating DataHub verification.

    Never calls a bare health endpoint to decide readiness. Every signal here requires the token
    to have been accepted by the MCP server.
    """
    if not settings.live_mode:
        ok = settings.app_env in FIXTURE_OK_ENVIRONMENTS
        return {
            "ok": ok,
            "mode": "fixture",
            "status": "not_configured",
            "detail": (
                "DATAHUB_MCP_URL/DATAHUB_TOKEN unset; running fixture-backed."
                if ok
                else "Fixture mode is not an acceptable ready state in "
                f"APP_ENV={settings.app_env!r}."
            ),
        }

    factory = client_factory or _default_client_factory
    result: dict[str, Any] = {"mode": "live", "endpoint_configured": True}

    try:
        client = factory(settings)
    except McpError as exc:
        return {**result, "ok": False, "status": "client_error", "detail": str(exc)}

    try:
        try:
            tools = set(client.list_tools())
        except McpError as exc:
            return {**result, "ok": False, "status": "unreachable", "detail": str(exc)}

        missing_read = sorted(REQUIRED_READ_TOOLS - tools)
        missing_write = sorted(REQUIRED_WRITE_TOOLS - tools)
        result["tools_verified"] = sorted(
            (REQUIRED_READ_TOOLS | REQUIRED_WRITE_TOOLS) & tools
        )
        if missing_read or missing_write:
            return {
                **result,
                "ok": False,
                "status": "missing_tools",
                "missing_read_tools": missing_read,
                "missing_write_tools": missing_write,
                "detail": "The MCP server does not expose the tools this project requires.",
            }

        # Authenticated check that this project's governance objects resolve.
        tag_urn = f"urn:li:tag:{namespace.project_tag}"
        domain_urn = settings.datahub_domain_urn
        try:
            governance = _present(client, [tag_urn, domain_urn])
        except McpError as exc:
            return {
                **result,
                "ok": False,
                "status": "governance_unverified",
                "detail": f"Could not resolve {tag_urn} / {domain_urn}: {exc}",
            }
        if tag_urn not in governance:
            return {
                **result,
                "ok": False,
                "status": "tag_missing",
                "detail": f"{tag_urn} is not present in DataHub.",
            }
        if domain_urn not in governance:
            return {
                **result,
                "ok": False,
                "status": "domain_missing",
                "detail": f"{domain_urn} is not present in DataHub.",
            }
        result["tag_verified"] = namespace.project_tag
        result["domain_verified"] = domain_urn

        # Authenticated check that the *complete* allocation exists. A sample is not enough:
        # a partially ingested catalogue produces a partial graph, and a partial graph reports
        # fewer conflicts than really exist.
        if not allocated_urns:
            return {
                **result,
                "ok": False,
                "status": "no_allocated_entities",
                "detail": "Seed manifest lists no entities to verify.",
            }
        try:
            namespace.require_all(allocated_urns, operation="Readiness entity check")
        except NamespaceViolation as exc:
            return {**result, "ok": False, "status": "namespace_violation", "detail": str(exc)}

        expected = sorted(set(allocated_urns))
        try:
            found = _present(client, expected)
        except McpError as exc:
            return {
                **result,
                "ok": False,
                "status": "entities_unverified",
                "detail": f"Could not read allocated entities: {exc}",
            }

        result["allocated_entities_expected"] = len(expected)
        result["allocated_entities_found"] = len(found & set(expected))
        missing = sorted(set(expected) - found)
        if missing:
            return {
                **result,
                "ok": False,
                "status": "entities_missing",
                "missing": missing,
                "detail": (
                    f"{len(missing)} of {len(expected)} allocated traffic. entities are absent "
                    "from DataHub. Run the namespace-scoped seed before serving."
                ),
            }

        # The snapshot itself, through the same provider `/api/graph` uses.
        #
        # Every check above passed on the live instance while `/api/graph` returned 503: entities
        # can all be present and individually readable, and the graph still be unbuildable,
        # because nothing above had ever read *lineage*. Readiness that answers 200 while the one
        # endpoint the coordinator depends on answers 503 is worse than no readiness check —
        # it certifies the failure. So readiness now builds the real snapshot.
        #
        # Still strictly non-mutating: `snapshot()` only reads.
        try:
            snapshot = DataHubContextProvider(client, namespace, expected).snapshot()
        except (ContextReadError, NamespaceViolation) as exc:
            return {
                **result,
                "ok": False,
                "status": "graph_unreadable",
                "detail": (
                    f"Every allocated entity is present, but the graph could not be built: {exc} "
                    "`/api/graph` would return 503, so this instance is not ready."
                ),
            }

        result["graph_entities"] = len(snapshot.entities)
        result["graph_edges"] = len(snapshot.edges)
        result["graph_fingerprint"] = snapshot.fingerprint()

        # The lineage this project seeded must actually be readable back.
        #
        # This is the entity-completeness rule (ADR-004) applied to edges, for the identical
        # reason: a partial graph reports fewer conflicts than really exist. It matters more for
        # edges than for entities, because this project's central claim *is* an edge — the
        # lineage-mediated conflict between two proposals that share no declared URN. A snapshot
        # with the right nine entities and no edges is not a quiet degradation; it answers "these
        # changes do not conflict" to every question, and it answers with HTTP 200.
        #
        # Entities present + edges missing is an index problem, not an ingestion problem, so the
        # message must not send anyone to re-seed a correctly seeded instance.
        try:
            expected_edges = {
                (edge["upstream"], edge["downstream"])
                for edge in load_fixture_graph(settings).get("edges", [])
            }
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as exc:
            return {
                **result,
                "ok": False,
                "status": "expected_lineage_unknown",
                "detail": (
                    f"Cannot verify live lineage because the recorded allocation could not be "
                    f"read: {exc}"
                ),
            }

        built_edges = {(edge.upstream, edge.downstream) for edge in snapshot.edges}
        if missing_edges := sorted(expected_edges - built_edges):
            return {
                **result,
                "ok": False,
                "status": "lineage_incomplete",
                "missing_edges": [list(edge) for edge in missing_edges],
                "detail": (
                    f"All {len(expected)} allocated entities are present, but "
                    f"{len(missing_edges)} of {len(expected_edges)} seeded lineage edge(s) cannot "
                    f"be read back — DataHub returned no downstream match for them. The entities "
                    f"and their upstreamLineage aspects were accepted, so this is a graph index "
                    f"problem, not a missing seed: do NOT re-seed. Reindex DataHub's graph service "
                    f"(the lineage index) and re-check. Serving now would report a graph with no "
                    f"lineage, and a graph with no lineage reports no conflicts."
                ),
            }
        result["lineage_edges_verified"] = len(expected_edges)

        return {**result, "ok": True, "status": "verified"}
    finally:
        client.close()


def _present(client: McpClient, urns: list[str]) -> set[str]:
    """URNs DataHub actually returns, read in bounded batches. Never mutates."""
    found: set[str] = set()
    for start in range(0, len(urns), ENTITY_PROBE_BATCH):
        batch = urns[start : start + ENTITY_PROBE_BATCH]
        payload = client.call_tool_structured(TOOL_GET_ENTITIES, {"urns": batch})
        found |= present_urns_from_result(payload)
    return found


def evaluate(
    settings: Settings,
    namespace: Namespace,
    allocated_urns: list[str],
    client_factory: ClientFactory | None = None,
) -> dict[str, Any]:
    checks = {
        "fixture_graph": check_fixture_graph(settings, namespace),
        "state": check_state(settings),
        "datahub": check_datahub(settings, namespace, allocated_urns, client_factory),
    }
    return {
        "ready": all(check["ok"] for check in checks.values()),
        "mode": checks["datahub"].get("mode", "unknown"),
        "checks": checks,
    }
