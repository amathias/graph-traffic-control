# Architecture Decisions

Maintained per `AGENTS.md`: "Maintain `docs/DECISIONS.md` as architectural decisions are made."

## ADR-001: Fixture-backed context provider until the shared DataHub is reachable

**Status:** Accepted with environment constraint
**Date:** 2026-07-24

The target integration is the shared open-source DataHub instance the portfolio coordinator is
provisioning on EC2, read through the DataHub MCP server / Agent Context Kit and written through
a supported SDK.

That instance does not exist yet, and `../AGENTS.md` forbids exposing GMS and MCP publicly while
also forbidding project chats from editing the EC2 host. Development therefore proceeds against a
recorded fixture graph behind a `ContextProvider` interface, and swaps to the live provider in
Phase 5.

This is not only a workaround. The test suite must be offline and deterministic regardless: five
projects share one mutable DataHub instance, so a suite that depended on live graph state would be
order-dependent and would fail whenever another project reseeded.

The interface boundary is explicit so fixture behaviour can never be mistaken for the live
integration, and the fixture graph is re-recorded from the live instance in Phase 5.

Consistent with ADR-003 in `../forgetmegraph-workspace/docs/DECISIONS.md`.

## ADR-002: The namespace guard fails closed on anything it cannot prove

**Status:** Accepted
**Date:** 2026-07-24

Five submissions share one DataHub instance. `../AGENTS.md` requires that a project reset never
delete another project's entities and that namespace collisions are blocking defects.

Every DataHub read, write, mutation, and reset target passes through `Namespace.require` before
use. The guard parses the URN, extracts the name the allocation prefix applies to, and requires
the `traffic.` prefix.

It raises rather than returning `False` for: unparseable URNs, tuple/flat body mismatches, empty
name components, and — importantly — **entity types it does not recognise**. A guard that guessed
at an unfamiliar URN shape could authorise a write into another submission's graph. The cost of
failing closed is a one-line addition to `_TUPLE_NAME_INDEX` when a new entity type is needed.

`schemaField` URNs recurse into their parent dataset, so a column-level operation on another
project's table is refused even though the field name itself looks innocuous.

Filesystem deletions in the reset path are separately bounded by `require_contained_path`, which
resolves symlinks before comparison.

## ADR-003: Single-page HTML + SSE instead of React/Vite

**Status:** Accepted
**Date:** 2026-07-24

`PROJECT_BRIEF.md` suggests React, TypeScript, and Vite. The coordinator UI is instead one
self-contained HTML page driven by server-sent events.

The demo surface is three proposal rows changing state, one highlighted lineage path, lease
countdowns, and an event timeline. That does not require a component framework. Removing the
second toolchain removes a build step from the judges' setup instructions — Submission Quality is
scored on reproducibility — and removes a build stage from the coordinator's promotion flow, since
the service deploys as a single process on port 8105.

Reversible: the API is JSON plus SSE, so a React front end can be added later without changing the
coordinator or the deployment contract.

## ADR-008: Prepared tokens are persisted, not held in process memory

**Status:** Accepted
**Date:** 2026-07-25

The first implementation kept prepared tokens in a dict on the coordinator. Because the API builds
a fresh runtime per request, every commit failed: the token issued during prepare no longer
existed. Caught by the API tests.

Tokens now live in a `prepared_tokens` table. This is the correct design independently of the bug:
a prepared transaction must outlive the request that created it and must survive a restart, and
persisting the registry makes the set of outstanding commit capabilities auditable rather than
invisible.

## ADR-009: Downstream validation is scoped to assets the proposal owns

**Status:** Accepted
**Date:** 2026-07-25

Validation originally failed any proposal whose rename left *any* downstream artifact referencing
the old column. That deadlocks the product: Agent A's rename breaks Agent B's metric, but B cannot
rebase until A lands, so no upstream schema change could ever commit.

The downstream check is now limited to assets in the proposal's own write set. Cross-owner
breakage is detected at prepare time by the conflict engine, which orders the two proposals and
requires the downstream one to rebase. That sequencing is the product; the validator's downstream
check is only a self-consistency guard for a proposal that owns both ends.

## ADR-010: Description is the writeback aspect, and the write is always reversed

**Status:** Accepted with environment constraint
**Date:** 2026-07-25

The coordinator permits tag, description, and structured-property writes, but only after a smoke
test against pinned DataHub Core v1.6.0 confirms the aspect. That smoke test has not run, so the
most broadly supported mutable aspect is used: `update_description`.

Its previous value can be captured and restored exactly, which makes the whole writeback
reversible. The cycle is capture → write → immediate re-read → restore, with restoration in a
`finally` block so a failed verification still leaves the shared instance as found. The receipt
records what was observed rather than what was assumed; `verified` is true only when the re-read
returned the written value.

## ADR-011: Receipts record token fingerprints, never tokens

**Status:** Accepted
**Date:** 2026-07-25

A prepared token is a capability: holding it permits a commit. Receipts therefore store a one-way
SHA-256 prefix instead, which still ties a lease receipt to its commit receipt without the file
being usable to authorise anything.

The receipt sanitiser redacts any key matching a secret-bearing word, with a deliberately narrow
exemption for keys ending in `_fingerprint`.

## ADR-004: Readiness is authenticated, entity-aware, and non-mutating

**Status:** Accepted, superseding the original liveness-probe design
**Date:** 2026-07-25

`/api/readiness` must verify DataHub without mutating shared state. Three rules:

1. **Non-mutating.** The first implementation wrote and deleted a probe file to test writability.
   That is a mutation and is now forbidden; writability is checked with `os.access`. Readiness
   calls no mutation tool.
2. **A liveness ping is never sufficient.** The original design called an unauthenticated GET on
   the GMS health endpoint. That proves a container is running, not that this project can do its
   job. Live readiness now requires authenticated MCP calls proving: the required read *and* write
   tools exist, this project's tag resolves, and its allocated `traffic.` entities are actually
   present. A reachable server with an empty graph is **not** ready.
3. **Mode is derived, not configured.** Live mode requires both `DATAHUB_MCP_URL` and
   `DATAHUB_TOKEN`; an endpoint without credentials cannot perform the checks above, so it is not
   live mode. In fixture mode the service is ready only in a `local`, `test`, or `dev`
   environment, so a deployed instance missing its credentials fails closed rather than quietly
   serving fixture data and appearing healthy.

Silently degrading to fixtures when a real instance was expected would let a deployment claim
DataHub integration it does not have, which would make the submission's central claim untrue.
