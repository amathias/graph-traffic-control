"""DataHub-backed context provider, read through the MCP server.

Reads only this project's allocated entities. The allocated URN list is supplied by the caller
(from the seed manifest) rather than discovered by an open search, which keeps every read inside
the ``traffic.`` namespace by construction and makes the read set deterministic. Every URN is
still passed through the namespace guard.

Tool contract
-------------
These are the coordinator-observed contracts for the pinned DataHub MCP server. They are
implemented exactly, not guessed at:

===================== ================================== ==========================================
Tool                  Arguments                          Payload location
===================== ================================== ==========================================
``get_entities``      ``urns``                           ``structuredContent.result``
``get_lineage``       ``urn``, ``upstream``,             ``structuredContent.downstreams``
                      ``max_hops``, ``max_results``      ``.searchResults[*].entity.urn``
``list_schema_fields`` ``urn``, ``limit``                ``structuredContent.fields``
``update_description`` ``entity_urn``, ``description``,  (see :mod:`..writeback.datahub`)
                      ``operation``
===================== ================================== ==========================================

Entity governance fields are nested under ``properties``, ``ownership``, ``tags``, and ``domain``.

Fail-closed reading
-------------------
An earlier revision of this module tolerated several plausible payload shapes and degraded to
"field unknown" on a mismatch, and swallowed MCP errors into an empty edge list. That is
**wrong**, and the coordinator rejected it: an empty graph is indistinguishable from a graph with
no conflicts, so a swallowed failure silently converts "the coordinator cannot see the graph" into
"nothing conflicts, commit away".

Every read here therefore raises :class:`~graph_traffic_control.context.provider.ContextReadError`
on a transport failure, a tool error, or a response whose shape is not the contract above. Absent
*optional governance* values (an entity with no description, no owners, no domain) are legitimate
and yield ``None``/empty — that is a value, not an unknown shape.
"""

from __future__ import annotations

from typing import Any

from graph_traffic_control.context.mcp_client import McpClient, McpContractError, McpError
from graph_traffic_control.context.namespace import Namespace
from graph_traffic_control.context.provider import ContextReadError
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

#: One hop per allocated entity. Every edge inside the allocation is discovered because every
#: allocated entity is queried, so a deeper per-call walk would only re-read known edges.
LINEAGE_MAX_HOPS = 1

#: Bounds on a single tool response. Large enough for the demo graph with headroom; small enough
#: that a runaway response is refused rather than paged in.
LINEAGE_MAX_RESULTS = 200
SCHEMA_FIELD_LIMIT = 500

#: URN prefixes whose entities carry a schema. ``list_schema_fields`` is only called for these.
#: A dashboard has no columns, so asking for them and then tolerating the resulting error would
#: reintroduce exactly the error-swallowing this module must not do.
SCHEMA_BEARING_URN_PREFIXES = ("urn:li:dataset:",)


# --------------------------------------------------------------------------------------
# Envelope readers. These enforce the contract and raise on anything else.
# --------------------------------------------------------------------------------------


