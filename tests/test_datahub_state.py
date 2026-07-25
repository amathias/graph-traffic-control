"""DataHub seed, reset, capture, and restore.

Three properties are under test:

1. **Completeness.** A seed covers the whole ``traffic.`` graph and every aspect the coordinator
   asked for: schemas, ownership, domain, tag, marker, and lineage.
2. **Determinism.** The same fixture yields byte-identical plans and the same fingerprint.
3. **Isolation.** Nothing can reach another submission's entities - not through the entity a
   plan is addressed to, and not through a URN buried inside an aspect payload.

Plans are inert here. **No plan in this suite has been applied to a live DataHub instance.**
"""

from __future__ import annotations

import json

import pytest

from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.context.namespace import NamespaceViolation
from graph_traffic_control.demo import datahub_cli
from graph_traffic_control.demo.datahub_state import (
    DATASET_ASPECTS,
    MARKER_KEY,
    MARKER_VALUE,
    AspectOperation,
    DataHubPlan,
    PlanError,
    apply_plan,
    capture_state,
    guard_plan,
    ingestion_recipe,
    reset_plan,
    restore_plan,
    seed_plan,
    write_plan,
)
from graph_traffic_control.demo.seed import load_fixture_graph, load_manifest
from tests.fake_mcp import FakeMcpServer, FakeMcpState

TOKEN = "test-token"  # noqa: S105 - fixture value

FOREIGN_DATASET = "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"
FOREIGN_PREFIXES = ["lifeboat.", "license.", "forgetme.", "fuzzer."]


@pytest.fixture
def graph(seeded_settings):
    return load_fixture_graph(seeded_settings)


@pytest.fixture
def allocated(seeded_settings):
    manifest = load_manifest(seeded_settings)
    return list(manifest["entities"])


@pytest.fixture
def plan(graph, namespace, seeded_settings):
    return seed_plan(graph, namespace, seeded_settings)


class TestSeedCompleteness:
    def test_every_allocated_entity_is_covered(self, plan, allocated):
        assert set(plan.entity_urns) == set(allocated)

    def test_every_dataset_gets_every_required_aspect(self, plan, graph):
        for dataset in graph["datasets"]:
            aspects = {
                op.aspect for op in plan.operations if op.entity_urn == dataset["urn"]
            }
            expected = set(DATASET_ASPECTS)
            if not dataset.get("fields"):
                expected.discard("schemaMetadata")
            # A source table has no upstreams, so it legitimately has no lineage aspect.
            if not any(e["downstream"] == dataset["urn"] for e in graph["edges"]):
                expected.discard("upstreamLineage")
            assert expected <= aspects, f"{dataset['name']} is missing {expected - aspects}"

    def test_schemas_carry_every_field(self, plan, graph):
        by_urn = {d["urn"]: d for d in graph["datasets"]}
        for op in plan.operations:
            if op.aspect != "schemaMetadata":
                continue
            planned = {f["fieldPath"] for f in op.payload["fields"]}
            expected = {f["path"] for f in by_urn[op.entity_urn]["fields"]}
            assert planned == expected

    def test_ownership_is_planned_for_every_entity(self, plan, allocated):
        owned = {op.entity_urn for op in plan.operations if op.aspect == "ownership"}
        assert owned == set(allocated)

    def test_every_entity_joins_the_allocated_domain(self, plan, allocated, seeded_settings):
        domains = {
            op.entity_urn: op.payload["domains"]
            for op in plan.operations
            if op.aspect == "domains"
        }
        assert set(domains) == set(allocated)
        assert all(d == [seeded_settings.datahub_domain_urn] for d in domains.values())

    def test_every_entity_carries_the_project_tag(self, plan, allocated, namespace):
        tag_urn = f"urn:li:tag:{namespace.project_tag}"
        tagged = {
            op.entity_urn
            for op in plan.operations
            if op.aspect == "globalTags" and {"tag": tag_urn} in op.payload["tags"]
        }
        assert tagged == set(allocated)

    def test_every_entity_carries_the_project_marker(self, plan, allocated):
        marked = {
            op.entity_urn
            for op in plan.operations
            if op.payload.get("customProperties", {}).get(MARKER_KEY) == MARKER_VALUE
        }
        assert marked == set(allocated), "the marker identifies our rows in a shared catalogue"

    def test_lineage_reproduces_every_fixture_edge(self, plan, graph):
        planned: set[tuple[str, str]] = set()
        for op in plan.operations:
            if op.aspect == "upstreamLineage":
                planned |= {(u["dataset"], op.entity_urn) for u in op.payload["upstreams"]}
            elif op.aspect == "dashboardInfo":
                planned |= {(d, op.entity_urn) for d in op.payload["datasets"]}
        expected = {(e["upstream"], e["downstream"]) for e in graph["edges"]}
        assert planned == expected

    def test_the_dashboard_is_planned_as_a_dashboard(self, plan):
        kinds = {op.entity_type for op in plan.operations}
        assert {"dataset", "dashboard"} <= kinds


