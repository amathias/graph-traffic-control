# Graph Traffic Control

## Submission title

**Graph Traffic Control: Transactional Coordination for Data Agents**

## Tagline

Let many data agents move quickly without colliding in the context graph.

## One-sentence pitch

Graph Traffic Control uses DataHub lineage and governance context to detect hidden conflicts between autonomous data-agent changes, coordinate safe execution with a semantic two-phase commit, and preserve an auditable record for the next agent.

## Basic idea

Multiple agents can independently produce valid changes that become invalid when combined. One agent renames a column while another builds a metric on the old name; a third alters an upstream model whose blast radius overlaps both. File locks cannot see these semantic conflicts.

Graph Traffic Control requires every agent to propose its read set, write set, expected graph version, evidence, and intended change. The coordinator queries DataHub for indirect lineage collisions, runs policy and impact checks during a prepare phase, permits unrelated work in parallel, sequences or rejects conflicts, verifies the commit, and writes the outcome back to DataHub.

## Why it can win

- **Forward-looking problem:** Agent coordination becomes essential as organizations deploy many autonomous data agents.
- **Deep DataHub use:** The context graph reveals conflicts that source-file or table-name locking misses.
- **Memorable mechanism:** “Semantic two-phase commit” is technical, explainable, and demoable.
- **Visible multi-agent demo:** Three agents propose work; two have a hidden downstream collision while the third safely proceeds.
- **Writes knowledge back:** Future agents inherit decisions, leases, change evidence, and updated context.

## Primary user

Data platform teams operating autonomous analytics, migration, governance, and code-generation agents.

## Challenge category

Primary: **Agents That Do Real Work**

## The memorable demo moment

Three agents request commits at once. DataHub lineage reveals that a schema rename and a metric change conflict even though they touch different files. The coordinator pauses and rebases one, allows the unrelated change through, and commits a verified audit trail.

## Name rationale

“Graph Traffic Control” preserves the air-traffic metaphor while identifying DataHub's graph as the coordination surface. “Semantic two-phase commit” remains the differentiating mechanism, not an overloaded brand name.

## Quickstart

Requires Python 3.12+. No Docker, no npm, and no DataHub instance are needed to run what exists
today.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"    # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # macOS / Linux