def _mapping(value: Any) -> dict[str, Any]:
    """A nested container, or an empty mapping when the field is legitimately absent."""
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    """A string value from either a bare string or a ``{"string": ...}``-style wrapper."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("string", "value", "urn"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def entities_from_result(payload: dict[str, Any], requested: list[str]) -> dict[str, Any]:
    """Read ``get_entities`` output from ``structuredContent.result``.

    Accepts the two forms the contract can legitimately take for a multi-URN request: a list of
    entity objects, or a mapping of URN to entity object. Anything else raises.
    """
    if "result" not in payload:
        raise McpContractError(
            f"{TOOL_GET_ENTITIES} response has no 'result' key under structuredContent "
            f"(keys: {sorted(payload)})."
        )
    result = payload["result"]

    by_urn: dict[str, Any] = {}
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                raise McpContractError(
                    f"{TOOL_GET_ENTITIES} result contains a {type(item).__name__}, not an entity."
                )
            urn = item.get("urn")
            if not isinstance(urn, str) or not urn:
                raise McpContractError(
                    f"{TOOL_GET_ENTITIES} result contains an entity with no 'urn'."
                )
            by_urn[urn] = item
    elif isinstance(result, dict):
        if isinstance(result.get("urn"), str):
            by_urn[result["urn"]] = result
        else:
            for urn, item in result.items():
                if not isinstance(item, dict):
                    raise McpContractError(
                        f"{TOOL_GET_ENTITIES} result[{urn!r}] is a {type(item).__name__}, "
                        "not an entity."
                    )
                by_urn[urn] = item
    else:
        raise McpContractError(
            f"{TOOL_GET_ENTITIES} result is a {type(result).__name__}; expected a list of "
            "entities or a mapping of URN to entity."
        )

    missing = [urn for urn in requested if urn not in by_urn]
    if missing:
        raise McpContractError(
            f"{TOOL_GET_ENTITIES} did not return {', '.join(missing)}. An allocated entity that "
            "is absent from DataHub is a seeding failure, not an empty graph."
        )
    return by_urn


def present_urns_from_result(payload: dict[str, Any]) -> set[str]:
    """URNs a ``get_entities`` response actually returned.

    Same envelope contract as :func:`entities_from_result`, but absence is reported rather than
    raised: readiness needs to say *which* allocated entities are missing, and a missing entity
    is a legitimate (unready) answer rather than a protocol violation.
    """
    return set(entities_from_result(payload, []))


def downstream_urns_from_lineage(payload: dict[str, Any]) -> list[str]:
    """Read ``get_lineage`` output from ``structuredContent.downstreams.searchResults``."""
    if "downstreams" not in payload:
        raise McpContractError(
            f"{TOOL_GET_LINEAGE} response has no 'downstreams' key under structuredContent "
            f"(keys: {sorted(payload)})."
        )
    downstreams = payload["downstreams"]
    if not isinstance(downstreams, dict):
        raise McpContractError(
            f"{TOOL_GET_LINEAGE} 'downstreams' is a {type(downstreams).__name__}; "
            "expected an object with 'searchResults'."
        )
    results = downstreams.get("searchResults")
    if not isinstance(results, list):
        raise McpContractError(
            f"{TOOL_GET_LINEAGE} downstreams.searchResults is a {type(results).__name__}; "
            "expected a list."
        )

    urns: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            raise McpContractError(
                f"{TOOL_GET_LINEAGE} searchResults contains a {type(item).__name__}."
            )
        entity = item.get("entity")
        if not isinstance(entity, dict):
            raise McpContractError(
                f"{TOOL_GET_LINEAGE} searchResults entry has no 'entity' object."
            )
        urn = entity.get("urn")
        if not isinstance(urn, str) or not urn:
            raise McpContractError(
                f"{TOOL_GET_LINEAGE} searchResults entity has no 'urn' string."
            )
        urns.append(urn)
    return urns


def fields_from_payload(payload: dict[str, Any]) -> list[SchemaField]:
    """Read ``list_schema_fields`` output from ``structuredContent.fields``."""
    if "fields" not in payload:
        raise McpContractError(
            f"{TOOL_LIST_SCHEMA_FIELDS} response has no 'fields' key under structuredContent "
            f"(keys: {sorted(payload)})."
        )
    raw_fields = payload["fields"]
    if not isinstance(raw_fields, list):
        raise McpContractError(
            f"{TOOL_LIST_SCHEMA_FIELDS} 'fields' is a {type(raw_fields).__name__}; "
            "expected a list."
        )

    fields: list[SchemaField] = []
    for raw in raw_fields:
        if not isinstance(raw, dict):
            raise McpContractError(
                f"{TOOL_LIST_SCHEMA_FIELDS} fields contains a {type(raw).__name__}."
            )
        path = _text(raw.get("fieldPath")) or _text(raw.get("path"))
        if not path:
            raise McpContractError(
                f"{TOOL_LIST_SCHEMA_FIELDS} returned a field with no fieldPath: {sorted(raw)}."
            )
        type_name = (
            _text(raw.get("nativeDataType")) or _text(raw.get("type")) or "unknown"
        )
        fields.append(SchemaField(path=path, type=type_name))
    return fields


# --------------------------------------------------------------------------------------
# Governance extraction. Nested under properties / ownership / tags / domain.
# --------------------------------------------------------------------------------------


def extract_name(entity: dict[str, Any], urn: str) -> str:
    properties = _mapping(entity.get("properties"))
    return (
        _text(properties.get("name"))
        or _text(properties.get("qualifiedName"))
        or _text(entity.get("name"))
        or urn
    )


def extract_description(entity: dict[str, Any]) -> str | None:
    """Description from ``properties``, preferring an editable override when present."""
    editable = _mapping(entity.get("editableProperties"))
    edited = _text(editable.get("description"))
    if edited:
        return edited
    return _text(_mapping(entity.get("properties")).get("description"))


def extract_owners(entity: dict[str, Any]) -> list[str]:
    """Owner URNs from ``ownership.owners[*].owner``."""
    owners_container = _mapping(entity.get("ownership")).get("owners")
    if not isinstance(owners_container, list):
        return []
    owners: list[str] = []
    for raw in owners_container:
        owner = _text(raw) if not isinstance(raw, dict) else _text(raw.get("owner")) or _text(raw)
        if owner:
            owners.append(owner)
    return owners


def extract_tags(entity: dict[str, Any]) -> list[str]:
    """Tag URNs from ``tags.tags[*].tag``."""
    tags_container = _mapping(entity.get("tags")).get("tags")
    if not isinstance(tags_container, list):
        return []
    tags: list[str] = []
    for raw in tags_container:
        tag = _text(raw) if not isinstance(raw, dict) else _text(raw.get("tag")) or _text(raw)
        if tag:
            tags.append(tag)
    return sorted(set(tags))


def extract_domain(entity: dict[str, Any]) -> str | None:
    """Domain URN from ``domain.domain``, or the first of ``domain.domains``."""
    container = _mapping(entity.get("domain"))
    single = _text(container.get("domain"))
    if single:
        return single
    domains = container.get("domains")
    if isinstance(domains, list):
        for raw in domains:
            found = _text(raw)
            if found:
                return found
    return None


def extract_criticality(entity: dict[str, Any]) -> Criticality:
    """Map a tier tag or an explicit criticality property onto the project's enum."""
    candidates = [
        *extract_tags(entity),
        _text(_mapping(entity.get("properties")).get("criticality")) or "",
        _text(_mapping(_mapping(entity.get("properties")).get("customProperties")).get("tier"))
        or "",
    ]
    for candidate in candidates:
        upper = candidate.upper().replace("-", "_")
        for tier in ("TIER_1", "TIER_2", "TIER_3"):
            if tier in upper:
                return Criticality(tier)
    return Criticality.UNKNOWN