class TestDeterminism:
    def test_two_builds_are_byte_identical(self, graph, namespace, seeded_settings):
        first = seed_plan(graph, namespace, seeded_settings)
        second = seed_plan(graph, namespace, seeded_settings)
        assert first.to_json() == second.to_json()
        assert first.fingerprint() == second.fingerprint()

    def test_the_plan_contains_no_timestamps_or_random_ids(self, plan):
        body = plan.to_json()
        assert "T00:00" not in body and "Z\"" not in body
        assert plan.fingerprint() == plan.fingerprint()

    def test_written_plans_are_byte_identical_across_runs(self, plan, seeded_settings):
        first = write_plan(plan, seeded_settings, "seed_plan.json")
        contents = first["paths"][0].read_text(encoding="utf-8")
        second = write_plan(plan, seeded_settings, "seed_plan.json")
        assert second["paths"][0].read_text(encoding="utf-8") == contents

    def test_a_changed_graph_changes_the_fingerprint(self, graph, namespace, seeded_settings):
        before = seed_plan(graph, namespace, seeded_settings).fingerprint()
        graph["datasets"][0]["owners"] = ["someone-else"]
        assert seed_plan(graph, namespace, seeded_settings).fingerprint() != before


class TestNamespaceIsolation:
    """The instance is shared with four other submissions."""

    @pytest.mark.parametrize("prefix", FOREIGN_PREFIXES)
    def test_a_foreign_entity_is_refused(self, graph, namespace, seeded_settings, prefix):
        graph["datasets"].append(
            {
                "urn": f"urn:li:dataset:(urn:li:dataPlatform:duckdb,{prefix}victim,PROD)",
                "name": f"{prefix}victim",
                "owners": [],
                "fields": [],
            }
        )
        with pytest.raises(NamespaceViolation):
            seed_plan(graph, namespace, seeded_settings)

    def test_a_foreign_urn_hidden_inside_a_lineage_aspect_is_refused(
        self, graph, namespace, seeded_settings
    ):
        """The entity addressed is ours; the upstream it names is not.

        Guarding only the entity URN would let this through and write a cross-project edge.
        """
        graph["edges"].append(
            {"upstream": FOREIGN_DATASET, "downstream": graph["datasets"][2]["urn"]}
        )
        with pytest.raises(NamespaceViolation):
            seed_plan(graph, namespace, seeded_settings)

    def test_a_foreign_urn_hidden_in_a_dashboard_input_is_refused(
        self, graph, namespace, seeded_settings
    ):
        graph["edges"].append(
            {"upstream": FOREIGN_DATASET, "downstream": graph["dashboards"][0]["urn"]}
        )
        with pytest.raises(NamespaceViolation):
            seed_plan(graph, namespace, seeded_settings)

    def test_a_plan_for_another_domain_is_refused(self, plan, namespace, seeded_settings):
        hijacked = DataHubPlan(
            kind=plan.kind,
            urn_prefix=plan.urn_prefix,
            domain_urn="urn:li:domain:someone-elses-domain",
            tag_urn=plan.tag_urn,
            operations=plan.operations,
        )
        with pytest.raises(NamespaceViolation, match="allocated domain"):
            guard_plan(hijacked, namespace, seeded_settings)

    def test_a_plan_for_another_prefix_is_refused(self, plan, namespace, seeded_settings):
        hijacked = DataHubPlan(
            kind=plan.kind,
            urn_prefix="fuzzer.",
            domain_urn=plan.domain_urn,
            tag_urn=plan.tag_urn,
            operations=plan.operations,
        )
        with pytest.raises(NamespaceViolation):
            guard_plan(hijacked, namespace, seeded_settings)

    def test_a_plan_for_another_projects_tag_is_refused(self, plan, namespace, seeded_settings):
        hijacked = DataHubPlan(
            kind=plan.kind,
            urn_prefix=plan.urn_prefix,
            domain_urn=plan.domain_urn,
            tag_urn="urn:li:tag:project-fuzzer",
            operations=plan.operations,
        )
        with pytest.raises(NamespaceViolation):
            guard_plan(hijacked, namespace, seeded_settings)

    def test_an_aspect_naming_a_foreign_domain_is_refused(
        self, plan, namespace, seeded_settings
    ):
        smuggled = AspectOperation(
            plan.entity_urns[0], "dataset", "domains", "UPSERT",
            {"domains": ["urn:li:domain:someone-else"]},
        )
        hijacked = DataHubPlan(
            kind=plan.kind,
            urn_prefix=plan.urn_prefix,
            domain_urn=plan.domain_urn,
            tag_urn=plan.tag_urn,
            operations=(*plan.operations, smuggled),
        )
        with pytest.raises(NamespaceViolation):
            guard_plan(hijacked, namespace, seeded_settings)


