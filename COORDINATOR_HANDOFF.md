# Coordinator Handoff: Graph Traffic Control

## Relationship to the portfolio coordinator

This project chat owns Graph Traffic Control's product, code, tests, demo, evidence, and
submission. The portfolio coordinator at `../COORDINATOR_PLAN.md` owns the shared DataHub and AWS
deployment contracts.

Before changing a port, public route, shared environment variable, DataHub namespace, deployment
topology, or global reset behavior, submit the proposed change to the coordinator. Do not edit the
live EC2 host from this project chat.

## Fixed project allocation

| Setting | Value |
|---|---|
| Project slug | `graph-traffic-control` |
| Internal port | `8105` |
| DataHub domain | `Demo / Graph Traffic Control` |
| Required DataHub tag | `project-graph-traffic-control` |
| Entity prefix | `traffic.` |
| Fixture root | `demo/fixtures/graph-traffic-control` |
| State root | `/var/lib/datahub-hackathon/graph-traffic-control` |

## Project-chat obligations

- Build only Graph Traffic Control business behavior.
- Keep proposals, leases, mutations, evidence, and reset operations inside this allocation.
- Fail closed if a commit or reset target falls outside the `traffic.` namespace.
- Implement `GET /api/health` and `GET /api/readiness`.
- Keep the project independently runnable without the other four submissions.
- Update the milestone handoff below whenever deployment-facing behavior changes.

## Milestone handoff