# --------------------------------------------------------------------------------------


class DataHubContextProvider:
    """Reads the allocated ``traffic.`` subgraph from DataHub through MCP.

    Any failure to read any allocated entity aborts the whole snapshot. Partial graphs are not
    produced, because a missing entity or a missing edge changes conflict decisions.
    """

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
        if not self._allocated:
            raise ContextReadError(
                "No allocated entities to read. Run `gtc-seed` so the manifest lists this "
                "project's traffic. entities; an empty allocation would produce an empty graph "
                "that falsely reports no conflicts."
            )

        entities: dict[str, EntityContext] = {}
        edges: set[tuple[str, str]] = set()

        for urn in self._allocated:
            entities[urn] = self._read_entity(urn)
            for downstream in self._read_downstream_urns(urn):
                edges.add((urn, downstream))

        ordered = [LineageEdge(upstream=u, downstream=d) for u, d in sorted(edges)]
        return GraphSnapshot(
            entities=entities, edges=ordered, captured_at=self._clock.now()
        )

    # -- reads -------------------------------------------------------------------------

    def _read_entity(self, urn: str) -> EntityContext:
        try:
            payload = self._client.call_tool_structured(TOOL_GET_ENTITIES, {"urns": [urn]})
            entity = entities_from_result(payload, [urn])[urn]
        except McpError as exc:
            raise ContextReadError(f"Could not read {urn} from DataHub: {exc}") from None

        return EntityContext(
            urn=urn,
            name=extract_name(entity, urn),
            description=extract_description(entity),
            criticality=extract_criticality(entity),
            owners=extract_owners(entity),
            tags=extract_tags(entity),
            domain=extract_domain(entity),
            fields=self._read_fields(urn),
        )

    def _read_fields(self, urn: str) -> list[SchemaField]:
        if not urn.startswith(SCHEMA_BEARING_URN_PREFIXES):
            # Not a schema-bearing entity type. Not asking is correct; tolerating an error
            # would be the failure-swallowing this module exists to avoid.
            return []
        try:
            payload = self._client.call_tool_structured(
                TOOL_LIST_SCHEMA_FIELDS, {"urn": urn, "limit": SCHEMA_FIELD_LIMIT}
            )
            return fields_from_payload(payload)
        except McpError as exc:
            raise ContextReadError(
                f"Could not read schema fields for {urn}: {exc}"
            ) from None

    def _read_downstream_urns(self, urn: str) -> list[str]:
        """One hop downstream. Edges leaving the allocation are dropped, not followed."""
        try:
            payload = self._client.call_tool_structured(
                TOOL_GET_LINEAGE,
                {
                    "urn": urn,
                    "upstream": False,
                    "max_hops": LINEAGE_MAX_HOPS,
                    "max_results": LINEAGE_MAX_RESULTS,
                },
            )
            downstreams = downstream_urns_from_lineage(payload)
        except McpError as exc:
            raise ContextReadError(
                f"Could not read downstream lineage for {urn}: {exc}. Refusing to continue with "
                "an incomplete graph."
            ) from None

        # Dropping a foreign downstream is a namespace decision about a value the server did
        # return, not a swallowed failure.
        return [
            downstream
            for downstream in downstreams
            if downstream != urn and self._namespace.contains(downstream)
        ]
