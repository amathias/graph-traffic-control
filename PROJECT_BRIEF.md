# Project Brief: Graph Traffic Control

## Product thesis

File locks cannot prevent semantic collisions between autonomous data agents. DataHub's context graph can. Graph Traffic Control uses graph-aware read/write sets and a semantic two-phase commit to coordinate safe changes.

## Problem

Imagine three agents:

- Agent A renames `gross_revenue` to `recognized_revenue` in an upstream transformation.
- Agent B publishes a metric that depends on `gross_revenue` through another model.
- Agent C updates an unrelated customer-support pipeline.

The first two may touch different repositories and assets, yet their combined outcome is invalid. Conventional worktrees, file locks, and message queues do not see the conflict.

## MVP scenario

Create a demo DataHub graph with:

1. Raw sales tables.
2. A revenue transformation and downstream semantic metric.
3. A report or dashboard.
4. A separate support-data branch.
5. Ownership and criticality metadata.

Launch three deterministic agent clients that submit the changes above. Graph Traffic Control should discover the hidden A/B collision through lineage, allow C to prepare and commit, then abort or rebase B after A.

## Core user journey

1. Each agent registers identity and capabilities.
2. Agent submits a structured change proposal with intent, read set, write set, expected entity versions, code patch or action, validation plan, and evidence.
3. Coordinator resolves DataHub context and expands semantic read/write impact.
4. Prepare phase checks direct overlap, lineage overlap, policy, criticality, ownership, leases, and graph versions.
5. Low-risk unrelated proposals receive leases and can execute concurrently.
6. Conflicting proposals are ordered, rejected, or returned with a concrete rebase requirement.
7. Immediately before commit, coordinator re-reads relevant graph state.
8. Executor applies the approved local change.
9. Validator tests the changed artifact and downstream contract.
10. Commit phase records the outcome and DataHub writeback; failure aborts and releases leases.

## Functional requirements

### Proposal protocol

Every proposal must contain:

- proposal and agent ID;
- human-readable intent;
- explicit DataHub URN read and write sets;
- expected schema or metadata versions;
- proposed artifact or executable adapter;
- validation plan;
- requested lease duration;
- risk declaration and evidence.

Reject malformed proposals before graph analysis.

### Semantic conflict detection

- Detect direct write/write and read/write intersections.
- Expand relevant upstream and downstream lineage within a bounded policy.
- Detect schema-field conflicts when column-level information is available.
- Weight conflicts by criticality, ownership, and blast radius.
- Explain the shortest DataHub lineage path that creates each indirect conflict.
- Avoid treating every connected asset as a conflict; define and test a precise matrix.

### Semantic two-phase commit

Prepare:

- validate proposal;
- fetch graph snapshot and expected versions;
- compute conflicts;
- evaluate policy and approvals;
- acquire expiring leases or record optimistic reservations;
- return prepared token and conditions.

Commit or abort:

- revalidate token, graph versions, and leases;
- execute approved change;
- run validations;
- write supported metadata/audit context to DataHub;
- mark committed or aborted;
- release leases;
- notify waiting proposals to rebase or retry.

### Demonstration clients

- Agent A: schema rename on revenue transformation.
- Agent B: metric change based on the old semantic dependency.
- Agent C: unrelated support-model change.
- Optional Agent D: stale proposal that misses its lease or graph version.

These may be deterministic agents with optional LLM-generated explanations. The coordination behavior must be real.

## Suggested architecture

```text
Agent clients
  -> proposal API
      -> identity and schema validation
      -> DataHub context/lineage adapter
      -> semantic conflict engine
      -> policy and approval engine
      -> lease/version store
      -> prepare token service
      -> execution adapter registry
      -> validator
      -> commit/abort log
      -> DataHub writeback
  -> coordinator UI / event stream
```

Suggested stack:

- Python 3.12, FastAPI, Pydantic, NetworkX, pytest.
- React, TypeScript, Vite, graph and transaction-timeline visualization.
- SQLite with explicit transactions for proposals, leases, and audit events.
- Local SQL or dbt fixture repositories as executable targets.
- Server-sent events or WebSockets for visible agent progress.
- Docker Compose for DataHub and the app.
- Optional LLM for intent extraction and conflict explanations only.

## Core data contracts

### Change proposal

- identity and intent
- explicit read/write URNs and optional field paths
- expected versions
- artifact/action reference
- validation specification
- risk and approval needs

### Prepared transaction

- prepared token
- graph snapshot/fingerprint
- expanded impact sets
- conflicts and resolution
- leases and expiration
- approval evidence
- commit preconditions

### Transaction event

- sequence number
- proposal and agent ID
- state transition
- actor, timestamp, evidence
- DataHub read/write receipt

## Conflict matrix for MVP

- Same target write/write: block one.
- One proposal writes an asset another reads: order or rebase.
- Upstream schema write affecting a downstream read: order or rebase.
- Two read-only proposals: allow.
- Disjoint lineage branches: allow.
- Shared high-level domain without lineage intersection: warn at most, do not block.
- Stale expected version: abort preparation or commit.
- High-blast-radius write: require approval.

## Safety model

- Fail closed on stale graph state, invalid tokens, expired leases, or failed validation.
- Use local disposable targets only.
- Require approval for high-impact writes.
- Keep coordinator decisions deterministic and auditable.
- Make commits and aborts idempotent.
- Ensure abandoned leases expire.
- Never represent advisory LLM text as transaction state.

## Must-have scope

- Real DataHub graph and metadata retrieval.
- Three concurrent structured proposals.
- Direct and indirect conflict detection.
- One unrelated proposal proceeds while conflicting work waits.
- Visible prepare and commit/abort lifecycle.
- Version recheck and at least one stale-state failure.
- Real local artifact mutation and validation.
- Real DataHub writeback.
- Deterministic automated concurrency tests.

## Stretch scope

- Agent-to-Agent or MCP-facing proposal protocol.
- Column-level semantic conflict resolution.
- Automatic patch rebase proposal.
- Human owner approval routed from DataHub ownership.
- Distributed lease backend.
- Reusable DataHub Skill or RFC for agent change proposals.

## Out of scope for the MVP

- General-purpose distributed database transactions.
- Arbitrary autonomous code merging.
- Exactly-once guarantees across uncontrolled SaaS systems.
- Coordinating every kind of AI agent.

## Acceptance criteria

- [ ] All proposals pass a strict schema.
- [ ] The hidden A/B collision is proven through a DataHub lineage path.
- [ ] Agent C prepares and commits without waiting for A/B.
- [ ] One conflicting proposal is ordered, rebased, or aborted for a documented reason.
- [ ] A stale version fails safely.
- [ ] Leases expire and release correctly.
- [ ] Real local artifact changes and validations execute.
- [ ] Transaction states and evidence are reproducible.
- [ ] A supported writeback is visible in DataHub.
- [ ] Tests cover the conflict matrix and deterministic concurrency.

## Competitive positioning

Multi-agent coding coordinators already manage files, branches, and worktrees. The defensible claim is:

> Graph Traffic Control coordinates data-agent changes using semantic dependencies and governance context from DataHub, not just shared files.

The demo must make the indirect conflict visible; otherwise the project will look like a generic task queue.