| Field | Current value |
|---|---|
| Status | `in progress` |
| Milestone | **Live capture and seed succeeded on the shared instance.** The live `/api/graph` lineage contract failure is fixed, and readiness can no longer report ready while `/api/graph` fails. Awaiting redeploy and the proposal/writeback leg. |
| Verified commit/artifact | See "Deployment candidate" below |
| Build command | `python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| DataHub extra | `pip install -e ".[datahub]"` on the host — **pinned exactly** to `acryl-datahub==1.6.0.15` and `mcp==1.28.1` (ADR-017). **Installed in a throwaway local venv this session** to verify the emitter contract against the real library; never pointed at any instance, and no network call was made with it. |
| Test command | `.venv/Scripts/python.exe -m pytest` — **583 passed, 1 skipped** in 183 s, no network required. The skip is `test_datahub_sdk_pinned.py`, which needs the optional extra. |
| Test command (with the extra) | `pytest` in a venv that also has `.[datahub]` — **594 passed, 0 skipped**. This is the host configuration. |
| Coverage | `pytest --cov=graph_traffic_control` — **89%** (2698 statements, 289 missed). `demo/datahub_state.py` is now **96%**: the emitter boundary is executed by tests rather than excluded by a `pragma: no cover`, which is what let the blocker through. The largest gap remains `release/archive.py` at 30%: its end-to-end path builds distributions and creates a virtual environment, so it runs as the `gtc-archive-verify` release command rather than in the suite. |
| Lint command | `.venv/Scripts/python.exe -m ruff check .` — clean |
| Build/archive check | `gtc-archive-verify` — **8/8 pass**, including a clean-environment wheel install |
| Safety scan | `gtc-safety-scan` — **0 blockers, 0 warnings** across 82 tracked files |
| Seed / reset | `gtc-seed` / `gtc-reset` (local, offline, always safe) |
| DataHub state | `gtc-datahub-seed`, `gtc-datahub-reset`, `gtc-datahub-capture`, `gtc-datahub-restore` — **plan-only by default**; `--apply` requires live credentials |
| DataHub artifacts | Under `APP_STATE_DIR/datahub/`: `pre_seed_capture.json`, `seed_plan.json`, `reset_plan.json`, `restore_plan.json`, `ingestion_recipe.yaml`. These names are asserted by the suite against this document, the README, and the runbook, so they cannot drift from the docs again. |
| Partial apply | If `--apply` fails part way, it raises with **how many operations were applied**, which one failed, and `gtc-datahub-restore --apply` as the recovery. "Seed failed" is never readable as "nothing happened". |
| First-time seeding | `gtc-datahub-capture --allow-absent` records the deliberate absence of the exact allocation, seed creates exactly that set, and restore soft-deletes it back to absent and **re-reads to prove it** (ADR-016). Absence never enters a capture implicitly. |
| Demo command | `gtc-demo [--export-examples examples]` |
| Run command | `gtc-api` (uvicorn, `APP_HOST`:`APP_PORT`) |
| Judge UI | `GET /` — self-contained page; one button runs the whole scenario |
| Health endpoint | `GET /api/health` — verified 200 on a running server |
| Readiness endpoint | `GET /api/readiness` — verified 200 seeded (fixture mode), 503 unseeded, 503 in non-local env without credentials, and **503 whenever the graph snapshot will not build**, so it can no longer report ready while `/api/graph` fails (ADR-019). Reports `graph_entities`, `graph_edges`, and `graph_fingerprint`. |
| Persistent volumes | `APP_STATE_DIR` (default `demo/state`) holds `transactions.sqlite`, `artifacts/`, `receipts/`, `datahub/` (plans), `judge/` (judge-run state). Disposable and recreated by `gtc-seed`. **SQLite means a single writer: run one replica.** |
| Long-running workers | None. Single uvicorn process, no background jobs. |
| DataHub read | **Partially verified live.** The coordinator's live run read the catalogue successfully; the graph read then failed on dashboard downstream lineage, which is fixed here (ADR-019). The fix itself is verified against the protocol double, now corrected to emit the live server's shape. **This session made no connection to the shared instance.** |
| DataHub writeback | **Not verified live.** Reversible capture → write → re-read → restore, with verification and restoration tracked independently. **No live receipt exists.** |
| DataHub ingestion | **Applied live by the coordinator.** All 49 typed operations of plan `cd44112ebd42b7de` were accepted by the shared instance. This build changes no plan — the fingerprint is byte-identical, so **do not seed again**. |
| DataHub emission | **Verified against the pinned SDK, offline.** All 103 operations across the seed, reset, and both restore plans construct as real typed aspects and serialise to the bytes the emitter would send. No emitter was ever connected. |
| Blockers | The proposal/writeback leg has not been run live and has no receipt. Live access requires SSM, which this session was instructed not to use. Everything else is complete. |
| Evidence produced | 583 passing tests offline / 594 with the extra; live capture and seed evidence recorded below; `examples/`; sanitized receipts under `APP_STATE_DIR/receipts`; `docs/DECISIONS.md` ADR-001..019; `docs/LIMITATIONS.md`; `docs/SUBMISSION.md`; `docs/DEMO_RUNBOOK.md` |

## Deployment candidate

| Field | Value |
|---|---|
| Branch | `main` |
| Product candidate | `754abcb` — full SHA `754abcb2ddb40892b8a6817534fa39a6a39ce202`. Supersedes `0f400a7`, whose `/api/graph` fails against the live instance. **Deploy only — do not capture or seed again.** |
| Tree state | Clean at that commit; `git status` empty |
| Pushed to origin | `origin/main` |
| Verified at that commit | **583 tests pass, 1 skipped** offline and **594 pass, 0 skipped** with the pinned extra, **89% coverage** (2698 statements, 289 missed), `ruff check` clean, `gtc-archive-verify` **8/8** (including a clean-environment wheel install), `gtc-safety-scan` **0 blockers / 0 warnings** across 82 tracked files, four-agent scenario runs end to end, judge workflow reproduces the result below unchanged, health 200, readiness 200 and `/api/graph` 200 seeded |

### The seed plan fingerprint has changed

| | Value |
|---|---|
| Previous | `7f91a665a5e5495e` / `7f91a665a5e5495e27b267b5462ae46b78e93ffe7b0ece0af2cf09db5c4c525d` |
| Current | `cd44112ebd42b7de` / `cd44112ebd42b7de6818f89a1005972a584f6dcbf94fde52882960a30c911ef6` |

Still **49 operations over 9 entities** — the plan's size and coverage are unchanged. What changed
is the aspect payloads, which were not constructible against the pinned SDK. Any recorded
expectation of `7f91a665a5e5495e` is now stale, and it identified a plan that could not be emitted.

Judge-workflow result at that commit, fixture-backed, no DataHub, read from `POST /api/demo/run`:

```
context: fixture | graph fp: a50e2614d4f19532 (9 entities, 7 edges)
  prop-a-rename-revenue      COMMITTED  approved=release-manager
  prop-b-net-revenue-metric  COMMITTED  approved=release-manager
      WRITE_READ       ORDER   with prop-a-rename-revenue      lineage_hops=0
      UPSTREAM_SCHEMA  REBASE  with prop-a-rename-revenue      lineage_hops=1   <- the hidden conflict
  prop-c-support-sla         COMMITTED  approved=-
      SHARED_DOMAIN    WARN    with prop-a-rename-revenue      lineage_hops=0
      SHARED_DOMAIN    WARN    with prop-b-net-revenue-metric  lineage_hops=0
  prop-d-stale               ABORTED    (stale expected version)
