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

## ADR-018: Plans are emitted as typed MCP wrappers, and the payloads are real aspect shapes

**Status:** Accepted
**Date:** 2026-07-26

`apply_plan` used to call `emitter.emit(op.as_dict())`. `DatahubRestEmitter.emit` dispatches on
**type**: anything that is not a `MetadataChangeProposal` or `MetadataChangeProposalWrapper` is
treated as a `MetadataChangeEvent` and dereferenced as `item.proposedSnapshot`
(`rest_emitter.py:778-811` in `acryl-datahub==1.6.0.15`). A `dict` therefore never reached the
network at all. Every `gtc-datahub-seed --apply` died locally with
`AttributeError: 'dict' object has no attribute 'proposedSnapshot'`.

Each operation is now converted through `ASPECT_MAP[aspect].from_obj(...)` into a typed aspect and
wrapped in a `MetadataChangeProposalWrapper` — the supported SDK path under coordinator ruling 3.

**Converting the whole plan happens before the first emit**, mirroring the whole-plan namespace
guard: the two failure modes detectable without a network are both resolved while the applied
count is still zero.

### What the raw dict was hiding

A dict is not validated by anything. Handing the SDK real aspect classes immediately surfaced
three further defects that had never been executed:

| Defect | Effect if it had reached a live instance |
|---|---|
| `schemaMetadata` had no `platformSchema`, and its field `type` was a bare string rather than a `SchemaFieldDataType` union | 8 of 49 seed operations unconstructible |
| `dashboardInfo` had no `lastModified` | 1 more unconstructible; the restore path for a captured dashboard also lacked `title` |
| Soft delete used `changeType: DELETE` on `status` | **Removes the `status` aspect, which un-deletes a soft-deleted entity.** Reset and the absent branch of restore would have left this project's rows live in a shared catalogue |

A soft delete is now an `UPSERT` of `status` with `removed: true` — the form the SDK itself uses.
Nothing this project emits is a destructive removal, as coordinator ruling 4 requires.

Audit stamps are fixed at the epoch with a constant actor. They are required fields, and a
wall-clock value would make every plan differ from the last and destroy the fingerprint the
coordinator diffs runs against.

**This changes the seed plan fingerprint**, because it changes what the plan says. The old
fingerprint identified a plan that could not be emitted.

### Unknown payload keys are refused

`from_obj` **silently discards** keys it does not recognise — verified against the real classes: a
misspelt field is dropped and the operation reports success having written nothing. Reads already
fail closed on an unrecognised shape (ADR-012); `_require_known_payload_keys` makes the write path
do the same, or the guarantee is one-sided.

Column types map to DataHub schema types through an explicit table and an unknown type is
**refused, not defaulted**. `NullType` as a fallback would be a silent lie about every column the
project has not been taught.

### Partial applies are reported truthfully

A mid-run failure raises `PartialApplyError` carrying how many operations were applied, which one
failed, and the recovery command. A bare failure would be untruthful in the most expensive way:
the shared instance has already been written to, and an operator who reads "seed failed" and
assumes "nothing happened" leaves this project's rows in a catalogue four submissions share.

### Why the suite missed all of this

The emitting loop was marked `# pragma: no cover - requires a live instance`. A boundary excluded
from coverage *and* untested is a boundary nobody has ever executed, and a plan being inert,
deterministic, and beautifully guarded says nothing about whether it can be emitted at all.

`tests/test_datahub_sdk_boundary.py` covers it against a double whose behaviour was read out of
the pinned SDK rather than its documentation. `tests/test_datahub_sdk_pinned.py` removes the
remaining claim: with the optional extra installed, every operation of every plan this project can
build is constructed as a real typed aspect and serialised to the bytes the emitter would send,
and the double's own field sets are asserted against the real ones so it cannot drift.

## ADR-019: Empty is not unknown, and a lineage sink is not asked

**Status:** Accepted
**Date:** 2026-07-26

The first live run of the promoted build seeded successfully and then failed on `/api/graph`:

```
Could not read downstream lineage for urn:li:dashboard:(looker,traffic.dash_exec_revenue):
get_lineage downstreams.searchResults is a NoneType; expected a list
```

All nine allocated entities were present and individually readable. One question that should not
have been asked was answered in a shape the reader did not accept.

### Two fixes, two separate claims

**1. A dashboard is a lineage sink and is not asked for downstream lineage.**
`DOWNSTREAM_LINEAGE_URN_PREFIXES` mirrors the existing `SCHEMA_BEARING_URN_PREFIXES` precedent:
`list_schema_fields` was already skipped for entities with no columns, for exactly this reason.

The completeness claim is that skipping loses no edge, and it is *proved*, not asserted. This
project's own seed plan makes a dashboard's edges its `dashboardInfo.datasets` **inputs**; no
operation this project can build ever names a dashboard as an upstream. The edge into the
dashboard is therefore discovered when the dataset at the other end is queried. The test asserts
the snapshot's edge set equals the fixture's edge set exactly, including that edge.

