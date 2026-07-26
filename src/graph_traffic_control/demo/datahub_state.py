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

``capture`` / ``restore``
    Record what was there before seeding, and put it back, so a shared instance is left as found.

First-time seeding and the absent state
---------------------------------------
The first time this project is seeded, the whole ``traffic.`` namespace is absent from the shared
instance. "Leave it as you found it" then means *delete what you created*, not *restore values
that never existed* — but a capture that simply skipped the missing entities would be
indistinguishable from a capture taken against a half-broken instance, and restoring from it would
silently leave this project's rows behind in a shared catalogue.

Absence is therefore a **captured value**, not a gap:

- capture records each allocated URN as either ``present`` (with its full state) or ``absent``,
  and only records absence when the operator asks for it with ``--allow-absent``;
- the union of present and absent must equal the allocation **exactly** — a capture that is
  partial, carries an extra or foreign URN, or lists the same URN as both present and absent is
  refused, because none of those can be turned into a correct restore;
- restore returns present entities to their captured values and initially-absent entities to a
  soft-deleted state, then **re-reads them and proves** they are absent again;
- absence is only ever proved by reading the exact allowlisted URNs. There is no search, no
  wildcard, and no scope in which "everything" is a legal target.

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
model, which is why the plan is emitted in aspect form.

Each operation is converted into a **typed** ``MetadataChangeProposalWrapper`` before it is
emitted, and the whole plan is converted before the first write — see :func:`plan_to_mcps` and
ADR-018. Handing the emitter a raw dict does not work at all: it dispatches on type and treats
anything unrecognised as a ``MetadataChangeEvent``.

**No plan in this repository has been applied to a live DataHub instance from this project chat**
— see ``docs/LIMITATIONS.md``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
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

#: The only scope a reset or restore may run at. Present so that "global" is a value someone has
#: to deliberately pass and be refused for, rather than an omission that silently widens the
#: blast radius.
NAMESPACE_SCOPE = "namespace"

#: Capture file contract. The version is checked on read: a capture written under a different
#: contract may not distinguish "absent" from "not looked at", and restoring from one that cannot
#: make that distinction is exactly the failure this contract exists to prevent.
CAPTURE_KIND = "pre-seed-capture"
CAPTURE_VERSION = 2

#: Per-entity capture states.
STATE_PRESENT = "present"
STATE_ABSENT = "absent"

#: Soft delete. Shared by reset and by the absent branch of restore so that "returned to absent"
#: and "removed by reset" are the same, inspectable operation.
#:
#: A soft delete is an **UPSERT of the ``status`` aspect with ``removed: true``** — the form the
#: pinned SDK itself uses (``stale_entity_removal_handler.py`` and
#: ``GraphClient.soft_delete_entity`` in ``acryl-datahub==1.6.0.15``). It is emphatically *not*
#: ``changeType: DELETE``: that removes the ``status`` aspect, which *un-deletes* a soft-deleted
#: entity instead of deleting it. Nothing in this project ever hard-deletes; coordinator ruling 4
#: forbids it.
SOFT_DELETE_ASPECT = "status"
SOFT_DELETE_CHANGE_TYPE = "UPSERT"
SOFT_DELETE_PAYLOAD = {"removed": True}

#: Namespace of DataHub's schema type records. The ``SchemaFieldDataType`` union is keyed by
#: fully-qualified record name, so a bare type string is not a legal value for it.
SCHEMA_TYPE_NAMESPACE = "com.linkedin.pegasus2avro.schema"

#: Native column types mapped to the union member that represents them. Unknown types are
#: **refused**, not defaulted: this table decides what a column claims to be in a shared
#: catalogue, and ``NullType`` as a fallback would be a silent lie about every column whose type
#: this project has not been taught. Same fail-closed rule as the context readers (ADR-012).
SCHEMA_FIELD_TYPES: dict[str, str] = {
    "bigint": "NumberType",
    "bool": "BooleanType",
    "boolean": "BooleanType",
    "bytes": "BytesType",
    "date": "DateType",
    "datetime": "TimeType",
    "decimal": "NumberType",
    "double": "NumberType",
    "float": "NumberType",
    "int": "NumberType",
    "integer": "NumberType",
    "long": "NumberType",
    "numeric": "NumberType",
    "string": "StringType",
    "text": "StringType",
    "time": "TimeType",
    "timestamp": "TimeType",
    "varchar": "StringType",
}

