"""DataHub seed, reset, capture, and restore.

Four properties are under test:

1. **Completeness.** A seed covers the whole ``traffic.`` graph and every aspect the coordinator
   asked for: schemas, ownership, domain, tag, marker, and lineage.
2. **Determinism.** The same fixture yields byte-identical plans and the same fingerprint.
3. **Isolation.** Nothing can reach another submission's entities - not through the entity a
   plan is addressed to, and not through a URN buried inside an aspect payload.
4. **The absent state is a captured value.** A first-time seed finds nothing in the shared
   instance. Absence can therefore be captured deliberately, restored to deliberately, and
   *proved* afterwards - and every partial, extra, foreign, or ambiguous capture is refused.

Plans are inert here. **No plan in this suite has been applied to a live DataHub instance.**
"""

from __future__ import annotations

import json

import pytest

from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.context.namespace import NamespaceViolation
from graph_traffic_control.demo import datahub_cli
from graph_traffic_control.demo.datahub_state import (
    CAPTURE_KIND,
    CAPTURE_VERSION,
    DATASET_ASPECTS,
    MARKER_KEY,
    MARKER_VALUE,
    SOFT_DELETE_ASPECT,
    SOFT_DELETE_CHANGE_TYPE,
    AspectOperation,
    DataHubPlan,
    PlanError,
    apply_plan,
    capture_state,
    guard_plan,
    ingestion_recipe,
    require_exact_allocation,
    reset_plan,
    restore_plan,
    seed_plan,
    verify_absent,
    verify_capture,
    write_capture,
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
        assert all(op.aspect == SOFT_DELETE_ASPECT for op in plan.operations)

    def test_reset_is_a_soft_delete(self, allocated, namespace, seeded_settings):
        """An UPSERT of ``status`` with ``removed: true``.

        Not ``changeType: DELETE`` — that removes the ``status`` aspect, which *un*-deletes a
        soft-deleted entity. See ``test_datahub_sdk_boundary.py``.
        """
        plan = reset_plan(allocated, namespace, seeded_settings)
        assert all(op.change_type == SOFT_DELETE_CHANGE_TYPE for op in plan.operations)
        assert all(op.payload == {"removed": True} for op in plan.operations)

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


def _populated_state(allocated: list[str]) -> FakeMcpState:
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


def _capture_via_server(state, namespace, allocated, **kwargs):
    """Capture over a real socket against the protocol double."""
    with FakeMcpServer(state) as server:
        client = McpClient(server.url, TOKEN)
        try:
            return capture_state(client, namespace, allocated, **kwargs)
        finally:
            client.close()


def _verify_via_server(state, namespace, urns):
    with FakeMcpServer(state) as server:
        client = McpClient(server.url, TOKEN)
        try:
            return verify_absent(client, namespace, urns)
        finally:
            client.close()


class TestCaptureAndRestore:
    """The instance already holds this project's entities."""

    @pytest.fixture
    def live_state(self, allocated) -> FakeMcpState:
        return _populated_state(allocated)

    def _capture(self, live_state, namespace, allocated):
        return _capture_via_server(live_state, namespace, allocated)

    def test_capture_records_every_allocated_entity(self, live_state, namespace, allocated):
        capture = self._capture(live_state, namespace, allocated)
        assert set(capture["entities"]) == set(allocated)
        assert capture["entity_count"] == len(allocated)
        assert capture["absent"] == []
        assert capture["allocated"] == sorted(allocated)

    def test_capture_declares_its_contract_version(self, live_state, namespace, allocated):
        capture = self._capture(live_state, namespace, allocated)
        assert capture["kind"] == CAPTURE_KIND
        assert capture["capture_version"] == CAPTURE_VERSION

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
        with pytest.raises(PlanError, match="allow-absent"):
            self._capture(live_state, namespace, allocated)

    def test_capture_reads_only_the_exact_allowlisted_urns(
        self, live_state, namespace, allocated
    ):
        """No search, no wildcard: every read names one URN from the allocation."""
        self._capture(live_state, namespace, allocated)
        tools = {name for name, _ in live_state.calls}
        assert tools == {"get_entities"}
        asked = [args["urns"] for _, args in live_state.calls]
        assert all(len(urns) == 1 for urns in asked)
        assert {urns[0] for urns in asked} == set(allocated)

    def test_capture_refuses_an_unrequested_entity_in_the_response(
        self, live_state, namespace, allocated
    ):
        """An answer naming something we did not ask about makes presence ambiguous."""
        live_state.malformed_tools["get_entities"] = {
            "result": [
                {"urn": allocated[0]},
                {"urn": allocated[1]},
            ]
        }
        with pytest.raises(PlanError, match="ambiguous"):
            self._capture(live_state, namespace, allocated)

    def test_restore_puts_back_the_captured_values(
        self, live_state, namespace, seeded_settings, allocated
    ):
        capture = self._capture(live_state, namespace, allocated)
        plan = restore_plan(capture, namespace, seeded_settings, allocated)

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
        plan = restore_plan(capture, namespace, seeded_settings, allocated)
        for urn in allocated:
            aspects = {op.aspect for op in plan.operations if op.entity_urn == urn}
            assert {"ownership", "globalTags", "domains"} <= aspects

    def test_restore_of_a_fully_present_capture_deletes_nothing(
        self, live_state, namespace, seeded_settings, allocated
    ):
        capture = self._capture(live_state, namespace, allocated)
        plan = restore_plan(capture, namespace, seeded_settings, allocated)
        assert all(op.change_type == "UPSERT" for op in plan.operations)

    def test_restore_is_deterministic(
        self, live_state, namespace, seeded_settings, allocated
    ):
        capture = self._capture(live_state, namespace, allocated)
        first = restore_plan(capture, namespace, seeded_settings, allocated)
        second = restore_plan(capture, namespace, seeded_settings, allocated)
        assert first.to_json() == second.to_json()

    def test_an_empty_capture_cannot_produce_a_restore(
        self, namespace, seeded_settings, allocated
    ):
        with pytest.raises(PlanError):
            restore_plan({"entities": {}}, namespace, seeded_settings, allocated)

    @pytest.mark.parametrize("scope", ["global", "all", "full-refresh", "nuke", ""])
    def test_restore_refuses_every_scope_but_namespace(
        self, live_state, namespace, seeded_settings, allocated, scope
    ):
        capture = self._capture(live_state, namespace, allocated)
        with pytest.raises(NamespaceViolation, match="Only 'namespace'"):
            restore_plan(capture, namespace, seeded_settings, allocated, scope=scope)


class TestAbsentStateCapture:
    """The first-time seed: the whole ``traffic.`` namespace is missing.

    Capture has to run before seed, so on a first run it has nothing to read. Skipping the
    missing entities would produce a capture indistinguishable from one taken against a damaged
    instance, and restoring from it would leave this project's rows in a shared catalogue.
    """

    @pytest.fixture
    def empty_state(self) -> FakeMcpState:
        return FakeMcpState()

    def test_absence_is_refused_unless_it_is_asked_for(
        self, empty_state, namespace, allocated
    ):
        with pytest.raises(PlanError, match="allow-absent"):
            _capture_via_server(empty_state, namespace, allocated)

    def test_absence_of_every_entity_can_be_captured_deliberately(
        self, empty_state, namespace, allocated
    ):
        capture = _capture_via_server(empty_state, namespace, allocated, allow_absent=True)
        assert capture["entities"] == {}
        assert capture["absent"] == sorted(allocated)
        assert capture["absent_count"] == len(allocated)
        assert capture["allocated"] == sorted(allocated)

    def test_a_soft_deleted_entity_counts_as_absent(
        self, allocated, namespace
    ):
        """DataHub still returns a soft-deleted row. Present in a response is not present."""
        state = _populated_state(allocated)
        state.soft_delete(allocated[0])
        capture = _capture_via_server(state, namespace, allocated, allow_absent=True)
        assert allocated[0] in capture["absent"]
        assert allocated[0] not in capture["entities"]

    def test_a_soft_deleted_entity_is_refused_without_allow_absent(
        self, allocated, namespace
    ):
        state = _populated_state(allocated)
        state.soft_delete(allocated[0])
        with pytest.raises(PlanError, match="soft-deleted"):
            _capture_via_server(state, namespace, allocated)

    def test_a_mixed_instance_captures_both_buckets(self, allocated, namespace):
        state = _populated_state(allocated)
        state.entities.pop(allocated[0])
        capture = _capture_via_server(state, namespace, allocated, allow_absent=True)
        assert capture["absent"] == [allocated[0]]
        assert set(capture["entities"]) == set(allocated) - {allocated[0]}

    def test_restore_soft_deletes_exactly_the_initially_absent_entities(
        self, empty_state, namespace, seeded_settings, allocated
    ):
        capture = _capture_via_server(empty_state, namespace, allocated, allow_absent=True)
        plan = restore_plan(capture, namespace, seeded_settings, allocated)

        deletes = {op.entity_urn for op in plan.operations if op.aspect == SOFT_DELETE_ASPECT}
        assert deletes == set(allocated)
        assert all(op.aspect == SOFT_DELETE_ASPECT for op in plan.operations)

    def test_the_absent_restore_is_a_soft_delete(
        self, empty_state, namespace, seeded_settings, allocated
    ):
        """Never a hard delete. Coordinator ruling 4 forbids destructive removal."""
        capture = _capture_via_server(empty_state, namespace, allocated, allow_absent=True)
        plan = restore_plan(capture, namespace, seeded_settings, allocated)
        assert all(op.payload == {"removed": True} for op in plan.operations)
        assert all(op.aspect == SOFT_DELETE_ASPECT for op in plan.operations)
        assert all(op.change_type == SOFT_DELETE_CHANGE_TYPE for op in plan.operations)

    def test_a_mixed_restore_deletes_the_absent_and_upserts_the_present(
        self, allocated, namespace, seeded_settings
    ):
        state = _populated_state(allocated)
        state.entities.pop(allocated[0])
        capture = _capture_via_server(state, namespace, allocated, allow_absent=True)
        plan = restore_plan(capture, namespace, seeded_settings, allocated)

        deleted = {op.entity_urn for op in plan.operations if op.aspect == SOFT_DELETE_ASPECT}
        restored = {
            op.entity_urn for op in plan.operations if op.aspect != SOFT_DELETE_ASPECT
        }
        assert deleted == {allocated[0]}
        assert restored == set(allocated) - {allocated[0]}

    def test_the_absent_restore_is_deterministic(
        self, empty_state, namespace, seeded_settings, allocated
    ):
        capture = _capture_via_server(empty_state, namespace, allocated, allow_absent=True)
        first = restore_plan(capture, namespace, seeded_settings, allocated)
        second = restore_plan(capture, namespace, seeded_settings, allocated)
        assert first.to_json() == second.to_json()
        assert first.fingerprint() == second.fingerprint()

    def test_seed_creates_exactly_what_was_captured_as_absent(
        self, empty_state, namespace, seeded_settings, allocated, graph
    ):
        """The three-step contract closes: absent set == seeded set == restored set."""
        capture = _capture_via_server(empty_state, namespace, allocated, allow_absent=True)
        seeded = seed_plan(graph, namespace, seeded_settings, allocated)
        restored = restore_plan(capture, namespace, seeded_settings, allocated)

        assert set(capture["absent"]) == set(seeded.entity_urns)
        assert set(seeded.entity_urns) == set(restored.entity_urns)


class TestCaptureIsRefusedWhenItCannotBeTrusted:
    """Every refusal here is a restore that would have been wrong."""

    @pytest.fixture
    def capture(self, allocated, namespace):
        return _capture_via_server(_populated_state(allocated), namespace, allocated)

    def test_a_partial_capture_is_refused(self, capture, namespace, allocated):
        dropped = allocated[0]
        capture["entities"].pop(dropped)
        capture["allocated"].remove(dropped)
        with pytest.raises(PlanError, match="not covered"):
            verify_capture(capture, namespace, allocated)

    def test_an_extra_in_namespace_urn_is_refused(self, capture, namespace, allocated):
        """In-namespace but never seeded is still out of scope."""
        extra = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.not_seeded,PROD)"
        capture["absent"].append(extra)
        capture["allocated"].append(extra)
        with pytest.raises(PlanError, match="not in this project's allocation"):
            verify_capture(capture, namespace, allocated)

    @pytest.mark.parametrize("prefix", FOREIGN_PREFIXES)
    def test_a_foreign_urn_is_refused(self, capture, namespace, allocated, prefix):
        foreign = f"urn:li:dataset:(urn:li:dataPlatform:duckdb,{prefix}victim,PROD)"
        capture["absent"].append(foreign)
        capture["allocated"].append(foreign)
        with pytest.raises(NamespaceViolation):
            verify_capture(capture, namespace, allocated)

    def test_an_ambiguous_capture_is_refused(self, capture, namespace, allocated):
        """Present and absent at once has no single state to restore to."""
        capture["absent"].append(allocated[0])
        with pytest.raises(PlanError, match="ambiguous"):
            verify_capture(capture, namespace, allocated)

    def test_a_capture_whose_declared_allocation_disagrees_is_refused(
        self, capture, namespace, allocated
    ):
        capture["allocated"] = sorted(allocated)[:2]
        with pytest.raises(PlanError, match="capture coverage"):
            verify_capture(capture, namespace, allocated)

    def test_a_capture_from_another_prefix_is_refused(self, capture, namespace, allocated):
        capture["urn_prefix"] = "fuzzer."
        with pytest.raises(NamespaceViolation):
            verify_capture(capture, namespace, allocated)

    def test_an_older_capture_version_is_refused(self, capture, namespace, allocated):
        """A v1 capture recorded no absent set, so its silence cannot be read as 'nothing'."""
        capture.pop("capture_version")
        with pytest.raises(PlanError, match="capture_version|Capture version"):
            verify_capture(capture, namespace, allocated)

    def test_a_file_that_is_not_a_capture_is_refused(self, namespace, allocated):
        with pytest.raises(PlanError, match="Not a capture file"):
            verify_capture({"kind": "seed_plan"}, namespace, allocated)

    @pytest.mark.parametrize(
        "mutation", [{"entities": []}, {"absent": {}}, {"allocated": "traffic."}]
    )
    def test_a_malformed_capture_is_refused(self, capture, namespace, allocated, mutation):
        capture.update(mutation)
        with pytest.raises(PlanError, match="malformed"):
            verify_capture(capture, namespace, allocated)

    def test_a_duplicated_urn_is_refused(self, namespace):
        with pytest.raises(PlanError, match="more than once"):
            require_exact_allocation(["a", "a"], ["a"], operation="test")

    def test_an_empty_allocation_is_refused(self, namespace):
        with pytest.raises(PlanError, match="allocation is empty"):
            require_exact_allocation([], [], operation="test")


