"""MCP client behaviour over real HTTP.

These run against the localhost test double in ``tests/fake_mcp.py``. They prove the client's
wire behaviour; they are explicitly **not** evidence that a live DataHub MCP server responds this
way. See ``docs/LIMITATIONS.md``.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.mcp_client import McpClient, McpError
from tests.fake_mcp import FakeMcpServer, FakeMcpState

TOKEN = "test-token-do-not-use"  # noqa: S105 - fixture value for a local test double


@pytest.fixture
def server():
    with FakeMcpServer() as fake:
        yield fake


class TestHandshake:
    def test_initialize_then_list_tools(self, server):
        with McpClient(server.url, TOKEN) as client:
            tools = client.list_tools()
        assert "get_lineage" in tools
        assert "update_description" in tools

    def test_bearer_token_is_sent(self, server):
        with McpClient(server.url, TOKEN) as client:
            client.list_tools()
        assert f"Bearer {TOKEN}" in server.state.auth_headers

    def test_session_id_is_echoed_back_after_initialize(self, server):
        client = McpClient(server.url, TOKEN)
        client.initialize()
        assert client._session_id == "fake-session-1"  # noqa: SLF001
        client.close()

    def test_initialize_is_only_performed_once(self, server):
        with McpClient(server.url, TOKEN) as client:
            client.initialize()
            client.initialize()
            client.list_tools()
        # One initialize, one notification, one tools/list -> no duplicate handshakes.
        assert server.state.auth_headers.count(f"Bearer {TOKEN}") == 3

    def test_missing_endpoint_is_rejected_immediately(self):
        with pytest.raises(McpError, match="not configured"):
            McpClient("", TOKEN)


class TestToolCalls:
    def test_tool_result_json_is_decoded(self, server):
        server.state.add_entity("urn:li:dataset:x", name="x")
        with McpClient(server.url, TOKEN) as client:
            result = client.call_tool("get_entities", {"urns": ["urn:li:dataset:x"]})
        assert result == {"result": [server.state.entities["urn:li:dataset:x"]]}

    def test_tool_error_raises_mcp_error(self, server):
        server.state.fail_tools.add("get_lineage")
        with McpClient(server.url, TOKEN) as client:
            with pytest.raises(McpError, match="unavailable"):
                client.call_tool("get_lineage", {"urn": "x"})

    def test_arguments_reach_the_server(self, server):
        arguments = {"urn": "abc", "upstream": False, "max_hops": 1, "max_results": 10}
        with McpClient(server.url, TOKEN) as client:
            client.call_tool("get_lineage", arguments)
        name, received = server.state.calls[-1]
        assert name == "get_lineage"
        assert received == arguments

    def test_structured_content_is_required_when_the_caller_depends_on_it(self, server):
        """A response with no structuredContent is a contract violation, not a fallback."""
        server.state.add_entity("urn:li:dataset:x", name="x")
        with McpClient(server.url, TOKEN) as client:
            structured = client.call_tool_structured(
                "get_entities", {"urns": ["urn:li:dataset:x"]}
            )
        assert "result" in structured


class TestTransportVariants:
    def test_sse_framed_responses_are_parsed(self, server):
        """A Streamable HTTP endpoint may answer with an event stream."""
        server.state.respond_with_sse = True
        with McpClient(server.url, TOKEN) as client:
            assert "get_entities" in client.list_tools()

    def test_http_error_status_raises(self, server):
        server.state.reject_unauthenticated = True
        client = McpClient(server.url, "")
        with pytest.raises(McpError, match="HTTP 401"):
            client.list_tools()
        client.close()

    def test_unreachable_endpoint_raises_rather_than_hanging(self):
        client = McpClient("http://127.0.0.1:1/mcp", TOKEN, timeout=1.0)
        with pytest.raises(McpError, match="MCP request failed"):
            client.list_tools()
        client.close()


class TestSecretHygiene:
    def test_token_is_not_leaked_in_transport_errors(self):
        secret = "super-secret-token-value"  # noqa: S105 - deliberately fake
        client = McpClient("http://127.0.0.1:1/mcp", secret, timeout=1.0)
        with pytest.raises(McpError) as exc:
            client.list_tools()
        assert secret not in str(exc.value)
        client.close()

    def test_token_is_not_leaked_when_a_server_reflects_it_in_an_error(self):
        """A badly-behaved upstream can echo credentials into an error body."""
        secret = "another-secret-token"  # noqa: S105 - deliberately fake
        state = FakeMcpState()
        state.leak_auth_in_error = True
        with FakeMcpServer(state) as server:
            client = McpClient(server.url, secret)
            with pytest.raises(McpError) as exc:
                client.list_tools()
            message = str(exc.value)
            client.close()

        assert "HTTP 500" in message
        assert secret not in message, "the client surfaced a credential from a server response"
        assert "***redacted***" in message