class TestReset:
    def test_reset_covers_the_whole_allocation(self, allocated, namespace, seeded_settings):
        plan = reset_plan(allocated, namespace, seeded_settings)
        assert set(plan.entity_urns) == set(allocated)
        assert all(op.change_type == "DELETE" for op in plan.operations)

    def test_reset_is_a_soft_delete(self, allocated, namespace, seeded_settings):
        plan = reset_plan(allocated, namespace, seeded_settings)
        assert all(op.payload["soft"] is True for op in plan.operations)

    def test_a_global_reset_is_refused(self, allocated, namespace, seeded_settings):
        """The one that would delete four other submissions."""
        with pytest.raises(NamespaceViolation, match="Only 'namespace'"):
            reset_plan(allocated, namespace, seeded_settings, scope="global")

    @pytest.mark.parametrize("scope", ["all", "full-refresh", "nuke", ""])
    def test_every_other_scope_is_refused(self, allocated, namespace, seeded_settings, scope):
        with pytest.raises(NamespaceViolation):
            reset_plan(allocated, namespace, seeded_settings, scope=scope)

    def test_a_foreign_urn_in_the_allocation_is_refused(self, namespace, seeded_settings):
        with pytest.raises(NamespaceViolation):
            reset_plan([FOREIGN_DATASET], namespace, seeded_settings)

    def test_an_empty_reset_is_refused_rather_than_reported_as_success(
        self, namespace, seeded_settings
    ):
        with pytest.raises(PlanError):
            reset_plan([], namespace, seeded_settings)

    def test_reset_is_deterministic(self, allocated, namespace, seeded_settings):
        a = reset_plan(allocated, namespace, seeded_settings)
        b = reset_plan(list(reversed(allocated)), namespace, seeded_settings)
        assert a.to_json() == b.to_json(), "ordering of the input must not change the plan"


class TestCaptureAndRestore:
    @pytest.fixture
    def live_state(self, allocated) -> FakeMcpState:
        state = FakeMcpState()
        for urn in allocated:
            state.add_entity(
                urn,
                name=urn,
                description=f"original description for {urn}",
                owners=["urn:li:corpGroup:sales-eng"],
                tags=["urn:li:tag:project-graph-traffic-control"],
                domain="urn:li:domain:graph-traffic-control",
            )
        return state

    def _capture(self, live_state, namespace, allocated):
        with FakeMcpServer(live_state) as server:
            client = McpClient(server.url, TOKEN)
            try:
                return capture_state(client, namespace, allocated)
            finally:
                client.close()

    def test_capture_records_every_allocated_entity(self, live_state, namespace, allocated):
        capture = self._capture(live_state, namespace, allocated)
        assert set(capture["entities"]) == set(allocated)
        assert capture["entity_count"] == len(allocated)

    def test_capture_refuses_a_foreign_urn(self, live_state, namespace):
        with FakeMcpServer(live_state) as server:
            client = McpClient(server.url, TOKEN)
            with pytest.raises(NamespaceViolation):
                capture_state(client, namespace, [FOREIGN_DATASET])
            client.close()

    def test_capture_fails_closed_when_an_entity_cannot_be_read(
        self, live_state, namespace, allocated
    ):
        """A partial capture would silently drop whatever it missed at restore time."""
        live_state.entities.pop(allocated[0])
        with FakeMcpServer(live_state) as server:
            client = McpClient(server.url, TOKEN)
            with pytest.raises(Exception):  # noqa: B017 - McpContractError subclass
                capture_state(client, namespace, allocated)
            client.close()

    def test_restore_puts_back_the_captured_values(
        self, live_state, namespace, seeded_settings, allocated
    ):
        capture = self._capture(live_state, namespace, allocated)
        plan = restore_plan(capture, namespace, seeded_settings)

        descriptions = {
            op.entity_urn: op.payload["description"]
            for op in plan.operations
            if op.aspect in {"datasetProperties", "dashboardInfo"}
        }
        assert set(descriptions) == set(allocated)
        assert all(
            value == f"original description for {urn}"
            for urn, value in descriptions.items()
        )

    def test_restore_covers_ownership_tags_and_domain(
        self, live_state, namespace, seeded_settings, allocated
    ):
        capture = self._capture(live_state, namespace, allocated)
        plan = restore_plan(capture, namespace, seeded_settings)
        for urn in allocated:
            aspects = {op.aspect for op in plan.operations if op.entity_urn == urn}
            assert {"ownership", "globalTags", "domains"} <= aspects

    def test_restore_is_deterministic(
        self, live_state, namespace, seeded_settings, allocated
    ):
        capture = self._capture(live_state, namespace, allocated)
        first = restore_plan(capture, namespace, seeded_settings)
        second = restore_plan(capture, namespace, seeded_settings)
        assert first.to_json() == second.to_json()

    def test_an_empty_capture_cannot_produce_a_restore(self, namespace, seeded_settings):
        with pytest.raises(PlanError):
            restore_plan({"entities": {}}, namespace, seeded_settings)