#: Audit stamps are required fields on ``schemaMetadata`` and ``dashboardInfo``, but a wall-clock
#: value would make every plan differ from the last and destroy the fingerprint that lets the
#: coordinator diff one run against another. The epoch and a fixed actor keep plans deterministic.
AUDIT_TIME = 0
AUDIT_ACTOR = "urn:li:corpuser:datahub"

DATASET_ASPECTS = (
    "datasetProperties",
    "schemaMetadata",
    "ownership",
    "domains",
    "globalTags",
    "upstreamLineage",
)


def _audit_stamp() -> dict[str, Any]:
    return {"time": AUDIT_TIME, "actor": AUDIT_ACTOR}


def _change_audit_stamps() -> dict[str, Any]:
    return {"created": _audit_stamp(), "lastModified": _audit_stamp()}


def _platform_schema() -> dict[str, Any]:
    """The required ``platformSchema`` union member.

    ``OtherSchema`` with an empty ``rawSchema``: the fixture graph carries no native DDL text, and
    the field has no default, so it must be supplied explicitly rather than omitted.
    """
    return {f"{SCHEMA_TYPE_NAMESPACE}.OtherSchema": {"rawSchema": ""}}


def _schema_field_type(native_type: str) -> dict[str, Any]:
    """Map a native column type onto DataHub's ``SchemaFieldDataType`` union. Fails closed."""
    member = SCHEMA_FIELD_TYPES.get(native_type.strip().lower())
    if member is None:
        raise PlanError(
            f"Column type {native_type!r} has no mapping to a DataHub schema type. Add it to "
            f"SCHEMA_FIELD_TYPES; it is refused rather than guessed, because a defaulted type "
            f"would publish a wrong column shape to the shared catalogue. Known types: "
            f"{', '.join(sorted(SCHEMA_FIELD_TYPES))}."
        )
    return {"type": {f"{SCHEMA_TYPE_NAMESPACE}.{member}": {}}}


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


