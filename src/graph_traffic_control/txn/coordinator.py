"""The semantic two-phase commit coordinator.

Prepare
-------
Validate the proposal, guard its namespace, read a graph snapshot, verify expected versions,
expand impact through lineage, compute conflicts against every other in-flight proposal, decide
whether approval is required, acquire leases, and issue a prepared token carrying the fingerprint
of the subgraph the proposal depends on.

Commit
------
Re-read the graph, recompute the subgraph fingerprint, and **fail closed on any drift**. Only then
execute the change, validate it, optionally perform the reversible DataHub writeback, and record
the outcome. A validation failure rolls the artifact back and aborts.

Determinism
-----------
Every decision here is a pure function of the proposals, the snapshot, and the injected clock.
No model output participates in conflict or commit decisions (``AGENTS.md``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from graph_traffic_control.conflict.engine import (
    blocking_conflicts,
    detect_conflicts,
    requires_approval,
)
from graph_traffic_control.conflict.lineage import DEFAULT_MAX_DEPTH, expand_impact
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.context.provider import ContextProvider, ContextReadError
from graph_traffic_control.domain.clock import Clock
from graph_traffic_control.domain.models import (
    ChangeProposal,
    Conflict,
    Criticality,
    GraphSnapshot,
    ImpactSet,
    Lease,
    PreparedToken,
    TransactionState,
    WritebackReceipt,
)
from graph_traffic_control.domain.states import is_terminal, require_transition
from graph_traffic_control.execute.targets import ArtifactExecutor, ExecutionError
from graph_traffic_control.execute.validator import Validator
from graph_traffic_control.txn.leases import LeaseConflict, LeaseManager
from graph_traffic_control.txn.store import TransactionStore

COORDINATOR_ACTOR = "coordinator"
PREPARE_TOKEN_TTL_SECONDS = 300

#: Reported when preparation aborts before any impact could be computed. Deliberately empty
#: rather than fabricated: no graph was readable, so nothing is known about the blast radius.
EMPTY_IMPACT = ImpactSet(
    declared_reads=[],
    declared_writes=[],
    expanded_downstream=[],
    expanded_upstream=[],
    blast_radius=0,
    max_criticality=Criticality.UNKNOWN,
)


class CoordinatorError(RuntimeError):
    """A coordination failure that is the caller's fault, not a conflict."""


@dataclass
class PrepareOutcome:
    proposal_id: str
    state: TransactionState
    impact: ImpactSet
    conflicts: list[Conflict] = field(default_factory=list)
    token: PreparedToken | None = None
    lease: Lease | None = None
    reason: str = ""

    @property
    def prepared(self) -> bool:
        return self.state is TransactionState.PREPARED


@dataclass
class CommitOutcome:
    proposal_id: str
    state: TransactionState
    reason: str = ""
    artifact_diff: str | None = None
    validation: dict[str, str] | None = None
    writeback: WritebackReceipt | None = None
    prepare_fingerprint: str = ""
    commit_fingerprint: str = ""

    @property
    def committed(self) -> bool:
        return self.state is TransactionState.COMMITTED

    @property
    def drift_detected(self) -> bool:
        return bool(
            self.prepare_fingerprint
            and self.commit_fingerprint
            and self.prepare_fingerprint != self.commit_fingerprint
        )