class TestAbsenceIsVerifiedAfterRestore:
    """Absence after a restore is a fact that gets re-read, not an intention reported."""

    def test_absence_is_confirmed_when_every_entity_is_gone(self, namespace, allocated):
        state = FakeMcpState()
        result = _verify_via_server(state, namespace, allocated)
        assert result["verified_absent"] == sorted(allocated)
        assert result["checked"] == len(allocated)

    def test_a_soft_deleted_entity_counts_as_verified_absent(self, namespace, allocated):
        state = _populated_state(allocated)
        for urn in allocated:
            state.soft_delete(urn)
        assert _verify_via_server(state, namespace, allocated)["checked"] == len(allocated)

    def test_a_still_live_entity_fails_the_verification(self, namespace, allocated):
        """The failure this exists for: nine deleted, one left in a shared catalogue."""
        state = FakeMcpState()
        state.add_entity(allocated[0], name=allocated[0])
        with pytest.raises(PlanError, match="still present"):
            _verify_via_server(state, namespace, allocated)

    def test_verification_refuses_a_foreign_urn(self, namespace):
        with pytest.raises(NamespaceViolation):
            _verify_via_server(FakeMcpState(), namespace, [FOREIGN_DATASET])

    def test_verification_reads_only_the_exact_urns(self, namespace, allocated):
        state = FakeMcpState()
        _verify_via_server(state, namespace, allocated)
        assert {name for name, _ in state.calls} == {"get_entities"}



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
        err = capsys.readouterr().err
        assert "gtc-datahub-capture" in err
        assert "--allow-absent" in err, "the first-time path must be named where it is needed"

    def test_seed_output_reports_the_fingerprint_and_scope(self, capsys):
        datahub_cli.seed_main([])
        out = capsys.readouterr().out
        assert "plan fingerprint:" in out
        assert "outside the 'traffic.' allocation were not touched" in out

    def test_a_global_restore_exits_refused(self, seeded_settings, namespace, allocated, capsys):
        capture = _capture_via_server(FakeMcpState(), namespace, allocated, allow_absent=True)
        write_capture(capture, seeded_settings)
        assert datahub_cli.restore_main(["--scope", "global"]) == 2
        assert "refused" in capsys.readouterr().err.lower()

    def test_restore_plans_from_an_absent_state_capture(
        self, seeded_settings, namespace, allocated, capsys
    ):
        """The first-time flow, end to end at the CLI: capture absent, then plan the restore."""
        capture = _capture_via_server(FakeMcpState(), namespace, allocated, allow_absent=True)
        write_capture(capture, seeded_settings)

        assert datahub_cli.restore_main([]) == 0
        assert "NOT applied" in capsys.readouterr().out

        body = json.loads(
            (seeded_settings.state_dir / "datahub" / "restore_plan.json").read_text("utf-8")
        )
        assert {op["entityUrn"] for op in body["operations"]} == set(allocated)
        assert all(op["aspectName"] == SOFT_DELETE_ASPECT for op in body["operations"])
        assert all(op["aspect"] == {"removed": True} for op in body["operations"])

    def test_restore_refuses_a_capture_that_does_not_match_the_manifest(
        self, seeded_settings, namespace, allocated, capsys
    ):
        capture = _capture_via_server(FakeMcpState(), namespace, allocated, allow_absent=True)
        dropped = capture["absent"].pop()
        capture["allocated"].remove(dropped)
        capture["absent_count"] -= 1
        write_capture(capture, seeded_settings)

        assert datahub_cli.restore_main([]) == 1
        assert "not covered" in capsys.readouterr().err

    def test_capture_reports_both_buckets(
        self, seeded_settings, namespace, allocated, monkeypatch, capsys
    ):
        state = _populated_state(allocated)
        state.entities.pop(allocated[0])
        with FakeMcpServer(state) as server:
            live = seeded_settings.model_copy(
                update={"datahub_mcp_url": server.url, "datahub_token": TOKEN}
            )
            monkeypatch.setattr(datahub_cli, "get_settings", lambda: live)
            assert datahub_cli.capture_main(["--allow-absent"]) == 0

        out = capsys.readouterr().out
        assert f"Captured {len(allocated) - 1} present and 1 absent" in out
        assert "soft-delete these entities and verify they are gone" in out

    def test_capture_without_allow_absent_fails_on_a_missing_entity(
        self, seeded_settings, namespace, allocated, monkeypatch, capsys
    ):
        state = _populated_state(allocated)
        state.entities.pop(allocated[0])
        with FakeMcpServer(state) as server:
            live = seeded_settings.model_copy(
                update={"datahub_mcp_url": server.url, "datahub_token": TOKEN}
            )
            monkeypatch.setattr(datahub_cli, "get_settings", lambda: live)
            assert datahub_cli.capture_main([]) == 1

        assert "--allow-absent" in capsys.readouterr().err


