"""Strict proposal, conflict, lease, and transaction contracts.

``AGENTS.md`` requires structured change proposals carrying agent identity, read set, write set,
expected versions, intent, and evidence, and requires that malformed proposals are rejected before
any graph analysis. Every model here forbids unknown fields so a malformed or drifting client
fails loudly rather than having input silently ignored.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------------------
# Identity and intent
# --------------------------------------------------------------------------------------


class AgentIdentity(Strict):
    agent_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=list)


class Criticality(StrEnum):
    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"
    UNKNOWN = "UNKNOWN"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FieldRef(Strict):
    """A dataset URN plus an optional column, for column-level conflict detection."""

    urn: str = Field(min_length=1)
    field_path: str | None = None


# --------------------------------------------------------------------------------------
# The change a proposal wants to make
# --------------------------------------------------------------------------------------


class ChangeAction(Strict):
    """The concrete, executable change.

    ``artifact_path`` is relative to the demo state root and is the SQL file the executor
    rewrites. Absolute paths and parent traversal are rejected so a proposal cannot direct a
    write outside the project's disposable state.
    """

    kind: Literal["rename_column", "redefine_metric", "update_model"]
    target_urn: str = Field(min_length=1)
    artifact_path: str = Field(min_length=1)
    field_path: str | None = None
    new_field_path: str | None = None

    @field_validator("artifact_path")
    @classmethod
    def _reject_escaping_paths(cls, value: str) -> str:
        normalised = value.replace("\\", "/")
        if normalised.startswith("/") or ".." in normalised.split("/"):
            raise ValueError(
                "artifact_path must be relative to the state root and must not traverse upward"
            )
        if len(normalised) > 1 and normalised[1] == ":":
            raise ValueError("artifact_path must not be an absolute Windows path")
        return value

    @model_validator(mode="after")
    def _rename_requires_both_field_paths(self) -> ChangeAction:
        if self.kind == "rename_column" and not (self.field_path and self.new_field_path):
            raise ValueError("rename_column requires field_path and new_field_path")
        return self


class ValidationPlan(Strict):
    """What the validator must prove after the change is applied."""

    expect_artifact_contains: list[str] = Field(default_factory=list)
    expect_artifact_absent: list[str] = Field(default_factory=list)
    expect_downstream_resolvable: bool = True


class RiskDeclaration(Strict):
    level: RiskLevel = RiskLevel.LOW
    blast_radius_hint: int = Field(default=0, ge=0)
    requires_approval: bool = False


class ChangeProposal(Strict):
    """A structured agent change request. Rejected before graph analysis if malformed."""

    proposal_id: str = Field(min_length=1, max_length=64)
    agent: AgentIdentity
    intent: str = Field(min_length=1, max_length=500)

    read_set: list[str] = Field(default_factory=list)
    write_set: list[str] = Field(min_length=1)
    read_fields: list[FieldRef] = Field(default_factory=list)
    write_fields: list[FieldRef] = Field(default_factory=list)

    expected_versions: dict[str, str] = Field(default_factory=dict)

    action: ChangeAction
    validation_plan: ValidationPlan = Field(default_factory=ValidationPlan)
    requested_lease_seconds: int = Field(default=120, ge=1, le=3600)
    risk: RiskDeclaration = Field(default_factory=RiskDeclaration)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _action_target_must_be_declared(self) -> ChangeProposal:
        if self.action.target_urn not in self.write_set:
            raise ValueError(
                f"action.target_urn {self.action.target_urn!r} is not in the declared write_set. "
                "A proposal may not mutate an asset it did not declare."
            )
        return self

    @property
    def all_urns(self) -> list[str]:
        seen: dict[str, None] = {}
        for urn in [*self.read_set, *self.write_set, self.action.target_urn]:
            seen.setdefault(urn, None)
        for ref in [*self.read_fields, *self.write_fields]:
            seen.setdefault(ref.urn, None)
        return list(seen)


# --------------------------------------------------------------------------------------
# Graph context
# --------------------------------------------------------------------------------------


class SchemaField(Strict):
    path: str
    type: str = "unknown"


class EntityContext(Strict):
    """Governance context for one entity, as read from DataHub or the recorded fixture."""

    urn: str
    name: str
    description: str | None = None
    criticality: Criticality = Criticality.UNKNOWN
    owners: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    domain: str | None = None
    fields: list[SchemaField] = Field(default_factory=list)

    def version_fingerprint(self) -> str:
        """Stable fingerprint of the parts a proposal can be stale against.

        Schema, criticality, ownership, tags, and domain are all included: a governance change
        under a proposal's feet is real drift, and a proposal that prepared against the old
        ownership or tier must be rechecked rather than committed.

        The description is deliberately excluded. The reversible writeback rewrites and restores
        it during commit, so including it would make every commit look like drift to the next
        proposal.
        """
        from hashlib import sha256

        payload = "|".join(
            [
                self.urn,
                self.criticality.value,
                ",".join(sorted(f"{f.path}:{f.type}" for f in self.fields)),
                ",".join(sorted(self.owners)),
                ",".join(sorted(self.tags)),
                self.domain or "",
            ]
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:16]


class LineageEdge(Strict):
    upstream: str
    downstream: str


class GraphSnapshot(Strict):
    """An immutable read of the project's graph, taken at prepare and again before commit."""

    entities: dict[str, EntityContext]
    edges: list[LineageEdge]
    captured_at: datetime

    def fingerprint(self) -> str:
        """Fingerprint of the whole snapshot. Any drift changes this value."""
        from hashlib import sha256

        entity_part = ",".join(
            f"{urn}={self.entities[urn].version_fingerprint()}" for urn in sorted(self.entities)
        )
        edge_part = ",".join(sorted(f"{e.upstream}->{e.downstream}" for e in self.edges))
        return sha256(f"{entity_part}|{edge_part}".encode()).hexdigest()[:16]

    def subgraph_fingerprint(self, urns: list[str]) -> str:
        """Fingerprint restricted to the entities a specific proposal depends on.

        Commit-time drift detection uses this so an unrelated change elsewhere in the project
        graph does not spuriously abort a safe commit.
        """
        from hashlib import sha256

        relevant = sorted(set(urns))
        entity_part = ",".join(
            f"{urn}={self.entities[urn].version_fingerprint()}"
            for urn in relevant
            if urn in self.entities
        )
        edge_part = ",".join(
            sorted(
                f"{e.upstream}->{e.downstream}"
                for e in self.edges
                if e.upstream in relevant or e.downstream in relevant
            )
        )
        return sha256(f"{entity_part}|{edge_part}".encode()).hexdigest()[:16]


