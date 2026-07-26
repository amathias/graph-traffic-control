"""A strict double of the pinned DataHub SDK's emitter boundary.

The suite must be able to exercise the SDK boundary without the optional extra installed, but a
double written from *documentation* is exactly what produced the defect this module exists to
catch. So every behaviour reproduced here was read out of ``acryl-datahub==1.6.0.15`` itself:

``emit`` dispatches on type
    ``rest_emitter.py`` lines 778-799: anything that is not a ``MetadataChangeProposal`` or a
    ``MetadataChangeProposalWrapper`` falls through to ``emit_mce``, whose first statement is
    ``mce.proposedSnapshot.to_obj()`` (line 811). A ``dict`` therefore raises
    ``AttributeError: 'dict' object has no attribute 'proposedSnapshot'`` and never reaches the
    network. :class:`FakeEmitter` reproduces that dispatch exactly.

``from_obj`` enforces required fields and silently drops unknown keys
    Verified against the real classes: a missing defaultless field raises, while an unrecognised
    key is discarded without complaint. Both halves matter — the first is what broke the seed,
    the second is why :func:`_require_known_payload_keys` has to exist.

``ASPECT_MAP`` field and required-field sets
    Read off ``RECORD_SCHEMA.fields`` of the real aspect classes, not transcribed from docs.

Anything the product relies on that this double gets wrong is caught by
``test_datahub_sdk_pinned.py``, which runs the same assertions against the real SDK whenever the
optional extra is installed.
"""

from __future__ import annotations

from typing import Any

#: Declared and required fields per aspect, read from ``RECORD_SCHEMA.fields`` of
#: ``acryl-datahub==1.6.0.15``. "Required" means defaultless and not a nullable union.
ASPECT_SCHEMA: dict[str, dict[str, list[str]]] = {
    "datasetProperties": {
        "fields": [
            "customProperties", "externalUrl", "name", "qualifiedName", "description",
            "uri", "created", "lastModified", "tags",
        ],
        "required": [],
    },
    "schemaMetadata": {
        "fields": [
            "schemaName", "platform", "version", "created", "lastModified", "deleted",
            "dataset", "cluster", "hash", "platformSchema", "fields", "primaryKeys",
            "foreignKeysSpecs", "foreignKeys",
        ],
        "required": ["schemaName", "platform", "version", "hash", "platformSchema", "fields"],
    },
    "ownership": {"fields": ["owners", "ownerTypes", "lastModified"], "required": ["owners"]},
    "domains": {"fields": ["domains", "domainAssociations"], "required": ["domains"]},
    "globalTags": {"fields": ["tags"], "required": ["tags"]},
    "upstreamLineage": {
        "fields": ["upstreams", "fineGrainedLineages"], "required": ["upstreams"]
    },
    "dashboardInfo": {
        "fields": [
            "customProperties", "externalUrl", "title", "description", "charts", "chartEdges",
            "datasets", "datasetEdges", "dashboards", "lastModified", "dashboardUrl", "access",
            "lastRefreshed",
        ],
        "required": ["title", "description", "lastModified"],
    },
    "status": {
        "fields": ["removed", "lifecycleStage", "lifecycleLastUpdated"], "required": []
    },
}


class _Field:
    def __init__(self, name: str) -> None:
        self.name = name


class _RecordSchema:
    def __init__(self, names: list[str]) -> None:
        self.fields = [_Field(name) for name in names]


class FakeAspect:
    """A constructed aspect. Stands in for a generated ``_Aspect`` subclass."""

    def __init__(self, aspect_name: str, values: dict[str, Any]) -> None:
        self.aspect_name = aspect_name
        self.values = values

    def get_aspect_name(self) -> str:
        return self.aspect_name


def _aspect_class(name: str) -> type:
    schema = ASPECT_SCHEMA[name]

    class _FakeAspectClass:
        __name__ = f"{name}Class"
        RECORD_SCHEMA = _RecordSchema(schema["fields"])

        @classmethod
        def from_obj(cls, obj: dict[str, Any]) -> FakeAspect:
            if missing := sorted(set(schema["required"]) - set(obj)):
                # Mirrors avro's ValueError from the real from_obj.
                raise ValueError(f"{name} is missing required field: {missing[0]}")
            # Unknown keys are dropped, exactly as the real implementation drops them.
            return FakeAspect(name, {k: v for k, v in obj.items() if k in set(schema["fields"])})

    _FakeAspectClass.__name__ = f"{name}Class"
    return _FakeAspectClass


ASPECT_MAP: dict[str, type] = {name: _aspect_class(name) for name in ASPECT_SCHEMA}


def post_json_transform(obj: dict[str, Any]) -> dict[str, Any]:
    """The real one rewrites union representations; identity is faithful enough here."""
    return obj


class FakeMcpw:
    """Stands in for ``MetadataChangeProposalWrapper``."""

    def __init__(
        self,
        entityUrn: str,  # noqa: N803 - mirrors the SDK's parameter names exactly
        entityType: str,  # noqa: N803
        changeType: str,  # noqa: N803
        aspect: Any,
    ) -> None:
        if not isinstance(aspect, FakeAspect):
            raise TypeError(f"aspect must be a typed aspect, got {type(aspect).__name__}")
        self.entityUrn = entityUrn
        self.entityType = entityType
        self.changeType = changeType
        self.aspect = aspect
        self.aspectName = aspect.get_aspect_name()


def fake_sdk() -> tuple[Any, Any, Any]:
    """The triple ``operation_to_mcp`` and ``plan_to_mcps`` accept."""
    return ASPECT_MAP, FakeMcpw, post_json_transform


class FakeEmitter:
    """Reproduces ``DatahubRestEmitter.emit``'s type dispatch.

    ``fail_on`` makes the *n*-th emit (1-based) raise, so partial-apply accounting can be checked
    without a network.
    """

    def __init__(self, fail_on: int | None = None, error: Exception | None = None) -> None:
        self.emitted: list[Any] = []
        self.fail_on = fail_on
        self.error = error or ConnectionError("connection reset by peer")

    def emit(self, item: Any) -> None:
        if not isinstance(item, FakeMcpw):
            # The real emitter's else-branch: treat it as an MCE and dereference the snapshot.
            snapshot = item.proposedSnapshot  # noqa: F841 - raises for anything untyped
            raise AssertionError("unreachable for a dict")  # pragma: no cover
        self.emitted.append(item)
        if self.fail_on is not None and len(self.emitted) == self.fail_on:
            self.emitted.pop()
            raise self.error