**2. `searchResults: null` is a valid empty answer — and only `null`.**
It is a *successful* response carrying no results, which is a value. A tool error still arrives as
`McpError` and still aborts the whole read. Nothing here swallows a failure.

The allowance is deliberately as narrow as the evidence:

| Response | Result |
|---|---|
| `searchResults: [...]` | read normally |
| `searchResults: []` | empty |
| `searchResults: null` | empty — the observed live variant |
| `searchResults` key absent | **raises** — a shape never observed, not guessed at |
| `searchResults: ""`, `0`, `false`, `{}` | **raises** |

The last two rows are the point. Had this been written as "falsy means no edges", a genuine read
failure would become an empty graph — the exact ADR-012 failure this project was rejected for
once already.

### Why the suite was green while the live instance failed

The protocol double returned `{"searchResults": []}` unconditionally. It could not produce the
one shape the real server produces, so no test could have caught this. The double now answers
`null` for lineage sinks exactly as the live instance does.

A double is only worth what it faithfully reproduces. This is the same lesson as ADR-018, where
the emitter boundary was doubled from documentation: **the double must be corrected from observed
behaviour the moment observed behaviour contradicts it.**

### Readiness must answer for the snapshot the API serves

Readiness returned **200** while `/api/graph` returned **503**. Every check it ran passed, because
none of them read lineage. That is worse than having no readiness check: it certified the outage.

Readiness now builds the real snapshot through the same provider `/api/graph` uses, in both live
and fixture mode, and reports the entity count, edge count, and fingerprint it built. A new
`graph_unreadable` status distinguishes "the catalogue is incomplete" from "the catalogue is
complete but the graph will not build", so an operator is not sent to re-seed a correctly seeded
instance. It remains strictly non-mutating: `snapshot()` only reads.

The invariant is now asserted directly — readiness may not be ready while `/api/graph` would 503,
tested through the real HTTP surface in both directions.

## ADR-020: An edgeless live graph is a hard stop, not an empty result

**Status:** Accepted
**Date:** 2026-07-26

The second live run reached `/api/readiness` → **503, `graph_unreadable`** with all nine allocated
entities found, failing on:

> downstream lineage for `urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_revenue,PROD)`
> has no `searchResults` key; observed keys are `facets` and `total`

The `facets` aggregations reported `count: 0` for every degree bucket.

### The 503 was correct, and accepting the shape as "empty" would have been the wrong fix

`traffic.fct_revenue` **has** a downstream. The seed applied `upstreamLineage` on
`traffic.metric_net_revenue` naming `fct_revenue` as its upstream, and all 49 operations were
accepted. A downstream query on `fct_revenue` that returns zero matches is therefore not reporting
the graph — it is reporting that the **lineage index cannot see the graph**, which is consistent
with the shared OpenSearch instance having been recovered without the graph service being
reindexed.

Measured, against the protocol double emitting that exact envelope for every dataset:

```
entities: 9   edges: 0    -> /api/graph would answer HTTP 200
```

Nine correct entities and no lineage. That snapshot answers **"nothing conflicts"** to every
question asked of it, and this project's central claim is precisely an edge — the conflict between
two proposals that share no declared URN and are connected only by DataHub lineage. A judge would
have watched A and B commit in parallel as unrelated changes. The loud 503 is enormously preferable
to that, so the fix is deliberately *not* "make the read succeed".

### Two changes

**1. The envelope is read properly, so the diagnosis is accurate.** `facets`/`total` without
`searchResults` is a real response shape. `total` — the server's own count of matches — decides
what it means, not the absence of a key:

| `downstreams` contents | Result |
|---|---|
| `searchResults: [...]` / `[]` / `null` | read / empty / empty (ADR-019) |
| no `searchResults`, `total: 0` | empty |
| no `searchResults`, `total: n > 0` | **raises** — told there are matches, given none |
| no `searchResults`, no integer `total` | **raises** — unrecognised |

A boolean `total` raises too: `True == 1` in Python, and a flag is not a count. **The first
implementation of this got that wrong — see ADR-021.**

**2. Readiness verifies the seeded lineage reads back.** This is ADR-004's complete-catalogue rule
applied to edges, for the identical stated reason — *a partial graph reports fewer conflicts than
really exist* — and it matters more for edges than for entities, because an edgeless graph is not a
degraded answer but a confidently wrong one.

Missing seeded edges produce a new `lineage_incomplete` status, distinct from
`entities_missing`. The distinction is operational, not cosmetic: entities present + edges missing
is an **index** problem, and the detail line says so explicitly — *reindex the graph service, do
NOT re-seed*. Sending an operator to re-seed a correctly seeded shared instance would be a
destructive answer to a read-only problem.

One missing edge is enough to refuse. There is no "mostly complete" tolerance, because the hidden
conflict this project exists to demonstrate rides on exactly one edge.

