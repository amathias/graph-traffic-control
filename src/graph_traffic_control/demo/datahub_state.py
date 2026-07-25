"""Deterministic, namespace-guarded DataHub state management for the ``traffic.`` allocation.

Five hackathon submissions share one open-source DataHub instance. This module owns everything
Graph Traffic Control puts into it and takes back out of it, and it is built so that it
*structurally cannot* touch another project's graph.

Three operations, all planned before anything is applied:

``seed``
    Materialise the complete ``traffic.`` graph: every dataset and dashboard, with schemas,
    ownership, the coordinator-allocated domain, the project tag, a project marker, and lineage.

``reset``
    Remove **only** this project's entities. Namespace-scoped soft deletes. A global refresh or a
    ``datahub docker nuke`` is not expressible here: :func:`reset_plan` refuses any scope other
    than ``namespace``, and every operation is guarded individually anyway.

``restore``
    Put back what was captured before seeding, so a shared instance is left as found.

Why a plan, not direct calls
----------------------------
Every operation is produced as an inert, inspectable :class:`AspectOperation` list first, and
guarded as a whole, before anything is applied. That means:

- the guard runs over the *complete* operation set, so a plan containing one foreign URN is
  rejected in full rather than applied up to the bad entry;
- the plan is deterministic and fingerprintable, so the coordinator can diff what a run *would*
  do against what a previous run did;
- the plan can be written, reviewed, and applied on the host by whoever holds credentials,
  without this project ever needing them.

Determinism
-----------
Plans contain no timestamps and no random identifiers, and every collection is sorted. Building
the same plan from the same fixture twice yields byte-identical JSON and the same fingerprint.

Applying
--------
:func:`apply_plan` refuses to do anything without live credentials, and refuses again per
operation via the same guard. The MCP tool set models description writes; the coordinator's
integration ruling 3 allows the supported DataHub SDK/GraphQL path for aspects MCP does not
model, which is why the plan is emitted in aspect form. **No plan in this repository has been
applied to a live DataHub instance from this project chat** — see ``docs/LIMITATIONS.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from graph_traffic_control.config import Settings
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation

#: Directory under ``APP_STATE_DIR`` holding plans and capture files.
DATAHUB_STATE_DIRNAME = "datahub"

SEED_PLAN_FILENAME = "seed_plan.json"
RESET_PLAN_FILENAME = "reset_plan.json"
RESTORE_PLAN_FILENAME = "restore_plan.json"
CAPTURE_FILENAME = "pre_seed_capture.json"
RECIPE_FILENAME = "ingestion_recipe.yaml"

#: Written into every seeded entity's custom properties. Makes this project's rows identifiable
#: in a shared catalogue, and lets a reset prove an entity is one of ours before removing it.
MARKER_KEY = "graph_traffic_control"
MARKER_VALUE = "graph-traffic-control-demo-seed-v1"

#: The only scope a reset may run at. Present so that "global" is a value someone has to
#: deliberately pass and be refused for, rather than an omission that silently widens the blast
#: radius.
NAMESPACE_SCOPE = "namespace"

DATASET_ASPECTS = (
    "datasetProperties",
    "schemaMetadata",
    "ownership",
    "domains",
    "globalTags",
    "upstreamLineage",
)


class PlanError(RuntimeError):
    """A plan could not be built or applied."""


@dataclass(frozen=True)
class AspectOperation:
    """One aspect write or removal against one entity."""

    entity_urn: str
    entity_type: str
    aspect: str
    change_type: str  # UPSERT | DELETE
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entityUrn": self.entity_urn,
            "entityType": self.entity_type,
            "aspectName": self.aspect,
            "changeType": self.change_type,
            "aspect": self.payload,
        }


@dataclass(frozen=True)
class DataHubPlan:
    """An inert, guarded, deterministic set of operations."""

    kind: str
    urn_prefix: str
    domain_urn: str
    tag_urn: str
    operations: tuple[AspectOperation, ...]

    @property
    def entity_urns(self) -> list[str]:
        return sorted({op.entity_urn for op in self.operations})

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "urn_prefix": self.urn_prefix,
            "domain_urn": self.domain_urn,
            "tag_urn": self.tag_urn,
            "entity_count": len(self.entity_urns),
            "operation_count": len(self.operations),
            "operations": [op.as_dict() for op in self.operations],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def fingerprint(self) -> str:
        """Stable digest of the plan's effect. Identical inputs give an identical value."""
        return sha256(self.to_json().encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------------------
# The guard. Runs over a whole plan, before anything is applied.
# --------------------------------------------------------------------------------------


def guard_plan(plan: DataHubPlan, namespace: Namespace, settings: Settings) -> DataHubPlan:
    """Prove every operation targets this project's allocation. Raises on the first outsider.

    Guarding the whole plan up front is the point: a plan with one foreign URN is refused
    entirely, so a partially applied cross-project write is not reachable.
    """
    if plan.urn_prefix != namespace.urn_prefix:
        raise NamespaceViolation(
            f"Plan declares prefix {plan.urn_prefix!r}, but this project is allocated "
            f"{namespace.urn_prefix!r}."
        )
    if plan.domain_urn != settings.datahub_domain_urn:
        raise NamespaceViolation(
            f"Plan targets domain {plan.domain_urn!r}, not this project's allocated domain "
            f"{settings.datahub_domain_urn!r}."
        )
    namespace.require_tag(plan.tag_urn, operation=f"DataHub {plan.kind}")

    for op in plan.operations:
        namespace.require(op.entity_urn, operation=f"DataHub {plan.kind}")
        _guard_payload_references(op, namespace, settings)
    return plan


def _guard_payload_references(
    op: AspectOperation, namespace: Namespace, settings: Settings
) -> None:
    """Guard URNs *inside* an aspect, not just the entity it is attached to.

    An unguarded payload is how a correctly-addressed write still reaches another project: a
    lineage aspect on one of our datasets can name someone else's dataset as its upstream.
    """
    for upstream in op.payload.get("upstreams", []):
        namespace.require(upstream["dataset"], operation=f"DataHub {plan_kind(op)} lineage")
    for dataset in op.payload.get("datasets", []):
        namespace.require(dataset, operation=f"DataHub {plan_kind(op)} dashboard input")
    for tag in op.payload.get("tags", []):
        urn = tag["tag"]
        # Tier tags are shared vocabulary; only the *project* tag is allocation-checked.
        if urn.startswith("urn:li:tag:") and namespace.entity_name(urn) == namespace.project_tag:
            namespace.require_tag(urn, operation="DataHub tag")
    for domain in op.payload.get("domains", []):
        if domain != settings.datahub_domain_urn:
            raise NamespaceViolation(
                f"Aspect on {op.entity_urn!r} references domain {domain!r}, not this project's "
                f"allocated domain {settings.datahub_domain_urn!r}."
            )


def plan_kind(op: AspectOperation) -> str:
    return "seed" if op.change_type == "UPSERT" else "reset"


# --------------------------------------------------------------------------------------
# Seed
# --------------------------------------------------------------------------------------


def _dataset_operations(
    entity: dict[str, Any],
    edges: list[dict[str, str]],
    settings: Settings,
    tag_urn: str,
    platform: str,
) -> list[AspectOperation]:
    urn = entity["urn"]
    ops: list[AspectOperation] = []

    ops.append(
        AspectOperation(
            urn,
            "dataset",
            "datasetProperties",
            "UPSERT",
            {
                "name": entity["name"],
                "qualifiedName": entity["name"],
                "description": entity.get("description", ""),
                # The marker. Identifies this row as ours in a shared catalogue, and lets a
                # reset prove an entity belongs to this project before removing it.
                "customProperties": {
                    MARKER_KEY: MARKER_VALUE,
                    "criticality": entity.get("criticality", "UNKNOWN"),
                },
            },
        )
    )

    fields = entity.get("fields", [])
    if fields:
        ops.append(
            AspectOperation(
                urn,
                "dataset",
                "schemaMetadata",
                "UPSERT",
                {
                    "schemaName": entity["name"],
                    "platform": f"urn:li:dataPlatform:{platform}",
                    "version": 0,
                    "hash": "",
                    "fields": [
                        {
                            "fieldPath": field["path"],
                            "nativeDataType": field.get("type", "unknown"),
                            "type": {"type": field.get("type", "unknown")},
                            "nullable": True,
                        }
                        for field in sorted(fields, key=lambda f: f["path"])
                    ],
                },
            )
        )

    ops.append(_ownership_operation(urn, "dataset", entity))
    ops.append(_domain_operation(urn, "dataset", settings))
    ops.append(_tags_operation(urn, "dataset", entity, tag_urn))

    upstreams = sorted(e["upstream"] for e in edges if e["downstream"] == urn)
    if upstreams:
        ops.append(
            AspectOperation(
                urn,
                "dataset",
                "upstreamLineage",
                "UPSERT",
                {
                    "upstreams": [
                        {"dataset": upstream, "type": "TRANSFORMED"} for upstream in upstreams
                    ]
                },
            )
        )
    return ops


def _dashboard_operations(
    entity: dict[str, Any], edges: list[dict[str, str]], settings: Settings, tag_urn: str
) -> list[AspectOperation]:
    urn = entity["urn"]
    inputs = sorted(e["upstream"] for e in edges if e["downstream"] == urn)
    return [
        AspectOperation(
            urn,
            "dashboard",
            "dashboardInfo",
            "UPSERT",
            {
                "title": entity["name"],
                "description": entity.get("description", ""),
                # A dashboard's lineage is its inputs, not an upstreamLineage aspect.
                "datasets": inputs,
                "customProperties": {
                    MARKER_KEY: MARKER_VALUE,
                    "criticality": entity.get("criticality", "UNKNOWN"),
                },
            },
        ),
        _ownership_operation(urn, "dashboard", entity),
        _domain_operation(urn, "dashboard", settings),
        _tags_operation(urn, "dashboard", entity, tag_urn),
    ]


def _ownership_operation(urn: str, entity_type: str, entity: dict[str, Any]) -> AspectOperation:
    return AspectOperation(
        urn,
        entity_type,
        "ownership",
        "UPSERT",
        {
            "owners": [
                {"owner": _owner_urn(owner), "type": "DATAOWNER"}
                for owner in sorted(entity.get("owners", []))
            ]
        },
    )


def _domain_operation(urn: str, entity_type: str, settings: Settings) -> AspectOperation:
    return AspectOperation(
        urn, entity_type, "domains", "UPSERT", {"domains": [settings.datahub_domain_urn]}
    )


def _tags_operation(
    urn: str, entity_type: str, entity: dict[str, Any], tag_urn: str
) -> AspectOperation:
    tags = set(entity.get("tags", [])) | {tag_urn}
    return AspectOperation(
        urn,
        entity_type,
        "globalTags",
        "UPSERT",
        {"tags": [{"tag": tag} for tag in sorted(tags)]},
    )


def _owner_urn(owner: str) -> str:
    return owner if owner.startswith("urn:li:") else f"urn:li:corpGroup:{owner}"


def seed_plan(
    graph: dict[str, Any], namespace: Namespace, settings: Settings
) -> DataHubPlan:
    """Build the complete, guarded seed plan for the ``traffic.`` graph."""
    tag_urn = f"urn:li:tag:{namespace.project_tag}"
    edges = list(graph.get("edges", []))
    platform = graph.get("namespace", {}).get("platform", "duckdb")

    operations: list[AspectOperation] = []
    for entity in sorted(graph.get("datasets", []), key=lambda e: e["urn"]):
        operations.extend(_dataset_operations(entity, edges, settings, tag_urn, platform))
    for entity in sorted(graph.get("dashboards", []), key=lambda e: e["urn"]):
        operations.extend(_dashboard_operations(entity, edges, settings, tag_urn))

    if not operations:
        raise PlanError(
            "Seed plan is empty. Refusing to emit a plan that would ingest nothing while "
            "reporting success."
        )

    plan = DataHubPlan(
        kind="seed",
        urn_prefix=namespace.urn_prefix,
        domain_urn=settings.datahub_domain_urn,
        tag_urn=tag_urn,
        operations=tuple(operations),
    )
    return guard_plan(plan, namespace, settings)


# --------------------------------------------------------------------------------------
# Reset
# --------------------------------------------------------------------------------------


def reset_plan(
    allocated_urns: list[str],
    namespace: Namespace,
    settings: Settings,
    scope: str = NAMESPACE_SCOPE,
) -> DataHubPlan:
    """Build a namespace-scoped removal plan.

    ``scope`` exists so a global refresh has to be asked for explicitly and can be refused
    explicitly. ``AGENTS.md`` and coordinator ruling 4 forbid global full-refresh and
    ``datahub docker nuke`` on the shared instance.
    """
    if scope != NAMESPACE_SCOPE:
        raise NamespaceViolation(
            f"Reset scope {scope!r} refused. Only {NAMESPACE_SCOPE!r} is permitted: a global "
            "refresh on the shared instance would delete four other submissions' entities."
        )
    if not allocated_urns:
        raise PlanError(
            "Reset plan is empty. Refusing to emit a plan that would report a successful reset "
            "without removing anything; run `gtc-seed` so the manifest lists this allocation."
        )

    operations = tuple(
        AspectOperation(
            urn,
            _entity_type_of(urn),
            "status",
            "DELETE",
            # Soft delete. Stale-entity removal stays disabled, per coordinator ruling 4.
            {"removed": True, "soft": True, "marker": MARKER_VALUE},
        )
        for urn in sorted(set(allocated_urns))
    )

    plan = DataHubPlan(
        kind="reset",
        urn_prefix=namespace.urn_prefix,
        domain_urn=settings.datahub_domain_urn,
        tag_urn=f"urn:li:tag:{namespace.project_tag}",
        operations=operations,
    )
    return guard_plan(plan, namespace, settings)


def _entity_type_of(urn: str) -> str:
    if urn.startswith("urn:li:dataset:"):
        return "dataset"
    if urn.startswith("urn:li:dashboard:"):
        return "dashboard"
    # The namespace guard has already refused unknown shapes; this is a label, not a decision.
    return urn.split(":")[2] if urn.count(":") >= 2 else "unknown"


# --------------------------------------------------------------------------------------
# Capture and restore
# --------------------------------------------------------------------------------------


def capture_state(client, namespace: Namespace, allocated_urns: list[str]) -> dict[str, Any]:
    """Read the current state of the allocated entities, before a seed changes them.

    Requires a live MCP client. Fails closed: a capture that could not read everything is not a
    capture, and restoring from a partial one would silently drop whatever was missed.
    """
    from graph_traffic_control.context.datahub import (
        TOOL_GET_ENTITIES,
        entities_from_result,
    )

    guarded = namespace.require_all(allocated_urns, operation="DataHub capture")
    if not guarded:
        raise PlanError("Nothing to capture: the allocation is empty.")

    captured: dict[str, Any] = {}
    for urn in sorted(guarded):
        payload = client.call_tool_structured(TOOL_GET_ENTITIES, {"urns": [urn]})
        captured[urn] = entities_from_result(payload, [urn])[urn]

    return {
        "kind": "pre-seed-capture",
        "urn_prefix": namespace.urn_prefix,
        "entity_count": len(captured),
        "entities": captured,
    }


def restore_plan(
    capture: dict[str, Any], namespace: Namespace, settings: Settings
) -> DataHubPlan:
    """Build a plan that puts every captured entity back the way it was found."""
    from graph_traffic_control.context.datahub import (
        extract_description,
        extract_domain,
        extract_owners,
        extract_tags,
    )

    entities = capture.get("entities", {})
    if not entities:
        raise PlanError(
            "Restore plan is empty. A capture with no entities cannot restore anything, and "
            "emitting an empty plan would report a successful restore that did nothing."
        )

    operations: list[AspectOperation] = []
    for urn in sorted(entities):
        entity = entities[urn]
        entity_type = _entity_type_of(urn)
        properties_aspect = (
            "datasetProperties" if entity_type == "dataset" else "dashboardInfo"
        )
        operations.append(
            AspectOperation(
                urn,
                entity_type,
                properties_aspect,
                "UPSERT",
                {"description": extract_description(entity) or ""},
            )
        )
        operations.append(
            AspectOperation(
                urn,
                entity_type,
                "ownership",
                "UPSERT",
                {
                    "owners": [
                        {"owner": owner, "type": "DATAOWNER"}
                        for owner in sorted(extract_owners(entity))
                    ]
                },
            )
        )
        operations.append(
            AspectOperation(
                urn,
                entity_type,
                "globalTags",
                "UPSERT",
                {"tags": [{"tag": tag} for tag in sorted(extract_tags(entity))]},
            )
        )
        captured_domain = extract_domain(entity)
        operations.append(
            AspectOperation(
                urn,
                entity_type,
                "domains",
                "UPSERT",
                {"domains": [captured_domain] if captured_domain else []},
            )
        )

    plan = DataHubPlan(
        kind="restore",
        urn_prefix=namespace.urn_prefix,
        domain_urn=settings.datahub_domain_urn,
        tag_urn=f"urn:li:tag:{namespace.project_tag}",
        operations=tuple(operations),
    )
    return guard_plan(plan, namespace, settings)


# --------------------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------------------


def ingestion_recipe(plan: DataHubPlan, settings: Settings) -> str:
    """A deterministic, namespace-scoped ingestion recipe for the seed plan.

    Stateful ingestion's stale-entity removal is disabled, as coordinator ruling 4 requires: with
    it enabled, ingesting only this project's entities would mark the other four submissions'
    entities stale and remove them.
    """
    return (
        "# Graph Traffic Control - namespace-scoped DataHub ingestion.\n"
        "# Generated deterministically by `gtc-datahub-seed`. Do not hand-edit.\n"
        f"# Plan fingerprint: {plan.fingerprint()}\n"
        "#\n"
        "# stale-entity removal is DISABLED on purpose: this recipe ingests only the\n"
        f"# {plan.urn_prefix} allocation, and stale-entity removal would treat the other four\n"
        "# submissions' entities as deleted.\n"
        "source:\n"
        "  type: file\n"
        "  config:\n"
        f"    path: ./{SEED_PLAN_FILENAME}\n"
        "sink:\n"
        "  type: datahub-rest\n"
        "  config:\n"
        "    server: ${DATAHUB_GMS_URL}\n"
        "    token: ${DATAHUB_TOKEN}\n"
        "stateful_ingestion:\n"
        "  enabled: false\n"
        "  remove_stale_metadata: false\n"
    )


def write_plan(plan: DataHubPlan, settings: Settings, filename: str) -> dict[str, Any]:
    """Write a plan (and, for a seed, its recipe) under ``APP_STATE_DIR/datahub``."""
    directory = settings.state_dir / DATAHUB_STATE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / filename
    path.write_text(plan.to_json(), encoding="utf-8")

    written = [path]
    if plan.kind == "seed":
        recipe = directory / RECIPE_FILENAME
        recipe.write_text(ingestion_recipe(plan, settings), encoding="utf-8")
        written.append(recipe)

    return {
        "paths": written,
        "fingerprint": plan.fingerprint(),
        "entity_count": len(plan.entity_urns),
        "operation_count": len(plan.operations),
    }


def apply_plan(plan: DataHubPlan, namespace: Namespace, settings: Settings) -> dict[str, Any]:
    """Apply a plan to a live DataHub instance.

    Refuses without live credentials rather than pretending. Re-guards the plan immediately
    before applying, so a plan that was mutated after being built cannot slip through.

    The emitter is the supported DataHub SDK path (coordinator ruling 3, for aspects the MCP
    tool set does not model). It is an optional dependency: install ``.[datahub]`` on the host.
    """
    guard_plan(plan, namespace, settings)

    if not settings.datahub_configured:
        raise PlanError(
            "Refusing to apply: DATAHUB_GMS_URL and DATAHUB_TOKEN are not both set. A plan is "
            "never applied on a guess about where it would land."
        )

    try:
        from datahub.emitter.rest_emitter import DatahubRestEmitter
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise PlanError(
            "The DataHub SDK is not installed. Install the optional extra on the host: "
            'pip install -e ".[datahub]"'
        ) from exc

    emitter = DatahubRestEmitter(  # pragma: no cover - requires a live instance
        gms_server=settings.datahub_gms_url, token=settings.datahub_token
    )
    applied = 0
    for op in plan.operations:  # pragma: no cover - requires a live instance
        emitter.emit(op.as_dict())
        applied += 1
    return {  # pragma: no cover - requires a live instance
        "applied": applied,
        "fingerprint": plan.fingerprint(),
        "kind": plan.kind,
    }