# --------------------------------------------------------------------------------------
# Conflicts
# --------------------------------------------------------------------------------------


class ConflictKind(StrEnum):
    WRITE_WRITE = "WRITE_WRITE"
    WRITE_READ = "WRITE_READ"
    UPSTREAM_SCHEMA = "UPSTREAM_SCHEMA"
    SHARED_DOMAIN = "SHARED_DOMAIN"


class ConflictDecision(StrEnum):
    BLOCK = "BLOCK"
    ORDER = "ORDER"
    REBASE = "REBASE"
    WARN = "WARN"


class Conflict(Strict):
    kind: ConflictKind
    decision: ConflictDecision
    proposal_id: str
    other_proposal_id: str
    subject_urn: str
    field_path: str | None = None
    lineage_path: list[str] = Field(default_factory=list)
    explanation: str

    @property
    def blocking(self) -> bool:
        return self.decision in {
            ConflictDecision.BLOCK,
            ConflictDecision.ORDER,
            ConflictDecision.REBASE,
        }


class ImpactSet(Strict):
    """A proposal's declared sets expanded through lineage within the policy depth."""

    declared_reads: list[str]
    declared_writes: list[str]
    expanded_downstream: list[str]
    expanded_upstream: list[str]
    blast_radius: int
    max_criticality: Criticality


# --------------------------------------------------------------------------------------
# Leases and prepared transactions
# --------------------------------------------------------------------------------------