def require_exact_allocation(
    urns: Iterable[str], allocated: Iterable[str], *, operation: str
) -> list[str]:
    """Prove a URN set is *exactly* the allocation — no more, no fewer, no duplicates.

    Set equality rather than containment is the whole point. Containment alone would accept a
    capture covering three of nine entities and call the resulting restore complete; the reverse
    would accept a plan reaching an entity this project never seeded. Both are refusals.
    """
    subject = list(urns)
    duplicates = sorted({urn for urn in subject if subject.count(urn) > 1})
    if duplicates:
        raise PlanError(
            f"{operation} refused: {', '.join(duplicates)} listed more than once. A duplicated "
            "URN makes the intended state ambiguous."
        )

    expected = set(allocated)
    if not expected:
        raise PlanError(
            f"{operation} refused: the allocation is empty. Run `gtc-seed` so the manifest lists "
            "this project's traffic. entities."
        )

    found = set(subject)
    if missing := sorted(expected - found):
        raise PlanError(
            f"{operation} refused: {', '.join(missing)} not covered. A partial set would leave "
            "those entities in whatever state the run left them."
        )
    if extra := sorted(found - expected):
        raise PlanError(
            f"{operation} refused: {', '.join(extra)} is not in this project's allocation. "
            "Operating on an entity this project did not seed is out of scope even inside the "
            "namespace."
        )
    return sorted(found)


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
                    # Required by the aspect and defaultless. Omitting it made every schema
                    # operation unconstructible against the real SDK.
                    "platformSchema": _platform_schema(),
                    "created": _audit_stamp(),
                    "lastModified": _audit_stamp(),
                    "fields": [
                        {
                            "fieldPath": field["path"],
                            "nativeDataType": field.get("type", "unknown"),
                            "type": _schema_field_type(field.get("type", "unknown")),
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
                # Required by the aspect and defaultless, like schemaMetadata's stamps.
                "lastModified": _change_audit_stamps(),
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
    graph: dict[str, Any],
    namespace: Namespace,
    settings: Settings,
    allocated_urns: Iterable[str] | None = None,
) -> DataHubPlan:
    """Build the complete, guarded seed plan for the ``traffic.`` graph.

    When ``allocated_urns`` is supplied, the plan must cover it **exactly**. That turns "seed
    creates the namespace" into a checked claim: a fixture that drifted from the manifest would
    otherwise produce a seed that quietly creates a different set of entities than the one capture
    recorded the absence of, and restore would then have nothing to delete for the difference.
    """
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
    guard_plan(plan, namespace, settings)
    if allocated_urns is not None:
        require_exact_allocation(
            plan.entity_urns, allocated_urns, operation="DataHub seed"
        )
    return plan


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

    operations = tuple(_soft_delete_operation(urn) for urn in sorted(set(allocated_urns)))

    plan = DataHubPlan(
        kind="reset",
        urn_prefix=namespace.urn_prefix,
        domain_urn=settings.datahub_domain_urn,
        tag_urn=f"urn:li:tag:{namespace.project_tag}",
        operations=operations,
    )
    return guard_plan(plan, namespace, settings)


def _soft_delete_operation(urn: str) -> AspectOperation:
    """Soft delete one entity. Stale-entity removal stays disabled, per coordinator ruling 4.

    ``UPSERT`` of ``status`` with ``removed: true`` — see :data:`SOFT_DELETE_PAYLOAD` for why this
    is not a ``DELETE`` change type.
    """
    return AspectOperation(
        urn,
        _entity_type_of(urn),
        SOFT_DELETE_ASPECT,
        SOFT_DELETE_CHANGE_TYPE,
        dict(SOFT_DELETE_PAYLOAD),
    )


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


def capture_state(
    client,
    namespace: Namespace,
    allocated_urns: list[str],
    *,
    allow_absent: bool = False,
) -> dict[str, Any]:
    """Record the current state of every allocated entity, before a seed changes it.

    Requires a live MCP client. Each allocated URN is read **by name** — one exact URN per call,
    never a search — and lands in exactly one of two buckets:

    ``present``
        the entity exists and is not soft-deleted; its full state is stored verbatim.

    ``absent``
        the entity is missing, or present but soft-deleted. Recorded only when ``allow_absent``
        is set, so first-time seeding is a deliberate declaration rather than a silent skip.

    Fails closed everywhere else. Without ``allow_absent`` a missing entity is still an error, so
    an instance that lost half this project's rows cannot be mistaken for a fresh one. A response
    naming a URN that was not asked for is refused outright: it makes presence ambiguous, and a
    restore built on an ambiguous capture could write to an entity nobody asked about.
    """
    from graph_traffic_control.context.datahub import (
        TOOL_GET_ENTITIES,
        entities_from_result,
        is_soft_deleted,
        present_urns_from_result,
    )

    guarded = namespace.require_all(allocated_urns, operation="DataHub capture")
    if not guarded:
        raise PlanError("Nothing to capture: the allocation is empty.")
    urns = require_exact_allocation(guarded, guarded, operation="DataHub capture")

    present: dict[str, Any] = {}
    absent: list[str] = []

    for urn in urns:
        payload = client.call_tool_structured(TOOL_GET_ENTITIES, {"urns": [urn]})
        returned = present_urns_from_result(payload)
        if unrequested := sorted(returned - {urn}):
            raise PlanError(
                f"Capture refused: asking for {urn} returned {', '.join(unrequested)} as well. "
                "An unrequested entity in the response makes presence ambiguous."
            )

        if urn in returned:
            entity = entities_from_result(payload, [urn])[urn]
            if not is_soft_deleted(entity):
                present[urn] = entity
                continue
            reason = "present but soft-deleted"
        else:
            reason = "absent"

        if not allow_absent:
            raise PlanError(
                f"Capture refused: {urn} is {reason} in DataHub. If this is a first-time seed "
                "and the whole allocation is expected to be missing, re-run with "
                "`--allow-absent` to capture the absent state deliberately. Otherwise this is a "
                "damaged catalogue, not a fresh one, and restoring from it would be wrong."
            )
        absent.append(urn)

    return {
        "kind": CAPTURE_KIND,
        "capture_version": CAPTURE_VERSION,
        "urn_prefix": namespace.urn_prefix,
        "allocated": urns,
        "entities": present,
        "absent": sorted(absent),
        "entity_count": len(present),
        "absent_count": len(absent),
    }


def verify_capture(
    capture: dict[str, Any], namespace: Namespace, allocated_urns: Iterable[str]
) -> tuple[dict[str, Any], list[str]]:
    """Prove a capture describes the exact allocation, and split it into present and absent.

    Every refusal here is a restore that would have been wrong:

    - a foreign URN would let a restore write outside this project's allocation;
    - a partial capture would leave the entities it missed behind after a restore;
    - an extra URN would restore something this project never seeded;
    - a URN listed as both present and absent has no single intended end state;
    - an unrecognised ``kind`` or ``capture_version`` may not distinguish "absent" from "not
      looked at", so its absent set cannot be acted on.
    """
    if capture.get("kind") != CAPTURE_KIND:
        raise PlanError(
            f"Not a capture file: kind is {capture.get('kind')!r}, expected {CAPTURE_KIND!r}."
        )
    if capture.get("capture_version") != CAPTURE_VERSION:
        raise PlanError(
            f"Capture version {capture.get('capture_version')!r} is not {CAPTURE_VERSION}. "
            "Re-run `gtc-datahub-capture`: an older capture cannot prove which entities were "
            "absent, only which ones it happened to record."
        )
    if capture.get("urn_prefix") != namespace.urn_prefix:
        raise NamespaceViolation(
            f"Capture declares prefix {capture.get('urn_prefix')!r}, but this project is "
            f"allocated {namespace.urn_prefix!r}."
        )

    entities = capture.get("entities")
    absent = capture.get("absent")
    if not isinstance(entities, dict) or not isinstance(absent, list):
        raise PlanError(
            "Capture is malformed: 'entities' must be an object and 'absent' a list. Re-run "
            "`gtc-datahub-capture`."
        )

    namespace.require_all(entities, operation="DataHub restore")
    namespace.require_all(absent, operation="DataHub restore")

    if overlap := sorted(set(entities) & set(absent)):
        raise PlanError(
            f"Capture is ambiguous: {', '.join(overlap)} recorded as both present and absent. "
            "There is no single state to restore to."
        )

    covered = [*entities, *absent]
    declared = capture.get("allocated")
    if not isinstance(declared, list):
        raise PlanError("Capture is malformed: 'allocated' must be a list of URNs.")
    require_exact_allocation(declared, covered, operation="DataHub restore (capture coverage)")
    require_exact_allocation(covered, allocated_urns, operation="DataHub restore")

    return entities, sorted(absent)


def verify_absent(client, namespace: Namespace, urns: Iterable[str]) -> dict[str, Any]:
    """Re-read the given URNs and prove each is absent or soft-deleted.

    This is what makes "restored to absent" a fact rather than an intention. A restore that
    soft-deleted nine entities and left one live would otherwise report success, and the leftover
    row would sit in a shared catalogue under this project's name.

    Reads by exact URN only. Refuses rather than reports if any entity is still live.
    """
    from graph_traffic_control.context.datahub import (
        TOOL_GET_ENTITIES,
        entities_from_result,
        is_soft_deleted,
        present_urns_from_result,
    )

    checked = namespace.require_all(urns, operation="DataHub absence verification")
    still_live: list[str] = []

    for urn in sorted(set(checked)):
        payload = client.call_tool_structured(TOOL_GET_ENTITIES, {"urns": [urn]})
        if urn not in present_urns_from_result(payload):
            continue
        if not is_soft_deleted(entities_from_result(payload, [urn])[urn]):
            still_live.append(urn)

    if still_live:
        raise PlanError(
            f"Absence not verified: {', '.join(sorted(still_live))} still present and not "
            "soft-deleted after restore. The shared instance has been left with this project's "
            "entities in it."
        )
    return {"verified_absent": sorted(set(checked)), "checked": len(set(checked))}


def restore_plan(
    capture: dict[str, Any],
    namespace: Namespace,
    settings: Settings,
    allocated_urns: Iterable[str],
    scope: str = NAMESPACE_SCOPE,
) -> DataHubPlan:
    """Build a plan that returns every allocated entity to the state the capture recorded.

    ``allocated_urns`` is required, not optional: a restore is only correct relative to a declared
    allowlist, and :func:`verify_capture` proves the capture covers that allowlist exactly before
    a single operation is built.

    Present entities are restored to their captured values. Initially-absent entities are
    soft-deleted, which is the only honest way to return an instance that never had them to the
    state it was in. ``scope`` mirrors reset's: "everything" has to be asked for and refused.
    """
    from graph_traffic_control.context.datahub import (
        extract_description,
        extract_domain,
        extract_name,
        extract_owners,
        extract_tags,
    )

    if scope != NAMESPACE_SCOPE:
        raise NamespaceViolation(
            f"Restore scope {scope!r} refused. Only {NAMESPACE_SCOPE!r} is permitted: a restore "
            "is a write against a shared instance and is only ever addressed to this project's "
            "captured allowlist."
        )

    entities, absent = verify_capture(capture, namespace, allocated_urns)

    operations: list[AspectOperation] = [_soft_delete_operation(urn) for urn in absent]
    for urn in sorted(entities):
        entity = entities[urn]
        entity_type = _entity_type_of(urn)
        description = extract_description(entity) or ""
        if entity_type == "dataset":
            properties_aspect = "datasetProperties"
            properties_payload: dict[str, Any] = {"description": description}
        else:
            # dashboardInfo requires title and lastModified. A description-only payload could not
            # be constructed as a typed aspect at all, so a restore of a captured dashboard would
            # have failed at the SDK boundary.
            properties_aspect = "dashboardInfo"
            properties_payload = {
                "title": extract_name(entity, urn),
                "description": description,
                "lastModified": _change_audit_stamps(),
            }
        operations.append(
            AspectOperation(urn, entity_type, properties_aspect, "UPSERT", properties_payload)
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
    guard_plan(plan, namespace, settings)
    # Belt and braces: the capture was already proved to cover the allocation exactly, so the
    # plan built from it must too. This catches a construction bug, not a bad input.
    require_exact_allocation(plan.entity_urns, allocated_urns, operation="DataHub restore plan")
    return plan


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


def capture_path(settings: Settings) -> Any:
    """Where the pre-seed capture lives. One fixed path, so restore cannot be pointed elsewhere."""
    return settings.state_dir / DATAHUB_STATE_DIRNAME / CAPTURE_FILENAME


def write_capture(capture: dict[str, Any], settings: Settings) -> Any:
    directory = settings.state_dir / DATAHUB_STATE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = capture_path(settings)
    path.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_capture(settings: Settings) -> dict[str, Any]:
    """Read the capture, or say exactly how to produce one. Never invents an empty capture."""
    path = capture_path(settings)
    if not path.is_file():
        raise PlanError(
            f"No capture at {path}. Run `gtc-datahub-capture` before seeding, so there is a "
            "recorded state to restore to. If the traffic. namespace does not exist yet, run "
            "`gtc-datahub-capture --allow-absent` to record that absence deliberately."
        )
    capture = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(capture, dict):
        raise PlanError(f"Capture at {path} is not an object.")
    return capture


class PartialApplyError(PlanError):
    """A plan was applied up to a point and then failed. Carries exactly how far it got.

    A bare failure here would be untruthful in the most expensive way: the shared instance has
    already been written to, and an operator who reads "seed failed" and assumes "nothing
    happened" leaves this project's rows in a catalogue four other submissions share.
    """

    def __init__(
        self, *, applied: int, total: int, operation: AspectOperation, cause: Exception
    ) -> None:
        self.applied = applied
        self.total = total
        self.operation = operation
        self.cause = cause
        super().__init__(
            f"Apply failed after {applied} of {total} operations. The failing operation was "
            f"{operation.change_type} {operation.aspect} on {operation.entity_urn}: "
            f"{type(cause).__name__}: {cause}. "
            f"The first {applied} operation(s) were accepted by DataHub and are still there — "
            f"this is a partial write, not a no-op. Run `gtc-datahub-restore --apply` to return "
            f"the allocation to its captured state before retrying."
        )


def _load_sdk() -> tuple[Any, Any, Any]:
    """Import the pinned DataHub SDK pieces, or say exactly how to install them.

    Isolated behind one function so the SDK boundary has a single seam that tests can double.
    """
    try:
        from datahub.emitter.aspect import ASPECT_MAP
        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.emitter.serialization_helper import post_json_transform
    except ImportError as exc:
        raise PlanError(
            "The DataHub SDK is not installed. Install the optional extra on the host: "
            'pip install -e ".[datahub]"'
        ) from exc
    return ASPECT_MAP, MetadataChangeProposalWrapper, post_json_transform


def _require_known_payload_keys(op: AspectOperation, aspect_cls: Any) -> None:
    """Refuse a payload key the aspect does not declare.

    ``from_obj`` **silently discards** keys it does not recognise, so a misspelled field would
    otherwise be dropped on the floor and the operation would report success having written
    nothing. Every read in this project fails closed on an unrecognised shape (ADR-012); the
    write path has to do the same or the guarantee is one-sided.
    """
    declared = {field.name for field in aspect_cls.RECORD_SCHEMA.fields}
    if unknown := sorted(set(op.payload) - declared):
        raise PlanError(
            f"Aspect {op.aspect!r} on {op.entity_urn!r} carries key(s) "
            f"{', '.join(repr(k) for k in unknown)} that the aspect does not declare. The SDK "
            f"would drop them silently and report success. Declared fields: "
            f"{', '.join(sorted(declared))}."
        )


def operation_to_mcp(op: AspectOperation, sdk: tuple[Any, Any, Any] | None = None) -> Any:
    """Convert one operation into a typed ``MetadataChangeProposalWrapper``.

    This is the boundary that was wrong. The emitter dispatches on **type**: anything that is not
    an MCP or an MCPW is treated as a ``MetadataChangeEvent`` and dereferenced as
    ``item.proposedSnapshot``. A plain ``dict`` therefore never reached the network at all — it
    raised ``AttributeError: 'dict' object has no attribute 'proposedSnapshot'`` inside
    ``emit_mce``. Handing the emitter a typed aspect is the supported path, and it validates the
    payload locally before a single byte is sent.
    """
    aspect_map, wrapper_cls, post_json_transform = sdk or _load_sdk()

    aspect_cls = aspect_map.get(op.aspect)
    if aspect_cls is None:
        raise PlanError(
            f"Aspect {op.aspect!r} on {op.entity_urn!r} is not a DataHub aspect known to the "
            f"pinned SDK. Refusing to emit an aspect the server may not understand."
        )
    _require_known_payload_keys(op, aspect_cls)

    try:
        aspect = aspect_cls.from_obj(post_json_transform(op.payload))
    except Exception as exc:
        raise PlanError(
            f"Aspect {op.aspect!r} on {op.entity_urn!r} could not be built as a typed "
            f"{aspect_cls.__name__}: {type(exc).__name__}: {exc}. The plan payload does not match "
            f"the aspect schema in the pinned SDK."
        ) from exc

    return wrapper_cls(
        entityUrn=op.entity_urn,
        entityType=op.entity_type,
        changeType=op.change_type,
        aspect=aspect,
    )


def plan_to_mcps(plan: DataHubPlan, sdk: tuple[Any, Any, Any] | None = None) -> list[Any]:
    """Convert a **whole** plan before any of it is emitted.

    Same reasoning as guarding the whole plan up front: a payload the SDK cannot build is found
    while the operation count applied is still zero, rather than half way through a run against a
    shared instance.
    """
    sdk = sdk or _load_sdk()
    return [operation_to_mcp(op, sdk) for op in plan.operations]


def _rest_emitter(settings: Settings) -> Any:  # pragma: no cover - requires the optional extra
    from datahub.emitter.rest_emitter import DatahubRestEmitter

    return DatahubRestEmitter(gms_server=settings.datahub_gms_url, token=settings.datahub_token)


def apply_plan(
    plan: DataHubPlan,
    namespace: Namespace,
    settings: Settings,
    *,
    emitter_factory: Any = None,
    sdk: tuple[Any, Any, Any] | None = None,
) -> dict[str, Any]:
    """Apply a plan to a live DataHub instance.

    Refuses without live credentials rather than pretending. Re-guards the plan immediately
    before applying, so a plan that was mutated after being built cannot slip through.

    The emitter is the supported DataHub SDK path (coordinator ruling 3, for aspects the MCP
    tool set does not model). It is an optional dependency: install ``.[datahub]`` on the host.

    Order matters. Guard, then convert everything, then emit: the two failure modes that can be
    detected without touching the network are both resolved while nothing has been written.
    ``emitter_factory`` and ``sdk`` exist so the boundary can be exercised by a double.
    """
    guard_plan(plan, namespace, settings)

    if not settings.datahub_configured:
        raise PlanError(
            "Refusing to apply: DATAHUB_GMS_URL and DATAHUB_TOKEN are not both set. A plan is "
            "never applied on a guess about where it would land."
        )

    # Every conversion happens before the first emit, so a bad payload costs zero writes.
    mcps = plan_to_mcps(plan, sdk)

    emitter = (emitter_factory or _rest_emitter)(settings)
    total = len(mcps)
    applied = 0
    for op, mcp in zip(plan.operations, mcps, strict=True):
        try:
            emitter.emit(mcp)
        except Exception as exc:
            raise PartialApplyError(
                applied=applied, total=total, operation=op, cause=exc
            ) from exc
        applied += 1

    return {
        "applied": applied,
        "fingerprint": plan.fingerprint(),
        "kind": plan.kind,
    }
