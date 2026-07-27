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

## What has been executed against live DataHub

**The live gate completed on the shared DataHub Core v1.6.0 instance, and it passed.** It was run
**by the portfolio coordinator**, who holds the credentials and the SSM access; the evidence below
is theirs, recorded here in sanitized form. **No project chat ever connected to the instance**, and
none of it was executed in this workspace.

Deployed product: `5ea880f61122f052210d014906fe5eab2c356851`.

| Area | Live result |
|---|---|
| MCP tool names, argument names, and response envelopes | **Confirmed.** Reads succeeded against the pinned server. |
| Strong readiness over the complete allocated catalogue | **Passed** — 9 entities and 7 lineage edges, the full fixture graph including the edge the hidden conflict rides on. |
| Ingestion of the `traffic.` graph | **Applied.** All 49 typed operations of plan `cd44112ebd42b7de` accepted. |
| The `apply_plan` typed-emitter path | **Executed live.** |
| `update_description` **`operation` value** | **Resolved.** `SET` — this project's own guess, never coordinator-supplied — was **rejected by live DataHub 1.6.0**. The same reversible write/re-read/restore cycle then **succeeded with `replace`**, which is now the default. Still overridable via `DATAHUB_DESCRIPTION_OPERATION`. |
| Reversible writeback | **Completed, verified, and restored.** Final receipt SHA-256 `621e022bc1253990be5fe328da8186ecc6be2d675d8242514d3ef81866db8782`. |
| Cross-project isolation, in production | **`sibling_new_rows=0`.** No other submission's rows were created, altered, or removed. |
| Rollback position | Pre-gate snapshot `snap-0cb18d5953f50482c`, taken before the gate ran. |

The lineage-index problem that blocked the previous attempt is resolved: readiness now reads all
7 seeded edges. It was an unindexed graph service, not a data or code defect, and it was fixed by
reindexing rather than by re-seeding.

## What still has NOT been executed

| Area | Status |
|---|---|
| Anything at all, from this workspace | **No project chat has connected to the shared instance.** Every result produced here is against the localhost protocol double and is simulated. |
| Structured properties as a writeback aspect | Not used. Gated behind a smoke test that has not run. |
| How the pinned server reports a soft-deleted entity | Both "omitted from `get_entities`" and "returned with `status.removed: true`" are treated as absent. The live gate restored rather than exercising an absent-state restore, so which one the server does is still unobserved. Either is handled; a third behaviour would surface as a loud refusal. |
| The absent-state contract (ADR-016) end to end | Exercised against the protocol double only. The live allocation already existed from an older baseline, so the first-time-seed path was never the live path. |
| The rendered judge console | The payload, script syntax, and element wiring are tested, and the endpoints were exercised against a running server. **The page has not been visually confirmed** — no browser tooling was available in the sessions that built it. |

**Every result produced against the protocol double is simulated.** The double is strict — it
rejects any argument set other than the coordinator-observed contract, so an argument-name
regression fails a test rather than passing quietly — but a strict double still only proves this
project speaks the contract it claims to. The live gate is what proves the server answers that way.

A shape mismatch surfaces as a **loud, fail-closed error naming the tool and the offending key**,
never as silently empty fields. That is what turned each live failure into a specific diagnosis
rather than a green endpoint over an empty graph.

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
