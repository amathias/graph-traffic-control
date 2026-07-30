# Demo and Submission Guide: Graph Traffic Control

## Devpost short description

Graph Traffic Control is a DataHub-powered coordinator for autonomous data agents. Agents submit structured read/write proposals; the coordinator expands their semantic impact through lineage, detects hidden conflicts, permits unrelated work in parallel, enforces a prepare/commit-or-abort lifecycle, validates changes, and records the result for future agents.

The public recording uses the clearly labeled isolated fixture scenario. Live DataHub context
loading and reversible outcome writeback were verified separately and should be described as
separate evidence.

## Three-minute demo target

Aim for **2 minutes 35 seconds to 2 minutes 45 seconds**.

### 0:00–0:18 — Introduce three agents

Show Agent A, B, and C proposals together.

> These agents touch different files, so ordinary worktree coordination says they are safe. Semantically, two of them collide.

### 0:18–0:50 — DataHub exposes the collision

Show explicit read/write sets, the fixture graph expansion, and the shortest path connecting A's
upstream rename to B's downstream metric.

> The scenario uses the same isolated graph contract that the live provider loads from DataHub:
> lineage, schemas, versions, ownership, and criticality reveal a conflict that file locks cannot
> see.

### 0:50–1:22 — Prepare phase

Show deterministic conflict rules, leases or reservations, approval for the high-impact proposal, and C entering prepared state.

> During prepare, proposals are validated, graph versions are captured, conflicts are resolved, and safe work receives expiring authority.

### 1:22–1:55 — Safe concurrency

Let C execute and commit while A proceeds. Show B waiting or being rejected as stale.

> Unrelated work is not globally serialized. Agent C commits while the conflicting metric proposal waits.

### 1:55–2:25 — Commit, drift, and rebase

Commit A, show B fail the pre-commit version check, then show the prepared rebase/resubmission and validation.

> The coordinator rechecks graph context immediately before commit. Stale work fails closed, then
> receives a concrete resolution.

### 2:25–2:40 — Evidence and close

Show the audit timeline, then identify the separate live DataHub write/reread/restore proof.

> Graph Traffic Control gives autonomous data agents a semantic two-phase commit—and leaves evidence for the next agent.

## Submission narrative

### Problem

Multiple data agents can make individually valid changes that conflict through upstream or downstream dependencies even when they edit different repositories and files.

### Solution

Graph Traffic Control models structured agent read/write sets, expands their impact through DataHub, performs deterministic conflict checks, and coordinates prepare, validation, commit, or abort.

### What makes it original

Existing multi-agent coordinators focus on files, tasks, and worktrees. This project coordinates semantic changes through a governed data graph.

### DataHub usage to state explicitly

- The live provider reads lineage, schemas, owners, criticality, and expected entity context.
- The graph expands impact and proves indirect conflicts.
- The coordinator rechecks graph state before commit.
- A separate live exercise proved supported transaction outcome write/reread/restore behavior.

## Judging evidence map

| Criterion | What judges should see |
|---|---|
| Use of DataHub | Live graph contract and reversible writeback proof, with the public fixture boundary labeled |
| Technical execution | Strict proposals, state machine, leases/versions, safe concurrency, validation |
| Originality | Semantic transaction coordinator rather than file/worktree locking |
| Real-world usefulness | Prevents conflicting autonomous data changes without serializing all work |
| Submission quality | Three-agent visual story, deterministic trace, runnable examples |

## Required repository evidence

- `examples/agent-a-proposal.json`
- `examples/agent-b-proposal.json`
- `examples/agent-c-proposal.json`
- `examples/transaction-trace.json`
- conflict matrix and lineage evidence
- artifact diffs and validation results
- DataHub screenshots
- architecture and limitations

## Claims to avoid

- “Provides distributed ACID transactions across every data platform.”
- “Solves all multi-agent coordination.”
- “Guarantees exactly-once effects in arbitrary SaaS systems.”
- “Lets an LLM decide whether a commit is safe.”

Prefer: “Coordinates the demonstrated data-agent changes using deterministic graph-aware prepare and commit checks.”

## Recording checklist

- [x] Video is public and under three minutes: <https://youtu.be/cPSkHw8bR9I> (2:13).
- [ ] All three proposals are legible.
- [ ] The indirect conflict path is visually obvious.
- [ ] Agent C visibly proceeds without waiting.
- [ ] Stale-state failure and resolution are shown.
- [ ] The fixture boundary and separate live DataHub evidence are stated clearly.
- [ ] No secrets or copyrighted music appears.
