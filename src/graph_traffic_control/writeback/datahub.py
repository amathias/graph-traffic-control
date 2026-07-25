"""One reversible, namespace-scoped DataHub writeback.

The coordinator's integration rulings permit tag, description, and structured-property
proposals, but only after a smoke test against pinned DataHub Core v1.6.0 confirms the selected
aspect. This module therefore uses ``update_description``: the most widely supported mutable
aspect, and one whose previous value can be captured and restored exactly.

The sequence is deliberately conservative:

1. **Capture** the current description via a read tool.
2. **Write** the coordination outcome.
3. **Immediately re-read** and compare, so the receipt records verification rather than an
   assumption that the write landed.
4. **Restore** the captured value.

Restoration runs in a ``finally`` block: if verification fails, the original value is still put
back. A writeback that cannot be restored is reported as unrestored in the receipt rather than
being silently left in place.

Every target URN passes the namespace guard first, so this can never write to another
submission's entity.
"""

from __future__ import annotations

from graph_traffic_control.context.datahub import TOOL_UPDATE_DESCRIPTION, extract_description
from graph_traffic_control.context.mcp_client import McpClient, McpError
from graph_traffic_control.context.namespace import Namespace
from graph_traffic_control.domain.clock import Clock, SystemClock
from graph_traffic_control.domain.models import WritebackReceipt

TOOL_GET_ENTITIES = "get_entities"


class WritebackError(RuntimeError):
    """Raised when a writeback cannot be attempted at all."""


class ReversibleDescriptionWriteback:
    """Writes a coordination outcome to a dataset description, then restores it."""

    aspect = "description"

    def __init__(
        self,
        client: McpClient,
        namespace: Namespace,
        clock: Clock | None = None,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._clock = clock or SystemClock()

    def _read_description(self, urn: str) -> str | None:
        payload = self._client.call_tool(TOOL_GET_ENTITIES, {"urns": [urn]})
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("urn") == urn:
                return extract_description(candidate)
        for candidate in candidates:
            if isinstance(candidate, dict):
                return extract_description(candidate)
        return None

    def _write_description(self, urn: str, description: str) -> None:
        self._client.call_tool(
            TOOL_UPDATE_DESCRIPTION, {"urn": urn, "description": description}
        )

    def apply(self, urn: str, outcome_note: str) -> WritebackReceipt:
        """Perform the capture/write/re-read/restore cycle and return a receipt.

        The receipt records what was actually observed. ``verified`` is only true when the
        re-read returned the written value.
        """
        self._namespace.require(urn, operation="DataHub writeback")

        try:
            previous = self._read_description(urn)
        except McpError as exc:
            raise WritebackError(f"Could not capture the current description: {exc}") from None

        written_at = self._clock.now()
        reread: str | None = None
        verified = False
        restored = False
        restored_value: str | None = None
        detail = ""

        try:
            self._write_description(urn, outcome_note)
            reread = self._read_description(urn)
            verified = reread == outcome_note
            if not verified:
                detail = "Re-read did not return the written value."
        except McpError as exc:
            detail = f"Writeback failed: {exc}"
        finally:
            # Restore even when verification failed, so the shared instance is left as found.
            try:
                self._write_description(urn, previous or "")
                restored_value = self._read_description(urn)
                restored = (restored_value or None) == (previous or None)
                if not restored:
                    detail = (detail + " Restoration could not be confirmed.").strip()
            except McpError as exc:
                restored = False
                detail = (detail + f" Restoration failed: {exc}").strip()

        return WritebackReceipt(
            entity_urn=urn,
            aspect=self.aspect,
            previous_value=previous,
            written_value=outcome_note,
            reread_value=reread,
            verified=verified,
            restored=restored,
            restored_value=restored_value,
            written_at=written_at,
            detail=detail,
        )
