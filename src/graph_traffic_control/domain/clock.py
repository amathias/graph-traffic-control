"""Injectable clock.

Lease expiry, prepare-token lifetime, and event ordering all depend on time. Tests must be able
to advance time without sleeping: ``AGENTS.md`` requires that the demo never depend on
uncontrolled concurrent timing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Production clock. Always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Test clock. Time only moves when a test moves it."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now
