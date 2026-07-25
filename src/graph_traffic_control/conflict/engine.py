"""The conflict matrix.

Implements ``PROJECT_BRIEF.md`` rows 1-8. Decisions are deterministic and depend only on the
proposals and the graph snapshot: no model output participates, per ``AGENTS.md``.

| # | Case                                    | Decision |
|---|-----------------------------------------|----------|
| 1 | Same target write/write                 | BLOCK    |
| 2 | One writes what another reads           | ORDER    |
| 3 | Upstream schema write vs downstream read| REBASE   |
| 4 | Two read-only proposals                 | allow    |
| 5 | Disjoint lineage branches               | allow    |
| 6 | Shared domain, no lineage intersection  | WARN     |
| 7 | Stale expected version                  | handled at prepare/commit, not here |
| 8 | High blast radius                       | approval, not a conflict |

Row 6 is the false-positive guard. Two proposals in the same domain with no lineage path between
them must not block each other, or the coordinator degenerates into a global lock.
"""

from __future__ import annotations

from graph_traffic_control.conflict.lineage import (
    DEFAULT_MAX_DEPTH,
    build_graph,
    shortest_lineage_path,
)
from graph_traffic_control.domain.models import (
    ChangeProposal,
    Conflict,
    ConflictDecision,
    ConflictKind,
    Criticality,
    GraphSnapshot,
)

#: Blast radius at or above which a write requires human approval (matrix row 8).
APPROVAL_BLAST_RADIUS = 2


def _fields_for(proposal: ChangeProposal, urn: str) -> set[str]:
    """Column paths a proposal writes on a given URN, if it declared any."""
    paths = {ref.field_path for ref in proposal.write_fields if ref.urn == urn}
    if proposal.action.target_urn == urn and proposal.action.field_path:
        paths.add(proposal.action.field_path)
    return {p for p in paths if p}


def _read_fields_for(proposal: ChangeProposal, urn: str) -> set[str]:
    return {ref.field_path for ref in proposal.read_fields if ref.urn == urn and ref.field_path}


