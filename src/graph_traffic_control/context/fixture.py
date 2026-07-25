"""Fixture-backed context provider.

Reads the recorded graph committed under ``demo/fixtures/graph-traffic-control``. Used by the
whole test suite so tests stay offline and deterministic, and used locally when the coordinator
has not supplied DataHub connection details (ADR-001).

Every entity is passed through the namespace guard on load, so a fixture that drifted outside the
``traffic.`` allocation fails loudly instead of feeding foreign URNs into the conflict engine.
"""

from __future__ import annotations

import json
from pathlib import Path

from graph_traffic_control.context.namespace import Namespace
from graph_traffic_control.domain.clock import Clock, SystemClock
from graph_traffic_control.domain.models import (
    Criticality,
    EntityContext,
    GraphSnapshot,
    LineageEdge,
    SchemaField,
)


class FixtureContextProvider:
    source = "fixture"

    def __init__(
        self,
        fixture_root: Path,
        namespace: Namespace,
        clock: Clock | None = None,
    ) -> None:
        self._path = Path(fixture_root) / "graph.json"
        self._namespace = namespace
        self._clock = clock or SystemClock()

    def snapshot(self) -> GraphSnapshot:
        if not self._path.is_file():
            raise FileNotFoundError(f"Fixture graph not found: {self._path}")
        payload = json.loads(self._path.read_text(encoding="utf-8"))

        entities: dict[str, EntityContext] = {}
        for raw in [*payload.get("datasets", []), *payload.get("dashboards", [])]:
            urn = self._namespace.require(raw["urn"], operation="Fixture context read")
            entities[urn] = EntityContext(
                urn=urn,
                name=raw["name"],
                description=raw.get("description"),
                criticality=Criticality(raw.get("criticality", "UNKNOWN")),
                owners=list(raw.get("owners", [])),
                fields=[
                    SchemaField(path=f["path"], type=f.get("type", "unknown"))
                    for f in raw.get("fields", [])
                ],
            )

        edges: list[LineageEdge] = []
        for raw_edge in payload.get("edges", []):
            upstream = self._namespace.require(raw_edge["upstream"], operation="Fixture lineage")
            downstream = self._namespace.require(
                raw_edge["downstream"], operation="Fixture lineage"
            )
            edges.append(LineageEdge(upstream=upstream, downstream=downstream))

        return GraphSnapshot(entities=entities, edges=edges, captured_at=self._clock.now())
