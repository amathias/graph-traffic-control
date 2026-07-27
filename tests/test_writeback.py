"""Reversible writeback: capture, write, immediate re-read, restore.

Run against the localhost MCP test double. These prove the *sequence* is correct and that the
shared instance is left as found; they are not evidence of a live DataHub write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_traffic_control.config import Settings
from graph_traffic_control.context.mcp_client import McpClient, McpError
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.demo.agents import FCT_REVENUE
from graph_traffic_control.domain.clock import ManualClock
from graph_traffic_control.writeback.datahub import (
    DEFAULT_DESCRIPTION_OPERATION,
    ReversibleDescriptionWriteback,
    WritebackError,
)
from tests.fake_mcp import FakeMcpServer, FakeMcpState

TOKEN = "test-token"  # noqa: S105 - fixture value
REPO_ROOT = Path(__file__).resolve().parent.parent

NAMESPACE = Namespace(
    urn_prefix="traffic.",
    project_tag="project-graph-traffic-control",
    domain="Demo / Graph Traffic Control",
)

ORIGINAL_DESCRIPTION = "Revenue fact table."


@pytest.fixture
def state() -> FakeMcpState:
    state = FakeMcpState()
    state.add_entity(
        FCT_REVENUE, name="traffic.fct_revenue", description=ORIGINAL_DESCRIPTION
    )
    return state


@pytest.fixture
def writeback(state):
    with FakeMcpServer(state) as server:
        client = McpClient(server.url, TOKEN)
        yield ReversibleDescriptionWriteback(client, NAMESPACE, ManualClock())
        client.close()


class TestTheDescriptionOperationIsLiveConfirmed:
    """``replace``, not ``SET``.

    ``SET`` was a guess from the aspect vocabulary — the coordinator supplied the argument *names*
    for ``update_description`` but never this *value*. Live DataHub 1.6.0 **rejected** it, and the
    same reversible write/re-read/restore cycle then succeeded with ``replace``.

    These assert the exact string rather than "some non-empty default", because the failure mode
    is a plausible-looking wrong value, and the whole restore guarantee rests on the operation
    having replace-in-place semantics.
    """

    def test_the_module_default_is_replace(self):
        assert DEFAULT_DESCRIPTION_OPERATION == "replace"

    def test_the_settings_default_is_replace(self):
        assert Settings().datahub_description_operation == "replace"

    def test_neither_default_has_reverted_to_the_rejected_guess(self):
        assert DEFAULT_DESCRIPTION_OPERATION != "SET"
        assert Settings().datahub_description_operation != "SET"

    def test_a_writeback_built_without_an_override_uses_replace(self, writeback):
        assert writeback.operation == "replace"

    def test_the_env_example_ships_the_confirmed_value(self):
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "DATAHUB_DESCRIPTION_OPERATION=replace" in text
        assert "DATAHUB_DESCRIPTION_OPERATION=SET" not in text

    def test_the_value_is_still_overridable_for_a_different_server(self, state):
        """Live-confirmed is not hardcoded: a future server must be accommodable."""
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            try:
                writeback = ReversibleDescriptionWriteback(
                    client, NAMESPACE, ManualClock(), operation="OVERRIDDEN"
                )
                writeback.apply(FCT_REVENUE, "note")
                writes = [args for name, args in state.calls if name == "update_description"]
                assert writes[0]["operation"] == "OVERRIDDEN"
            finally:
                client.close()


class TestHappyPath:
    def test_write_is_verified_by_an_immediate_reread(self, writeback):
        receipt = writeback.apply(FCT_REVENUE, "committed prop-a")
        assert receipt.previous_value == ORIGINAL_DESCRIPTION
        assert receipt.written_value == "committed prop-a"
        assert receipt.reread_value == "committed prop-a"
        assert receipt.verified is True

    def test_original_value_is_restored(self, writeback, state):
        receipt = writeback.apply(FCT_REVENUE, "committed prop-a")
        assert receipt.restored is True
        assert receipt.restoration_attempted is True
        assert receipt.restored_value == ORIGINAL_DESCRIPTION
        assert state.description_of(FCT_REVENUE) == ORIGINAL_DESCRIPTION

    def test_write_uses_the_coordinator_observed_argument_contract(self, writeback, state):
        writeback.apply(FCT_REVENUE, "committed prop-a")
        writes = [args for name, args in state.calls if name == "update_description"]
        assert writes[0] == {
            "entity_urn": FCT_REVENUE,
            "description": "committed prop-a",
            "operation": writeback.operation,
        }
        assert "urn" not in writes[0], "entity_urn is the contract; 'urn' was the wrong guess"

    def test_the_operation_used_is_recorded_on_the_receipt(self, writeback):
        receipt = writeback.apply(FCT_REVENUE, "note")
        assert receipt.operation == writeback.operation

    def test_call_sequence_is_capture_write_reread_restore(self, writeback, state):
        writeback.apply(FCT_REVENUE, "committed prop-a")
        sequence = [name for name, _ in state.calls]
        assert sequence == [
            "get_entities",      # capture
            "update_description",  # write
            "get_entities",      # immediate re-read
            "update_description",  # restore
            "get_entities",      # confirm restoration
        ]

    def test_receipt_records_the_aspect(self, writeback):
        assert writeback.apply(FCT_REVENUE, "note").aspect == "description"


class TestFailureHandling:
    def test_restoration_still_runs_when_the_write_fails(self, state):
        state.fail_tools.add("update_description")
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            writeback = ReversibleDescriptionWriteback(client, NAMESPACE, ManualClock())
            receipt = writeback.apply(FCT_REVENUE, "note")
            client.close()

        assert receipt.verified is False
        assert receipt.restoration_attempted is True
        assert "Writeback failed" in receipt.detail
        # The description was never changed, so the instance is still as found.
        assert state.description_of(FCT_REVENUE) == ORIGINAL_DESCRIPTION

    def test_verification_and_restoration_are_tracked_independently(self, state):
        """A verified write whose restoration failed must report exactly that, not one flag."""

        class RestoreFails(ReversibleDescriptionWriteback):
            def _write_description(self, urn: str, description: str) -> None:
                if description == ORIGINAL_DESCRIPTION:
                    raise McpError("restore rejected")
                super()._write_description(urn, description)

        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            receipt = RestoreFails(client, NAMESPACE, ManualClock()).apply(FCT_REVENUE, "note")
            client.close()

        assert receipt.verified is True, "the write did land and was re-read"
        assert receipt.restoration_attempted is True
        assert receipt.restored is False, "restoration failed and must not be reported as done"
        assert "Restoration failed" in receipt.detail

    def test_unreadable_entity_raises_before_any_write(self, state):
        state.fail_tools.add("get_entities")
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            writeback = ReversibleDescriptionWriteback(client, NAMESPACE, ManualClock())
            with pytest.raises(WritebackError, match="capture"):
                writeback.apply(FCT_REVENUE, "note")
            client.close()
        assert "update_description" not in {name for name, _ in state.calls}

    def test_entity_with_no_previous_description_restores_to_empty(self, state):
        state.add_entity(FCT_REVENUE, name="traffic.fct_revenue")
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            writeback = ReversibleDescriptionWriteback(client, NAMESPACE, ManualClock())
            receipt = writeback.apply(FCT_REVENUE, "note")
            client.close()
        assert receipt.previous_value is None
        assert receipt.restored is True


class TestNamespaceGuard:
    def test_foreign_entity_is_refused_before_any_call(self, writeback):
        foreign = "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos,PROD)"
        with pytest.raises(NamespaceViolation):
            writeback.apply(foreign, "note")

    def test_refusal_happens_before_the_capture_read(self, state):
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, TOKEN)
            writeback = ReversibleDescriptionWriteback(client, NAMESPACE, ManualClock())
            foreign = "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.x,PROD)"
            with pytest.raises(NamespaceViolation):
                writeback.apply(foreign, "note")
            client.close()
        assert state.calls == [], "no MCP call may be made for an out-of-namespace target"
