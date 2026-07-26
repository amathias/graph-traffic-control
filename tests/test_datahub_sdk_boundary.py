"""The DataHub SDK boundary: what ``apply_plan`` actually hands the emitter.

This file exists because of a release-blocking defect that the whole rest of the suite missed.
``apply_plan`` called ``emitter.emit(op.as_dict())``. The emitter dispatches on *type*: anything
that is not an MCP or MCPW is treated as a ``MetadataChangeEvent`` and dereferenced as
``item.proposedSnapshot``. So every ``--apply`` run died locally, before the first network
operation, with ``AttributeError: 'dict' object has no attribute 'proposedSnapshot'``.

Nothing caught it because the emitting loop was marked ``# pragma: no cover - requires a live
instance``. A boundary that is excluded from coverage *and* untested is a boundary nobody has
ever executed, and the fact that a plan was inert and beautifully guarded says nothing about
whether it can be emitted at all.

Every test here runs against :mod:`tests.fake_datahub_sdk`, whose behaviour was read out of the
pinned SDK rather than its documentation. ``test_datahub_sdk_pinned.py`` re-runs the load-bearing
assertions against the real ``acryl-datahub==1.6.0.15`` whenever the optional extra is installed.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.namespace import NamespaceViolation
from graph_traffic_control.demo.datahub_state import (
    SOFT_DELETE_ASPECT,
    SOFT_DELETE_CHANGE_TYPE,
    AspectOperation,
    DataHubPlan,
    PartialApplyError,
    PlanError,
    apply_plan,
    plan_to_mcps,
    reset_plan,
    seed_plan,
)
from graph_traffic_control.demo.seed import load_fixture_graph, load_manifest
from tests.fake_datahub_sdk import FakeEmitter, FakeMcpw, fake_sdk

TOKEN = "test-token"  # noqa: S105 - fixture value
FOREIGN_DATASET = "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"


@pytest.fixture
def configured(seeded_settings):
    """Settings that pass the credentials check, so the boundary itself is reachable."""
    return seeded_settings.model_copy(
        update={"datahub_gms_url": "http://localhost:8080", "datahub_token": TOKEN}
    )


@pytest.fixture
def plan(seeded_settings, namespace):
    return seed_plan(load_fixture_graph(seeded_settings), namespace, seeded_settings)


@pytest.fixture
def allocated(seeded_settings):
    return list(load_manifest(seeded_settings)["entities"])


def _apply(plan, namespace, settings, emitter):
    return apply_plan(
        plan, namespace, settings, emitter_factory=lambda _s: emitter, sdk=fake_sdk()
    )


class TestTheRegression:
    """The defect itself: a raw dict must never reach the emitter."""

    def test_a_raw_dict_is_rejected_by_the_emitter_dispatch(self, plan):
        """The original failure, reproduced. This is what `--apply` did on the host."""
        emitter = FakeEmitter()
        with pytest.raises(AttributeError, match="proposedSnapshot"):
            emitter.emit(plan.operations[0].as_dict())

    def test_apply_emits_typed_proposals_and_never_a_dict(self, plan, namespace, configured):
        emitter = FakeEmitter()
        result = _apply(plan, namespace, configured, emitter)

        assert result["applied"] == len(plan.operations)
        assert len(emitter.emitted) == len(plan.operations)
        assert all(isinstance(item, FakeMcpw) for item in emitter.emitted)
        assert not any(isinstance(item, dict) for item in emitter.emitted)

    def test_every_operation_in_the_plan_converts(self, plan):
        mcps = plan_to_mcps(plan, fake_sdk())
        assert len(mcps) == len(plan.operations)
        for op, mcp in zip(plan.operations, mcps, strict=True):
            assert mcp.entityUrn == op.entity_urn
            assert mcp.entityType == op.entity_type
            assert mcp.changeType == op.change_type
            assert mcp.aspectName == op.aspect

    def test_the_seed_plan_still_has_the_shape_the_coordinator_reviewed(self, plan):
        """The payload corrections must not change the plan's size or coverage."""
        assert len(plan.operations) == 49
        assert len(plan.entity_urns) == 9


