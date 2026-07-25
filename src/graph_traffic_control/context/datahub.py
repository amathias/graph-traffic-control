"""DataHub-backed context provider, read through the MCP server.

Reads only this project's allocated entities. The allocated URN list is supplied by the caller
(from the seed manifest) rather than discovered by an open search, which keeps every read inside
the ``traffic.`` namespace by construction and makes the read set deterministic. Every URN is
still passed through the namespace guard.

Response-shape caution
----------------------
``mcp-server-datahub`` returns tool output as JSON text whose exact shape is not pinned by this
project. The extractors below accept several plausible shapes and fall back rather than raising,
because a shape mismatch must degrade to "field unknown", never to a wrong conflict decision.
These extractors have **not** been exercised against a live DataHub Core v1.6.0 instance from
this session; see ``docs/LIMITATIONS.md``.
"""

from __future__ import annotations

from typing import Any

from graph_traffic_control.context.mcp_client import McpClient, McpError
from graph_traffic_control.context.namespace import Namespace
from graph_traffic_control.domain.clock import Clock, SystemClock
from graph_traffic_control.domain.models import (
    Criticality,
    EntityContext,
    GraphSnapshot,
    LineageEdge,
    SchemaField,
)

TOOL_GET_ENTITIES = "get_entities"
TOOL_GET_LINEAGE = "get_lineage"
TOOL_LIST_SCHEMA_FIELDS = "list_schema_fields"
TOOL_UPDATE_DESCRIPTION = "update_description"

#: Read tools the provider needs before it can claim a live read.
REQUIRED_READ_TOOLS = frozenset({TOOL_GET_ENTITIES, TOOL_GET_LINEAGE, TOOL_LIST_SCHEMA_FIELDS})

#: Write tools needed for the reversible writeback.
REQUIRED_WRITE_TOOLS = frozenset({TOOL_UPDATE_DESCRIPTION})


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("entities", "results", "items", "fields", "relationships"):
            if isinstance(value.get(key), list):
                return value[key]
    return [value]


def _first_str(payload: Any, *keys: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = value.get("string") or value.get("value") or value.get("description")
            if isinstance(nested, str) and nested.strip():
                return nested
    return None


def extract_description(entity_payload: Any) -> str | None:
    """Best-effort description extraction across plausible payload shapes."""
    direct = _first_str(entity_payload, "description", "editableDescription")
    if direct:
        return direct
    if isinstance(entity_payload, dict):
        for container in ("properties", "editableProperties", "datasetProperties"):
            nested = entity_payload.get(container)
            found = _first_str(nested, "description")
            if found:
                return found
    return None


def extract_criticality(entity_payload: Any) -> Criticality:
    """Map a DataHub tier/criticality signal onto the project's enum, defaulting to UNKNOWN."""
    raw = _first_str(entity_payload, "criticality", "tier")
    if raw is None and isinstance(entity_payload, dict):
        tags = entity_payload.get("tags") or entity_payload.get("globalTags")
        for tag in _as_list(tags):
            name = tag if isinstance(tag, str) else _first_str(tag, "name", "urn", "tag") or ""
            upper = name.upper().replace("-", "_")
            for tier in ("TIER_1", "TIER_2", "TIER_3"):
                if tier in upper:
                    return Criticality(tier)
        return Criticality.UNKNOWN
    if raw is None:
        return Criticality.UNKNOWN
    upper = raw.upper().replace("-", "_")
    for tier in ("TIER_1", "TIER_2", "TIER_3"):
        if tier in upper:
            return Criticality(tier)
    return Criticality.UNKNOWN


def extract_owners(entity_payload: Any) -> list[str]:
    if not isinstance(entity_payload, dict):
        return []
    owners: list[str] = []
    for owner in _as_list(entity_payload.get("owners") or entity_payload.get("ownership")):
        if isinstance(owner, str):
            owners.append(owner)
        else:
            name = _first_str(owner, "owner", "urn", "name")
            if name:
                owners.append(name)
    return owners


def extract_fields(payload: Any) -> list[SchemaField]:
    fields: list[SchemaField] = []
    for raw in _as_list(payload):
        if isinstance(raw, str):
            fields.append(SchemaField(path=raw))
            continue
        path = _first_str(raw, "fieldPath", "path", "name")
        if not path:
            continue
        type_name = _first_str(raw, "type", "nativeDataType", "dataType") or "unknown"
        fields.append(SchemaField(path=path, type=type_name))
    return fields


class DataHubContextProvider:
    """Reads the allocated ``traffic.`` subgraph from DataHub through MCP."""

    source = "datahub-mcp"

    def __init__(
        self,
        client: McpClient,
        namespace: Namespace,
        allocated_urns: list[str],
        clock: Clock | None = None,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._allocated = namespace.require_all(
            allocated_urns, operation="DataHub context read"
        )
        self._clock = clock or SystemClock()

    def snapshot(self) -> GraphSnapshot:
        entities: dict[str, EntityContext] = {}
        edges: list[LineageEdge] = []

        for urn in self._allocated:
            entities[urn] = self._read_entity(urn)
            edges.extend(self._read_downstream_edges(urn))

        # De-duplicate while preserving determinism.
        unique = {(e.upstream, e.downstream) for e in edges}
        ordered = [LineageEdge(upstream=u, downstream=d) for u, d in sorted(unique)]

        return GraphSnapshot(
            entities=entities, edges=ordered, captured_at=self._clock.now()
        )

    def _read_entity(self, urn: str) -> EntityContext:
        payload = self._client.call_tool(TOOL_GET_ENTITIES, {"urns": [urn]})
        entity = self._select_entity(payload, urn)

        try:
            field_payload = self._client.call_tool(TOOL_LIST_SCHEMA_FIELDS, {"urn": urn})
            fields = extract_fields(field_payload)
        except McpError:
            # A dashboard has no schema fields; absence must not fail the whole snapshot.
            fields = []

        return EntityContext(
            urn=urn,
            name=_first_str(entity, "name", "qualifiedName") or urn,
            description=extract_description(entity),
            criticality=extract_criticality(entity),
            owners=extract_owners(entity),
            fields=fields,
        )

    @staticmethod
    def _select_entity(payload: Any, urn: str) -> dict[str, Any]:
        for candidate in _as_list(payload):
            if isinstance(candidate, dict) and candidate.get("urn") == urn:
                return candidate
        for candidate in _as_list(payload):
            if isinstance(candidate, dict):
                return candidate
        return {}

    def _read_downstream_edges(self, urn: str) -> list[LineageEdge]:
        """Read one hop downstream. Edges leaving the allocation are dropped, not followed."""
        try:
            payload = self._client.call_tool(
                TOOL_GET_LINEAGE, {"urn": urn, "direction": "DOWNSTREAM", "hops": 1}
            )
        except McpError:
            return []

        edges: list[LineageEdge] = []
        for raw in _as_list(payload):
            downstream = (
                raw if isinstance(raw, str) else _first_str(raw, "urn", "entity", "downstream")
            )
            if not downstream or downstream == urn:
                continue
            # Never admit another project's entity into this project's graph.
            if not self._namespace.contains(downstream):
                continue
            edges.append(LineageEdge(upstream=urn, downstream=downstream))
        return edges
