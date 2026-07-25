"""Transaction state machine.

``AGENTS.md`` requires that every state transition is audited and that commit operations are
idempotent. This module owns the legal-transition table; the coordinator owns orchestration.

Terminal states are absorbing. Re-entering a terminal state from itself is permitted and is how
idempotent commit and abort retries are expressed: a repeated commit is a no-op that returns the
original outcome rather than an error.
"""

from __future__ import annotations

from graph_traffic_control.domain.models import TERMINAL_STATES, TransactionState

S = TransactionState

LEGAL_TRANSITIONS: dict[TransactionState, frozenset[TransactionState]] = {
    S.SUBMITTED: frozenset({S.ANALYZING, S.ABORTED}),
    S.ANALYZING: frozenset({S.PREPARED, S.BLOCKED, S.ABORTED}),
    # A blocked proposal can be re-analysed once the proposal ahead of it commits.
    S.BLOCKED: frozenset({S.ANALYZING, S.ABORTED, S.EXPIRED}),
    S.PREPARED: frozenset({S.EXECUTING, S.ABORTED, S.EXPIRED}),
    S.EXECUTING: frozenset({S.VALIDATING, S.ABORTED}),
    S.VALIDATING: frozenset({S.COMMITTED, S.ABORTED}),
    S.COMMITTED: frozenset({S.COMMITTED}),
    S.ABORTED: frozenset({S.ABORTED}),
    S.EXPIRED: frozenset({S.EXPIRED}),
}


class IllegalTransition(RuntimeError):
    """Raised when a caller attempts a transition the state machine does not permit."""

    def __init__(self, from_state: TransactionState, to_state: TransactionState) -> None:
        super().__init__(
            f"Illegal transition {from_state.value} -> {to_state.value}. "
            f"Legal targets: {sorted(s.value for s in LEGAL_TRANSITIONS[from_state])}"
        )
        self.from_state = from_state
        self.to_state = to_state


def can_transition(from_state: TransactionState, to_state: TransactionState) -> bool:
    return to_state in LEGAL_TRANSITIONS[from_state]


def require_transition(from_state: TransactionState, to_state: TransactionState) -> None:
    if not can_transition(from_state, to_state):
        raise IllegalTransition(from_state, to_state)


def is_terminal(state: TransactionState) -> bool:
    return state in TERMINAL_STATES
