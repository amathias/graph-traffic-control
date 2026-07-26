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

## ADR-012: Reads fail closed; a degraded read is never an empty graph

**Status:** Accepted, superseding the tolerant-extractor design in the rejected candidate
**Date:** 2026-07-25

The first DataHub provider was written from documentation rather than observed payloads. It
accepted several plausible response shapes, degraded to "field unknown" on a mismatch, and
swallowed MCP errors into an empty edge list. The reasoning recorded at the time was that a wrong
guess would produce a missing field rather than a wrong conflict decision.

That reasoning was wrong, and the coordinator rejected the candidate for it.

**An empty graph is not a neutral result.** It is indistinguishable from a graph with no
conflicts. A swallowed lineage failure therefore converts "the coordinator cannot see the graph"
into "nothing conflicts, commit away" — the most dangerous possible misreading, arrived at
silently.

The rule now: every read raises `ContextReadError` on a transport failure, a tool error, or a
payload outside the documented contract. `McpContractError` covers the shape case specifically.
`prepare` aborts and audits `fail_closed=context_read`; the pre-commit re-read aborts the same
way; `GET /api/graph` answers 503. A partial snapshot is never produced, because a missing entity
or edge changes conflict decisions.

Two distinctions this deliberately preserves:

- **A missing optional value is not an unknown shape.** An entity with no description, no owners,
  or no domain is a legitimate answer and yields `None` or an empty list.
- **Not asking beats tolerating an error.** A dashboard has no columns, so `list_schema_fields`
  is not called for entity types that have no schema. The previous code asked anyway and then had
  to tolerate the resulting error — which is precisely the tolerance that hid real failures.

## ADR-013: `COMMITTED` requires positive verification of every step

**Status:** Accepted
**Date:** 2026-07-25

`COMMITTED` previously meant the executor and the writeback had returned without raising. That is
weaker than it reads. The executor returning cleanly does not say the intended bytes are on disk,
and a writeback call succeeding does not say DataHub holds the written value.

`CommitVerification` now tracks seven independent signals: mutation applied, mutation re-read,
validation passed, writeback attempted, writeback verified, writeback restored, artifact rolled
back — plus the receipts written. A proposal reaches `COMMITTED` only when the artifact mutation
is confirmed by **re-reading the file from disk**, validation passed, and — when a writeback was
attempted — DataHub **returned the written value on re-read**. Any failure rolls the artifact back
and aborts, so a half-applied change cannot survive.

`commit_permitted()` is re-checked at the end of the commit path rather than assumed from the
control flow above it. Adding a step without adding it to the gate should fail, not quietly widen
what "committed" means.

**Verification and restoration are separate facts.** A verified write whose restoration failed
still proves the write landed; the receipt records the unrestored value loudly rather than
collapsing both into one boolean. Restoration failure therefore does not retract a commit, but is
never reported as success. Collapsing them would hide which of the two actually happened, and
"we left the shared instance dirty" is exactly the thing a reader needs to know.

## ADR-014: DataHub state changes are planned, guarded whole, and applied only on request

**Status:** Accepted
**Date:** 2026-07-25

Seed, reset, and restore operate on an instance shared with four other submissions. Each is
therefore produced as an inert, inspectable list of aspect operations and guarded **as a whole**
before anything is applied.

Guarding the complete plan up front means a plan containing one foreign URN is refused entirely,
rather than applied up to the bad entry and leaving the shared instance half-modified. It also
makes the plan deterministic and fingerprintable, so a coordinator can diff what a run *would* do
against what a previous run did, and can apply it on the host without this project ever holding
credentials.

Three specific guards:

- **Payloads are guarded, not just addresses.** An aspect attached to one of our datasets can
  name someone else's dataset as an upstream, or another project's domain. Guarding only the
  entity URN would let that through and write a cross-project edge.
- **Reset takes an explicit scope and accepts only `namespace`.** A global refresh has to be
  asked for and be refused, rather than being an omission that silently widens the blast radius.
  Deletes are soft, and the generated ingestion recipe disables stale-entity removal — with it
  enabled, ingesting only our allocation would mark everyone else's entities stale.
- **Empty plans are refused.** An empty seed, reset, or restore that exits 0 is a claim that work
  happened when none did.

`--apply` is opt-in and refuses without live credentials; a plan is not applied on a guess about
where it would land. `apply_plan` re-guards immediately before emitting, so a plan mutated after
being built cannot ride the earlier check.

