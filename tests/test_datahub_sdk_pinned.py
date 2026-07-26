"""The same boundary, against the **real** ``acryl-datahub==1.6.0.15``.

``test_datahub_sdk_boundary.py`` runs against a double, so it can run everywhere. A double is
still a claim about someone else's library, and the defect that blocked this release was precisely
a wrong claim about someone else's library. These tests remove the claim: when the pinned optional
extra is installed, every operation of every plan this project can build is constructed as a real
typed aspect and serialised to the exact bytes the emitter would put on the wire.

Skipped when the extra is absent, which is the normal state of the offline suite. Run them with::

    pip install -e ".[datahub]"
    pytest tests/test_datahub_sdk_pinned.py

Nothing here touches the network. The emitter is never constructed; only the conversion and
serialisation are exercised.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.demo.datahub_state import (
    capture_state,
    plan_to_mcps,
    reset_plan,
    restore_plan,
    seed_plan,
)
from graph_traffic_control.demo.seed import load_fixture_graph, load_manifest
from tests.fake_mcp import FakeMcpServer, FakeMcpState

datahub = pytest.importorskip(
    "datahub.emitter.mcp", reason='requires the pinned optional extra: pip install -e ".[datahub]"'
)

TOKEN = "test-token"  # noqa: S105 - fixture value
EXPECTED_SDK_VERSION = "1.6.0.15"


@pytest.fixture
def allocated(seeded_settings):
    return list(load_manifest(seeded_settings)["entities"])


@pytest.fixture
def seed(seeded_settings, namespace):
    return seed_plan(load_fixture_graph(seeded_settings), namespace, seeded_settings)


def _capture(state, namespace, allocated):
    with FakeMcpServer(state) as server:
        client = McpClient(server.url, TOKEN)
        try:
            return capture_state(client, namespace, allocated, allow_absent=True)
        finally:
            client.close()


def _populated(allocated):
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


class TestThePinnedVersion:
    def test_the_installed_sdk_is_the_pinned_one(self):
        """The shapes below were verified against this version specifically (ADR-017)."""
        from importlib.metadata import version

        assert version("acryl-datahub") == EXPECTED_SDK_VERSION


class TestTheRealEmitterRejectsARawDict:
    def test_emit_mce_dereferences_proposed_snapshot(self, seed):
        """The actual defect, against the actual library.

        No network is reachable and none is needed: ``emit_mce`` builds its URL and then
        immediately dereferences ``mce.proposedSnapshot``, which is where a dict dies. The
        emitter is left unconstructed apart from the one attribute the URL line reads, so the
        failure is the payload's and not a connection's.
        """
        from datahub.emitter.rest_emitter import DatahubRestEmitter

        emitter = DatahubRestEmitter.__new__(DatahubRestEmitter)
        emitter._gms_server = "http://localhost:8080"
        with pytest.raises(AttributeError, match="proposedSnapshot"):
            emitter.emit_mce(seed.operations[0].as_dict())

    def test_a_dict_is_not_a_proposal_the_emitter_recognises(self, seed):
        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.metadata.schema_classes import MetadataChangeProposalClass

        item = seed.operations[0].as_dict()
        assert not isinstance(item, MetadataChangeProposalClass | MetadataChangeProposalWrapper)


class TestEveryPlanConvertsAgainstTheRealSdk:
    def _assert_converts(self, plan):
        from datahub.emitter.mcp import MetadataChangeProposalWrapper

        mcps = plan_to_mcps(plan)
        assert len(mcps) == len(plan.operations)
        for op, mcp in zip(plan.operations, mcps, strict=True):
            assert isinstance(mcp, MetadataChangeProposalWrapper)
            assert mcp.entityUrn == op.entity_urn
            assert mcp.aspectName == op.aspect
            mcp.validate()
            # The bytes the emitter would send. Raises if any aspect is malformed.
            mcp.make_mcp().to_obj()
        return mcps

    def test_the_seed_plan_converts(self, seed):
        mcps = self._assert_converts(seed)
        assert len(mcps) == 49

    def test_the_reset_plan_converts(self, allocated, namespace, seeded_settings):
        self._assert_converts(reset_plan(allocated, namespace, seeded_settings))

    def test_an_absent_state_restore_converts(self, allocated, namespace, seeded_settings):
        capture = _capture(FakeMcpState(), namespace, allocated)
        self._assert_converts(restore_plan(capture, namespace, seeded_settings, allocated))

    def test_a_present_state_restore_converts(self, allocated, namespace, seeded_settings):
        """Covers the dashboardInfo restore payload, which needs title and lastModified."""
        capture = _capture(_populated(allocated), namespace, allocated)
        self._assert_converts(restore_plan(capture, namespace, seeded_settings, allocated))


class TestTheShapesTheDoubleAsserts:
    """Proves the double in ``fake_datahub_sdk`` is not lying about the real library."""

    def test_from_obj_drops_unknown_keys_silently(self):
        """The reason ``_require_known_payload_keys`` exists."""
        from datahub.metadata.schema_classes import StatusClass

        assert StatusClass.from_obj({"remvoed": True}).to_obj() == {"removed": False}

    def test_from_obj_raises_on_a_missing_required_field(self):
        from datahub.metadata.schema_classes import DashboardInfoClass

        with pytest.raises(ValueError, match="lastModified"):
            DashboardInfoClass.from_obj({"title": "t", "description": "d"})

    def test_the_declared_field_sets_match_the_double(self):
        from datahub.emitter.aspect import ASPECT_MAP

        from tests.fake_datahub_sdk import ASPECT_SCHEMA

        for name, schema in ASPECT_SCHEMA.items():
            real = [f.name for f in ASPECT_MAP[name].RECORD_SCHEMA.fields]
            assert real == schema["fields"], f"{name} field set drifted from the pinned SDK"

    def test_the_soft_delete_matches_the_sdks_own_form(self, allocated, namespace,
                                                       seeded_settings):
        """The SDK soft-deletes with UPSERT + StatusClass(removed=True); so does this project."""
        from datahub.metadata.schema_classes import ChangeTypeClass, StatusClass

        mcps = plan_to_mcps(reset_plan(allocated, namespace, seeded_settings))
        assert all(m.changeType == ChangeTypeClass.UPSERT for m in mcps)
        assert all(isinstance(m.aspect, StatusClass) and m.aspect.removed for m in mcps)
