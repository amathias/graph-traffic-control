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
| Milestone | Proposal → lease → commit vertical slice complete and tested. **Live DataHub run outstanding.** |
| Verified commit/artifact | See "Deployment candidate" below |
| Build command | `python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| Test command | `.venv/Scripts/python.exe -m pytest` — **246 passed** in 65 s, no network required |
| Lint command | `.venv/Scripts/python.exe -m ruff check .` — clean |
| Seed command | `gtc-seed` |
| Reset command | `gtc-reset` |
| Demo command | `gtc-demo [--export-examples examples]` |
| Run command | `gtc-api` (uvicorn, `APP_HOST`:`APP_PORT`) |
| Health endpoint | `GET /api/health` — verified 200 on a running server |
| Readiness endpoint | `GET /api/readiness` — verified 200 seeded (fixture mode), 503 unseeded, 503 in non-local env without credentials |
| Persistent volumes | `APP_STATE_DIR` (default `demo/state`) holds `transactions.sqlite`, `artifacts/`, `receipts/`. Disposable and recreated by `gtc-seed`. **SQLite means a single writer: run one replica.** |
| Long-running workers | None. Single uvicorn process, no background jobs. |
| DataHub read | **Not verified.** Client and provider implemented and tested against a localhost protocol double over real HTTP. No connection to the shared instance was made from this session. |
| DataHub writeback | **Not verified.** Reversible capture → write → re-read → restore implemented and tested against the same double. **No live receipt exists.** |
| Blockers | Live DataHub verification requires SSM access, which this session was instructed not to use. Everything else is complete. |
| Evidence produced | 246 passing tests; `examples/` (three proposals + transaction trace); sanitized receipts under `APP_STATE_DIR/receipts`; `docs/DECISIONS.md` ADR-001..011; `docs/LIMITATIONS.md` |

## Deployment candidate

| Field | Value |
|---|---|
| Branch | `main` |
| Product candidate | _recorded in the follow-up docs commit_ |
| Documentation handoff HEAD | _this commit_ |
| Tree state | Clean; `git status` empty |
| Pushed to origin | `origin/main` |

Promote this commit only for a **fixture-mode** deployment, or after the live checks below are
run on the host. In a non-local `APP_ENV` without `DATAHUB_MCP_URL` and `DATAHUB_TOKEN`, readiness
correctly returns 503, so the service will not report ready until credentials are supplied.

## Live verification the coordinator must run on the host

None of these have been executed. They are the remaining gate between this candidate and a
truthful "reads and writes real DataHub context" claim.

1. `GET /api/readiness` with `DATAHUB_MCP_URL` and `DATAHUB_TOKEN` set. Expect `mode: "live"` and
   `status: "verified"`. Any other status is a real failure, not a configuration nuisance.
2. Confirm the MCP server exposes `get_entities`, `get_lineage`, `list_schema_fields`, and
   `update_description`. Readiness reports precisely which are missing.
3. Ingest the `traffic.` demo graph into the `Demo / Graph Traffic Control` domain, tagged
   `project-graph-traffic-control`, with stale-entity removal disabled.
4. Run one writeback and keep the receipt. Verify `verified: true` **and** `restored: true`, and
   confirm in the DataHub UI that the description returned to its original value.
5. Re-record `demo/fixtures/graph-traffic-control/graph.json` from the live instance so the
   offline suite reflects real shapes.

**Expect step 2 or 4 to surface response-shape mismatches.** The extractors were written from
documentation, not from observed payloads, and degrade to "unknown" rather than guessing — so a
mismatch shows up as empty fields, not as a wrong conflict decision.

## Milestone evidence

| Coordinator gate | Status |
|---|---|
| Clean setup and tests pass | Verified — 246 passed, `ruff check` clean |
| Demo seed and reset deterministic | Verified — repeated seed byte-identical; reset idempotent and fixture-preserving |
| Reads real context from shared DataHub | **Not verified** — implemented, tested against a protocol double only |
| Performs and verifies supported writeback | **Not verified** — implemented and reversible; no live receipt |
| Namespace and reset isolation tests pass | Verified — guard refuses `lifeboat.`, `license.`, `forgetme.`, `fuzzer.` entities, unknown URN shapes, and foreign `schemaField` parents; writeback refuses out-of-namespace targets before any MCP call |
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
- Strong non-mutating readiness (ADR-004).

### Resource profile

Single `uvicorn` process, no workers. Idle footprint well under 200 MB. Startup ~1 s.
No inbound network calls except the read-only DataHub probe in `/api/readiness`
(GET `${DATAHUB_GMS_URL}/health`, 3 s timeout, never mutates).

### Environment variables consumed

All from the shared contract, no additions: `PROJECT_SLUG`, `APP_ENV`, `APP_HOST`, `APP_PORT`,
`APP_PUBLIC_URL`, `APP_STATE_DIR`, `DATAHUB_GMS_URL`, `DATAHUB_MCP_URL`, `DATAHUB_TOKEN`,
`DATAHUB_DOMAIN`, `DATAHUB_PROJECT_TAG`, `DATAHUB_URN_PREFIX`, `DEMO_FIXTURE_ROOT`.
See `.env.example`. No secrets committed.

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