pytest                 # full suite, no network required
gtc-seed               # materialise deterministic demo state and SQL artifacts
gtc-api                # serve on http://127.0.0.1:8105
```

Then open **<http://127.0.0.1:8105/>** and press **Run four-agent scenario**.

That is the whole demo. The console shows the agents, their proposals, the DataHub lineage path
that proves the hidden conflict, leases, approvals, commits with every verification flag,
rollback, the append-only audit log, and the receipts behind it — with no DataHub instance, cloud
account, or paid service required. It states on screen which context source it read from.

Prefer a terminal? `gtc-demo` runs the same scenario and prints the trace.

```bash
curl http://127.0.0.1:8105/api/health
curl http://127.0.0.1:8105/api/readiness
curl http://127.0.0.1:8105/api/graph
```

Copy `.env.example` to `.env` to override defaults. DataHub connection variables are blank by
default; the application runs fixture-backed until they are supplied.

### All commands

| Command | What it does |
|---|---|
| `gtc-seed` / `gtc-reset` | Materialise or clear local demo state. Scoped to this project's state directory; cannot delete version-controlled fixtures. |
| `gtc-demo` | Run the four-agent scenario in the terminal. |
| `gtc-api` | Serve the API and the judge console. |
| `gtc-datahub-seed` | **Plan** the complete `traffic.` graph for DataHub — entities, schemas, ownership, domain, tag, marker, lineage. `--apply` needs live credentials. |
| `gtc-datahub-reset` | Plan removal of *this project's* entities only. A global refresh is refused. |
| `gtc-datahub-capture` / `gtc-datahub-restore` | Record the pre-seed state, and plan a return to it. |
| `gtc-safety-scan` | Check git-tracked content for anything that must not be published. |
| `gtc-archive-verify` | Build the distributions and prove they install and run in a clean environment. |

The `gtc-datahub-*` commands **plan by default and never touch DataHub without `--apply`**. The
instance is shared with four other submissions, so "run it and see" must not be the easy path.

## What it does

Agents submit structured proposals declaring intent, read set, write set, expected entity
versions, an executable action, a validation plan, and evidence. The coordinator then:

1. guards every URN against this project's `traffic.` allocation, failing closed;
2. reads the graph and rejects proposals whose expected versions are stale;
3. expands each proposal's impact through lineage within a bounded depth;
4. applies a deterministic conflict matrix, including **lineage-mediated conflicts between
   proposals that share no declared URN at all**;
5. grants expiring leases so unrelated work runs in parallel and abandoned work cannot block;
6. issues a prepared token fingerprinting the subgraph the proposal depends on;
7. **re-reads the graph immediately before commit and aborts on any drift**;
8. executes the change against a real local SQL artifact, validates it, and rolls back on failure;
9. performs one reversible DataHub writeback — capture, write, immediate re-read, restore;
10. writes sanitized proposal, lease, and commit receipts.

No language model participates in any conflict or commit decision.

### Why this is not file locking

Agent A renames a column on `traffic.fct_revenue`. Agent B publishes a metric in
`traffic.metric_net_revenue`. **Different files, different write targets, no overlap** — every
file-, branch-, or worktree-based coordinator says these are safe. They are not: the lineage path
`fct_revenue -> metric_net_revenue` means A's change reaches B's asset. The coordinator reports
that path as the conflict evidence. Meanwhile Agent C's support-branch change is lineage-disjoint
and commits in parallel rather than being serialised behind either of them.

### Two things worth knowing about how it fails

**Reads fail closed.** An MCP error or an unrecognised response shape aborts the read and says
so. It never becomes an empty graph — an empty graph is indistinguishable from a graph with no
conflicts, so degrading to one would silently turn "the coordinator cannot see the graph" into
"nothing conflicts, commit away".

**`COMMITTED` means proved, not attempted.** The artifact is re-read from disk and the DataHub
writeback is re-read from DataHub before a proposal is marked committed. Mutation, mutation
re-read, validation, writeback verification, writeback restoration, rollback, and receipts are
tracked as seven independent signals, and every commit receipt records which of them were true.

## Current status

Implemented and tested: the conflict matrix, two-phase commit with pre-commit graph recheck,
expiring leases, real artifact mutation with rollback, verified reversible writeback, sanitized
receipts, non-mutating readiness over the complete catalogue, deterministic namespace-guarded
DataHub seed/reset/capture/restore planning, the judge console, release safety scanning, and
archive verification.

**No connection to the shared DataHub instance has been made from this workspace.** The MCP
client, context provider, and writeback are exercised against a strict localhost protocol double
over real HTTP — not against DataHub Core v1.6.0. The DataHub seed has been *planned*, never
applied. There are no live receipts, and this README claims none.

[LIMITATIONS.md](./docs/LIMITATIONS.md) is the authoritative line between what has been executed
and what has not. Everything produced against the protocol double is labelled **simulated**.

Remaining: the live DataHub run and the recorded demo.

## Workspace map

- [Project brief](./PROJECT_BRIEF.md)
- [Build plan](./BUILD_PLAN.md)
- [Implementation plan](./IMPLEMENTATION_PLAN.md)
- [Architecture decisions](./docs/DECISIONS.md)
- [Limitations and evidence status](./docs/LIMITATIONS.md)
- [Submission copy](./docs/SUBMISSION.md)
- [Demo runbook](./docs/DEMO_RUNBOOK.md)
- [Demo and submission](./DEMO_AND_SUBMISSION.md)
- [Hackathon rules](./HACKATHON_RULES.md)
- [AI builder instructions](./AGENTS.md)
- [Coordinator handoff](./COORDINATOR_HANDOFF.md)
