"""Context provider interface.

Everything the coordinator knows about the graph arrives through this interface, so the
fixture-backed and DataHub-backed implementations are interchangeable and a fixture can never be
silently mistaken for the live integration (ADR-001).

Providers are read-only. Writeback lives in :mod:`graph_traffic_control.writeback`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from graph_traffic_control.domain.models import GraphSnapshot


class ContextReadError(RuntimeError):
    """The project's graph could not be read.

    Raised instead of returning a partial or empty snapshot. An empty graph is indistinguishable
    from a graph with no conflicts, so a read failure that degraded to one would silently convert
    "we do not know" into "it is safe to commit". Callers must treat this as fail-closed.
    """


@runtime_checkable
class ContextProvider(Protocol):
    #: Human-readable provenance, surfaced in receipts and readiness so evidence always states
    #: whether it came from live DataHub or a recorded fixture.
    source: str

    def snapshot(self) -> GraphSnapshot:
        """Read the project's graph. Must not mutate anything."""
        ...