class TestConversionFailsClosed:
    def test_an_unknown_aspect_name_is_refused(self, namespace, seeded_settings):
        bad = DataHubPlan(
            kind="seed",
            urn_prefix=namespace.urn_prefix,
            domain_urn=seeded_settings.datahub_domain_urn,
            tag_urn=f"urn:li:tag:{namespace.project_tag}",
            operations=(
                AspectOperation(
                    "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.x,PROD)",
                    "dataset",
                    "notAnAspect",
                    "UPSERT",
                    {},
                ),
            ),
        )
        with pytest.raises(PlanError, match="not a DataHub aspect"):
            plan_to_mcps(bad, fake_sdk())

    def test_an_undeclared_payload_key_is_refused(self, namespace, seeded_settings):
        """The SDK drops unknown keys silently; a silent drop would report a false success."""
        bad = DataHubPlan(
            kind="seed",
            urn_prefix=namespace.urn_prefix,
            domain_urn=seeded_settings.datahub_domain_urn,
            tag_urn=f"urn:li:tag:{namespace.project_tag}",
            operations=(
                AspectOperation(
                    "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.x,PROD)",
                    "dataset",
                    "globalTags",
                    "UPSERT",
                    {"tags": [], "tagz": ["typo"]},
                ),
            ),
        )
        with pytest.raises(PlanError, match="does not declare"):
            plan_to_mcps(bad, fake_sdk())

    def test_a_payload_missing_a_required_field_is_refused(self, namespace, seeded_settings):
        """Exactly the shape of the latent schemaMetadata and dashboardInfo defects."""
        bad = DataHubPlan(
            kind="seed",
            urn_prefix=namespace.urn_prefix,
            domain_urn=seeded_settings.datahub_domain_urn,
            tag_urn=f"urn:li:tag:{namespace.project_tag}",
            operations=(
                AspectOperation(
                    "urn:li:dashboard:(looker,traffic.d)",
                    "dashboard",
                    "dashboardInfo",
                    "UPSERT",
                    {"description": "no title, no lastModified"},
                ),
            ),
        )
        with pytest.raises(PlanError, match="could not be built as a typed"):
            plan_to_mcps(bad, fake_sdk())

    def test_the_namespace_guard_still_runs_before_conversion(self, plan, namespace, configured):
        """The fix must not have weakened the whole-plan guard."""
        hijacked = DataHubPlan(
            kind=plan.kind,
            urn_prefix=plan.urn_prefix,
            domain_urn=plan.domain_urn,
            tag_urn=plan.tag_urn,
            operations=(
                *plan.operations,
                AspectOperation(FOREIGN_DATASET, "dataset", "domains", "UPSERT", {"domains": []}),
            ),
        )
        emitter = FakeEmitter()
        with pytest.raises(NamespaceViolation):
            _apply(hijacked, namespace, configured, emitter)
        assert emitter.emitted == []

    def test_a_conversion_failure_emits_nothing_at_all(self, plan, namespace, configured):
        """Whole-plan conversion before the first emit: a bad payload costs zero writes."""
        broken = DataHubPlan(
            kind=plan.kind,
            urn_prefix=plan.urn_prefix,
            domain_urn=plan.domain_urn,
            tag_urn=plan.tag_urn,
            operations=(
                *plan.operations,
                AspectOperation(
                    plan.operations[0].entity_urn, "dataset", "ownership", "UPSERT", {}
                ),
            ),
        )
        emitter = FakeEmitter()
        with pytest.raises(PlanError):
            _apply(broken, namespace, configured, emitter)
        assert emitter.emitted == []


class TestPartialApplyAccounting:
    """If a later network operation fails, the count reported must be the count applied."""

    def test_a_mid_run_failure_reports_exactly_how_far_it_got(self, plan, namespace, configured):
        emitter = FakeEmitter(fail_on=13)
        with pytest.raises(PartialApplyError) as exc:
            _apply(plan, namespace, configured, emitter)

        assert exc.value.applied == 12
        assert exc.value.total == len(plan.operations)
        assert len(emitter.emitted) == 12
        assert exc.value.operation is plan.operations[12]

    def test_the_failure_says_it_was_a_partial_write_and_how_to_recover(
        self, plan, namespace, configured
    ):
        """'Seed failed' must never be readable as 'nothing happened' on a shared instance."""
        with pytest.raises(PartialApplyError) as exc:
            _apply(plan, namespace, configured, FakeEmitter(fail_on=5))

        message = str(exc.value)
        assert "4 of 49" in message
        assert "partial write, not a no-op" in message
        assert "gtc-datahub-restore --apply" in message

    def test_a_failure_on_the_first_operation_reports_zero_applied(
        self, plan, namespace, configured
    ):
        with pytest.raises(PartialApplyError) as exc:
            _apply(plan, namespace, configured, FakeEmitter(fail_on=1))
        assert exc.value.applied == 0
        assert "0 of 49" in str(exc.value)

    def test_a_partial_apply_is_still_a_plan_error(self, plan, namespace, configured):
        """The CLI catches PlanError; a partial apply must not escape as an unhandled crash."""
        with pytest.raises(PlanError):
            _apply(plan, namespace, configured, FakeEmitter(fail_on=2))

    def test_a_successful_run_reports_every_operation(self, plan, namespace, configured):
        result = _apply(plan, namespace, configured, FakeEmitter())
        assert result["applied"] == 49
        assert result["fingerprint"] == plan.fingerprint()
        assert result["kind"] == "seed"


class TestSoftDeleteIsAnUpsert:
    """A soft delete is an UPSERT of ``status``; ``changeType: DELETE`` would un-delete."""

    def test_reset_soft_deletes_by_upserting_removed_true(
        self, allocated, namespace, seeded_settings
    ):
        plan = reset_plan(allocated, namespace, seeded_settings)
        assert all(op.aspect == SOFT_DELETE_ASPECT for op in plan.operations)
        assert all(op.change_type == SOFT_DELETE_CHANGE_TYPE for op in plan.operations)
        assert all(op.payload == {"removed": True} for op in plan.operations)

    def test_no_operation_anywhere_uses_the_delete_change_type(
        self, allocated, namespace, seeded_settings
    ):
        """Coordinator ruling 4: nothing this project emits is a destructive removal."""
        plan = reset_plan(allocated, namespace, seeded_settings)
        assert not any(op.change_type == "DELETE" for op in plan.operations)

    def test_the_soft_delete_converts_to_a_typed_status_aspect(
        self, allocated, namespace, seeded_settings
    ):
        plan = reset_plan(allocated, namespace, seeded_settings)
        mcps = plan_to_mcps(plan, fake_sdk())
        assert all(m.aspectName == "status" for m in mcps)
        assert all(m.aspect.values == {"removed": True} for m in mcps)
