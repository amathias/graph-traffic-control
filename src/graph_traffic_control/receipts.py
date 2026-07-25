"""Sanitized proposal, lease, and commit receipts.

Receipts are the judge-facing evidence trail. They are written to the disposable state directory,
which is git-ignored, because they are runtime evidence rather than source (``AGENTS.md`` forbids
committing runtime receipts).

Sanitisation is mandatory and belt-and-braces:

- Receipts are assembled from typed models, so a secret can only appear if something explicitly
  puts it there.
- :func:`sanitize` then walks the assembled structure and redacts any value that matches a known
  secret or a key that looks secret-bearing.

Both layers exist because the second is the one that still works after someone adds a field to a
model without thinking about it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from graph_traffic_control.domain.models import (
    ChangeProposal,
    Conflict,
    ImpactSet,
    Lease,
    PreparedToken,
    TransactionEvent,
    TransactionState,
    WritebackReceipt,
)

RECEIPTS_DIRNAME = "receipts"

#: Keys whose values are always redacted, regardless of content.
SECRET_KEY_PATTERN = re.compile(
    r"(token|secret|password|authorization|api[_-]?key|credential|bearer)", re.IGNORECASE
)

#: Keys exempt from redaction even though they mention a secret-bearing word. A ``*_fingerprint``
#: key holds a one-way digest, which is the whole reason it exists: it links a receipt to a
#: capability without being usable as one. Kept deliberately narrow.
SAFE_KEY_PATTERN = re.compile(r"_fingerprint$", re.IGNORECASE)

REDACTED = "***redacted***"


def sanitize(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Recursively redact secret-bearing keys and any literal secret values."""
    live_secrets = tuple(s for s in secrets if s)

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if (
                isinstance(key, str)
                and SECRET_KEY_PATTERN.search(key)
                and not SAFE_KEY_PATTERN.search(key)
            ):
                cleaned[key] = REDACTED
            else:
                cleaned[key] = sanitize(item, live_secrets)
        return cleaned
    if isinstance(value, list):
        return [sanitize(item, live_secrets) for item in value]
    if isinstance(value, str):
        for secret in live_secrets:
            if secret in value:
                value = value.replace(secret, REDACTED)
        return value
    return value


def token_fingerprint(token: str) -> str:
    """One-way fingerprint of a capability token, safe to persist as evidence."""
    from hashlib import sha256

    return sha256(token.encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


class ReceiptWriter:
    """Writes sanitized receipts as deterministic JSON under the state directory."""

    def __init__(self, state_dir: Path, secrets: tuple[str, ...] = ()) -> None:
        self._dir = Path(state_dir) / RECEIPTS_DIRNAME
        self._secrets = tuple(s for s in secrets if s)

    @property
    def directory(self) -> Path:
        return self._dir

    def _write(self, name: str, payload: dict[str, Any]) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        clean = sanitize(payload, self._secrets)
        path = self._dir / name
        path.write_text(
            json.dumps(clean, indent=2, sort_keys=True, default=_jsonable) + "\n",
            encoding="utf-8",
        )
        return path

    def proposal_receipt(
        self,
        proposal: ChangeProposal,
        impact: ImpactSet,
        conflicts: list[Conflict],
        state: TransactionState,
        context_source: str,
        snapshot_fingerprint: str,
    ) -> Path:
        return self._write(
            f"proposal-{proposal.proposal_id}.json",
            {
                "kind": "proposal",
                "proposal_id": proposal.proposal_id,
                "agent_id": proposal.agent.agent_id,
                "intent": proposal.intent,
                "state": state.value,
                "context_source": context_source,
                "snapshot_fingerprint": snapshot_fingerprint,
                "read_set": proposal.read_set,
                "write_set": proposal.write_set,
                "expected_versions": proposal.expected_versions,
                "declared_evidence": proposal.evidence,
                "impact": _jsonable(impact),
                "conflicts": [_jsonable(c) for c in conflicts],
            },
        )

    def lease_receipt(self, lease: Lease, token: PreparedToken) -> Path:
        return self._write(
            f"lease-{lease.lease_id}.json",
            {
                "kind": "lease",
                "lease_id": lease.lease_id,
                "proposal_id": lease.proposal_id,
                "agent_id": lease.agent_id,
                "urns": lease.urns,
                "granted_at": lease.granted_at,
                "expires_at": lease.expires_at,
                # The prepared token is a capability: holding it permits a commit. Receipts
                # record a one-way fingerprint so the lease can still be tied to its commit
                # without the file itself being usable to authorise one.
                "prepared_token_fingerprint": token_fingerprint(token.token),
                "snapshot_fingerprint": token.snapshot_fingerprint,
                "subgraph_fingerprint": token.subgraph_fingerprint,
                "approval_required": token.approval_required,
                "conditions": token.conditions,
            },
        )

    def commit_receipt(
        self,
        proposal: ChangeProposal,
        final_state: TransactionState,
        events: list[TransactionEvent],
        context_source: str,
        prepare_fingerprint: str,
        commit_fingerprint: str,
        artifact_diff: str | None = None,
        validation: dict[str, str] | None = None,
        writeback: WritebackReceipt | None = None,
        abort_reason: str | None = None,
    ) -> Path:
        return self._write(
            f"commit-{proposal.proposal_id}.json",
            {
                "kind": "commit",
                "proposal_id": proposal.proposal_id,
                "agent_id": proposal.agent.agent_id,
                "final_state": final_state.value,
                "context_source": context_source,
                "graph_fingerprint_at_prepare": prepare_fingerprint,
                "graph_fingerprint_at_commit": commit_fingerprint,
                "drift_detected": prepare_fingerprint != commit_fingerprint,
                "artifact_diff": artifact_diff,
                "validation": validation,
                "writeback": _jsonable(writeback) if writeback else None,
                "abort_reason": abort_reason,
                "events": [_jsonable(event) for event in events],
            },
        )
