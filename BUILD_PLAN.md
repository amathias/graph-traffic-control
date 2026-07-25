# Build Plan: Graph Traffic Control

## Delivery strategy

Build the conflict engine and transaction state machine before adding conversational agents. The critical proof is:

> DataHub reveals an indirect semantic conflict that file-level coordination misses, while an unrelated proposal proceeds safely through prepare and commit.

## Recommended repository shape

```text
/
  coordinator/          # API, conflict engine, transaction state
  web/                  # graph and transaction timeline
  agents/               # deterministic demo clients
  adapters/             # DataHub and executable target adapters
  demo/                 # graph, code/data fixtures, ingestion/reset
  examples/             # proposals and transaction traces
  tests/
  docs/
  docker-compose.yml
  .env.example
  LICENSE
  README.md
```

## Phase 0: Prove DataHub connectivity

- Pin and start open-source DataHub.
- Ingest two connected branches.
- Read lineage and metadata through MCP or Agent Context Kit.
- Perform and verify one harmless supported writeback.
- Record exact versions.

Exit condition: smoke test proves read and write.

## Phase 1: Proposal schema and state machine

- Define agent, proposal, impact set, conflict, lease, prepared token, validation, and transaction event schemas.
- Implement states such as submitted, analyzing, blocked, prepared, executing, validating, committed, aborted, expired.
- Define legal transitions and make every transition append an audit event.
- Unit-test invalid transitions and idempotent retries.

Exit condition: deterministic state-machine tests pass without DataHub or agents.

## Phase 2: Semantic conflict engine

- Implement the documented conflict matrix.
- Query/consume graph context through a clean adapter interface.
- Return shortest lineage evidence for indirect conflicts.
- Add schema/version checks and criticality-based approval.
- Test false-positive cases such as shared domains with disjoint lineage.

Exit condition: fixtures prove direct block, indirect order/rebase, read/read allow, and disjoint allow.

## Phase 3: Prepare/commit implementation

- Implement graph fingerprints or expected versions.
- Implement expiring leases or optimistic reservations in SQLite.
- Recheck relevant graph state immediately before commit.
- Implement commit and abort idempotency.
- Add deterministic concurrency barriers in tests.

Exit condition: expired and stale proposals fail safely and never strand a lease.

## Phase 4: Executable demo agents

- Agent A proposes and applies the revenue schema rename.
- Agent B proposes the dependent metric change from stale context.
- Agent C proposes an unrelated support-pipeline change.
- Add local validation for schemas, generated SQL, and expected outputs.
- Make B rebase or resubmit after A commits.

Exit condition: A/B conflict for semantic reasons and C commits in parallel.

## Phase 5: DataHub writeback and UI

- Record supported change status/evidence references in DataHub.
- Display:
  1. live proposals;
  2. explicit and expanded impact sets;
  3. DataHub lineage path causing each conflict;
  4. prepare tokens and lease timers;
  5. approvals;
  6. commit/abort timeline;
  7. validation and writeback receipts.

Exit condition: judges can understand the coordination without reading logs.

## Phase 6: Agent layer and hardening

- Optionally use an LLM to convert human intent into a draft proposal and explain resolutions.
- Validate all model output against the strict proposal schema.
- Add examples and transaction traces.
- Test clean setup.
- Add Apache 2.0 license.
- Pin dependencies and scan secrets.
- Record a deterministic demo under 2:45.

## Test plan

### Unit

- Proposal validation.
- Legal state transitions.
- Conflict matrix.
- Lineage path evidence.
- Lease expiration.
- Version/fingerprint checks.
- Idempotent commit and abort.

### Integration

- DataHub read/write.
- Real local artifact mutation.
- Validation failure and rollback/abort.
- Waiting proposal notification and rebase.
- Concurrent unrelated proposal.

### End to end

- Seed graph and fixtures.
- Submit A, B, and C behind deterministic barriers.
- Show A/B conflict and C prepare.
- Commit C and A.
- Reject stale B, resubmit/rebase it, and commit.
- Confirm DataHub context and event report.

## Scope cuts if behind

Cut in this order:

1. Natural-language proposal generation.
2. Automated patch rebase.
3. Column-level conflicts.
4. Distributed lease backend.
5. More than three demo agents.

Never cut indirect DataHub conflict proof, prepare/commit states, safe parallel work, stale-state failure, real validation, or writeback.

## Evidence to preserve

- Structured proposal examples.
- Conflict matrix test output.
- DataHub lineage path causing the hidden conflict.
- Concurrent transaction event trace.
- Stale-version failure.
- Real artifact diff and validation.
- DataHub before/after screenshot.

## Final engineering checklist

- [ ] Coordinator decisions are deterministic.
- [ ] LLM output cannot directly commit.
- [ ] Leases expire.
- [ ] Commits and aborts are idempotent.
- [ ] Demo concurrency is reproducible.
- [ ] No committed secrets.
- [ ] Clean setup is tested.
- [ ] README maps proof to judging criteria.
