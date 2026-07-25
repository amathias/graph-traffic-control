"""A fake MCP server, served over real HTTP on localhost.

Exercising the client through an actual socket rather than a mocked transport is deliberate: it
proves the JSON-RPC framing, the ``Mcp-Session-Id`` handling, the SSE branch, and the auth header
all work end to end. It touches no AWS resource and no shared DataHub instance.

This is a **test double**, not evidence of live DataHub behaviour. It encodes the tool names and
response shapes this project expects; it cannot confirm that a real ``mcp-server-datahub`` 0.6.0
matches them.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_TOOLS = [
    "search",
    "get_entities",
    "get_lineage",
    "get_lineage_paths_between",
    "list_schema_fields",
    "update_description",
    "add_tags",
]


class FakeMcpState:
    """Mutable server state a test can inspect and steer."""

    def __init__(self) -> None:
        self.tools: list[str] = list(DEFAULT_TOOLS)
        self.entities: dict[str, dict[str, Any]] = {}
        self.lineage: dict[str, list[str]] = {}
        self.schema_fields: dict[str, list[dict[str, str]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.auth_headers: list[str | None] = []
        self.respond_with_sse = False
        self.fail_tools: set[str] = set()
        self.reject_unauthenticated = False
        #: When true, respond 500 with the received Authorization header echoed into the body.
        #: Simulates a badly-behaved upstream that reflects credentials in an error, so the
        #: client's redaction can be tested against a real response.
        self.leak_auth_in_error = False

    def descriptions(self) -> dict[str, str | None]:
        return {urn: entity.get("description") for urn, entity in self.entities.items()}


def _handler_factory(state: FakeMcpState):
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 semantics with an explicit `Connection: close` on every response. Keep-alive
        # would leave a handler blocked reading a request that never arrives, which deadlocks
        # server shutdown when the whole suite runs in one process.
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):  # noqa: A002 - silence test server logging
            return

        def _send(self, payload: dict[str, Any], status: int = 200) -> None:
            if state.respond_with_sse:
                body = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()
                content_type = "text/event-stream"
            else:
                body = json.dumps(payload).encode()
                content_type = "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Mcp-Session-Id", "fake-session-1")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _send_status(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
            state.auth_headers.append(self.headers.get("Authorization"))

            if state.reject_unauthenticated and not self.headers.get("Authorization"):
                self._send_status(401)
                return

            if state.leak_auth_in_error:
                received = self.headers.get("Authorization", "")
                body = f"upstream rejected credentials: {received}".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
                return

            method = request.get("method")

            if method == "notifications/initialized":
                self._send_status(202)
                return

            request_id = request.get("id")

            if method == "initialize":
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "fake-datahub-mcp", "version": "0.6.0"},
                        },
                    }
                )
                return

            if method == "tools/list":
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"tools": [{"name": name} for name in state.tools]},
                    }
                )
                return

            if method == "tools/call":
                params = request.get("params", {})
                name = params.get("name", "")
                arguments = params.get("arguments", {})
                state.calls.append((name, arguments))

                if name in state.fail_tools:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32000, "message": f"{name} is unavailable"},
                        }
                    )
                    return

                payload = _dispatch(state, name, arguments)
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(payload)}],
                            "isError": False,
                        },
                    }
                )
                return

            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Unknown method {method}"},
                }
            )

    return Handler


def _dispatch(state: FakeMcpState, name: str, arguments: dict[str, Any]) -> Any:
    if name == "get_entities":
        urns = arguments.get("urns") or [arguments.get("urn")]
        return [state.entities[urn] for urn in urns if urn in state.entities]
    if name == "list_schema_fields":
        return state.schema_fields.get(arguments.get("urn", ""), [])
    if name == "get_lineage":
        return [{"urn": urn} for urn in state.lineage.get(arguments.get("urn", ""), [])]
    if name == "update_description":
        urn = arguments.get("urn", "")
        if urn in state.entities:
            state.entities[urn] = {
                **state.entities[urn],
                "description": arguments.get("description"),
            }
        return {"status": "ok", "urn": urn}
    if name == "add_tags":
        return {"status": "ok"}
    return {}


class FakeMcpServer:
    """Context manager that runs the fake server on an ephemeral localhost port."""

    def __init__(self, state: FakeMcpState | None = None) -> None:
        self.state = state or FakeMcpState()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(self.state))
        # Handler threads must never outlive the test that created them.
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/mcp"

    def __enter__(self) -> FakeMcpServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
