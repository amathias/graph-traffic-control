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

## ADR-004: Readiness treats an unconfigured DataHub as ready, an unreachable one as not ready

**Status:** Accepted
**Date:** 2026-07-24

`/api/readiness` must verify DataHub connectivity without mutating shared state. Two distinct
situations need different answers:

- **Unconfigured** (`DATAHUB_GMS_URL` / `DATAHUB_TOKEN` unset): report `not_configured` and stay
  ready. Phases 0-4 and 6 are fixture-backed by design and must remain runnable and testable with
  no DataHub at all.
- **Configured but unreachable**: fail readiness with HTTP 503. Silently degrading to fixtures
  when a real instance was expected would let a deployment claim DataHub integration it does not
  have, which would make the submission's central claim untrue.

The probe is a read-only GET against the GMS health endpoint with a short timeout, and never
raises.