class TestSeedCoversExactlyTheAllocation:
    """Seed must create exactly the namespace that capture recorded the absence of."""

    def test_a_seed_matching_the_manifest_is_accepted(
        self, graph, namespace, seeded_settings, allocated
    ):
        plan = seed_plan(graph, namespace, seeded_settings, allocated)
        assert set(plan.entity_urns) == set(allocated)

    def test_a_seed_missing_an_allocated_entity_is_refused(
        self, graph, namespace, seeded_settings, allocated
    ):
        dropped = graph["datasets"][0]["urn"]
        graph["datasets"] = [d for d in graph["datasets"] if d["urn"] != dropped]
        graph["edges"] = [
            e for e in graph["edges"] if dropped not in (e["upstream"], e["downstream"])
        ]
        with pytest.raises(PlanError, match="not covered"):
            seed_plan(graph, namespace, seeded_settings, allocated)

    def test_a_seed_creating_an_unallocated_entity_is_refused(
        self, graph, namespace, seeded_settings, allocated
    ):
        """In-namespace but absent from the manifest: capture never recorded its state."""
        graph["datasets"].append(
            {
                "urn": "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.surprise,PROD)",
                "name": "traffic.surprise",
                "owners": [],
                "fields": [],
            }
        )
        with pytest.raises(PlanError, match="not in this project's allocation"):
            seed_plan(graph, namespace, seeded_settings, allocated)
