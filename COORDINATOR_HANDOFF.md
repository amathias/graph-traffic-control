# Coordinator Handoff: Graph Traffic Control

## 2026-07-29 anonymous mutation hardening — deployed and verified

- `APP_ENV=hackathon` and `APP_ENV=production` now reject direct proposal create, approve,
  commit, and abort requests before opening the transaction store.
- The fixed, namespace-bounded judge scenario remains public, with a process-local single-flight
  lock and a 30-second cooldown.
- Readiness explicitly reports whether direct mutations are enabled and the active demo cooldown.
- Local, test, and coordinator-controlled live environments retain the full coordination API.
- Verification: the complete 643-test suite passed with the documented `D:\pt` Windows ACL
  workaround; after extending single-flight parity to local runs, the 60 focused API/UI tests
  passed again. Ruff and whitespace checks passed.
- Exact commit `8e5307d53ed224fc7f6e056c1f378000cc5127da` passed GitHub Actions and was
  promoted by the coordinator.
- Public root, health, and strong readiness returned 200. Anonymous direct abort returned 403,
  readiness reported `direct_mutations_enabled=false` and a 30-second cooldown, and the isolated
  judge scenario returned 200 followed by 429 on an immediate repeat.

## 2026-07-29 public-demo boundary closeout

| Field | Verified value |
|---|---|
| Exact deployed product | `32e0c632c85b51a1d5311e042e3b3d767b25c7ff` |
| Public endpoint | `https://traffic.datahub-hackathon.aaronmathias.com` |
| Public acceptance | Root, health, and strong readiness returned 200 |
| Browser acceptance | One prominent `PUBLIC DEMO` notice rendered above the workflow and identified the disposable `traffic.*` artifacts, isolated live graph, no-production/no-personal-data boundary, and source/API/self-hosting link |
| Hosted API documentation | `/docs`, `/redoc`, and `/openapi.json` returned 404 in `APP_ENV=hackathon`; local/development/test documentation remains enabled |
| Verification | 638 tests passed, Ruff passed, GitHub Actions passed, and exact `main` matched `origin/main` before promotion |

