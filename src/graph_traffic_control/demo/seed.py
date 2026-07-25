"""Deterministic demo seed.

Materialises local demo state from the recorded fixture graph:

- a seed manifest listing every allocated entity;
- disposable SQL artifacts under ``APP_STATE_DIR/artifacts`` that proposals really rewrite.

Every entity in the fixture is validated against this project's namespace allocation before
anything is written, so a fixture that drifted outside ``traffic.`` fails the seed instead of
reaching DataHub later.

The seed is deterministic: running it twice produces byte-identical state. It contains no
timestamps and no random identifiers, which the coordinator's integration gate requires.

DataHub ingestion is deliberately not performed here. Ingestion against the shared instance is
coordinator-gated (namespace-scoped, stale-entity removal disabled), and this command must remain
safe to run offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from graph_traffic_control.config import Settings, get_settings
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.execute.targets import ARTIFACTS_DIRNAME

SEED_MANIFEST = "seed_manifest.json"

PLATFORM = "urn:li:dataPlatform:duckdb"


def dataset_urn(name: str) -> str:
    return f"urn:li:dataset:({PLATFORM},{name},PROD)"


#: Disposable SQL artifacts. Agent A renames a column in fct_revenue; Agent B's metric depends on
#: the old name; Agent C's support model is lineage-disjoint from both.
ARTIFACTS: dict[str, str] = {
    "stg_sales.sql": """-- model: traffic.stg_sales
select
    order_id,
    customer_id,
    order_ts,
    quantity * unit_price as line_amount,
    discount as discount_amount
from traffic.raw_sales_orders
join traffic.raw_sales_line_items using (order_id)
""",
    "fct_revenue.sql": """-- model: traffic.fct_revenue
select
    order_id,
    customer_id,
    order_ts as recognized_at,
    sum(line_amount - discount_amount) as gross_revenue,
    0.0 as refund_amount
from traffic.stg_sales
group by order_id, customer_id, order_ts
""",
    "metric_net_revenue.sql": """-- model: traffic.metric_net_revenue
select
    date_trunc('month', recognized_at) as period,
    sum(gross_revenue) - sum(refund_amount) as net_revenue
from traffic.fct_revenue
group by 1
""",
    "fct_support_sla.sql": """-- model: traffic.fct_support_sla
select
    date_trunc('month', opened_at) as period,
    priority,
    avg(case when resolution_minutes <= 240 then 1.0 else 0.0 end) as sla_attainment
from traffic.stg_support
group by 1, 2
""",
}

#: Maps each entity to the artifact that implements it, so the validator can check downstream
#: artifacts after an upstream schema change.
ARTIFACT_BY_URN: dict[str, str] = {
    dataset_urn("traffic.stg_sales"): "stg_sales.sql",
    dataset_urn("traffic.fct_revenue"): "fct_revenue.sql",
    dataset_urn("traffic.metric_net_revenue"): "metric_net_revenue.sql",
    dataset_urn("traffic.fct_support_sla"): "fct_support_sla.sql",
}


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

    artifacts_dir = state_dir / ARTIFACTS_DIRNAME
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for name, body in sorted(ARTIFACTS.items()):
        (artifacts_dir / name).write_text(body, encoding="utf-8")

    manifest = {
        "project_slug": settings.project_slug,
        "urn_prefix": namespace.urn_prefix,
        "domain": namespace.domain,
        "tag": namespace.project_tag,
        "fixture_root": str(settings.demo_fixture_root),
        "entity_count": len(urns),
        "edge_count": len(graph.get("edges", [])),
        "entities": urns,
        "artifacts": sorted(ARTIFACTS),
    }
    manifest_path = state_dir / SEED_MANIFEST
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {"manifest_path": manifest_path, "manifest": manifest}


def load_manifest(settings: Settings) -> dict[str, Any] | None:
    path = settings.state_dir / SEED_MANIFEST
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Graph Traffic Control demo state.")
    parser.add_argument(
        "--print-entities", action="store_true", help="List every seeded entity URN."
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
    print(f"Artifacts: {len(manifest['artifacts'])} SQL files")
    print(f"Namespace: {manifest['urn_prefix']}  Domain: {manifest['domain']}")
    print(f"Manifest:  {result['manifest_path']}")
    if args.print_entities:
        for urn in manifest["entities"]:
            print(f"  {urn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
