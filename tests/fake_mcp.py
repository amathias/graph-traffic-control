"""A fake DataHub MCP server, served over real HTTP on localhost.

Exercising the client through an actual socket rather than a mocked transport is deliberate: it
proves the JSON-RPC framing, the ``Mcp-Session-Id`` handling, the SSE branch, and the auth header
all work end to end. It touches no AWS resource and no shared DataHub instance.

**This is a protocol double. Every result produced with it is simulated, not live DataHub
evidence.** What it *can* prove is that this project speaks the contract it claims to speak,
because the double is strict rather than permissive:

- each tool validates its argument names against the coordinator-observed contract and returns a
  JSON-RPC error if the client sends anything else, so an argument-name regression fails a test
  instead of silently "working";
- payloads are emitted in the coordinator-observed envelopes
  (``structuredContent.result``, ``structuredContent.downstreams.searchResults[*].entity.urn``,
  ``structuredContent.fields``) with governance nested under ``properties``, ``ownership``,
  ``tags``, and ``domain``.

It still cannot confirm that the pinned ``mcp-server-datahub`` behaves this way; only a live run
can. See ``docs/LIMITATIONS.md``.
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

#: Coordinator-observed argument contracts. The double refuses anything else.
REQUIRED_ARGUMENTS: dict[str, set[str]] = {
    "get_entities": {"urns"},
    "get_lineage": {"urn", "upstream", "max_hops", "max_results"},
    "list_schema_fields": {"urn", "limit"},
    "update_description": {"entity_urn", "description", "operation"},
}


class ContractViolation(Exception):
    """The client called a tool with arguments the real server would not accept."""


def entity_payload(
    urn: str,
    *,
    name: str | None = None,
    description: str | None = None,
    owners: list[str] | None = None,
    tags: list[str] | None = None,
    domain: str | None = None,
    removed: bool | None = None,
) -> dict[str, Any]:
    """Build an entity in the nested governance shape the real server returns."""
    payload: dict[str, Any] = {"urn": urn, "properties": {}}
    if removed is not None:
        # A soft-deleted entity is still returned by get_entities, carrying status.removed.
        payload["status"] = {"removed": removed}
    if name is not None:
        payload["properties"]["name"] = name
    if description is not None:
        payload["properties"]["description"] = description
    if owners is not None:
        payload["ownership"] = {"owners": [{"owner": owner} for owner in owners]}
    if tags is not None:
        payload["tags"] = {"tags": [{"tag": tag} for tag in tags]}
    if domain is not None:
        payload["domain"] = {"domain": domain}
    return payload


class FakeMcpState:
    """Mutable server state a test can inspect and steer."""

    def __init__(self) -> None:
        self.tools: list[str] = list(DEFAULT_TOOLS)
        self.entities: dict[str, dict[str, Any]] = {}
        self.lineage: dict[str, list[str]] = {}
        #: URNs whose downstream lineage answers ``searchResults: null`` instead of a list. Any
        #: non-dataset URN does so by default, matching the live instance; adding a dataset here
        #: forces the same shape onto it.
        self.null_downstreams: set[str] = set()
        #: URNs whose downstream lineage answers with ``facets``/``total`` and **no**
        #: ``searchResults`` key — what an unindexed graph service returns for an entity that
        #: does have lineage. Takes precedence over :attr:`null_downstreams`.
        self.facet_only_downstreams: set[str] = set()
        self.schema_fields: dict[str, list[dict[str, str]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.auth_headers: list[str | None] = []
        self.respond_with_sse = False
        self.fail_tools: set[str] = set()
        self.reject_unauthenticated = False
        #: Tools that answer 200 with a payload in a shape this project does not accept, so the
        #: fail-closed behaviour can be tested against a real response rather than a mock.
        self.malformed_tools: dict[str, Any] = {}
        #: When true, respond 500 with the received Authorization header echoed into the body.
        #: Simulates a badly-behaved upstream that reflects credentials in an error, so the
        #: client's redaction can be tested against a real response.
        self.leak_auth_in_error = False

    def add_entity(self, urn: str, **kwargs: Any) -> dict[str, Any]:
        self.entities[urn] = entity_payload(urn, **kwargs)
        return self.entities[urn]

    def soft_delete(self, urn: str) -> None:
        """Mark an existing entity removed, the way a soft delete leaves it."""
        entity = self.entities[urn]
        self.entities[urn] = {**entity, "status": {"removed": True}}

    def description_of(self, urn: str) -> str | None:
        entity = self.entities.get(urn, {})
        return entity.get("properties", {}).get("description")

    def descriptions(self) -> dict[str, str | None]:
        return {urn: self.description_of(urn) for urn in self.entities}


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

                try:
                    payload = _dispatch(state, name, arguments)
                except ContractViolation as exc:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32602, "message": str(exc)},
                        }
                    )
                    return

                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(payload)}],
                            "structuredContent": payload,
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


def _require_arguments(name: str, arguments: dict[str, Any]) -> None:
    """Reject any call that does not match the coordinator-observed argument contract."""
    expected = REQUIRED_ARGUMENTS.get(name)
    if expected is None:
        return
    received = set(arguments)
    if missing := sorted(expected - received):
        raise ContractViolation(
            f"{name} requires argument(s) {missing}; received {sorted(received)}."
        )
    if unexpected := sorted(received - expected):
        raise ContractViolation(
            f"{name} does not accept argument(s) {unexpected}; contract is {sorted(expected)}."
        )


def _answers_null_downstreams(state: FakeMcpState, urn: str) -> bool:
    """Whether this URN's downstream lineage comes back as ``null`` rather than a list.

    Mirrors the live instance: a dashboard is a lineage sink and its downstream query answers
    ``searchResults: null``. Datasets answer with a list, empty or otherwise. Tests can force the
    shape onto any URN via ``state.null_downstreams``.
    """
    if urn in state.null_downstreams:
        return True
    return not urn.startswith("urn:li:dataset:")


def _dispatch(state: FakeMcpState, name: str, arguments: dict[str, Any]) -> Any:
    if name in state.malformed_tools:
        return state.malformed_tools[name]

    _require_arguments(name, arguments)

    if name == "get_entities":
        urns = arguments["urns"]
        if not isinstance(urns, list):
            raise ContractViolation("get_entities 'urns' must be a list.")
        return {"result": [state.entities[urn] for urn in urns if urn in state.entities]}

    if name == "list_schema_fields":
        return {"fields": list(state.schema_fields.get(arguments["urn"], []))}

    if name == "get_lineage":
        urn = arguments["urn"]
        upstream = arguments["upstream"]
        if upstream:
            related = [u for u, downs in state.lineage.items() if urn in downs]
            key = "upstreams"
        else:
            if urn in state.facet_only_downstreams:
                # The second live shape: a result envelope with `facets` and `total` and no
                # `searchResults` key at all. This is what an *unindexed* graph service returns
                # for a dataset that does have lineage, so the double must be able to produce it.
                return {
                    "downstreams": {
                        "total": 0,
                        "facets": [
                            {
                                "displayName": "Degree",
                                "aggregations": [
                                    {"count": 0, "value": "1"},
                                    {"count": 0, "value": "2"},
                                    {"count": 0, "value": "3+"},
                                ],
                            }
                        ],
                    }
                }
            if _answers_null_downstreams(state, urn):
                # What the live instance actually returns for a lineage sink: a successful
                # response whose nullable searchResults list is null, not an empty list. The
                # double emitted `[]` unconditionally, which is why the suite passed while
                # `/api/graph` failed against the real server.
                return {"downstreams": {"searchResults": None}}
            related = list(state.lineage.get(urn, []))
            key = "downstreams"
        limited = related[: int(arguments["max_results"])]
        return {key: {"searchResults": [{"entity": {"urn": u}} for u in limited]}}

    if name == "update_description":
        urn = arguments["entity_urn"]
        operation = arguments["operation"]
        if urn in state.entities:
            entity = state.entities[urn]
            properties = dict(entity.get("properties", {}))
            properties["description"] = arguments["description"]
            state.entities[urn] = {**entity, "properties": properties}
        return {"result": {"status": "ok", "urn": urn, "operation": operation}}

    if name == "add_tags":
        return {"result": {"status": "ok"}}

    return {"result": {}}


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
