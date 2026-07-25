"""Reversible writeback: capture, write, immediate re-read, restore.

Run against the localhost MCP test double. These prove the *sequence* is correct and that the
shared instance is left as found; they are not evidence of a live DataHub write.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.demo.agents import FCT_REVENUE
from graph_traffic_control.domain.clock import ManualClock
from graph_traffic_control.writeback.datahub import (
    ReversibleDescriptionWriteback,
    WritebackError,
)
from tests.fake_mcp import FakeMcpServer, FakeMcpState

TOKEN = "test-token"  # noqa: S105 - fixture value

NAMESPACE = Namespace(
    urn_prefix="traffic.",
    project_tag="project-graph-traffic-control",
    domain="Demo / Graph Traffic Control",
)

ORIGINAL_DESCRIPTION = "Revenue fact table."


@pytest.fixture
def state() -> FakeMcpState:
    state = FakeMcpState()
    state.entities[FCT_REVENUE] = {
        "urn": FCT_REVENUE,
        "name": "traffic.fct_revenue",
        "description": ORIGINAL_DESCRIPTION,
    }
    return state


@pytest.fixture
def writeback(state):
    with FakeMcpServer(state) as server:
        client = McpClient(server.url, TOKEN)
        yield ReversibleDescriptionWriteback(client, NAMESPACE, ManualClock())
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
        assert receipt.restored_value == ORIGINAL_DESCRIPTION
        assert state.entities[FCT_REVENUE]["description"] == ORIGINAL_DESCRIPTION

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
        assert "Writeback failed" in receipt.detail
        # The description was never changed, so the instance is still as found.
        assert state.entities[FCT_REVENUE]["description"] == ORIGINAL_DESCRIPTION

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
        state.entities[FCT_REVENUE] = {"urn": FCT_REVENUE, "name": "traffic.fct_revenue"}
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
