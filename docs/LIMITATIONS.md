# Limitations and Evidence Status

This file exists so no reader has to guess which claims are backed by captured evidence. The
hackathon rules require truthful claims, and `AGENTS.md` requires that simulated behaviour is
labelled and claimed real behaviour is verifiable.

## What has actually been executed

Verified locally, in this workspace, by the committed test suite:

- Proposal schema rejection of malformed, foreign-namespace, and path-escaping input.
- The full transaction state machine, including illegal transitions and idempotent retries.
- The conflict matrix, including the lineage-mediated conflict with **zero declared URN overlap**
  between the two proposals.
- Lease grant, contention, expiry, and release, driven by an injected clock.
- Prepare, graph re-read before commit, fail-closed drift detection, execution, validation,
  rollback on validation failure, abort, and commit.
- Real rewriting of local SQL artifacts, with before/after content asserted.
- Sanitized proposal, lease, and commit receipts, including that a token never reaches disk.
- The MCP client's JSON-RPC framing, session handling, SSE parsing, auth header, and error
  redaction — over real HTTP sockets against a localhost test double.
- The reversible writeback's capture → write → re-read → restore sequence, including that
  restoration still runs when the write fails, against the same test double.
- Strong, non-mutating readiness, including every fail-closed branch.

## What has NOT been executed

**No connection to the shared DataHub instance has been made from this session.** The coordinator
instructed this chat not to access AWS or deploy, and DataHub reaches the developer machine only
through an SSM tunnel, which was deliberately not opened.

Therefore the following are **implemented and unit-tested against a test double, but unverified
against a live DataHub Core v1.6.0 instance**:

| Area | Status |
|---|---|
| MCP tool names (`get_entities`, `get_lineage`, `list_schema_fields`, `update_description`) | Taken from DataHub documentation; not confirmed against the pinned server |
| MCP tool **response shapes** | Extractors accept several plausible shapes and degrade to "unknown" rather than guessing; the true shape is unconfirmed |
| `update_description` argument names (`urn`, `description`) | Unconfirmed |
| Whether mutation tools are enabled on the deployed bridge | Coordinator states they are; not observed from here |
| Structured properties as a writeback aspect | Not used. The coordinator gated this behind a smoke test that has not run |
| Ingestion of the `traffic.` graph into the shared instance | Not performed |
| Any writeback receipt from a real DataHub entity | **None exists** |

The provider deliberately degrades rather than fails on shape mismatch, so a wrong guess produces
"field unknown", never a wrong conflict decision. The first live run should be treated as a
shape-discovery exercise, and `docs/DECISIONS.md` ADR-001 covers the swap.

## Scope boundaries

- Leases are single-process, backed by SQLite. A distributed lease backend is out of MVP scope.
- Lineage expansion is bounded to three hops. Conflicts beyond that bound are not reported.
- Column-level conflict detection uses declared field paths and a rename's target column. It does
  not parse SQL to infer column dependencies.
- The executor performs textual transformation of SQL artifacts. It does not compile or run SQL.
- Validation checks artifact content and self-owned downstream references. It does not execute
  queries against a warehouse.
- Cross-owner downstream breakage is resolved by ordering and rebase at prepare time, not by
  failing the upstream proposal. See the reasoning in `execute/validator.py`.
- The demo graph is a fixture of nine entities. It is not a production-scale catalogue.

## Claims this project does not make

Per `DEMO_AND_SUBMISSION.md`:

- It does not provide distributed ACID transactions across data platforms.
- It does not solve multi-agent coordination in general.
- It does not guarantee exactly-once effects in external systems.
- No LLM decides whether a commit is safe. No model output participates in any conflict or commit
  decision; the coordinator is deterministic.