def detect_conflicts(
    proposal: ChangeProposal,
    other: ChangeProposal,
    snapshot: GraphSnapshot,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[Conflict]:
    """Every conflict between two proposals, most severe first.

    ``proposal`` is the one being evaluated; ``other`` is an already-active proposal.
    """
    if proposal.proposal_id == other.proposal_id:
        return []

    conflicts: list[Conflict] = []
    graph = build_graph(snapshot)

    proposal_writes = set(proposal.write_set)
    other_writes = set(other.write_set)
    other_reads = set(other.read_set)

    # Row 1: same target write/write.
    for urn in sorted(proposal_writes & other_writes):
        our_fields = _fields_for(proposal, urn)
        their_fields = _fields_for(other, urn)
        # Column-level refinement: distinct columns on one table are not a hard conflict.
        if our_fields and their_fields and not (our_fields & their_fields):
            conflicts.append(
                Conflict(
                    kind=ConflictKind.WRITE_WRITE,
                    decision=ConflictDecision.WARN,
                    proposal_id=proposal.proposal_id,
                    other_proposal_id=other.proposal_id,
                    subject_urn=urn,
                    explanation=(
                        f"Both proposals write {urn} but touch disjoint columns "
                        f"({sorted(our_fields)} vs {sorted(their_fields)}). Allowed with a warning."
                    ),
                )
            )
            continue
        overlap = sorted(our_fields & their_fields)
        conflicts.append(
            Conflict(
                kind=ConflictKind.WRITE_WRITE,
                decision=ConflictDecision.BLOCK,
                proposal_id=proposal.proposal_id,
                other_proposal_id=other.proposal_id,
                subject_urn=urn,
                field_path=overlap[0] if overlap else None,
                explanation=(
                    f"Both proposals write {urn}"
                    + (f" column {overlap[0]}" if overlap else "")
                    + ". Direct write/write collision; one must wait."
                ),
            )
        )

    # Row 2: this proposal writes something the other reads directly.
    for urn in sorted(proposal_writes & other_reads):
        conflicts.append(
            Conflict(
                kind=ConflictKind.WRITE_READ,
                decision=ConflictDecision.ORDER,
                proposal_id=proposal.proposal_id,
                other_proposal_id=other.proposal_id,
                subject_urn=urn,
                explanation=(
                    f"{proposal.proposal_id} writes {urn}, which {other.proposal_id} reads. "
                    "The two must be ordered."
                ),
            )
        )

    # Row 3: lineage-mediated. This proposal's write reaches something the other reads,
    # through the graph, even though the declared sets do not intersect.
    for write_urn in sorted(proposal_writes):
        for read_urn in sorted(other_reads | other_writes):
            if write_urn == read_urn:
                continue  # already covered by rows 1 and 2
            path = shortest_lineage_path(graph, write_urn, read_urn)
            if len(path) < 2 or len(path) - 1 > max_depth:
                continue

            field = None
            if proposal.action.kind == "rename_column" and proposal.action.field_path:
                field = proposal.action.field_path

            conflicts.append(
                Conflict(
                    kind=ConflictKind.UPSTREAM_SCHEMA,
                    decision=ConflictDecision.REBASE,
                    proposal_id=proposal.proposal_id,
                    other_proposal_id=other.proposal_id,
                    subject_urn=read_urn,
                    field_path=field,
                    lineage_path=path,
                    explanation=(
                        f"{proposal.proposal_id} writes {write_urn}"
                        + (f" (renaming column {field})" if field else "")
                        + f", which reaches {read_urn} used by {other.proposal_id} through "
                        f"{len(path) - 1} lineage hop(s): {' -> '.join(path)}. "
                        "The declared read and write sets do not intersect; only the graph "
                        "reveals this conflict."
                    ),
                )
            )

    # Row 6: shared domain but no lineage intersection. Warn, never block.
    if not conflicts and (proposal_writes or other_writes):
        conflicts.extend(_shared_domain_warning(proposal, other, snapshot, graph, max_depth))

    order = {
        ConflictDecision.BLOCK: 0,
        ConflictDecision.ORDER: 1,
        ConflictDecision.REBASE: 2,
        ConflictDecision.WARN: 3,
    }
    conflicts.sort(key=lambda c: order[c.decision])
    return conflicts


def _shared_domain_warning(
    proposal: ChangeProposal,
    other: ChangeProposal,
    snapshot: GraphSnapshot,
    graph,  # noqa: ANN001 - networkx DiGraph
    max_depth: int,
) -> list[Conflict]:
    """Row 6. Both proposals sit in the project's domain but no path connects them."""
    for write_urn in proposal.write_set:
        for other_urn in [*other.read_set, *other.write_set]:
            if shortest_lineage_path(graph, write_urn, other_urn):
                return []
            if shortest_lineage_path(graph, other_urn, write_urn):
                return []

    known = set(snapshot.entities)
    if not (set(proposal.write_set) & known and set(other.write_set) & known):
        return []

    return [
        Conflict(
            kind=ConflictKind.SHARED_DOMAIN,
            decision=ConflictDecision.WARN,
            proposal_id=proposal.proposal_id,
            other_proposal_id=other.proposal_id,
            subject_urn=proposal.write_set[0],
            explanation=(
                f"{proposal.proposal_id} and {other.proposal_id} operate in the same domain but "
                f"no lineage path within {max_depth} hops connects them. Not blocking."
            ),
        )
    ]


def requires_approval(blast_radius: int, max_criticality: Criticality) -> bool:
    """Matrix row 8. High blast radius or a TIER_1 asset in the impact set needs a human."""
    return blast_radius >= APPROVAL_BLAST_RADIUS or max_criticality is Criticality.TIER_1


def blocking_conflicts(conflicts: list[Conflict]) -> list[Conflict]:
    return [c for c in conflicts if c.blocking]
