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
| Milestone | Milestone A (contracts) complete. Phase 1 (proposal schema and state machine) next. |
| Verified commit/artifact | Pending local baseline commit; coordinator records exact hash before promotion |
| Build command | `python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| Test command | `.venv/Scripts/python.exe -m pytest` â€” **57 passed** |
| Seed command | `gtc-seed` |
| Reset command | `gtc-reset` |
| Run command | `gtc-api` (uvicorn, `APP_HOST`:`APP_PORT`) |
| Health endpoint | `GET /api/health` â€” verified 200 on live server |
| Readiness endpoint | `GET /api/readiness` â€” verified 200 seeded, 503 unseeded |
| Persistent volumes | `APP_STATE_DIR` (default `demo/state`) is disposable and recreated by seed. No persistent volume required yet; SQLite lands here in Phase 3. |
| Long-running workers | None currently |
| DataHub read | Not yet verified â€” blocked, see below |
| DataHub writeback | Not yet verified â€” blocked, see below |
| Blockers | Shared DataHub deployment and live read/write receipts; local Phases 1-4 and 6 remain unblocked |
| Evidence produced | 57 passing tests incl. namespace and reset isolation; deterministic seed manifest; `docs/DECISIONS.md` ADR-001..004; `IMPLEMENTATION_PLAN.md` |

## Milestone A evidence

| Coordinator gate | Status |
|---|---|
| Clean setup and tests pass | Verified â€” clean venv, editable install, 57 passed, `ruff check` clean |
| Demo seed and reset deterministic | Verified â€” repeated seed produces byte-identical manifest; reset idempotent |
| Reads real context from shared DataHub | **Not started** â€” Phase 5 |
| Performs and verifies supported writeback | **Not started** â€” Phase 5 |
| Namespace and reset isolation tests pass | Verified â€” 26 namespace tests; guard refuses `lifeboat.`, `license.`, `forgetme.`, `fuzzer.` entities and unknown URN shapes |
| Health endpoint works behind reverse proxy | Health/readiness verified on `127.0.0.1:8105`; proxy path untested from this chat |
| Demo does not depend on another submission | Verified â€” no cross-project imports; runs with no DataHub |
| Handoff record current | This document |

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

Every submission uses an independent local Git repository on branch `main`. The coordinator may
promote clean local commits through the encrypted artifact bucket before GitHub exists. GitHub
repository creation, remotes, and public pushes are deliberately deferred until the user is
available.

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



