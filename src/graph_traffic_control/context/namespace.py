"""Fail-closed namespace isolation for the shared DataHub instance.

Five hackathon projects share one open-source DataHub deployment. Graph Traffic Control is
allocated the ``traffic.`` entity prefix, the ``Demo / Graph Traffic Control`` domain, and the
``project-graph-traffic-control`` tag (``COORDINATOR_HANDOFF.md``).

``../AGENTS.md`` requires that a project reset never delete another project's entities and that
cross-project namespace collisions are treated as blocking defects. Every DataHub read, write,
mutation, and reset target in this project passes through :func:`Namespace.require` first.

The guard fails closed. An unparseable URN, an unknown entity type, or a name outside the
allocated prefix raises :class:`NamespaceViolation` rather than being allowed through. A guard
that guesses is worse than no guard, because it would silently authorise a write into another
submission's graph.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["NamespaceViolation", "Namespace"]


class NamespaceViolation(RuntimeError):
    """Raised when an operation targets something outside this project's allocation."""


_URN_RE = re.compile(r"^urn:li:(?P<entity>[a-zA-Z][a-zA-Z0-9]*):(?P<body>.+)$")

# Position of the human-readable name inside a tuple-form URN body, by entity type.
# Example: urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_revenue,PROD) -> index 1.
_TUPLE_NAME_INDEX = {
    "dataset": 1,
    "dashboard": 1,
    "chart": 1,
    "dataFlow": 1,
    "dataJob": 1,
    "mlModel": 1,
    "mlFeatureTable": 1,
}

# Entity types whose URN body is the bare name.
# Example: urn:li:tag:project-graph-traffic-control -> "project-graph-traffic-control".
_FLAT_NAME_ENTITIES = frozenset(
    {"tag", "domain", "glossaryTerm", "glossaryNode", "container", "corpuser", "corpGroup"}
)

# Entity types that wrap another URN; the guard recurses into the referenced entity.
# Example: urn:li:schemaField:(urn:li:dataset:(...),gross_revenue) -> check the dataset.
_WRAPPER_NAME_INDEX = {"schemaField": 0}


def _split_top_level(body: str) -> list[str]:
    """Split a tuple-form URN body on commas that are not nested inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise NamespaceViolation(f"Unbalanced parentheses in URN body: {body!r}")
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if depth != 0:
        raise NamespaceViolation(f"Unbalanced parentheses in URN body: {body!r}")
    parts.append("".join(current))
    return parts


@dataclass(frozen=True)
class Namespace:
    """This project's DataHub allocation, and the guard that enforces it."""

    urn_prefix: str
    project_tag: str
    domain: str

    @classmethod
    def from_settings(cls, settings) -> Namespace:  # noqa: ANN001 - avoids a circular import
        return cls(
            urn_prefix=settings.datahub_urn_prefix,
            project_tag=settings.datahub_project_tag,
            domain=settings.datahub_domain,
        )

    def entity_name(self, urn: str) -> str:
        """Extract the name an allocation prefix applies to. Raises on anything unrecognised."""
        if not isinstance(urn, str) or not urn.strip():
            raise NamespaceViolation("URN must be a non-empty string")

        match = _URN_RE.match(urn.strip())
        if match is None:
            raise NamespaceViolation(f"Not a DataHub URN: {urn!r}")

        entity = match.group("entity")
        body = match.group("body")

        if entity in _FLAT_NAME_ENTITIES:
            if body.startswith("("):
                raise NamespaceViolation(f"Unexpected tuple body for {entity} URN: {urn!r}")
            return body

        if not body.startswith("(") or not body.endswith(")"):
            raise NamespaceViolation(f"Expected tuple body for {entity} URN: {urn!r}")

        parts = _split_top_level(body[1:-1])

        if entity in _WRAPPER_NAME_INDEX:
            index = _WRAPPER_NAME_INDEX[entity]
            if len(parts) <= index:
                raise NamespaceViolation(f"Malformed {entity} URN: {urn!r}")
            # Recurse into the wrapped entity, e.g. a schemaField's parent dataset.
            return self.entity_name(parts[index])

        if entity in _TUPLE_NAME_INDEX:
            index = _TUPLE_NAME_INDEX[entity]
            if len(parts) <= index:
                raise NamespaceViolation(f"Malformed {entity} URN: {urn!r}")
            name = parts[index].strip()
            if not name:
                raise NamespaceViolation(f"Empty name component in URN: {urn!r}")
            return name

        # Fail closed: an entity type this guard has never seen cannot be proven in-namespace.
        raise NamespaceViolation(
            f"Unsupported entity type {entity!r} in URN {urn!r}. "
            "Add it to the namespace guard before operating on it."
        )

    def contains(self, urn: str) -> bool:
        """True when the URN is inside this project's allocation. Never raises."""
        try:
            return self.entity_name(urn).startswith(self.urn_prefix)
        except NamespaceViolation:
            return False

    def require(self, urn: str, *, operation: str) -> str:
        """Return the URN if it is in-namespace, otherwise raise :class:`NamespaceViolation`."""
        name = self.entity_name(urn)
        if not name.startswith(self.urn_prefix):
            raise NamespaceViolation(
                f"{operation} refused: {urn!r} resolves to name {name!r}, which is outside the "
                f"{self.urn_prefix!r} allocation for this project. Operating on it could corrupt "
                "another submission's DataHub state."
            )
        return urn

    def require_all(self, urns: Iterable[str], *, operation: str) -> list[str]:
        """Guard every URN. Raises on the first violation so partial writes cannot start."""
        return [self.require(urn, operation=operation) for urn in urns]

    def require_tag(self, tag_urn: str, *, operation: str) -> str:
        """Guard a tag URN against this project's required tag specifically."""
        name = self.entity_name(tag_urn)
        if name != self.project_tag:
            raise NamespaceViolation(
                f"{operation} refused: tag {name!r} is not this project's tag "
                f"{self.project_tag!r}."
            )
        return tag_urn


def require_contained_path(path: Path, root: Path, *, operation: str) -> Path:
    """Guard a filesystem target so destructive demo operations cannot escape their root.

    Used by the reset command. Symlinks are resolved before comparison so a link out of the
    state directory cannot be used to delete something else.
    """
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise NamespaceViolation(
            f"{operation} refused: {resolved_path} is outside {resolved_root}."
        )
    return resolved_path
