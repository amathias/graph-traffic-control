"""SQLite-backed proposal, lease, and audit store.

Every state transition appends an immutable event, so the transaction history is reconstructable
and the demo's audit timeline is real rather than rendered from memory.

Concurrency: leases are acquired inside an ``IMMEDIATE`` transaction so two proposals racing for
the same URN cannot both win. SQLite serialises writers, which is sufficient for one coordinator
process; a distributed lease backend is explicitly out of MVP scope (``PROJECT_BRIEF.md``).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from graph_traffic_control.domain.models import (
    ChangeProposal,
    Lease,
    PreparedToken,
    TransactionEvent,
    TransactionState,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    state       TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id    TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    urns        TEXT NOT NULL,
    granted_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    released_at TEXT
);

CREATE TABLE IF NOT EXISTS prepared_tokens (
    token       TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    payload     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    sequence    INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    actor       TEXT NOT NULL,
    at          TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_proposal ON events(proposal_id);
CREATE INDEX IF NOT EXISTS idx_leases_proposal ON leases(proposal_id);
"""


class TransactionStore:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(SCHEMA)

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit IMMEDIATE transaction so lease races are resolved by the database."""
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    # -- proposals ---------------------------------------------------------------------

    def save_proposal(
        self, proposal: ChangeProposal, state: TransactionState, now: datetime
    ) -> None:
        payload = proposal.model_dump_json()
        self._connection.execute(
            """
            INSERT INTO proposals (proposal_id, agent_id, state, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(proposal_id) DO UPDATE SET
                state = excluded.state,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                proposal.proposal_id,
                proposal.agent.agent_id,
                state.value,
                payload,
                now.isoformat(),
                now.isoformat(),
            ),
        )

    def set_state(self, proposal_id: str, state: TransactionState, now: datetime) -> None:
        self._connection.execute(
            "UPDATE proposals SET state = ?, updated_at = ? WHERE proposal_id = ?",
            (state.value, now.isoformat(), proposal_id),
        )

    def get_state(self, proposal_id: str) -> TransactionState | None:
        row = self._connection.execute(
            "SELECT state FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return TransactionState(row["state"]) if row else None

    def get_proposal(self, proposal_id: str) -> ChangeProposal | None:
        row = self._connection.execute(
            "SELECT payload FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return ChangeProposal.model_validate_json(row["payload"]) if row else None

    def list_proposals(self) -> list[tuple[ChangeProposal, TransactionState]]:
        rows = self._connection.execute(
            "SELECT payload, state FROM proposals ORDER BY created_at, proposal_id"
        ).fetchall()
        return [
            (ChangeProposal.model_validate_json(row["payload"]), TransactionState(row["state"]))
            for row in rows
        ]

    def list_active_proposals(self) -> list[ChangeProposal]:
        """Proposals still in flight: anything not in a terminal state."""
        rows = self._connection.execute(
            """
            SELECT payload FROM proposals
            WHERE state NOT IN ('COMMITTED', 'ABORTED', 'EXPIRED')
            ORDER BY created_at, proposal_id
            """
        ).fetchall()
        return [ChangeProposal.model_validate_json(row["payload"]) for row in rows]

    # -- leases ------------------------------------------------------------------------

    def insert_lease(self, lease: Lease, connection: sqlite3.Connection | None = None) -> None:
        (connection or self._connection).execute(
            """
            INSERT INTO leases
                (lease_id, proposal_id, agent_id, urns, granted_at, expires_at, released_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lease.lease_id,
                lease.proposal_id,
                lease.agent_id,
                json.dumps(sorted(lease.urns)),
                lease.granted_at.isoformat(),
                lease.expires_at.isoformat(),
                lease.released_at.isoformat() if lease.released_at else None,
            ),
        )

    def _row_to_lease(self, row: sqlite3.Row) -> Lease:
        return Lease(
            lease_id=row["lease_id"],
            proposal_id=row["proposal_id"],
            agent_id=row["agent_id"],
            urns=json.loads(row["urns"]),
            granted_at=datetime.fromisoformat(row["granted_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            released_at=(
                datetime.fromisoformat(row["released_at"]) if row["released_at"] else None
            ),
        )

    def get_lease(self, lease_id: str) -> Lease | None:
        row = self._connection.execute(
            "SELECT * FROM leases WHERE lease_id = ?", (lease_id,)
        ).fetchone()
        return self._row_to_lease(row) if row else None

    def list_leases(self, connection: sqlite3.Connection | None = None) -> list[Lease]:
        rows = (connection or self._connection).execute(
            "SELECT * FROM leases ORDER BY granted_at, lease_id"
        ).fetchall()
        return [self._row_to_lease(row) for row in rows]

    def release_lease(self, lease_id: str, now: datetime) -> None:
        self._connection.execute(
            "UPDATE leases SET released_at = ? WHERE lease_id = ? AND released_at IS NULL",
            (now.isoformat(), lease_id),
        )

    # -- prepared tokens ---------------------------------------------------------------
    #
    # Tokens are persisted rather than held in process memory. A prepared transaction must
    # survive across requests (and across a restart) for commit to be possible at all, and
    # persisting them makes the capability registry auditable rather than invisible.

    def save_token(self, token: PreparedToken) -> None:
        self._connection.execute(
            """
            INSERT INTO prepared_tokens (token, proposal_id, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET payload = excluded.payload
            """,
            (token.token, token.proposal_id, token.model_dump_json()),
        )

    def get_token(self, token: str) -> PreparedToken | None:
        row = self._connection.execute(
            "SELECT payload FROM prepared_tokens WHERE token = ?", (token,)
        ).fetchone()
        return PreparedToken.model_validate_json(row["payload"]) if row else None

    # -- events ------------------------------------------------------------------------

    def append_event(
        self,
        proposal_id: str,
        agent_id: str,
        from_state: TransactionState | None,
        to_state: TransactionState,
        actor: str,
        at: datetime,
        detail: str = "",
        evidence: dict[str, str] | None = None,
    ) -> TransactionEvent:
        cursor = self._connection.execute(
            """
            INSERT INTO events
                (proposal_id, agent_id, from_state, to_state, actor, at, detail, evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                agent_id,
                from_state.value if from_state else None,
                to_state.value,
                actor,
                at.isoformat(),
                detail,
                json.dumps(evidence or {}, sort_keys=True),
            ),
        )
        return TransactionEvent(
            sequence=int(cursor.lastrowid or 0),
            proposal_id=proposal_id,
            agent_id=agent_id,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            at=at,
            detail=detail,
            evidence=evidence or {},
        )

    def list_events(self, proposal_id: str | None = None) -> list[TransactionEvent]:
        if proposal_id:
            rows = self._connection.execute(
                "SELECT * FROM events WHERE proposal_id = ? ORDER BY sequence", (proposal_id,)
            ).fetchall()
        else:
            rows = self._connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        return [
            TransactionEvent(
                sequence=row["sequence"],
                proposal_id=row["proposal_id"],
                agent_id=row["agent_id"],
                from_state=(
                    TransactionState(row["from_state"]) if row["from_state"] else None
                ),
                to_state=TransactionState(row["to_state"]),
                actor=row["actor"],
                at=datetime.fromisoformat(row["at"]),
                detail=row["detail"],
                evidence=json.loads(row["evidence"]),
            )
            for row in rows
        ]