audited transitions: 23 | receipts: 10
```

`lineage_hops` is `len(lineage_path) - 1`, matching the engine's own explanation string. The
previous revision of this document recorded that row as `lineage_hops=2` and described a "two-hop"
path; that counted the two *nodes* on the path, not the hops between them. The conflict itself is
unchanged — the corrected figure is **1 hop** across a two-node path,
`traffic.fct_revenue -> traffic.metric_net_revenue`.

The `UPSTREAM_SCHEMA / REBASE` row is the project's central claim: A and B **share no declared
URN**, and that DataHub lineage path is the only thing that connects them. C's shared-domain rows
are `WARN` and do not block, which is why C commits in parallel rather than queueing.

Promote this commit only for a **fixture-mode** deployment, or after the live checks below are
run on the host. In a non-local `APP_ENV` without `DATAHUB_MCP_URL` and `DATAHUB_TOKEN`, readiness
correctly returns 503, so the service will not report ready until credentials are supplied.

Artifact digests are reported by `gtc-archive-verify`, but the distributions are **not**
bit-for-bit reproducible — a digest identifies one specific build, it does not certify one.

## Live run of `0f400a7`: what happened, and what this build changes

**Product SHA for the fix:** `754abcb2ddb40892b8a6817534fa39a6a39ce202` (`754abcb`).

`0f400a7` was promoted and run against the shared instance. **Recorded as live evidence:**

| Step | Result |
|---|---|
| `gtc-datahub-capture --allow-absent` | Succeeded — **9 present, 0 absent**. The allocation already existed from an older coordinator baseline, so the capture recorded present state, not absence. |
| `gtc-datahub-seed --apply` | Succeeded — **all 49 typed operations applied**, plan fingerprint `cd44112ebd42b7de`. The ADR-018 fix held: no raw dict, no unconstructible aspect. |
| `GET /api/readiness` | **200** |
| `GET /api/graph` | **503** — `Could not read downstream lineage for urn:li:dashboard:(looker,traffic.dash_exec_revenue): get_lineage downstreams.searchResults is a NoneType; expected a list` |
| Proposal / writeback | **Not attempted.** No writeback receipt exists. |
| Shared OpenSearch | Recovered by the coordinator; unrelated to this project's code. |

Two defects, fixed here. ADR-019 has the full reasoning.

**1. A lineage sink was asked a question with no answer.** A dashboard has no downstream lineage,
and the live server says so with `searchResults: null`. Dashboards are no longer asked, mirroring
the existing rule that skips `list_schema_fields` for entities with no columns. **No edge is lost**
— a dashboard's inbound edge is discovered from the dataset at the other end, and the suite proves
the snapshot's edge set is identical either way rather than assuming it.

**2. `searchResults: null` is now understood as an empty result — and only `null`.** An absent
`searchResults` key, or a `""`/`0`/`false`/`{}`, still raises; a tool error still aborts the read.
The allowance is exactly as wide as the observed evidence and no wider, because "falsy means no
edges" is how a read failure becomes an empty graph.

**Why the suite was green.** The protocol double returned `{"searchResults": []}` unconditionally
and could not produce the shape the real server produces. It now answers `null` for lineage sinks
exactly as the live instance does. Both fixes were verified to be independently load-bearing:
removing either one fails the new tests.

### Readiness could report 200 while `/api/graph` returned 503

Every readiness check passed on the live instance because none of them read lineage. A readiness
endpoint that answers 200 while the endpoint it vouches for answers 503 does not merely miss the
problem — it certifies it.

Readiness now builds the **same snapshot** `/api/graph` serves, through the same provider, in both
live and fixture mode, and reports the entity count, edge count, and fingerprint it built. A new
`graph_unreadable` status distinguishes "the catalogue is incomplete" from "the catalogue is
complete but the graph will not build", so nobody is sent to re-seed a correctly seeded instance.
Still strictly non-mutating. The invariant — never ready while `/api/graph` would 503 — is
asserted through the real HTTP surface, in both directions.

### Recovery: deploy only. Do not capture or seed again.

**The shared instance is already correctly seeded and must be left alone.** This build changes no
plan: the seed plan is still 49 operations over 9 entities with fingerprint `cd44112ebd42b7de`,
byte-identical to what was applied. Only read-path and readiness code changed.

1. Deploy this SHA. **Do not run `gtc-datahub-capture`** — a capture taken now would record this
   project's own seeded rows as the state to restore to, and the pre-seed capture from the live
   run is the only correct one. **Do not run `gtc-datahub-seed --apply`** — it is already applied.
2. `GET /api/readiness` — expect 200 with `mode: "live"`, `status: "verified"`, and the new
   `graph_entities: 9`, `graph_edges: 7`, `graph_fingerprint` fields. If the graph is unbuildable
   readiness will now return 503 with `status: "graph_unreadable"` instead of a false 200.
3. `GET /api/graph` — expect 200, 9 entities, 7 edges, `source` naming DataHub.
4. Then the outstanding leg: one proposal through prepare/commit, and one reversible writeback.
   Keep the receipt and confirm `verified: true` **and** `restored: true`.

## The SDK-boundary blocker, and the three defects it was hiding

**Product SHA:** `0f400a7da09349cf14489ab082c80ac7caf9a03c` (`0f400a7`).

The coordinator's live gate found that `gtc-datahub-seed --apply` failed locally, before the first
network operation: `apply_plan` called `DatahubRestEmitter.emit(op.as_dict())`, and the emitter
dispatches on **type** — anything that is not an MCP or MCPW is treated as a `MetadataChangeEvent`
and dereferenced as `item.proposedSnapshot`. A dict never reached the network at all.

Confirmed, then fixed and re-verified, against the real `acryl-datahub==1.6.0.15` installed in a
throwaway local venv. **No instance was contacted, no token was handled, and no AWS or EC2 access
was used.**

Operations are now converted to typed `MetadataChangeProposalWrapper` objects via
`ASPECT_MAP[...].from_obj(...)`, and the **whole plan is converted before the first emit**, so a
bad payload costs zero writes. ADR-018 has the full reasoning.

Handing the SDK real aspect classes surfaced three defects that had never been executed, because a
dict is not validated by anything:

1. **`schemaMetadata` was unconstructible** — no `platformSchema` (a required, defaultless field),
   and the field `type` was a bare string where a `SchemaFieldDataType` union is required. 8 of 49
   seed operations.
2. **`dashboardInfo` was unconstructible** — no `lastModified`. The restore path for a captured
   dashboard was also missing `title`.
3. **The soft delete was inverted.** It used `changeType: DELETE` on the `status` aspect, which
   *removes* that aspect and therefore **un-deletes** a soft-deleted entity. Reset and the absent
   branch of restore would have left this project's rows live in a shared catalogue while
   reporting success. It is now an `UPSERT` of `status` with `removed: true`, the form the SDK
   itself uses. Nothing this project emits is a destructive removal (coordinator ruling 4).

Two hardening changes came with it:

- **Unknown payload keys are refused.** `from_obj` silently discards keys it does not recognise, so
  a misspelt field would be dropped and the operation would report success having written nothing.
  Reads already fail closed on unrecognised shapes (ADR-012); the write path now does too.
- **Partial applies are reported truthfully.** A mid-run failure raises with how many operations
  were applied, which one failed, and the recovery command.

**Why the suite missed it:** the emitting loop was marked `# pragma: no cover - requires a live
instance`. A boundary excluded from coverage *and* untested is a boundary nobody has ever
executed. `demo/datahub_state.py` is now 96% covered with the pragma gone from that path.

### On the capture filename

The gate also hit a stale expectation of the capture filename. The cause: `pre_seed_capture.json`
existed only as a constant in the source and **was named in no document at all**, so an operator
told to "capture, then inspect the capture" had to guess. It is now named in this document, the
README, and the runbook, and `tests/test_datahub_artifact_names.py` asserts the constants against
those documents in both directions, so a rename fails the suite instead of surfacing during a live
run.

## What changed after the rejection

The previous candidate (`61998c0`) was rejected before deployment. The substantive corrections:

1. **MCP contracts implemented exactly as observed**, replacing extractors written from
   documentation. Arguments and payload envelopes for `get_entities`, `get_lineage`,
   `list_schema_fields`, and `update_description`; governance read from its real nesting under
   `properties`, `ownership`, `tags`, and `domain`.
2. **Reads fail closed.** The previous design degraded a tool error or shape mismatch into an
   empty graph. That is indistinguishable from "no conflicts". See ADR-012.
3. **`COMMITTED` requires positive verification** of artifact mutation (re-read from disk) and
   DataHub writeback (re-read from DataHub), with seven independently tracked signals. ADR-013.
4. **Deterministic, namespace-guarded DataHub seed/reset/capture/restore** covering the complete
   `traffic.` graph, schemas, ownership, domain, tag, marker, and lineage. ADR-014.
5. **Readiness verifies the complete catalogue**, plus the tag and the domain — not a five-entity
   sample. Still strictly non-mutating.
6. **The protocol double now enforces the contract**, rejecting any argument set but the observed
   one, so an argument-name regression fails a test instead of passing quietly.
7. **Judge console, submission copy, demo runbook, archive verification, isolation tests, and
   release safety scanning** added.

## Deployment blockers cleared after coordinator verification

Coordinator verification passed the suite and Ruff but found two blockers. Both are fixed:

1. **DataHub optional dependencies pinned exactly** to `acryl-datahub==1.6.0.15` and
   `mcp==1.28.1`, replacing compatible ranges. Every read fails closed on an unrecognised shape,
   so a floating range would convert a patch release into a refused deploy against a shared
   instance. Asserted by the test suite. ADR-017.
2. **First-time seeding is now recoverable.** The previous instructions required capture before
   seed, but capture refused a missing allocated entity — which is every entity, on the first run.
   Absence is now an explicitly captured value with a fail-closed contract: `--allow-absent`,
   exact-allowlist checks on capture and restore, soft delete back to absent, and post-apply
   verification that the entities are actually gone. ADR-016.

Neither change touches the product behaviour the previous candidate was verified on.

## Live verification the coordinator must run on the host

None of these have been executed. They are the remaining gate between this candidate and a
truthful "reads and writes real DataHub context" claim.

1. `GET /api/readiness` with `DATAHUB_MCP_URL` and `DATAHUB_TOKEN` set. Expect `mode: "live"` and
   `status: "verified"`. Any other status is a real failure, not a configuration nuisance.
   Readiness now names precisely what is missing: tools, tag, domain, or specific entities.
2. Confirm the MCP server exposes `get_entities`, `get_lineage`, `list_schema_fields`, and
   `update_description`.
3. **Capture first, and expect the `traffic.` namespace to be absent on the first run.** Run
   `gtc-datahub-capture`. If it refuses because entities are missing, that refusal is correct and
   is telling you this is a first-time seed: re-run `gtc-datahub-capture --allow-absent` to record
   that absence deliberately. The capture is written to
   **`APP_STATE_DIR/datahub/pre_seed_capture.json`** — that exact filename, which restore reads
   from that exact path and nowhere else. Then `gtc-datahub-seed --apply`. Inspect
   `APP_STATE_DIR/datahub/seed_plan.json` first — it is inert and deterministic, and its
   fingerprint is printed. Ingestion is namespace-scoped with stale-entity removal disabled.
   Do **not** seed before capturing: a capture taken after a seed records this project's own rows
   as the state to return the shared instance to, and the catalogue never gets clean again.

   Artifact names are asserted by the suite against these documents, so they cannot drift again:
   `pre_seed_capture.json`, `seed_plan.json`, `reset_plan.json`, `restore_plan.json`,
   `ingestion_recipe.yaml`, all under `APP_STATE_DIR/datahub/`.
4. Run one writeback and keep the receipt. Verify `verified: true` **and** `restored: true`, and
   confirm in the DataHub UI that the description returned to its original value.
5. `gtc-datahub-restore --apply` at the end. If the capture recorded absent entities, restore
   soft-deletes exactly those and then re-reads them through MCP to prove they are gone; it
   refuses to report success otherwise, and refuses outright if `DATAHUB_MCP_URL` and
   `DATAHUB_TOKEN` are not both set to perform that re-read. Confirm the printed
   `verified absent: N ...` line, then confirm in the DataHub UI that no `traffic.` entity
   remains listed.
6. **Confirm the `update_description` `operation` value.** The argument *names* were supplied by
   the coordinator; this *value* was not. It defaults to `SET` and is configurable via
   `DATAHUB_DESCRIPTION_OPERATION`. It must have replace-in-place semantics, or restoration
   cannot return the captured original exactly.
7. Re-record `demo/fixtures/graph-traffic-control/graph.json` from the live instance so the
   offline suite reflects real shapes.
8. Open `/` and confirm the judge console renders. **No browser tooling was available in this
   session**, so the page's payload, script syntax, and element wiring are tested but its
   rendering is unconfirmed.

**A shape mismatch will now be loud.** Unlike the rejected candidate, an unrecognised payload
raises a fail-closed error naming the tool and the offending key, rather than silently producing
empty fields. Treat the first live run as shape discovery: expect it either to pass or to state
exactly what differs.

## Milestone evidence

| Coordinator gate | Status |
|---|---|
| Clean setup and tests pass | Verified — 552 passed / 1 skipped offline, 563 passed / 0 skipped with the pinned extra, 89% coverage, `ruff check` clean |
| Plans can actually be emitted | Verified offline against the real `acryl-datahub==1.6.0.15` — all 103 operations across seed, reset, and both restore plans construct as typed aspects and serialise to the emitter's wire form (ADR-018). **No emitter was connected and no instance was contacted.** |
| Distributions build and install cleanly | Verified — `gtc-archive-verify` 8/8, wheel installed and run in a fresh venv outside the source tree |
| Demo seed and reset deterministic | Verified — repeated seed byte-identical; reset idempotent and fixture-preserving; DataHub plans byte-identical with a stable fingerprint |
| Reads real context from shared DataHub | **Partially verified live** — capture read all 9 allocated entities from the shared instance; the graph read failed on dashboard lineage and is fixed here (ADR-019). Not yet re-run live. |
| Performs and verifies supported writeback | **Not verified** — implemented, reversible, verification and restoration tracked independently; no live receipt |
| Namespace and reset isolation tests pass | Verified — every surface that can reach shared state refuses all four sibling allocations (`lifeboat.`, `license.`, `forgetme.`, `fuzzer.`), unknown URN shapes, foreign `schemaField` parents, foreign domains and tags, and foreign URNs smuggled inside lineage and dashboard-input payloads |
| Global reset is impossible | Verified — reset **and restore** take an explicit scope and accept only `namespace`; every removal is a soft delete addressed to an exact allowlisted URN; no surface performs a search or wildcard read; ingestion recipe disables stale-entity removal |
| First-time seeding is recoverable | Verified — absence of the exact allocation can be captured deliberately, seed creates exactly that set, restore returns it to a verified soft-deleted state, and partial, extra, foreign, ambiguous, and version-mismatched captures are refused (ADR-016) |
| No secrets or private evidence publishable | Verified — `gtc-safety-scan` 0 blockers / 0 warnings across 78 tracked files |
| Judge can evaluate without infrastructure | Verified — `GET /` runs the full scenario fixture-backed, with no DataHub, cloud account, or paid service. **Rendering not visually confirmed** (no browser tooling this session) |
| Health endpoint works behind reverse proxy | Health/readiness verified on `127.0.0.1:8105`; the proxy path itself is untested from this chat |
| Demo does not depend on another submission | Verified — no cross-project imports; full demo runs with no DataHub |
| Handoff record current | This document |

## Vertical slice: what is implemented

- Strict proposal schema (`extra="forbid"`), rejecting malformed input, undeclared write targets,
  and path traversal in artifact paths.
- Nine-state transaction machine; every transition appends an audit event.
- Conflict matrix rows 1-8, including a **lineage-mediated conflict between proposals sharing no
  declared URN at all**, evidenced by the shortest directed path.
- Expiring leases with clock injection; expiry needs no sweeper, so an abandoned agent cannot
  strand a URN.
- Prepared tokens persisted in SQLite (ADR-008) — they must survive across requests.
- Pre-commit graph re-read with subgraph fingerprint comparison; **any drift aborts**.
- Real SQL artifact mutation with rollback on validation failure.
- Reversible writeback with restoration in a `finally` block.
- Sanitized receipts; capability tokens stored only as one-way fingerprints (ADR-011).
- Strong non-mutating readiness over the complete allocated catalogue (ADR-004).
- Fail-closed context reads: an MCP error or unrecognised shape aborts, never empties (ADR-012).
- `COMMITTED` gated on positively verified mutation and writeback, seven independently tracked
  signals (ADR-013).
- Deterministic, whole-plan-guarded DataHub seed/reset/capture/restore (ADR-014), with absence as
  an explicitly captured, exactly-checked, post-apply-verified state (ADR-016).
- Emission through typed MCP wrappers built from the pinned SDK's own aspect classes, converted
  whole-plan before the first write, with undeclared payload keys refused and partial applies
  reported with a real applied count (ADR-018).
- Self-contained judge console served from inside the package (ADR-015).
- `gtc-safety-scan` and `gtc-archive-verify` as pre-publication gates.

### Resource profile

Single `uvicorn` process, no workers. Idle footprint well under 200 MB. Startup ~1 s.
No inbound network calls except the read-only DataHub probe in `/api/readiness`
(GET `${DATAHUB_GMS_URL}/health`, 3 s timeout, never mutates).

### Environment variables consumed

From the shared contract: `PROJECT_SLUG`, `APP_ENV`, `APP_HOST`, `APP_PORT`, `APP_PUBLIC_URL`,
`APP_STATE_DIR`, `DATAHUB_GMS_URL`, `DATAHUB_MCP_URL`, `DATAHUB_TOKEN`, `DATAHUB_DOMAIN`,
`DATAHUB_PROJECT_TAG`, `DATAHUB_URN_PREFIX`, `DEMO_FIXTURE_ROOT`.

Two project-local additions, both defaulted so nothing new is required to deploy. **Flagging
these for coordinator awareness** since they are not in the shared contract:

| Variable | Default | Why it exists |
|---|---|---|
| `DATAHUB_DOMAIN_URN` | `urn:li:domain:graph-traffic-control` | Readiness and the seed plan need the domain's URN, not only its display name. Overridable if the shared instance minted a different id. |
| `DATAHUB_DESCRIPTION_OPERATION` | `SET` | The `operation` argument for `update_description`. The argument *name* is coordinator-observed; this *value* is not, so it is configurable rather than hardcoded and can be corrected on the host without a code change. |

See `.env.example`. No secrets committed; `gtc-safety-scan` enforces this.

### Coordinator repository decision

Every submission uses an independent local Git repository on branch `main`. This project's public
repository is `https://github.com/amathias/graph-traffic-control`, and local `origin` uses the
repository-scoped `github-datahub-graph-traffic-control` SSH alias. The primary project writer may
push verified milestones under the no-force rules in `AGENTS.md`; remotes and deploy keys remain
coordinator-owned. The encrypted artifact bucket remains the source of deployment artifacts and
coordinator evidence rather than a substitute for the public source repository.