Extra live lineage does not fail readiness. The guard is about seeded edges that have gone
missing, not about forbidding lineage the instance legitimately grew.

### The double, again

The protocol double could not produce this envelope either, so the suite could not have caught it —
the same gap as ADR-018 and ADR-019, third time. `FakeMcpState.facet_only_downstreams` now emits
it verbatim. The lesson stands: **the double is only worth what it faithfully reproduces, and every
live observation that contradicts it is a correction to make immediately.**

## ADR-021: Prove the type before comparing the value

**Status:** Accepted
**Date:** 2026-07-26

ADR-020 stated that a boolean `total` is not a count and must fail closed. The implementation did
not do that. Coordinator review of `caf03d4` found:

```python
total = downstreams.get("total")
if total == 0:          # <-- False == 0 is True
    return []
if isinstance(total, int):
    raise ...
```

`bool` subclasses `int` and `False == 0`, so a JSON `false` was accepted as "no downstream
matches" — the exact outcome ADR-020 exists to prevent, in the code ADR-020 shipped.

### Why the tests missed it

The regression covered `True` and not `False`. `True` never reaches the value comparison —
`True == 0` is false, so it falls to the `isinstance` branch and raises. Only `False` takes the bad
path. Testing one boolean gave the appearance of covering both, and a passing `True` case is
actively misleading evidence here.

Both are now parametrised, and the parametrisation is the point rather than tidiness: for this
class of defect, asserting one member of a pair proves nothing about the other.

### The same bug had a second instance

Re-running the widened regression against the shipped reader showed `total: 0.0` was **also**
accepted, for the same reason: `0.0 == 0`. It was never reported, because nobody thought to send a
float. A value-first check does not have one hole; it has as many holes as Python has types that
compare equal to zero.

### The rule

**Prove the type, then compare the value.** Never the other way round on data that crossed a
network boundary:

```python
if isinstance(total, bool) or not isinstance(total, int):
    raise ...          # bool excluded explicitly, before any comparison
if total < 0:
    raise ...          # not a possible count
if total == 0:
    return []          # now provably a real integer zero
raise ...              # positive: matches claimed but withheld
```

Exactly one value is accepted as empty: integer `0`. `False`, `True`, `0.0`, `"0"`, `None`, `[]`,
`{}`, and negatives are all refused. Negative totals gained their own refusal — a negative match
count is malformed, and the previous message would have described it as "reports -1 match(es)",
which is not a truthful reading of a nonsense value.

### Standing lesson

This is the fourth consecutive defect (ADR-018, 019, 020, 021) at the boundary where external data
enters, and the third where the test suite's shape was the reason it went unnoticed. The pattern is
consistent: a check that is *nearly* right passes every test written by whoever wrote the check.
Widening a regression to the full set of adversarial values — not just the one that failed in
production — is what turns a fix into a guarantee.

## ADR-022: The description operation default is `replace`, and it is live-confirmed

**Status:** Accepted
**Date:** 2026-07-27

`DATAHUB_DESCRIPTION_OPERATION` defaults to `replace`. It previously defaulted to `SET`.

The coordinator supplied the *argument names* for `update_description` as observed contracts. It
never supplied this *value* — `SET` was inferred from DataHub's aspect vocabulary, where change
types are `UPSERT`/`DELETE` and setting a value reads naturally as `SET`. That inference was
plausible, documented as unverified in `docs/LIMITATIONS.md`, and wrong.

Live DataHub Core v1.6.0 **rejected `SET`**. The same reversible capture → write → re-read →
restore cycle then **succeeded with `replace`**, and the entity was returned to its original
description. Final receipt SHA-256
`621e022bc1253990be5fe328da8186ecc6be2d675d8242514d3ef81866db8782`.

### Why the default changes rather than the runbook

The live gate passed because the operator supplied `replace` in the host environment. Leaving the
default at `SET` would make every future deploy depend on someone remembering an override that is
not written down in the code — and the failure would be a *rejected writeback*, which surfaces as
a proposal that cannot reach `COMMITTED`. Making the confirmed value the default means a fresh
deploy is correct with no environment tuning, and the override remains for a server that wants
something else.

### Why a regression asserts the literal string

`test_writeback.py` asserts the module default, the settings default, the value a writeback is
constructed with, and the value shipped in `.env.example`, each against the exact string
`replace`, plus an explicit assertion that neither default has reverted to `SET`.

Asserting "some non-empty default" would have passed with `SET` in place. The failure mode here is
not a missing value, it is a **plausible-looking wrong one**, and the entire reversibility
guarantee rests on the operation having replace-in-place semantics: an append-style operation
cannot restore a captured original exactly, so a wrong value here silently converts "left as
found" into "left with our note appended".

### What this does not change

Nothing about the write sequence, the verification signals, or the restore logic. The value is
still settings-driven and still overridable. The deployed product `5ea880f` is unaffected in
behaviour — it ran with `replace` supplied in its environment, which is the identical value.
