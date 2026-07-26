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
- **Fail-closed reading.** An MCP transport error, tool error, or unrecognised response shape
  aborts the snapshot. Tests assert it never degrades to an empty or partial graph, at prepare,
  at the pre-commit re-read, and at `GET /api/graph`.
- **Commit verification.** Mutation, mutation re-read from disk, validation, writeback
  verification, writeback restoration, rollback, and receipts as seven independent signals, and
  the gate that refuses `COMMITTED` unless the required ones are positively true.
- Sanitized proposal, lease, and commit receipts, including that a token never reaches disk.
- The MCP client's JSON-RPC framing, session handling, SSE parsing, auth header, and error
  redaction — over real HTTP sockets against a localhost protocol double.
- The reversible writeback's capture → write → re-read → restore sequence, including that
  restoration still runs when the write fails, and that a verified write with a failed
  restoration is reported as exactly that.
- Strong, non-mutating readiness, including verification of the **complete** allocated catalogue
  rather than a sample, and every fail-closed branch.
- Deterministic, namespace-guarded DataHub seed, reset, capture, and restore **planning**,
  including refusal of all four sibling allocations, of foreign URNs smuggled inside lineage and
  dashboard-input payloads, of a foreign domain or tag, and of any reset or restore scope but
  `namespace`.
- **The absent-state contract** (ADR-016): capturing the deliberate absence of every allocated
  entity, treating a soft-deleted entity as absent, planning a soft delete back to absent,
  proving that absence by re-reading afterwards, and refusing partial, extra, foreign, ambiguous,
  and version-mismatched captures. Exercised against the protocol double over real HTTP.
- Cross-project isolation asserted at every surface that can reach shared state.
- The judge console's payload, self-containment, script syntax, and element wiring.
- Public-release safety scanning and archive verification, including a clean-environment install.

Command results at the handoff commit are recorded in `COORDINATOR_HANDOFF.md`.

## What has NOT been executed

**No connection to the shared DataHub instance has been made from this session.** The coordinator
instructed this chat not to access AWS or deploy, and DataHub reaches the developer machine only
through an SSM tunnel, which was deliberately not opened.

Therefore the following are **implemented and tested against a protocol double, but unverified
against a live DataHub Core v1.6.0 instance**:

| Area | Status |
|---|---|
| MCP tool names and argument names | Supplied by the coordinator as observed contracts. Implemented exactly; not confirmed by this session against the pinned server. |
| MCP response envelopes (`structuredContent.result`, `.downstreams.searchResults[*].entity.urn`, `.fields`) | Same: coordinator-observed, implemented exactly, not confirmed here. |
| `update_description` **`operation` value** | **Not coordinator-supplied.** The argument *name* is; the value is not. Defaults to `SET` and is configurable via `DATAHUB_DESCRIPTION_OPERATION`. Replace-in-place semantics are required, because an append-style operation could not restore the captured original exactly. Confirm this on the host before trusting a restore. |
| Whether mutation tools are enabled on the deployed bridge | Coordinator states they are; not observed from here. |
| How the pinned server reports a soft-deleted entity | Both "omitted from `get_entities`" and "returned with `status.removed: true`" are treated as absent. Which one the server actually does is not observed from here. |
| The pinned versions `acryl-datahub==1.6.0.15` and `mcp==1.28.1` | Declared as the deployment target and asserted by the test suite. **Not installed or imported in this session** — the suite runs without the `datahub` extra, so no code path through either package has been executed here. |
| Structured properties as a writeback aspect | Not used. Gated behind a smoke test that has not run. |
| Ingestion of the `traffic.` graph into the shared instance | **Planned, not applied.** `gtc-datahub-seed` produces a guarded, deterministic plan and recipe. No plan in this repository has been applied to a live instance. |
| The `apply_plan` emitter path | Implemented against the DataHub SDK and guarded, but never executed. It requires the optional `.[datahub]` extra and live credentials. |
| Any writeback receipt from a real DataHub entity | **None exists.** |
| The rendered judge console | The payload, script syntax, and element wiring are tested, and the endpoints were exercised against a running server. **The page was not visually confirmed** — no browser tooling was available in this session. |

**Every result produced against the protocol double is simulated.** The double is strict — it
rejects any argument set other than the coordinator-observed contract, so an argument-name
regression fails a test rather than passing quietly — but a strict double still only proves this
project speaks the contract it claims to. It cannot prove the pinned server answers that way.

Unlike the previous candidate, a shape mismatch on the first live run will now surface as a
**loud, fail-closed error naming the tool and the offending key**, not as silently empty fields.
That is the intended behaviour: treat the first live run as shape discovery, and expect it to
either pass or tell you exactly what differs.

## Scope boundaries

- Leases are single-process, backed by SQLite. A distributed lease backend is out of MVP scope,
  and SQLite's single-writer model means the service must run as one replica.
- Lineage expansion is bounded to three hops. Conflicts beyond that bound are not reported.
- Lineage is read one hop downstream per allocated entity. Every edge *inside* the allocation is
  therefore discovered, but an edge that leaves and re-enters the allocation through a foreign
  entity is not followed — by design, since adopting a foreign entity into this graph is exactly
  what the namespace guard forbids.
- Column-level conflict detection uses declared field paths and a rename's target column. It does
  not parse SQL to infer column dependencies.
- The executor performs textual transformation of SQL artifacts. It does not compile or run SQL.
- Validation checks artifact content and self-owned downstream references. It does not execute
  queries against a warehouse.
- Cross-owner downstream breakage is resolved by ordering and rebase at prepare time, not by
  failing the upstream proposal. See the reasoning in `execute/validator.py`.
- The demo graph is a fixture of nine entities. It is not a production-scale catalogue.
- A restore is only as complete as its capture. `gtc-datahub-capture` requires live credentials
  and fails closed if any allocated entity cannot be read, because restoring from a partial
  capture would silently drop whatever was missed. On a first-time seed the whole namespace is
  legitimately missing; `--allow-absent` records that absence deliberately (ADR-016) and is the
  only way absence enters a capture.
- Restoring an initially-absent entity soft-deletes it. It does **not** hard delete, and it does
  not remove a domain, tag, or lineage edge that something outside this project may also
  reference. Soft delete is what coordinator ruling 4 permits; a shared instance keeps the tomb-
  stone.
- Absence after a restore is verified by re-reading the exact allocated URNs through MCP. If
  `DATAHUB_MCP_URL` and `DATAHUB_TOKEN` are not both set, `gtc-datahub-restore --apply` fails
  rather than reporting an unverified success — so a GMS-only host can apply a seed but cannot
  complete an absent-state restore.
- The absent-state contract has been exercised only against the protocol double. Whether the
  pinned server reports a soft-deleted entity as `status.removed` (rather than omitting it) is
  **not confirmed live**. Both are handled as absent, so either behaviour is correct here, but a
  third behaviour would surface as a loud refusal on the first live restore.
- Artifact digests from `gtc-archive-verify` differ between builds. The distributions are not
  bit-for-bit reproducible; the digests identify one specific build, they do not certify one.

## Claims this project does not make

Per `DEMO_AND_SUBMISSION.md`:

- It does not provide distributed ACID transactions across data platforms.
- It does not solve multi-agent coordination in general.
- It does not guarantee exactly-once effects in external systems.
- No LLM decides whether a commit is safe. No model output participates in any conflict or commit
  decision; the coordinator is deterministic.