class Coordinator:
    def __init__(
        self,
        store: TransactionStore,
        provider: ContextProvider,
        namespace: Namespace,
        clock: Clock,
        executor: ArtifactExecutor,
        validator: Validator | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        downstream_artifacts: dict[str, str] | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._namespace = namespace
        self._clock = clock
        self._executor = executor
        self._validator = validator or Validator(executor)
        self._leases = LeaseManager(store, clock)
        self._max_depth = max_depth
        self._downstream_artifacts = downstream_artifacts or {}

    def token(self, token: str) -> PreparedToken | None:
        """Look up a persisted prepared token."""
        return self._store.get_token(token)

    @property
    def leases(self) -> LeaseManager:
        return self._leases

    @property
    def context_source(self) -> str:
        return self._provider.source

    # -- helpers -----------------------------------------------------------------------

    def _transition(
        self,
        proposal: ChangeProposal,
        to_state: TransactionState,
        detail: str = "",
        evidence: dict[str, str] | None = None,
    ) -> None:
        current = self._store.get_state(proposal.proposal_id)
        if current is not None:
            require_transition(current, to_state)
        self._store.set_state(proposal.proposal_id, to_state, self._clock.now())
        self._store.append_event(
            proposal_id=proposal.proposal_id,
            agent_id=proposal.agent.agent_id,
            from_state=current,
            to_state=to_state,
            actor=COORDINATOR_ACTOR,
            at=self._clock.now(),
            detail=detail,
            evidence=evidence or {},
        )

    def _guard_namespace(self, proposal: ChangeProposal) -> None:
        self._namespace.require_all(proposal.all_urns, operation="Proposal submission")

    def _stale_urns(self, proposal: ChangeProposal, snapshot: GraphSnapshot) -> list[str]:
        """URNs whose declared expected version no longer matches the graph."""
        stale: list[str] = []
        for urn, expected in proposal.expected_versions.items():
            entity = snapshot.entities.get(urn)
            if entity is None or entity.version_fingerprint() != expected:
                stale.append(urn)
        return sorted(stale)

    # -- prepare -----------------------------------------------------------------------

    def submit(self, proposal: ChangeProposal) -> None:
        """Record a proposal. Malformed input has already been rejected by the model."""
        try:
            self._guard_namespace(proposal)
        except NamespaceViolation:
            self._store.save_proposal(proposal, TransactionState.SUBMITTED, self._clock.now())
            self._store.append_event(
                proposal_id=proposal.proposal_id,
                agent_id=proposal.agent.agent_id,
                from_state=None,
                to_state=TransactionState.SUBMITTED,
                actor=COORDINATOR_ACTOR,
                at=self._clock.now(),
                detail="submitted",
            )
            self._transition(
                proposal,
                TransactionState.ABORTED,
                detail="Proposal targets entities outside the traffic. allocation.",
            )
            raise

        self._store.save_proposal(proposal, TransactionState.SUBMITTED, self._clock.now())
        self._store.append_event(
            proposal_id=proposal.proposal_id,
            agent_id=proposal.agent.agent_id,
            from_state=None,
            to_state=TransactionState.SUBMITTED,
            actor=COORDINATOR_ACTOR,
            at=self._clock.now(),
            detail="submitted",
        )

    def prepare(self, proposal: ChangeProposal) -> PrepareOutcome:
        existing = self._store.get_state(proposal.proposal_id)

        # Proposal IDs are the client's idempotency key. Re-using one that already reached a
        # terminal state is a client error, not an illegal transition to blow up on.
        if existing is not None and is_terminal(existing):
            raise CoordinatorError(
                f"Proposal {proposal.proposal_id!r} already reached {existing.value}. "
                "Submit a new proposal id rather than reusing a terminal one."
            )

        if existing is None:
            self.submit(proposal)

        self._transition(proposal, TransactionState.ANALYZING, detail="analyzing")

        # A graph the coordinator cannot read is not a graph with no conflicts. Abort rather
        # than analyse against a partial or empty snapshot.
        try:
            snapshot = self._provider.snapshot()
        except ContextReadError as exc:
            reason = f"Graph context unavailable: {exc}"
            self._transition(
                proposal,
                TransactionState.ABORTED,
                detail=reason,
                evidence={"fail_closed": "context_read"},
            )
            return PrepareOutcome(
                proposal_id=proposal.proposal_id,
                state=TransactionState.ABORTED,
                impact=EMPTY_IMPACT,
                reason=reason,
            )

        # Row 7: stale expected versions abort preparation.
        stale = self._stale_urns(proposal, snapshot)
        if stale:
            reason = f"Stale expected version for {', '.join(stale)}"
            impact = expand_impact(
                snapshot, proposal.read_set, proposal.write_set, self._max_depth
            )
            self._transition(
                proposal,
                TransactionState.ABORTED,
                detail=reason,
                evidence={"stale_urns": ",".join(stale)},
            )
            return PrepareOutcome(
                proposal_id=proposal.proposal_id,
                state=TransactionState.ABORTED,
                impact=impact,
                reason=reason,
            )

        impact = expand_impact(snapshot, proposal.read_set, proposal.write_set, self._max_depth)

        conflicts: list[Conflict] = []
        for other in self._store.list_active_proposals():
            if other.proposal_id == proposal.proposal_id:
                continue
            conflicts.extend(detect_conflicts(proposal, other, snapshot, self._max_depth))
            conflicts.extend(detect_conflicts(other, proposal, snapshot, self._max_depth))

        blocking = blocking_conflicts(conflicts)
        if blocking:
            first = blocking[0]
            reason = first.explanation
            self._transition(
                proposal,
                TransactionState.BLOCKED,
                detail=reason,
                evidence={
                    "conflict_kind": first.kind.value,
                    "conflict_decision": first.decision.value,
                    "lineage_path": " -> ".join(first.lineage_path),
                    "other_proposal": first.other_proposal_id,
                },
            )
            return PrepareOutcome(
                proposal_id=proposal.proposal_id,
                state=TransactionState.BLOCKED,
                impact=impact,
                conflicts=conflicts,
                reason=reason,
            )

        try:
            lease = self._leases.acquire(proposal, proposal.write_set)
        except LeaseConflict as exc:
            self._transition(proposal, TransactionState.BLOCKED, detail=str(exc))
            return PrepareOutcome(
                proposal_id=proposal.proposal_id,
                state=TransactionState.BLOCKED,
                impact=impact,
                conflicts=conflicts,
                reason=str(exc),
            )

        approval_required = (
            requires_approval(impact.blast_radius, impact.max_criticality)
            or proposal.risk.requires_approval
        )

        now = self._clock.now()
        guarded = sorted(set(proposal.all_urns) | set(impact.expanded_downstream))
        token = PreparedToken(
            token=f"prepared-{uuid.uuid4().hex[:16]}",
            proposal_id=proposal.proposal_id,
            lease_id=lease.lease_id,
            snapshot_fingerprint=snapshot.fingerprint(),
            subgraph_fingerprint=snapshot.subgraph_fingerprint(guarded),
            guarded_urns=guarded,
            prepared_at=now,
            expires_at=now + timedelta(seconds=PREPARE_TOKEN_TTL_SECONDS),
            conditions=[c.explanation for c in conflicts],
            approval_required=approval_required,
        )
        self._store.save_token(token)

        self._transition(
            proposal,
            TransactionState.PREPARED,
            detail="prepared",
            evidence={
                "lease_id": lease.lease_id,
                "subgraph_fingerprint": token.subgraph_fingerprint,
                "blast_radius": str(impact.blast_radius),
                "approval_required": str(approval_required).lower(),
                "context_source": self._provider.source,
            },
        )
        return PrepareOutcome(
            proposal_id=proposal.proposal_id,
            state=TransactionState.PREPARED,
            impact=impact,
            conflicts=conflicts,
            token=token,
            lease=lease,
        )

    def approve(self, token: PreparedToken, approver: str) -> PreparedToken:
        """Record human approval for a high-blast-radius change."""
        stored = self._store.get_token(token.token)
        if stored is None:
            raise CoordinatorError("Unknown prepared token")
        stored.approved_by = approver
        self._store.save_token(stored)
        return stored

    # -- commit ------------------------------------------------------------------------

    def commit(
        self,
        proposal: ChangeProposal,
        token: PreparedToken,
        writeback=None,  # noqa: ANN001 - ReversibleDescriptionWriteback | None
    ) -> CommitOutcome:
        state = self._store.get_state(proposal.proposal_id)

        # Idempotency: committing twice returns the original outcome rather than erroring.
        if state is TransactionState.COMMITTED:
            return CommitOutcome(
                proposal_id=proposal.proposal_id,
                state=TransactionState.COMMITTED,
                reason="already committed",
                prepare_fingerprint=token.subgraph_fingerprint,
                commit_fingerprint=token.subgraph_fingerprint,
            )
        if state in {TransactionState.ABORTED, TransactionState.EXPIRED}:
            return CommitOutcome(
                proposal_id=proposal.proposal_id,
                state=state,
                reason=f"already {state.value.lower()}",
            )
        if state is not TransactionState.PREPARED:
            raise CoordinatorError(
                f"Cannot commit from state {state.value if state else 'UNKNOWN'}"
            )

        stored = self._store.get_token(token.token)
        if stored is None or stored.proposal_id != proposal.proposal_id:
            return self._abort(proposal, "Invalid or unknown prepared token")

        now = self._clock.now()

        if now >= stored.expires_at:
            return self._expire(proposal, "Prepared token expired before commit")

        if not self._leases.is_live(stored.lease_id):
            return self._expire(proposal, "Lease expired before commit")

        if stored.approval_required and not stored.approved_by:
            return self._abort(
                proposal,
                "High-blast-radius change requires approval, which was not granted",
            )

        # Re-read the graph immediately before commit and fail closed on drift. A failed re-read
        # is itself fail-closed: without it there is no way to know the graph has not drifted.
        try:
            fresh = self._provider.snapshot()
        except ContextReadError as exc:
            return self._abort(
                proposal,
                f"Pre-commit graph re-read failed: {exc}",
                evidence={"fail_closed": "context_read"},
            )
        commit_fingerprint = fresh.subgraph_fingerprint(stored.guarded_urns)
        if commit_fingerprint != stored.subgraph_fingerprint:
            outcome = self._abort(
                proposal,
                "Graph drifted between prepare and commit; the proposal is stale.",
                evidence={
                    "fingerprint_at_prepare": stored.subgraph_fingerprint,
                    "fingerprint_at_commit": commit_fingerprint,
                },
            )
            outcome.prepare_fingerprint = stored.subgraph_fingerprint
            outcome.commit_fingerprint = commit_fingerprint
            return outcome

        stale = self._stale_urns(proposal, fresh)
        if stale:
            outcome = self._abort(
                proposal, f"Expected version no longer matches for {', '.join(stale)}"
            )
            outcome.prepare_fingerprint = stored.subgraph_fingerprint
            outcome.commit_fingerprint = commit_fingerprint
            return outcome

        self._transition(proposal, TransactionState.EXECUTING, detail="executing")
        try:
            execution = self._executor.apply(proposal.action)
        except ExecutionError as exc:
            outcome = self._abort(proposal, f"Execution failed: {exc}")
            outcome.prepare_fingerprint = stored.subgraph_fingerprint
            outcome.commit_fingerprint = commit_fingerprint
            return outcome

        self._transition(proposal, TransactionState.VALIDATING, detail="validating")
        validation = self._validator.validate(proposal, fresh, self._downstream_artifacts)
        if not validation.passed:
            self._executor.rollback(execution)
            outcome = self._abort(
                proposal,
                f"Validation failed: {'; '.join(validation.failures)}",
                evidence=validation.as_evidence(),
            )
            outcome.artifact_diff = "rolled back"
            outcome.validation = validation.as_evidence()
            outcome.prepare_fingerprint = stored.subgraph_fingerprint
            outcome.commit_fingerprint = commit_fingerprint
            return outcome

        writeback_receipt: WritebackReceipt | None = None
        if writeback is not None:
            note = (
                f"Graph Traffic Control committed {proposal.proposal_id} "
                f"by {proposal.agent.agent_id}: {proposal.intent}"
            )
            writeback_receipt = writeback.apply(proposal.action.target_urn, note)

        evidence = {
            "artifact": str(execution.artifact_path.name),
            "diff": execution.diff_summary,
            "context_source": self._provider.source,
            "fingerprint_at_commit": commit_fingerprint,
            **validation.as_evidence(),
        }
        if writeback_receipt is not None:
            evidence["writeback_verified"] = str(writeback_receipt.verified).lower()
            evidence["writeback_restored"] = str(writeback_receipt.restored).lower()

        self._transition(
            proposal, TransactionState.COMMITTED, detail="committed", evidence=evidence
        )
        self._leases.release(stored.lease_id)

        return CommitOutcome(
            proposal_id=proposal.proposal_id,
            state=TransactionState.COMMITTED,
            artifact_diff=execution.diff_summary,
            validation=validation.as_evidence(),
            writeback=writeback_receipt,
            prepare_fingerprint=stored.subgraph_fingerprint,
            commit_fingerprint=commit_fingerprint,
        )

    # -- abort / expire ----------------------------------------------------------------

    def _release_lease_for(self, proposal_id: str) -> None:
        for lease in self._leases.active_leases():
            if lease.proposal_id == proposal_id:
                self._leases.release(lease.lease_id)

    def _abort(
        self,
        proposal: ChangeProposal,
        reason: str,
        evidence: dict[str, str] | None = None,
    ) -> CommitOutcome:
        self._transition(proposal, TransactionState.ABORTED, detail=reason, evidence=evidence)
        self._release_lease_for(proposal.proposal_id)
        return CommitOutcome(
            proposal_id=proposal.proposal_id, state=TransactionState.ABORTED, reason=reason
        )

    def _expire(self, proposal: ChangeProposal, reason: str) -> CommitOutcome:
        self._transition(proposal, TransactionState.EXPIRED, detail=reason)
        self._release_lease_for(proposal.proposal_id)
        return CommitOutcome(
            proposal_id=proposal.proposal_id, state=TransactionState.EXPIRED, reason=reason
        )

    def abort(self, proposal: ChangeProposal, reason: str) -> CommitOutcome:
        """Idempotent public abort."""
        state = self._store.get_state(proposal.proposal_id)
        if state in {TransactionState.ABORTED, TransactionState.EXPIRED}:
            return CommitOutcome(
                proposal_id=proposal.proposal_id, state=state, reason="already terminal"
            )
        if state is TransactionState.COMMITTED:
            return CommitOutcome(
                proposal_id=proposal.proposal_id,
                state=TransactionState.COMMITTED,
                reason="already committed",
            )
        return self._abort(proposal, reason)

    def reanalyze(self, proposal: ChangeProposal) -> PrepareOutcome:
        """Retry a blocked proposal after the proposal ahead of it reached a terminal state."""
        state = self._store.get_state(proposal.proposal_id)
        if state is not TransactionState.BLOCKED:
            raise CoordinatorError(f"Only BLOCKED proposals can be re-analysed, not {state}")
        return self.prepare(proposal)
