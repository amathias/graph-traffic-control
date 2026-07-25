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

pytest                 # 57 tests
gtc-seed               # materialise deterministic demo state
gtc-api                # serve on http://127.0.0.1:8105
```

Then:

```bash
curl http://127.0.0.1:8105/api/health
curl http://127.0.0.1:8105/api/readiness
```

`gtc-reset` clears demo state. It is scoped to this project's state directory and cannot delete
version-controlled fixtures or another project's DataHub entities.

Copy `.env.example` to `.env` to override defaults. DataHub connection variables are blank by
default; the application runs fixture-backed until they are supplied.

## Current status

Phase 0 of eight is complete: project scaffold, the shared health/readiness contract, the
fail-closed DataHub namespace guard, and a deterministic seed and reset.

The conflict engine, two-phase commit, demo agents, live DataHub integration, and UI are not built
yet. See [the implementation plan](./IMPLEMENTATION_PLAN.md) for sequencing, and
[decisions](./docs/DECISIONS.md) for why the context provider is fixture-backed until the shared
DataHub instance is reachable.

Nothing in this repository yet claims a DataHub read or writeback. Those arrive in Phase 5 and
will be evidenced with receipts.

## Workspace map

- [Project brief](./PROJECT_BRIEF.md)
- [Build plan](./BUILD_PLAN.md)
- [Implementation plan](./IMPLEMENTATION_PLAN.md)
- [Architecture decisions](./docs/DECISIONS.md)
- [Demo and submission](./DEMO_AND_SUBMISSION.md)
- [Hackathon rules](./HACKATHON_RULES.md)
- [AI builder instructions](./AGENTS.md)
- [Coordinator handoff](./COORDINATOR_HANDOFF.md)