### Coordinator integration rulings

1. Use AWS Systems Manager port forwarding for local access to shared DataHub. Do not expose GMS,
   MCP, SSH, databases, or project ports publicly.
2. Use the private coordinator-hosted Streamable HTTP MCP endpoint
   `http://127.0.0.1:8000/mcp` and retain `DATAHUB_TOKEN` as the shared secret name.
3. MCP mutation tools are enabled on the pinned server, but each project must still enforce its
   own namespace and approval gates. The supported Python SDK/GraphQL path remains acceptable for
   writebacks that the MCP tool set does not model.
4. Namespace-scoped ingestion is allowed with stale-entity removal disabled. Global full-refresh
   and `datahub docker nuke` are forbidden.
5. Tag, description, and structured-property proposals are allowed only after a smoke test against
   pinned DataHub Core `v1.6.0` confirms the selected aspect.

## Required deployment handoff format

When requesting deployment, replace all placeholder values and include:

1. Exact commit or immutable artifact identifier.
2. Required environment variables without secret values.
3. Build, test, seed, reset, run, and rollback commands.
4. Health/readiness results.
5. DataHub entities, reads, writes, and receipts.
6. Filesystem volumes and disposable paths.
7. Expected CPU, memory, startup time, and job duration.
8. Known limitations and demo concurrency behavior.



