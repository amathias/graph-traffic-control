"""Deterministic demo seed.

Materialises local demo state from the recorded fixture graph. Every entity in the fixture is
validated against this project's namespace allocation before anything is written, so a fixture
that drifted outside ``traffic.`` fails the seed instead of reaching DataHub later.

The seed is deterministic: running it twice from the same fixture produces byte-identical state
(no timestamps, no random identifiers). The coordinator's integration gate requires this.

Phase 0 seeds local state only. DataHub ingestion is added in Phase 5, behind the same guard.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from graph_traffic_control.config import Settings, get_settings
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation

SEED_MANIFEST = "seed_manifest.json"


def load_fixture_graph(settings: Settings) -> dict[str, Any]:
    path = settings.fixture_root / "graph.json"
    if not path.is_file():
        raise FileNotFoundError(f"Fixture graph not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def collect_urns(graph: dict[str, Any]) -> list[str]:
    """Every URN the fixture references, including both ends of every lineage edge."""
    urns: list[str] = []
    for dataset in graph.get("datasets", []):
        urns.append(dataset["urn"])
    for dashboard in graph.get("dashboards", []):
        urns.append(dashboard["urn"])
    for edge in graph.get("edges", []):
        urns.append(edge["upstream"])
        urns.append(edge["downstream"])
    # Deterministic order, de-duplicated.
    return sorted(set(urns))


def verify_namespace(graph: dict[str, Any], namespace: Namespace) -> list[str]:
    """Guard every fixture URN. Raises :class:`NamespaceViolation` on the first outsider."""
    urns = collect_urns(graph)
    namespace.require_all(urns, operation="Demo seed")
    return urns


def seed(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    namespace = Namespace.from_settings(settings)

    graph = load_fixture_graph(settings)
    urns = verify_namespace(graph, namespace)

    state_dir = settings.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "project_slug": settings.project_slug,
        "urn_prefix": namespace.urn_prefix,
        "domain": namespace.domain,
        "tag": namespace.project_tag,
        "fixture_root": str(settings.demo_fixture_root),
        "entity_count": len(urns),
        "edge_count": len(graph.get("edges", [])),
        "entities": urns,
    }
    manifest_path = state_dir / SEED_MANIFEST
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {"manifest_path": manifest_path, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Graph Traffic Control demo state.")
    parser.add_argument(
        "--print-entities",
        action="store_true",
        help="List every seeded entity URN.",
    )
    args = parser.parse_args(argv)

    try:
        result = seed()
    except NamespaceViolation as exc:
        print(f"Seed refused: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1

    manifest = result["manifest"]
    print(f"Seeded {manifest['entity_count']} entities, {manifest['edge_count']} lineage edges.")
    print(f"Namespace: {manifest['urn_prefix']}  Domain: {manifest['domain']}")
    print(f"Manifest:  {result['manifest_path']}")
    if args.print_entities:
        for urn in manifest["entities"]:
            print(f"  {urn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
