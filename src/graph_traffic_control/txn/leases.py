"""Expiring leases over graph entities.

``AGENTS.md`` requires that abandoned work cannot block the system forever. A lease is granted for
a bounded window and is treated as released the instant it expires, whether or not anything
explicitly released it. Nothing sweeps or reaps: expiry is evaluated from the injected clock at
read time, so an abandoned agent cannot strand a URN even if the coordinator never notices.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from graph_traffic_control.domain.clock import Clock
from graph_traffic_control.domain.models import ChangeProposal, Lease
from graph_traffic_control.txn.store import TransactionStore


class LeaseConflict(RuntimeError):
    """Raised when a requested URN is already held by a live lease."""

    def __init__(self, urn: str, holder: Lease) -> None:
        super().__init__(
            f"{urn} is leased by proposal {holder.proposal_id} until "
            f"{holder.expires_at.isoformat()}"
        )
        self.urn = urn
        self.holder = holder


class LeaseManager:
    def __init__(self, store: TransactionStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def active_leases(self) -> list[Lease]:
        now = self._clock.now()
        return [lease for lease in self._store.list_leases() if lease.is_active(now)]

    def expired_leases(self) -> list[Lease]:
        now = self._clock.now()
        return [lease for lease in self._store.list_leases() if lease.is_expired(now)]

    def holder_of(self, urn: str) -> Lease | None:
        for lease in self.active_leases():
            if urn in lease.urns:
                return lease
        return None

    def acquire(self, proposal: ChangeProposal, urns: list[str]) -> Lease:
        """Grant a lease over ``urns``, or raise :class:`LeaseConflict`.

        Acquisition happens inside an IMMEDIATE transaction and re-checks holders inside that
        transaction, so two concurrent prepares cannot both be granted the same URN.
        """
        now = self._clock.now()
        wanted = sorted(set(urns))

        with self._store.write_transaction() as connection:
            for lease in self._store.list_leases(connection):
                if not lease.is_active(now):
                    continue
                if lease.proposal_id == proposal.proposal_id:
                    continue
                for urn in wanted:
                    if urn in lease.urns:
                        raise LeaseConflict(urn, lease)

            lease = Lease(
                lease_id=f"lease-{uuid.uuid4().hex[:12]}",
                proposal_id=proposal.proposal_id,
                agent_id=proposal.agent.agent_id,
                urns=wanted,
                granted_at=now,
                expires_at=now + timedelta(seconds=proposal.requested_lease_seconds),
            )
            self._store.insert_lease(lease, connection)
            return lease

    def release(self, lease_id: str) -> None:
        self._store.release_lease(lease_id, self._clock.now())

    def is_live(self, lease_id: str) -> bool:
        lease = self._store.get_lease(lease_id)
        return bool(lease and lease.is_active(self._clock.now()))

    def seconds_remaining(self, lease_id: str) -> float:
        lease = self._store.get_lease(lease_id)
        if lease is None or lease.released_at is not None:
            return 0.0
        delta: timedelta = lease.expires_at - self._clock.now()
        return max(0.0, delta.total_seconds())

    def now(self) -> datetime:
        return self._clock.now()