## ADR-015: The judge console ships inside the package and fetches nothing

**Status:** Accepted, refining ADR-003
**Date:** 2026-07-25

The console is a single self-contained document served at `/` from inside the package: inline
CSS, inline JS, inline SVG, no external stylesheet, script, font, or fetch target. Judges may
review on a locked-down machine, and a page that degrades without a CDN would make the project
look broken for a reason that has nothing to do with the project.

Its scenario runs in a dedicated state directory that is reset and reseeded on each press. A
judge may press the button more than once, and doing that to the live state directory would
destroy proposals submitted through the API alongside it.

Because the console is the only non-Python runtime asset, it is the only one a packaging change
can drop while every source-tree test still passes. Archive verification therefore requires it in
the wheel explicitly, and reads it back from an installed package in a directory with no access
to this source tree.

Two static guards stand in for a browser, since none was available: the inline script is parsed
with `node --check` (skipped when node is absent), and every element id the script looks up must
exist in the document. Both catch failures that would leave the console blank while the payload
tests stayed green.

## ADR-016: Absence is a captured value, not a gap

**Status:** Accepted, extending ADR-014
**Date:** 2026-07-25

ADR-014 made seed, reset, and restore inert, guarded plans, and required capture to run before
seed so a shared instance is left as found. That contract had a hole at exactly the moment it
matters most: the **first** seed.

On a first run the whole `traffic.` namespace is absent, so capture — which fails closed on any
allocated entity it cannot read — refuses. The instructions therefore told an operator to run a
command that cannot succeed, and the only ways past it were to skip capture (leaving nothing to
restore to) or to seed first and capture the seeded state as if it were the original (recording
this project's own rows as the state to return the shared instance to). Both leave the catalogue
permanently dirty.

The fix is to make **absence a value the capture records**, rather than a gap it tolerates:

- `gtc-datahub-capture --allow-absent` records each allocated URN as `present` (with its full
  state) or `absent`. Without the flag, a missing entity is still a hard failure — "the namespace
  does not exist yet" and "half this project's rows have disappeared" look identical to a reader,
  and only the operator knows which one is true, so the operator has to say.
- A soft-deleted entity counts as absent. DataHub still returns it, so presence in a response is
  not presence in the catalogue.
- `restore` returns present entities to their captured values and initially-absent entities to a
  **soft-deleted** state. That is the only honest reading of "leave it as you found it" for an
  instance that never had these entities: delete what this project created.
- After `--apply`, the initially-absent entities are **re-read and proved absent**. A restore
  that soft-deleted nine of ten entities would otherwise report success while leaving the tenth
  in a shared catalogue under this project's name. If MCP credentials are not available to
  perform that re-read, the restore fails rather than reporting an unverified success.

Every input to this is checked for exactness rather than containment, because each loose check is
a specific wrong restore:

| Refused | The restore it prevents |
|---|---|
| Partial capture | Leaves the entities it missed behind |
| Extra URN (even in-namespace) | Writes to an entity this project never seeded |
| Foreign URN | Writes into another submission's graph |
| Same URN present *and* absent | No single end state to restore to |
| Unrecognised `kind` / `capture_version` | Cannot distinguish "absent" from "not looked at" |

Seed is held to the same standard: it must create **exactly** the manifest allocation, so the set
that was captured as absent, the set that is created, and the set that is restored are provably
the same set.

**No global or search-based path exists anywhere in this.** Absence is only ever established by
reading the exact allowlisted URNs one at a time; there is no wildcard, no search tool call, and
no scope value other than `namespace` — restore now refuses any other scope for the same reason
reset always has.

## ADR-017: The DataHub optional dependencies are pinned exactly

**Status:** Accepted
**Date:** 2026-07-25

`acryl-datahub` and `mcp` are pinned to `==1.6.0.15` and `==1.28.1` rather than to compatible
ranges.

Everything this project does with them fails closed on an unrecognised shape (ADR-012). That is
the right behaviour, and it is precisely why a floating range is wrong here: the argument names,
response envelopes, and aspect shapes were observed against these versions, so a patch release
that moved a key would not degrade gracefully — it would turn a working deployment into a refusal
at the first read, on a shared instance, during a demo.

Pinning also makes the deployment reproducible in the one way that matters to the coordinator: the
host installs the same two versions this project was built and reasoned about, and a version bump
becomes a deliberate change with a smoke test attached rather than a side effect of when `pip`
happened to run.
