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
| Milestone | Rejection corrected and product complete offline. **Live DataHub run outstanding.** |
| Verified commit/artifact | See "Deployment candidate" below |
| Build command | `python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| Test command | `.venv/Scripts/python.exe -m pytest` — **458 passed** in 146 s, no network required |
| Coverage | `pytest --cov=graph_traffic_control` — **88%** (2490 statements, 294 missed) |
| Lint command | `.venv/Scripts/python.exe -m ruff check .` — clean |
| Build/archive check | `gtc-archive-verify` — **8/8 pass**, including a clean-environment wheel install |
| Safety scan | `gtc-safety-scan` — **0 blockers, 0 warnings** across 71 tracked files |
| Seed / reset | `gtc-seed` / `gtc-reset` (local, offline, always safe) |
| DataHub state | `gtc-datahub-seed`, `gtc-datahub-reset`, `gtc-datahub-capture`, `gtc-datahub-restore` — **plan-only by default**; `--apply` requires live credentials |
| Demo command | `gtc-demo [--export-examples examples]` |
| Run command | `gtc-api` (uvicorn, `APP_HOST`:`APP_PORT`) |
| Judge UI | `GET /` — self-contained page; one button runs the whole scenario |
| Health endpoint | `GET /api/health` — verified 200 on a running server |
| Readiness endpoint | `GET /api/readiness` — verified 200 seeded (fixture mode), 503 unseeded, 503 in non-local env without credentials |
| Persistent volumes | `APP_STATE_DIR` (default `demo/state`) holds `transactions.sqlite`, `artifacts/`, `receipts/`, `datahub/` (plans), `judge/` (judge-run state). Disposable and recreated by `gtc-seed`. **SQLite means a single writer: run one replica.** |
| Long-running workers | None. Single uvicorn process, no background jobs. |
| DataHub read | **Not verified live.** Implemented against the coordinator-observed contracts and tested against a strict localhost protocol double over real HTTP. No connection to the shared instance was made from this session. |
| DataHub writeback | **Not verified live.** Reversible capture → write → re-read → restore, with verification and restoration tracked independently. **No live receipt exists.** |
| DataHub ingestion | **Planned, never applied.** Deterministic guarded plans and recipe are produced; nothing has been written to the shared instance. |
| Blockers | Live DataHub verification requires SSM access, which this session was instructed not to use. Everything else is complete. |
| Evidence produced | 458 passing tests; `examples/`; sanitized receipts under `APP_STATE_DIR/receipts`; `docs/DECISIONS.md` ADR-001..015; `docs/LIMITATIONS.md`; `docs/SUBMISSION.md`; `docs/DEMO_RUNBOOK.md` |

## Deployment candidate

| Field | Value |
|---|---|
| Branch | `main` |
| Product candidate | See the handoff HEAD recorded in the final report for this session |
| Tree state | Clean at that commit; `git status` empty |
| Pushed to origin | `origin/main` |
| Verified at that commit | 458 tests pass, 88% coverage, `ruff check` clean, archive verification 8/8, safety scan 0/0, four-agent scenario runs end to end, judge console and endpoints exercised on a running server |

Promote this commit only for a **fixture-mode** deployment, or after the live checks below are
run on the host. In a non-local `APP_ENV` without `DATAHUB_MCP_URL` and `DATAHUB_TOKEN`, readiness
correctly returns 503, so the service will not report ready until credentials are supplied.

Artifact digests are reported by `gtc-archive-verify`, but the distributions are **not**
bit-for-bit reproducible — a digest identifies one specific build, it does not certify one.

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

## Live verification the coordinator must run on the host

None of these have been executed. They are the remaining gate between this candidate and a
truthful "reads and writes real DataHub context" claim.

1. `GET /api/readiness` with `DATAHUB_MCP_URL` and `DATAHUB_TOKEN` set. Expect `mode: "live"` and
   `status: "verified"`. Any other status is a real failure, not a configuration nuisance.
   Readiness now names precisely what is missing: tools, tag, domain, or specific entities.
2. Confirm the MCP server exposes `get_entities`, `get_lineage`, `list_schema_fields`, and
   `update_description`.
3. `gtc-datahub-capture`, then `gtc-datahub-seed --apply`. Inspect
   `APP_STATE_DIR/datahub/seed_plan.json` first — it is inert and deterministic, and its
   fingerprint is printed. Ingestion is namespace-scoped with stale-entity removal disabled.
4. Run one writeback and keep the receipt. Verify `verified: true` **and** `restored: true`, and
   confirm in the DataHub UI that the description returned to its original value.
5. **Confirm the `update_description` `operation` value.** The argument *names* were supplied by
   the coordinator; this *value* was not. It defaults to `SET` and is configurable via
   `DATAHUB_DESCRIPTION_OPERATION`. It must have replace-in-place semantics, or restoration
   cannot return the captured original exactly.
6. Re-record `demo/fixtures/graph-traffic-control/graph.json` from the live instance so the
   offline suite reflects real shapes.
7. Open `/` and confirm the judge console renders. **No browser tooling was available in this
   session**, so the page's payload, script syntax, and element wiring are tested but its
   rendering is unconfirmed.

**A shape mismatch will now be loud.** Unlike the rejected candidate, an unrecognised payload
raises a fail-closed error naming the tool and the offending key, rather than silently producing
empty fields. Treat the first live run as shape discovery: expect it either to pass or to state
exactly what differs.

## Milestone evidence

| Coordinator gate | Status |
|---|---|
| Clean setup and tests pass | Verified — 458 passed, 88% coverage, `ruff check` clean |
| Distributions build and install cleanly | Verified — `gtc-archive-verify` 8/8, wheel installed and run in a fresh venv outside the source tree |
| Demo seed and reset deterministic | Verified — repeated seed byte-identical; reset idempotent and fixture-preserving; DataHub plans byte-identical with a stable fingerprint |
| Reads real context from shared DataHub | **Not verified** — implemented against the observed contracts, tested against a strict protocol double only |
| Performs and verifies supported writeback | **Not verified** — implemented, reversible, verification and restoration tracked independently; no live receipt |
| Namespace and reset isolation tests pass | Verified — every surface that can reach shared state refuses all four sibling allocations (`lifeboat.`, `license.`, `forgetme.`, `fuzzer.`), unknown URN shapes, foreign `schemaField` parents, foreign domains and tags, and foreign URNs smuggled inside lineage and dashboard-input payloads |
| Global reset is impossible | Verified — reset takes an explicit scope and accepts only `namespace`; ingestion recipe disables stale-entity removal |
| No secrets or private evidence publishable | Verified — `gtc-safety-scan` 0 blockers / 0 warnings across 71 tracked files |
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
- Deterministic, whole-plan-guarded DataHub seed/reset/capture/restore (ADR-014).
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