class Lease(Strict):
    lease_id: str
    proposal_id: str
    agent_id: str
    urns: list[str]
    granted_at: datetime
    expires_at: datetime
    released_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return self.released_at is None and now < self.expires_at

    def is_expired(self, now: datetime) -> bool:
        return self.released_at is None and now >= self.expires_at


class PreparedToken(Strict):
    token: str
    proposal_id: str
    lease_id: str
    snapshot_fingerprint: str
    subgraph_fingerprint: str
    guarded_urns: list[str]
    prepared_at: datetime
    expires_at: datetime
    conditions: list[str] = Field(default_factory=list)
    approval_required: bool = False
    approved_by: str | None = None


# --------------------------------------------------------------------------------------
# Transaction state and events
# --------------------------------------------------------------------------------------


class TransactionState(StrEnum):
    SUBMITTED = "SUBMITTED"
    ANALYZING = "ANALYZING"
    BLOCKED = "BLOCKED"
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"


TERMINAL_STATES = frozenset(
    {TransactionState.COMMITTED, TransactionState.ABORTED, TransactionState.EXPIRED}
)


class TransactionEvent(Strict):
    sequence: int
    proposal_id: str
    agent_id: str
    from_state: TransactionState | None
    to_state: TransactionState
    actor: str
    at: datetime
    detail: str = ""
    evidence: dict[str, str] = Field(default_factory=dict)


class CommitVerification(Strict):
    """What a commit actually proved, one independently tracked signal per step.

    ``AGENTS.md`` requires that committed changes are *verified* and the result recorded. A
    single boolean cannot carry that: "the executor returned without raising" is not "the file on
    disk now holds the new content", and "the writeback call succeeded" is not "DataHub returned
    the written value". Each step below is therefore observed separately and recorded separately,
    so a receipt says which ones actually happened.

    :meth:`commit_permitted` is the gate. A proposal may not reach ``COMMITTED`` unless the
    artifact mutation is confirmed by a re-read and, when a writeback was attempted, that
    writeback was confirmed by a re-read too.
    """

    #: The executor reported that it applied the change.
    mutation_applied: bool = False
    #: The artifact was re-read from disk and holds exactly the expected content.
    mutation_reread_verified: bool = False
    #: The validator passed against the re-read artifact and the fresh graph.
    validation_passed: bool = False
    #: A DataHub writeback was attempted at all (false in fixture mode).
    writeback_attempted: bool = False
    #: The writeback was re-read from DataHub and returned the written value.
    writeback_verified: bool = False
    #: The writeback's original value was restored and confirmed. Independent of ``verified``.
    writeback_restored: bool = False
    #: The artifact was restored to its pre-execution content after a failure.
    artifact_rolled_back: bool = False
    #: Receipt filenames written for this commit. Tracked separately: a commit is not evidenced
    #: merely because it happened.
    receipts: list[str] = Field(default_factory=list)
    #: Human-readable note for whichever step failed.
    detail: str = ""

    def commit_permitted(self) -> bool:
        if not (self.mutation_applied and self.mutation_reread_verified):
            return False
        if not self.validation_passed:
            return False
        if self.writeback_attempted and not self.writeback_verified:
            return False
        return True


class WritebackReceipt(Strict):
    """Evidence of one reversible DataHub writeback, including its restoration.

    ``verified`` and ``restored`` are tracked independently and neither implies the other: a
    write can land and be confirmed while its restoration fails, and a write can fail while the
    entity is confirmed untouched. Collapsing them would hide which of the two actually happened.
    """

    entity_urn: str
    aspect: str
    operation: str = ""
    previous_value: str | None
    written_value: str
    reread_value: str | None
    #: The write landed and an immediate re-read returned exactly the written value.
    verified: bool
    #: A restoration was attempted, regardless of whether it succeeded.
    restoration_attempted: bool = False
    #: A re-read after restoration returned the captured original value.
    restored: bool
    restored_value: str | None = None
    written_at: datetime
    detail: str = ""
