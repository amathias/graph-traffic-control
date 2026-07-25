"""Lease expiry and contention.

``AGENTS.md`` requires that abandoned work cannot block the system forever. Every test here
advances an injected clock rather than sleeping, so the suite is fast and never flaky.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.demo.agents import FCT_REVENUE, FCT_SUPPORT_SLA, proposal_a, proposal_c
from graph_traffic_control.txn.leases import LeaseConflict, LeaseManager


@pytest.fixture
def leases(store, clock) -> LeaseManager:
    return LeaseManager(store, clock)


class TestAcquisition:
    def test_lease_is_granted_and_live(self, leases):
        lease = leases.acquire(proposal_a(), [FCT_REVENUE])
        assert leases.is_live(lease.lease_id)
        assert leases.holder_of(FCT_REVENUE).lease_id == lease.lease_id

    def test_second_proposal_cannot_take_a_held_urn(self, leases):
        leases.acquire(proposal_a(), [FCT_REVENUE])
        with pytest.raises(LeaseConflict) as exc:
            leases.acquire(proposal_c(), [FCT_REVENUE])
        assert exc.value.urn == FCT_REVENUE

    def test_disjoint_urns_are_granted_concurrently(self, leases):
        """Agent C must never wait behind Agent A."""
        first = leases.acquire(proposal_a(), [FCT_REVENUE])
        second = leases.acquire(proposal_c(), [FCT_SUPPORT_SLA])
        assert leases.is_live(first.lease_id)
        assert leases.is_live(second.lease_id)
        assert len(leases.active_leases()) == 2

    def test_reacquiring_own_lease_is_allowed(self, leases):
        proposal = proposal_a()
        leases.acquire(proposal, [FCT_REVENUE])
        again = leases.acquire(proposal, [FCT_REVENUE])
        assert again.proposal_id == proposal.proposal_id


class TestExpiry:
    def test_lease_expires_without_anything_sweeping_it(self, leases, clock):
        proposal = proposal_a()  # requests 120s
        lease = leases.acquire(proposal, [FCT_REVENUE])
        assert leases.is_live(lease.lease_id)

        clock.advance(121)

        assert not leases.is_live(lease.lease_id)
        assert lease.lease_id in {expired.lease_id for expired in leases.expired_leases()}
        assert leases.active_leases() == []

    def test_expired_lease_does_not_block_a_new_acquisition(self, leases, clock):
        leases.acquire(proposal_a(), [FCT_REVENUE])
        clock.advance(121)
        # An abandoned agent must not strand the URN forever.
        replacement = leases.acquire(proposal_c(), [FCT_REVENUE])
        assert leases.is_live(replacement.lease_id)

    def test_expiry_boundary_is_exclusive(self, leases, clock):
        lease = leases.acquire(proposal_a(), [FCT_REVENUE])
        clock.advance(119)
        assert leases.is_live(lease.lease_id)
        clock.advance(1)  # exactly at expires_at
        assert not leases.is_live(lease.lease_id)

    def test_seconds_remaining_decreases_and_floors_at_zero(self, leases, clock):
        lease = leases.acquire(proposal_a(), [FCT_REVENUE])
        assert leases.seconds_remaining(lease.lease_id) == pytest.approx(120, abs=1)
        clock.advance(60)
        assert leases.seconds_remaining(lease.lease_id) == pytest.approx(60, abs=1)
        clock.advance(999)
        assert leases.seconds_remaining(lease.lease_id) == 0.0

    def test_holder_of_ignores_expired_leases(self, leases, clock):
        leases.acquire(proposal_a(), [FCT_REVENUE])
        clock.advance(121)
        assert leases.holder_of(FCT_REVENUE) is None


class TestRelease:
    def test_release_frees_the_urn_immediately(self, leases):
        lease = leases.acquire(proposal_a(), [FCT_REVENUE])
        leases.release(lease.lease_id)
        assert not leases.is_live(lease.lease_id)
        assert leases.holder_of(FCT_REVENUE) is None
        leases.acquire(proposal_c(), [FCT_REVENUE])  # must not raise

    def test_release_is_idempotent(self, leases):
        lease = leases.acquire(proposal_a(), [FCT_REVENUE])
        leases.release(lease.lease_id)
        leases.release(lease.lease_id)
        assert not leases.is_live(lease.lease_id)

    def test_released_lease_reports_no_time_remaining(self, leases):
        lease = leases.acquire(proposal_a(), [FCT_REVENUE])
        leases.release(lease.lease_id)
        assert leases.seconds_remaining(lease.lease_id) == 0.0

    def test_unknown_lease_is_not_live(self, leases):
        assert not leases.is_live("lease-does-not-exist")
