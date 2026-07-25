"""Minimal Model Context Protocol client over Streamable HTTP.

Talks to the coordinator-hosted DataHub MCP bridge at ``DATAHUB_MCP_URL``
(``mcp-server-datahub`` 0.6.0 behind ``mcp-proxy`` 0.12.0, per ``../COORDINATOR_DECISIONS.md``
ADR-004). The endpoint is private; this client never assumes a public route and never hardcodes
a port.

Only the parts of MCP this project needs are implemented: ``initialize``, the
``notifications/initialized`` acknowledgement, ``tools/list``, and ``tools/call``. A Streamable
HTTP endpoint may answer with either ``application/json`` or an SSE stream, so both are handled.

Security notes:
- The bearer token is read from settings and attached per request. It is never logged, never
  included in exception messages, and never written to a receipt.
- :class:`McpError` messages are sanitised before they propagate.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "graph-traffic-control", "version": "0.1.0"}


class McpError(RuntimeError):
    """An MCP transport or protocol failure, with any secret material removed."""


def _redact(text: str, secret: str | None) -> str:
    if secret and secret in text:
        text = text.replace(secret, "***redacted***")
    return text


def _parse_sse(body: str) -> dict[str, Any]:
    """Extract the first JSON-RPC payload from an SSE response body."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            payload = stripped[len("data:") :].strip()
            if payload and payload != "[DONE]":
                return json.loads(payload)
    raise McpError("MCP endpoint returned an SSE stream containing no data payload")


class McpClient:
    """Synchronous MCP client. One instance per logical session."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not endpoint:
            raise McpError("DATAHUB_MCP_URL is not configured")
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._session_id: str | None = None
        self._initialized = False
        self._request_id = 0

    # -- lifecycle ---------------------------------------------------------------------

    def __enter__(self) -> McpClient:
        self.initialize()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- transport ---------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            return self._client.post(
                self._endpoint,
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise McpError(
                f"MCP request failed: {_redact(str(exc), self._token)}"
            ) from None

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        response = self._post(payload)

        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

        if response.status_code >= 400:
            raise McpError(
                f"MCP {method} returned HTTP {response.status_code}: "
                f"{_redact(response.text[:400], self._token)}"
            )

        content_type = response.headers.get("content-type", "")
        try:
            body = (
                _parse_sse(response.text)
                if "text/event-stream" in content_type
                else response.json()
            )
        except json.JSONDecodeError as exc:
            raise McpError(f"MCP {method} returned unparseable body: {exc}") from None

        if "error" in body:
            error = body["error"]
            raise McpError(
                f"MCP {method} error {error.get('code')}: "
                f"{_redact(str(error.get('message', '')), self._token)}"
            )
        result = body.get("result")
        if not isinstance(result, dict):
            raise McpError(f"MCP {method} returned no result object")
        return result

    def _notify(self, method: str) -> None:
        payload = {"jsonrpc": JSONRPC_VERSION, "method": method}
        response = self._post(payload)
        # Notifications legitimately answer 202 Accepted with an empty body.
        if response.status_code >= 400:
            raise McpError(f"MCP notification {method} returned HTTP {response.status_code}")

    # -- protocol ----------------------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return {}
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self._notify("notifications/initialized")
        self._initialized = True
        return result

    def list_tools(self) -> list[str]:
        """Names of every tool the server exposes."""
        self.initialize()
        result = self._rpc("tools/list")
        tools = result.get("tools", [])
        return [tool["name"] for tool in tools if isinstance(tool, dict) and "name" in tool]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool and return its decoded payload.

        MCP returns tool output as a content list. DataHub's tools return JSON in a text block,
        so a text payload that parses as JSON is decoded; otherwise the raw text is returned.
        """
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})

        if result.get("isError"):
            raise McpError(f"MCP tool {name} reported an error: {result.get('content')}")

        if "structuredContent" in result:
            return result["structuredContent"]

        content = result.get("content", [])
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if not texts:
            return result
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined
