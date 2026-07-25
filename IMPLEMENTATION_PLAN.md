# Implementation Plan: Graph Traffic Control

Derived from `AGENTS.md`, `PROJECT_BRIEF.md`, `BUILD_PLAN.md`, `DEMO_AND_SUBMISSION.md`,
`HACKATHON_RULES.md`, and the portfolio contracts in `COORDINATOR_HANDOFF.md` and
`../COORDINATOR_PLAN.md`.

Written 2026-07-24. Submission deadline 2026-08-10 17:00 ET (17 days). Judging runs Aug 17–31.

## The one thing this project must prove

> DataHub lineage reveals a semantic conflict between two agent changes that touch different
> files and different write targets, while a third unrelated change commits in parallel.

Every phase below is ordered to protect that proof. If time runs short, cut toward it, never
through it.

## Fixed allocation (do not change without a coordinator proposal)

| Setting | Value |
|---|---|
| Project slug | `graph-traffic-control` |
| Internal port | `8105` |
| DataHub domain | `Demo / Graph Traffic Control` |
| Required tag | `project-graph-traffic-control` |
| Entity prefix | `traffic.` |
| Fixture root | `demo/fixtures/graph-traffic-control` |
| State root | `/var/lib/datahub-hackathon/graph-traffic-control` |

Environment variables use the shared contract names exactly: `PROJECT_SLUG`, `APP_ENV`,
`APP_HOST`, `APP_PORT`, `APP_PUBLIC_URL`, `APP_STATE_DIR`, `DATAHUB_GMS_URL`, `DATAHUB_MCP_URL`,
`DATAHUB_TOKEN`, `DATAHUB_DOMAIN`, `DATAHUB_PROJECT_TAG`, `DATAHUB_URN_PREFIX`,
`DEMO_FIXTURE_ROOT`. Note `DATAHUB_TOKEN`, not `DATAHUB_GMS_TOKEN`.

## Stack decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Matches `PROJECT_BRIEF.md:118` and the four sibling projects |
| API | FastAPI + Pydantic v2 | Strict proposal schemas are a scored requirement; shared contract needs `/api/health` and `/api/readiness` |
| Graph | NetworkX over a materialized lineage snapshot | Shortest-path evidence is the demo's centerpiece |
| Store | SQLite with explicit transactions | Proposals, leases, audit events; one file under `APP_STATE_DIR` |
| UI | One self-contained HTML page + Server-Sent Events | No npm, no build step; deploys as a single process on port 8105 |
| Tests | pytest | Conflict matrix, state machine, concurrency barriers |
| LLM | Optional, explanation-only | Coordinator decisions stay deterministic (`AGENTS.md:29`) |

### On the UI choice

`PROJECT_BRIEF.md:119-120` suggests React/TypeScript/Vite. I am proposing a single-page
HTML + SSE UI instead. The demo is three proposal rows changing state, one highlighted lineage
path, lease countdowns, and an event timeline — that is a modest amount of DOM. Dropping the
second toolchain removes a build step from the judges' setup instructions (Submission Quality is
scored on reproducibility), removes a build stage from the coordinator's promotion flow, and
saves roughly three days. Reversible: the API is JSON + SSE, so a React front end can be added
later without touching the coordinator.

## Repository shape

```text
graphtrafficcontrol-workspace/
  src/graph_traffic_control/
    api.py                   # FastAPI app, health/readiness, proposal API, SSE stream
    domain/models.py         # Agent, Proposal, ImpactSet, Conflict, Lease, PreparedToken, Event
    domain/states.py         # state machine and legal transitions
    context/provider.py      # ContextProvider protocol
    context/fixture.py       # deterministic recorded-graph provider
    context/datahub.py       # live provider (MCP reads + SDK writeback)
    context/namespace.py     # traffic. prefix guard, fail-closed
    conflict/engine.py       # the conflict matrix
    conflict/lineage.py      # impact expansion + shortest-path evidence
    txn/coordinator.py       # prepare / commit / abort orchestration
    txn/store.py             # SQLite proposals, leases, audit events
    txn/leases.py            # expiring leases
    execute/targets.py       # local artifact mutation
    execute/validator.py     # artifact and downstream-contract validation
    writeback/datahub.py     # transaction outcome written back to DataHub
    agents/                  # deterministic demo clients A, B, C, D
    demo/seed.py             # deterministic seed
    demo/reset.py            # namespace-scoped reset
    web/index.html           # single-page coordinator UI
  demo/fixtures/graph-traffic-control/
  examples/                  # proposals, transaction traces, receipts
  tests/
  docs/DECISIONS.md
  docs/LIMITATIONS.md
  pyproject.toml
  .env.example
  LICENSE                    # Apache 2.0
  README.md
```

## Demo graph

All URNs carry the `traffic.` prefix and the `project-graph-traffic-control` tag.

```text
traffic.raw_sales_orders ─┐
                          ├─> traffic.stg_sales ──> traffic.fct_revenue ──> traffic.metric_net_revenue ──> traffic.dash_exec_revenue
traffic.raw_sales_items ──┘                              │
                                                    gross_revenue

traffic.raw_support_tickets ──> traffic.stg_support ──> traffic.fct_support_sla   (disjoint branch)
```

## The four agents

| Agent | Intent | Write set | Read set | Expected outcome |
|---|---|---|---|---|
| A | Rename `gross_revenue` to `recognized_revenue` | `traffic.stg_sales`, `traffic.fct_revenue` | `traffic.raw_sales_*` | Prepares, requires approval (high blast radius), commits |
| B | Publish net-revenue metric on the old column | `traffic.metric_net_revenue` | `traffic.fct_revenue` | Indirect conflict with A; ordered behind A, then fails pre-commit recheck as stale, then rebases and commits |
| C | Update support SLA model | `traffic.fct_support_sla` | `traffic.stg_support` | Disjoint lineage; prepares and commits in parallel, never waits |
| D | Stale proposal | `traffic.fct_revenue` | — | Expired lease / stale expected version; fails closed |

A and B have **no direct write/write overlap and touch different files**. The conflict exists only
because `traffic.fct_revenue` sits on the lineage path between A's write set and B's read set. That
is the entire pitch, and it is what `get_lineage_paths_between` proves.

## Conflict matrix

From `PROJECT_BRIEF.md:155-164`. Each row gets a test.

| # | Case | Decision |
|---|---|---|
| 1 | Same target write/write | Block one |
| 2 | One proposal writes what another reads | Order or rebase |
| 3 | Upstream schema write vs downstream read | Order or rebase — **the A/B case** |
| 4 | Two read-only proposals | Allow |
| 5 | Disjoint lineage branches | Allow — **the C case** |
| 6 | Shared domain, no lineage intersection | Warn only, never block |
| 7 | Stale expected version | Abort prepare or commit |
| 8 | High blast radius | Require approval |

Row 6 is the false-positive guard. Without a passing test for it the project looks like it blocks
anything vaguely related, and the originality claim collapses.

## Phases

### Phase 0 — Scaffold and shared contracts (satisfies coordinator Milestone A)

- `pyproject.toml`, Apache 2.0 `LICENSE`, `.env.example` with placeholders only, `.gitignore`.
- FastAPI app on `APP_PORT` (8105) with `GET /api/health` and `GET /api/readiness`.
  `readiness` verifies local state and DataHub reachability **without mutating shared state**.
- `context/namespace.py`: every read, write, mutation, and reset target is checked against
  `DATAHUB_URN_PREFIX`. Out-of-namespace targets raise and fail closed.
- Deterministic `seed` and `reset` commands, both namespace-scoped.

Exit: clean install passes, health/readiness respond, namespace guard tests pass, reset provably
cannot touch another project's entities.

### Phase 1 — Proposal schema and state machine

- Pydantic models for agent, proposal, impact set, conflict, lease, prepared token, validation
  plan, transaction event. Malformed proposals are rejected before any graph work.
- States: `submitted`, `analyzing`, `blocked`, `prepared`, `executing`, `validating`, `committed`,
  `aborted`, `expired`. Every transition appends an audit event.
- SQLite store with explicit transactions.

Exit: state-machine tests pass with no DataHub and no agents. Illegal transitions rejected,
retries idempotent.

### Phase 2 — Conflict engine and lineage evidence

- `ContextProvider` protocol with the fixture implementation first.
- Impact expansion over NetworkX within a bounded policy depth.
- Shortest-path evidence for every indirect conflict.
- Column-level detection where schema fields are available.
- Weighting by criticality, ownership, blast radius.

Exit: all eight matrix rows tested, including the row-6 false-positive guard.

### Phase 3 — Prepare, commit, leases, drift

- Graph fingerprints / expected versions captured at prepare.
- Expiring leases in SQLite; abandoned leases cannot block the system.
- Relevant graph state re-read immediately before commit; drift fails closed.
- Commit and abort idempotent.
- Deterministic concurrency barriers in tests — never wall-clock timing.

Exit: stale and expired proposals fail safely and never strand a lease.

### Phase 4 — Executable targets, validation, demo agents

- Real local artifact mutation against disposable SQL fixtures under the fixture root.
- Validator checks the changed artifact and the downstream contract.
- Agents A–D as deterministic clients.
- B rebases after A commits, then validates and commits.

Exit: A/B conflict for semantic reasons, C commits in parallel, D fails closed, real diffs exist.

### Phase 5 — Live DataHub and writeback (satisfies coordinator Milestone B) — **BLOCKED**

Blocked on the coordinator providing DataHub reachability and credentials.

- Swap `FixtureContextProvider` for the live provider: `get_lineage`,
  `get_lineage_paths_between`, `list_schema_fields`, `get_entities`.
- Re-record fixtures from the live instance so tests stay deterministic and offline.
- Ingest the demo graph into the `Demo / Graph Traffic Control` domain under `traffic.`.
- Writeback on every terminal transaction: outcome, decision reason, lineage evidence path,
  transaction ID, timestamp. Preserve receipts in `examples/`.

Exit: real read verified, real writeback verified and visible in the DataHub UI, receipts saved.

### Phase 6 — Coordinator UI

Single page, SSE-driven, showing: live proposals with read/write sets; expanded impact sets; the
DataHub lineage path causing each conflict; prepare tokens and lease countdowns; approvals; the
commit/abort timeline; validation and writeback receipts.

Exit: the coordination is understandable without reading logs.

### Phase 7 — Submission evidence

- `examples/agent-{a,b,c}-proposal.json`, `examples/transaction-trace.json`, conflict-matrix
  output, artifact diffs, DataHub before/after screenshots.
- `README.md` mapping each judging criterion to its proof, and stating plainly how semantic graph
  coordination differs from file locking.
- `docs/DECISIONS.md` and `docs/LIMITATIONS.md`.
- Clean-checkout setup test.
- Secret scan.
- Demo video, 2:35–2:45, per `DEMO_AND_SUBMISSION.md`.
- Updated `COORDINATOR_HANDOFF.md` with the exact commit proposed for deployment.

## Sequencing around the EC2 dependency

Phases 0–4 and 6 need no live DataHub. Phase 5 does. Building fixture-first behind the
`ContextProvider` interface keeps the critical path off the EC2 provisioning schedule, matches
the pattern already recorded in `../forgetmegraph-workspace/docs/DECISIONS.md` ADR-003, and keeps
the test suite fast, offline, and deterministic — which it must be regardless, since CI cannot
depend on a shared mutable instance.

This will be recorded as ADR-001 in `docs/DECISIONS.md`, with the interface boundary making it
impossible to mistake fixture behavior for the live integration.

## Open items for the coordinator

1. **Local DataHub access.** `../AGENTS.md:113` forbids exposing GMS and MCP publicly, and
   project chats must not edit EC2. So how does this chat reach DataHub for Phase 5 development —
   SSH tunnel, VPN, a dev token bound to a restricted path, or does live verification happen only
   during promotion? This gates Phase 5, not Phases 0–4.
2. **Mutation tools enabled?** Writeback needs `TOOLS_IS_MUTATION_ENABLED=true` and
   `mcp-server-datahub` ≥ 0.5.0, or the `acryl-datahub` Python SDK as the write path. Confirm
   which is sanctioned so all five projects write the same way.
3. **Writeback aspect.** Proposing tag + description + structured property on affected datasets,
   carrying transaction outcome and evidence. Confirm structured properties are available on the
   pinned DataHub version.
4. **Ingestion.** Confirm project chats may run namespace-scoped ingestion recipes against the
   shared instance, with stale-entity removal disabled. A full-refresh or cleanup ingestion from
   any project would soft-delete other projects' entities.

## Risks

| Risk | Mitigation |
|---|---|
| EC2 slips and Phase 5 compresses | Fixture-first; Phase 5 is a provider swap, not a rewrite |
| Shared DataHub is a single point of failure for all five submissions | Keep ingestion recipes and a recorded fixture graph in-repo so the demo rebuilds standalone |
| Conflict engine over-blocks and looks like a task queue | Matrix row 6 tested explicitly; warn-not-block for shared domains |
| Concurrency demo is flaky on video | Deterministic barriers, never wall-clock sleeps |
| Scope creep into the React UI | Deferred; JSON + SSE API keeps it reversible |
| Writeback unavailable on the pinned version | Fall back to tag + description via the Python SDK; keep receipts either way |

## Cut order if behind

Per `BUILD_PLAN.md:129-139`: natural-language proposal generation, then automated patch rebase,
then column-level conflicts, then distributed leases, then Agent D.

Never cut: indirect DataHub conflict proof, prepare/commit states, safe parallel work,
stale-state failure, real validation, real writeback.
