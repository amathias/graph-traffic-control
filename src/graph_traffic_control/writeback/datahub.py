"""One reversible, namespace-scoped DataHub writeback.

The coordinator's integration rulings permit tag, description, and structured-property
proposals. This module uses ``update_description``: the most widely supported mutable aspect, and
one whose previous value can be captured and restored exactly.

Tool contract (coordinator-observed)
------------------------------------
``update_description`` takes ``entity_urn``, ``description``, and ``operation``. The capture and
re-read legs use ``get_entities`` with ``urns``, reading ``structuredContent.result``, and the
description is nested under the entity's ``properties``.

The sequence is deliberately conservative:

1. **Capture** the current description via a read tool.
2. **Write** the coordination outcome.
3. **Immediately re-read** and compare, so the receipt records verification rather than an
   assumption that the write landed.
4. **Restore** the captured value.

Restoration runs in a ``finally`` block: if verification fails, the original value is still put
back. Each leg is tracked **independently** on the receipt — a write can be verified while its
restoration failed, and the receipt must say so rather than collapsing both into one flag.

Every target URN passes the namespace guard first, so this can never write to another
submission's entity. Every failure raises or is recorded; nothing is swallowed.
"""

from __future__ import annotations

from graph_traffic_control.context.datahub import (
    TOOL_GET_ENTITIES,
    TOOL_UPDATE_DESCRIPTION,
    entities_from_result,
    extract_description,
)
from graph_traffic_control.context.mcp_client import McpClient, McpError
from graph_traffic_control.context.namespace import Namespace
from graph_traffic_control.domain.clock import Clock, SystemClock
from graph_traffic_control.domain.models import WritebackReceipt

#: ``update_description`` requires an ``operation``. ``replace`` is the replace-in-place semantic
#: the capture/write/re-read/restore cycle depends on: an append-style operation could not restore
#: the original value exactly.
#:
#: **This value is now live-confirmed.** It was previously ``SET``, guessed from the aspect
#: vocabulary because the coordinator supplied the argument *names* but not this *value*. Live
#: DataHub 1.6.0 **rejected** ``SET``; the same reversible write/re-read/restore cycle then
#: succeeded with ``replace``. It stays settings-driven via ``DATAHUB_DESCRIPTION_OPERATION`` so a
#: future server can be accommodated without a code change, but the default is no longer a guess.
DEFAULT_DESCRIPTION_OPERATION = "replace"


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
        operation: str = DEFAULT_DESCRIPTION_OPERATION,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._clock = clock or SystemClock()
        self._operation = operation

    @property
    def operation(self) -> str:
        return self._operation

    def _read_description(self, urn: str) -> str | None:
        payload = self._client.call_tool_structured(TOOL_GET_ENTITIES, {"urns": [urn]})
        entity = entities_from_result(payload, [urn])[urn]
        return extract_description(entity)

    def _write_description(self, urn: str, description: str) -> None:
        self._client.call_tool_structured(
            TOOL_UPDATE_DESCRIPTION,
            {
                "entity_urn": urn,
                "description": description,
                "operation": self._operation,
            },
        )

    def apply(self, urn: str, outcome_note: str) -> WritebackReceipt:
        """Perform the capture/write/re-read/restore cycle and return a receipt.

        The receipt records what was actually observed. ``verified`` is true only when the
        re-read returned the written value; ``restored`` is true only when a further re-read
        confirmed the original value is back. They are independent: neither implies the other.
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
        restoration_attempted = False
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
            restoration_attempted = True
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
            operation=self._operation,
            previous_value=previous,
            written_value=outcome_note,
            reread_value=reread,
            verified=verified,
            restoration_attempted=restoration_attempted,
            restored=restored,
            restored_value=restored_value,
            written_at=written_at,
            detail=detail,
        )
