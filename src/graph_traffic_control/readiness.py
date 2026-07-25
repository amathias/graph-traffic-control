"""Readiness evaluation.

Two hard rules, both from the coordinator's live-milestone instruction:

1. **Non-mutating.** Readiness may not write anything, anywhere — not a probe file, not a tag,
   not a description. Writability is tested with ``os.access``, never with a real write.
2. **A basic GMS health response is never sufficient.** In live mode the project is ready only
   after an *authenticated* check that the required MCP tools exist, that this project's tag is
   resolvable, and that its allocated ``traffic.`` entities are actually present. An unauthenticated
   liveness ping proves a container is up, not that the coordinator can do its job.

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
)
from graph_traffic_control.context.mcp_client import McpClient, McpError
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.demo.seed import (
    SEED_MANIFEST,
    collect_urns,
    load_fixture_graph,
)
from graph_traffic_control.execute.targets import ARTIFACTS_DIRNAME

#: Environments where running fixture-backed is a legitimate ready state.
FIXTURE_OK_ENVIRONMENTS = frozenset({"local", "test", "dev"})

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

    return {"ok": True, "entities": len(urns), "edges": len(graph.get("edges", []))}


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

        # Authenticated check that this project's tag resolves.
        tag_urn = f"urn:li:tag:{namespace.project_tag}"
        try:
            tag_payload = client.call_tool(TOOL_GET_ENTITIES, {"urns": [tag_urn]})
        except McpError as exc:
            return {
                **result,
                "ok": False,
                "status": "tag_unverified",
                "detail": f"Could not resolve {tag_urn}: {exc}",
            }
        if not _payload_mentions(tag_payload, tag_urn):
            return {
                **result,
                "ok": False,
                "status": "tag_missing",
                "detail": f"{tag_urn} is not present in DataHub.",
            }
        result["tag_verified"] = namespace.project_tag

        # Authenticated check that allocated entities exist.
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

        probe = sorted(allocated_urns)[:5]
        try:
            entity_payload = client.call_tool(TOOL_GET_ENTITIES, {"urns": probe})
        except McpError as exc:
            return {
                **result,
                "ok": False,
                "status": "entities_unverified",
                "detail": f"Could not read allocated entities: {exc}",
            }

        found = [urn for urn in probe if _payload_mentions(entity_payload, urn)]
        result["allocated_entities_probed"] = len(probe)
        result["allocated_entities_found"] = len(found)
        if len(found) != len(probe):
            missing = sorted(set(probe) - set(found))
            return {
                **result,
                "ok": False,
                "status": "entities_missing",
                "missing": missing,
                "detail": "Allocated traffic. entities are not present in DataHub. Ingest first.",
            }

        return {**result, "ok": True, "status": "verified"}
    finally:
        client.close()


def _payload_mentions(payload: Any, urn: str) -> bool:
    """True when a tool payload actually contains the requested URN."""
    if isinstance(payload, str):
        return urn in payload
    if isinstance(payload, dict):
        if payload.get("urn") == urn:
            return True
        return any(_payload_mentions(value, urn) for value in payload.values())
    if isinstance(payload, list):
        return any(_payload_mentions(item, urn) for item in payload)
    return False


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
