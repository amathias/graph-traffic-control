"""Seed and reset must be deterministic and namespace-scoped.

Backs the coordinator integration gate "its demo seed and reset are deterministic" and the
``../AGENTS.md`` rule that a reset must never delete another project's entities.
"""

from __future__ import annotations

import json

import pytest

from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.demo.reset import reset
from graph_traffic_control.demo.seed import (
    SEED_MANIFEST,
    collect_urns,
    load_fixture_graph,
    seed,
    verify_namespace,
)


class TestFixtureGraph:
    def test_fixture_graph_loads(self, settings):
        graph = load_fixture_graph(settings)
        assert graph["datasets"]
        assert graph["edges"]

    def test_every_fixture_entity_is_in_namespace(self, settings):
        graph = load_fixture_graph(settings)
        namespace = Namespace.from_settings(settings)
        urns = verify_namespace(graph, namespace)
        assert urns, "fixture graph produced no URNs"
        assert all(namespace.contains(urn) for urn in urns)

    def test_collect_urns_is_sorted_and_deduplicated(self, settings):
        graph = load_fixture_graph(settings)
        urns = collect_urns(graph)
        assert urns == sorted(set(urns))

    def test_demo_lineage_path_exists_from_revenue_to_metric(self, settings):
        """The A/B conflict depends on this edge. If it disappears, the demo has no story."""
        graph = load_fixture_graph(settings)
        edges = {(edge["upstream"], edge["downstream"]) for edge in graph["edges"]}
        fct = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_revenue,PROD)"
        metric = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.metric_net_revenue,PROD)"
        assert (fct, metric) in edges

    def test_support_branch_is_disjoint_from_revenue_branch(self, settings):
        """Agent C's parallel commit depends on this. Guard it against fixture drift."""
        graph = load_fixture_graph(settings)
        support = {
            urn
            for edge in graph["edges"]
            for urn in (edge["upstream"], edge["downstream"])
            if "support" in urn
        }
        revenue = {
            urn
            for edge in graph["edges"]
            for urn in (edge["upstream"], edge["downstream"])
            if "support" not in urn
        }
        assert support and revenue
        assert support.isdisjoint(revenue)

    def test_gross_revenue_column_exists(self, settings):
        """Agent A renames this column. Its absence would silently void the demo."""
        graph = load_fixture_graph(settings)
        fct = next(d for d in graph["datasets"] if d["name"] == "traffic.fct_revenue")
        assert any(field["path"] == "gross_revenue" for field in fct["fields"])


class TestSeed:
    def test_seed_creates_manifest(self, settings):
        result = seed(settings)
        manifest_path = settings.state_dir / SEED_MANIFEST
        assert manifest_path.is_file()
        assert result["manifest"]["entity_count"] > 0

    def test_seed_materialises_sql_artifacts(self, settings):
        seed(settings)
        artifacts = settings.state_dir / "artifacts"
        names = sorted(p.name for p in artifacts.glob("*.sql"))
        assert names == [
            "fct_revenue.sql",
            "fct_support_sla.sql",
            "metric_net_revenue.sql",
            "stg_sales.sql",
        ]

    def test_artifacts_encode_the_demo_dependency(self, settings):
        """fct_revenue defines gross_revenue; the metric downstream consumes it."""
        seed(settings)
        artifacts = settings.state_dir / "artifacts"
        assert "gross_revenue" in (artifacts / "fct_revenue.sql").read_text(encoding="utf-8")
        assert "gross_revenue" in (
            artifacts / "metric_net_revenue.sql"
        ).read_text(encoding="utf-8")

    def test_seeded_artifacts_are_byte_identical_across_runs(self, settings):
        seed(settings)
        first = (settings.state_dir / "artifacts" / "fct_revenue.sql").read_bytes()
        seed(settings)
        second = (settings.state_dir / "artifacts" / "fct_revenue.sql").read_bytes()
        assert first == second

    def test_seed_is_deterministic(self, settings):
        seed(settings)
        first = (settings.state_dir / SEED_MANIFEST).read_text(encoding="utf-8")
        seed(settings)
        second = (settings.state_dir / SEED_MANIFEST).read_text(encoding="utf-8")
        assert first == second

    def test_manifest_records_the_namespace_allocation(self, settings):
        seed(settings)
        manifest = json.loads((settings.state_dir / SEED_MANIFEST).read_text(encoding="utf-8"))
        assert manifest["urn_prefix"] == "traffic."
        assert manifest["tag"] == "project-graph-traffic-control"
        assert manifest["domain"] == "Demo / Graph Traffic Control"

    def test_seed_refuses_a_fixture_containing_a_foreign_entity(self, settings, tmp_path):
        """A fixture that drifted outside the allocation must fail the seed, not reach DataHub."""
        graph = load_fixture_graph(settings)
        graph["datasets"].append(
            {
                "urn": "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos_table,PROD)",
                "name": "fuzzer.chaos_table",
                "fields": [],
            }
        )
        rogue_root = tmp_path / "rogue-fixtures"
        rogue_root.mkdir()
        (rogue_root / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

        rogue_settings = settings.model_copy(update={"demo_fixture_root": rogue_root})
        with pytest.raises(NamespaceViolation, match="outside"):
            seed(rogue_settings)


class TestReset:
    def test_reset_removes_state_but_leaves_fixtures(self, settings):
        seed(settings)
        assert (settings.state_dir / SEED_MANIFEST).is_file()

        reset(settings)

        assert settings.state_dir.is_dir()
        assert not (settings.state_dir / SEED_MANIFEST).exists()
        assert (settings.fixture_root / "graph.json").is_file(), "fixtures must survive a reset"

    def test_reset_is_idempotent(self, settings):
        seed(settings)
        first = reset(settings)
        second = reset(settings)
        assert first["removed"]
        assert second["removed"] == []

    def test_reset_on_missing_state_dir_creates_it(self, settings):
        assert not settings.state_dir.exists()
        result = reset(settings)
        assert settings.state_dir.is_dir()
        assert result["removed"] == []

    def test_reset_removes_nested_directories(self, settings):
        settings.state_dir.mkdir(parents=True)
        nested = settings.state_dir / "runs" / "txn-001"
        nested.mkdir(parents=True)
        (nested / "evidence.json").write_text("{}", encoding="utf-8")

        reset(settings)

        assert not (settings.state_dir / "runs").exists()
        assert settings.state_dir.is_dir()

    def test_reset_refuses_when_state_dir_is_the_fixture_root(self, settings):
        """Guards against a misconfiguration that would delete version-controlled fixtures."""
        misconfigured = settings.model_copy(
            update={"app_state_dir": settings.fixture_root}
        )
        with pytest.raises(NamespaceViolation, match="fixture root"):
            reset(misconfigured)
        assert (settings.fixture_root / "graph.json").is_file()