class TestEmission:
    def test_the_recipe_disables_stale_entity_removal(self, plan, seeded_settings):
        """With it enabled, a namespace-scoped ingest would delete the other four submissions."""
        recipe = ingestion_recipe(plan, seeded_settings)
        assert "remove_stale_metadata: false" in recipe
        assert "enabled: false" in recipe

    def test_the_recipe_records_the_plan_fingerprint(self, plan, seeded_settings):
        assert plan.fingerprint() in ingestion_recipe(plan, seeded_settings)

    def test_writing_a_seed_emits_plan_and_recipe(self, plan, seeded_settings):
        result = write_plan(plan, seeded_settings, "seed_plan.json")
        names = sorted(p.name for p in result["paths"])
        assert names == ["ingestion_recipe.yaml", "seed_plan.json"]

    def test_the_written_plan_is_valid_json_with_every_operation(self, plan, seeded_settings):
        result = write_plan(plan, seeded_settings, "seed_plan.json")
        body = json.loads(result["paths"][0].read_text(encoding="utf-8"))
        assert len(body["operations"]) == len(plan.operations)
        assert body["urn_prefix"] == "traffic."


class TestApplyRefusesWithoutCredentials:
    def test_apply_refuses_without_configuration(self, plan, namespace, seeded_settings):
        """A plan is never applied on a guess about where it would land."""
        with pytest.raises(PlanError, match="DATAHUB_GMS_URL"):
            apply_plan(plan, namespace, seeded_settings)

    def test_apply_reguards_the_plan_before_touching_anything(
        self, plan, namespace, seeded_settings
    ):
        """A plan mutated after it was built must not slip through on the earlier guard."""
        configured = seeded_settings.model_copy(
            update={"datahub_gms_url": "http://localhost:8080", "datahub_token": TOKEN}
        )
        tampered = DataHubPlan(
            kind=plan.kind,
            urn_prefix=plan.urn_prefix,
            domain_urn=plan.domain_urn,
            tag_urn=plan.tag_urn,
            operations=(
                *plan.operations,
                AspectOperation(FOREIGN_DATASET, "dataset", "domains", "UPSERT", {}),
            ),
        )
        with pytest.raises(NamespaceViolation):
            apply_plan(tampered, namespace, configured)


class TestCli:
    """The commands default to planning. Nothing reaches a shared instance by accident."""

    @pytest.fixture(autouse=True)
    def _isolated_settings(self, seeded_settings, monkeypatch):
        monkeypatch.setattr(datahub_cli, "get_settings", lambda: seeded_settings)
        return seeded_settings

    def test_seed_plans_without_applying(self, seeded_settings, capsys):
        assert datahub_cli.seed_main([]) == 0
        out = capsys.readouterr().out
        assert "NOT applied" in out
        assert (seeded_settings.state_dir / "datahub" / "seed_plan.json").is_file()
        assert (seeded_settings.state_dir / "datahub" / "ingestion_recipe.yaml").is_file()

    def test_reset_plans_without_applying(self, seeded_settings, capsys):
        assert datahub_cli.reset_main([]) == 0
        assert "NOT applied" in capsys.readouterr().out
        assert (seeded_settings.state_dir / "datahub" / "reset_plan.json").is_file()

    def test_a_global_reset_exits_refused(self, capsys):
        assert datahub_cli.reset_main(["--scope", "global"]) == 2
        assert "refused" in capsys.readouterr().err.lower()

    def test_apply_without_credentials_exits_failed(self, capsys):
        assert datahub_cli.seed_main(["--apply"]) == 1
        assert "DATAHUB_GMS_URL" in capsys.readouterr().err

    def test_capture_without_credentials_is_refused(self, capsys):
        assert datahub_cli.capture_main([]) == 2
        assert "refused" in capsys.readouterr().err.lower()

    def test_restore_without_a_capture_is_a_clear_failure(self, capsys):
        assert datahub_cli.restore_main([]) == 1
        assert "gtc-datahub-capture" in capsys.readouterr().err

    def test_seed_output_reports_the_fingerprint_and_scope(self, capsys):
        datahub_cli.seed_main([])
        out = capsys.readouterr().out
        assert "plan fingerprint:" in out
        assert "outside the 'traffic.' allocation were not touched" in out