The standard deployment ran `gtc-seed`, which rebuilds only the local fixture manifest and SQL
artifacts. It did not run `gtc-datahub-seed`, reseed the shared catalog, or change the preserved
live write/re-read/restore evidence.

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
| Status | `complete` |
| Milestone | **The live gate is complete: verified and restored.** Deployed product `5ea880f`. Strong readiness passed with 9 entities and 7 lineage edges, the reversible writeback succeeded with `DATAHUB_DESCRIPTION_OPERATION=replace` after live DataHub 1.6.0 rejected `SET`, and `sibling_new_rows=0`. This build carries the `SET` → `replace` correction, the submission package, and the judge recording plan. |
| Verified commit/artifact | See "Deployment candidate" below |
| Build command | `python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| DataHub extra | `pip install -e ".[datahub]"` on the host — **pinned exactly** to `acryl-datahub==1.6.0.15` and `mcp==1.28.1` (ADR-017). **Installed in a throwaway local venv this session** to verify the emitter contract against the real library; never pointed at any instance, and no network call was made with it. |
| Test command | `.venv/Scripts/python.exe -m pytest` — **632 passed, 1 skipped** in 175 s, no network required. The skip is `test_datahub_sdk_pinned.py`, which needs the optional extra. |
| Test command (with the extra) | `pytest` in a venv that also has `.[datahub]` — **643 passed, 0 skipped**. This is the host configuration. |
| Coverage | `pytest --cov=graph_traffic_control` — **89%** (2715 statements, 291 missed). `demo/datahub_state.py` is now **96%**: the emitter boundary is executed by tests rather than excluded by a `pragma: no cover`, which is what let the blocker through. The largest gap remains `release/archive.py` at 30%: its end-to-end path builds distributions and creates a virtual environment, so it runs as the `gtc-archive-verify` release command rather than in the suite. |
| Lint command | `.venv/Scripts/python.exe -m ruff check .` — clean |
| Build/archive check | `gtc-archive-verify` — **8/8 pass**, including a clean-environment wheel install |
| Safety scan | `gtc-safety-scan` — **0 blockers, 0 warnings** across 84 tracked files |
| Seed / reset | `gtc-seed` / `gtc-reset` (local, offline, always safe) |
| DataHub state | `gtc-datahub-seed`, `gtc-datahub-reset`, `gtc-datahub-capture`, `gtc-datahub-restore` — **plan-only by default**; `--apply` requires live credentials |
| DataHub artifacts | Under `APP_STATE_DIR/datahub/`: `pre_seed_capture.json`, `seed_plan.json`, `reset_plan.json`, `restore_plan.json`, `ingestion_recipe.yaml`. These names are asserted by the suite against this document, the README, and the runbook, so they cannot drift from the docs again. |
| Partial apply | If `--apply` fails part way, it raises with **how many operations were applied**, which one failed, and `gtc-datahub-restore --apply` as the recovery. "Seed failed" is never readable as "nothing happened". |
| First-time seeding | `gtc-datahub-capture --allow-absent` records the deliberate absence of the exact allocation, seed creates exactly that set, and restore soft-deletes it back to absent and **re-reads to prove it** (ADR-016). Absence never enters a capture implicitly. |
| Demo command | `gtc-demo [--export-examples examples]` |
| Run command | `gtc-api` (uvicorn, `APP_HOST`:`APP_PORT`) |
| Judge UI | `GET /` — self-contained page; one button runs the whole scenario |
| Public demo video | <https://youtu.be/xW1IczBUh0g> (2:40, published English captions) |
| Health endpoint | `GET /api/health` — verified 200 on a running server |
| Readiness endpoint | `GET /api/readiness` — 200 seeded (fixture mode), 503 unseeded, 503 in non-local env without credentials, **503 whenever the graph snapshot will not build** (ADR-019), and **503 when seeded lineage edges cannot be read back** (`lineage_incomplete`, ADR-020). Reports `graph_entities`, `graph_edges`, `graph_fingerprint`, `lineage_edges_verified`. |
| Persistent volumes | `APP_STATE_DIR` (default `demo/state`) holds `transactions.sqlite`, `artifacts/`, `receipts/`, `datahub/` (plans), `judge/` (judge-run state). Disposable and recreated by `gtc-seed`. **SQLite means a single writer: run one replica.** |
| Long-running workers | None. Single uvicorn process, no background jobs. |
| DataHub read | **Verified live.** Strong readiness passed against the shared instance with 9 entities and 7 lineage edges — the complete allocated catalogue and every seeded edge, including the one the hidden conflict depends on. Run by the coordinator; **this session made no connection to the shared instance.** |
| DataHub writeback | **Verified live, and restored.** Reversible capture → write → re-read → restore completed with `operation=replace`. Final receipt SHA-256 `621e022bc1253990be5fe328da8186ecc6be2d675d8242514d3ef81866db8782`. Pre-gate snapshot `snap-0cb18d5953f50482c`. |
| Cross-project isolation, live | **`sibling_new_rows=0`.** No sibling submission's rows were created, altered, or removed by the gate. |
| DataHub ingestion | **Applied live by the coordinator.** All 49 typed operations of plan `cd44112ebd42b7de` were accepted by the shared instance. This build changes no plan — the fingerprint is byte-identical, so **do not seed again**. |
| DataHub emission | **Verified against the pinned SDK, offline.** All 103 operations across the seed, reset, and both restore plans construct as real typed aspects and serialise to the bytes the emitter would send. No emitter was ever connected. |
| Blockers | **None.** The lineage index was reindexed and readiness now reads all 7 seeded edges; the writeback leg ran and produced a verified, restored receipt. |
| Evidence produced | 632 passing tests offline / 643 with the extra; **completed live gate evidence recorded below**; `examples/`; sanitized receipts under `APP_STATE_DIR/receipts`; `docs/DECISIONS.md` ADR-001..022; `docs/LIMITATIONS.md`; `docs/SUBMISSION.md`; `docs/DEMO_RUNBOOK.md` |

## Deployment candidate

| Field | Value |
|---|---|
| Branch | `main` |
| Deployed product | **`5ea880f61122f052210d014906fe5eab2c356851`** (`5ea880f`) — this is what ran the live gate and what is serving. |
| Public app URL | <https://traffic.datahub-hackathon.aaronmathias.com> |
| Public repository | <https://github.com/amathias/graph-traffic-control> |
| This build vs the deployed product | The deployed `5ea880f` still defaults `DATAHUB_DESCRIPTION_OPERATION` to `SET`; the gate passed because the coordinator supplied `replace` **in the host environment**. This build makes `replace` the default, so a redeploy no longer depends on that override being remembered. **No behaviour change when the override is set** — the value is identical. Everything else here is documentation and tests. |
| Reseed/capture | **Do not capture and do not seed.** The instance is correctly seeded and the pre-seed capture from the live run is the only correct one. |
| Tree state | Clean at that commit; `git status` empty |
| Pushed to origin | `origin/main` |
| Verified at this closeout commit | **632 tests pass, 1 skipped** offline and **643 pass, 0 skipped** with the pinned extra, **89% coverage** (2715 statements, 291 missed), `ruff check` clean, `gtc-archive-verify` **8/8** (including a clean-environment wheel install), `gtc-safety-scan` **0 blockers / 0 warnings** across 84 tracked files, submission links and relative-link/placeholder checks pass, four-agent scenario runs end to end, judge workflow reproduces the result below unchanged, health 200, readiness 200 and `/api/graph` 200 seeded (fixture mode) |

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

## The lineage index is empty. This is not a code defect, and must not be "fixed" in code.

**Product SHA:** `caf03d45ba83b399c1d101c411a11e090d7408de` (`caf03d4`).

The live run of `754abcb` returned **503 `graph_unreadable`** with all nine allocated entities
found, failing on:

> downstream lineage for `urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_revenue,PROD)`
> has no `searchResults` key; observed keys are `facets` and `total`

with `facets` reporting `count: 0` for every degree bucket.

**`traffic.fct_revenue` has a downstream.** The seed applied `upstreamLineage` on
`traffic.metric_net_revenue` naming `fct_revenue` as its upstream, and all 49 operations were
accepted. Zero downstream matches therefore does not describe the graph — it describes a **lineage
index that cannot see the graph**, consistent with the shared OpenSearch instance having been
recovered without the graph service being reindexed.

### Why the obvious fix would have been the dangerous one

Measured against the protocol double emitting that exact envelope for every dataset:

```
entities: 9   edges: 0    -> /api/graph answers HTTP 200
```

Treating the response as "no downstreams" makes readiness green and `/api/graph` succeed — with
**nine correct entities and no lineage**. That snapshot answers *"nothing conflicts"* to every
question, and this project's entire claim is an edge: the conflict between two proposals that share
no declared URN and are connected only by DataHub lineage. A judge would watch A and B commit in
parallel as unrelated changes, with a green readiness endpoint vouching for it.

The 503 is the correct behaviour. **Do not deploy a build that reads this as empty.**

### What this build changes (neither unblocks the deploy)

1. **The envelope is read properly, so the error is a diagnosis rather than a shape complaint.**
   `total` decides the meaning: `total: 0` is empty; `total: n > 0` without results raises
   ("told there are matches, given none"); no integer `total` raises. ADR-020 has the table.
2. **Readiness verifies the seeded lineage reads back** — ADR-004's complete-catalogue rule applied
   to edges. Missing seeded edges give a new **`lineage_incomplete`** status, distinct from
   `entities_missing`, whose detail says explicitly: *reindex the graph service, do NOT re-seed.*
   One missing edge is enough; the hidden conflict rides on exactly one.

### Remediation, in order

1. **Reindex DataHub's graph service** (the lineage index) on the shared instance. Nothing else in
   this project's allocation needs touching. **Do not capture. Do not seed.** The entities and
   their `upstreamLineage` aspects are already correct and accepted.
2. `GET /api/readiness` — expect 200 with `status: "verified"`, `graph_edges: 7`, and
   `lineage_edges_verified: 7`. If lineage is still unindexed you will now get 503
   `lineage_incomplete` naming the exact missing edges, instead of a shape error.
3. `GET /api/graph` — expect 200, 9 entities, **7 edges**. An edgeless 200 is a failure even
   though it is a 200.
4. Then the outstanding leg: one proposal through prepare/commit and one reversible writeback,
   keeping the receipt with `verified: true` **and** `restored: true`.

**Both open questions are now answered by the coordinator, and both confirm the diagnosis.** The
live `total` was the exact numeric `0`, and **the DataHub graph service was not reindexed after the
OpenSearch recovery.** So the zero downstream count is fully explained by the unindexed graph
service, and nothing about the seeded data is in doubt: the entities and their `upstreamLineage`
aspects are correct and accepted. The remediation below stands unchanged — reindex, do not re-seed.

### Follow-up: the `total` type guard was wrong on first implementation (ADR-021)

**Product SHA:** `5ea880f61122f052210d014906fe5eab2c356851` (`5ea880f`).

Coordinator review of `caf03d4` found that `total == 0` was evaluated *before* the integer check.
`bool` subclasses `int` and `False == 0`, so a JSON `false` was read as "no downstream matches" —
precisely the outcome this reader exists to prevent, in the code that introduced the rule. The
regression covered `True`, which takes a different branch and raised, so the gap was invisible.

Fixed by proving the type before comparing the value. Re-running the widened regression against
`caf03d4` also showed `total: 0.0` had been accepted, for the same reason (`0.0 == 0`) — a second
instance of the same defect that had not been reported. Exactly one value is now accepted as empty:

| `total` | Result |
|---|---|
| integer `0` | empty |
| `false`, `true` | **refused** — a flag is not a count |
| `0.0`, `"0"`, `None`, `[]`, `{}` | **refused** — not an integer |
| negative | **refused** — not a possible count |
| positive | **refused** — matches claimed but withheld |

This changes no behaviour for the observed live payload, which carried integer `0` and is still
read as empty. It closes the path where a differently-typed zero would have been.

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

## Live verification: COMPLETE

**The live gate ran to completion and passed.** It was executed by the portfolio coordinator, who
holds the credentials and SSM access. No project chat connected to the shared instance at any
point, including this one.

Deployed product: **`5ea880f61122f052210d014906fe5eab2c356851`**.

| Step | Result |
|---|---|
| Strong readiness over the complete catalogue | **Passed — 9 entities, 7 lineage edges.** The full fixture graph, including the single edge the hidden conflict depends on. |
| Lineage index | **Resolved.** The reindex fixed it; the seeded data was never wrong and was not re-seeded. |
| `update_description` `operation` | `SET` **rejected** by live DataHub 1.6.0. **`replace` accepted.** |
| Reversible write → re-read → restore | **Succeeded, verified, and restored** with `operation=replace`. |
| Final receipt SHA-256 | `621e022bc1253990be5fe328da8186ecc6be2d675d8242514d3ef81866db8782` |
| Pre-gate snapshot | `snap-0cb18d5953f50482c` |
| Cross-project isolation | **`sibling_new_rows=0`** — no sibling submission's rows created, altered, or removed. |

The `SET` → `replace` correction is carried in this build: `config.py`, `writeback/datahub.py`,
`.env.example`, the tests, and this document. `SET` was never a coordinator-supplied value — the
argument *names* were, this *value* was a guess from the aspect vocabulary, and it was wrong. A
regression now asserts the default is `replace` and has not reverted, because the failure mode is
a plausible-looking wrong string and the entire restore guarantee depends on replace-in-place
semantics.

### Historical: what the gate was asked to run

Retained for provenance. All of it has now been executed.

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
   the coordinator; this *value* was not. **Answered:** the then-default `SET` was rejected and
   `replace` was accepted, so `replace` is the default from this build onward.
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
| Reads real context from shared DataHub | **Verified live** — strong readiness passed with 9 entities and 7 lineage edges, the complete allocated catalogue and every seeded edge |
| Performs and verifies supported writeback | **Verified live and restored** — reversible write → re-read → restore with `operation=replace`; receipt SHA-256 `621e022bc1253990be5fe328da8186ecc6be2d675d8242514d3ef81866db8782` |
| Leaves sibling submissions untouched | **Verified live** — `sibling_new_rows=0` across the gate |
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
| `DATAHUB_DESCRIPTION_OPERATION` | `replace` | The `operation` argument for `update_description`. **Live-confirmed:** DataHub 1.6.0 rejected the earlier `SET` default (this project's guess — the argument *name* was coordinator-observed, the *value* never was) and accepted `replace`, which completed the write/re-read/restore cycle. Still overridable for a different server. |

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



