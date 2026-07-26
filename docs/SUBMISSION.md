# Devpost submission copy: Graph Traffic Control

Ready-to-paste text for the submission form. Every claim here is backed by something in this
repository. Claims that are **not** yet backed by a live run are marked, and must stay marked
until the coordinator has run the live gates in `COORDINATOR_HANDOFF.md`.

---

## Project name

Graph Traffic Control

## Tagline (Devpost "elevator pitch", ≤200 characters)

> File locks can't see a semantic collision. Graph Traffic Control gives autonomous data agents a
> DataHub-powered two-phase commit that can.

## Challenge category

**Agents That Do Real Work.** Agents read connected context from DataHub, act on it, and write
results back.

---

## The problem

Three data agents each make a change that is individually correct.

- **Agent A** renames `gross_revenue` on the revenue fact table.
- **Agent B** publishes a metric computed from `gross_revenue`.
- **Agent C** updates an unrelated support-SLA model.

A and B touch **different files, different models, and share no declared asset at all**. Every
file lock, worktree, branch, and task queue says they are safe to run in parallel. They are not:
B reads a column A is about to rename, through a dependency neither of them declared. The
combined result is broken, and nothing in a file-level coordinator can see it coming.

Serialising everything would prevent it, and would also stall C, which genuinely is unrelated.
The problem is not "how do we take a lock" — it is "how does a coordinator know what a change
actually touches".

## The solution

Graph Traffic Control is a coordinator that resolves that question from DataHub's context graph.

Agents submit structured change proposals — identity, intent, explicit read and write URN sets,
expected entity versions, an executable action, a validation plan, a requested lease, and risk
declaration. The coordinator then:

1. **Expands** each proposal's declared sets through DataHub lineage, schemas, ownership, tags,
   and domain, within a bounded depth.
2. **Detects conflicts** against a documented eight-row matrix — including the
   **lineage-mediated conflict between A and B, who share no declared URN** — and reports the
   shortest directed DataHub path that proves each one.
3. **Prepares** safe work: issues expiring leases and a prepared token carrying a fingerprint of
   the subgraph the proposal depends on. C prepares and commits immediately; it is not queued
   behind the revenue branch.
4. **Requires approval** for high-blast-radius changes, routed from DataHub criticality.
5. **Re-reads the graph immediately before commit** and fails closed on any drift.
6. **Executes** the real artifact change, validates it, and **verifies** it — the file is read
   back from disk, and the DataHub writeback is read back from DataHub. A proposal is not marked
   committed until both are positively confirmed.
7. **Writes the outcome back to DataHub** reversibly: capture, write, immediate re-read, restore.
8. **Audits** every state transition in an append-only log, with receipts as evidence.

## What makes it original

Multi-agent coding coordinators manage files, branches, and worktrees. Graph Traffic Control
coordinates *semantic* dependencies read from a governed data graph. The defensible claim, and
the one the demo makes visible:

> The conflict it catches is between two proposals that declare no overlapping asset. Only the
> lineage graph reveals it.

## How DataHub is used

Through the **DataHub MCP Server**, on the coordinator-hosted Streamable HTTP endpoint:

| Tool | Used for |
|---|---|
| `get_entities` | entity properties, ownership, tags, and domain for each allocated URN |
| `get_lineage` | downstream lineage, one hop per allocated entity |
| `list_schema_fields` | column-level schema for conflict detection and drift fingerprints |
| `update_description` | the reversible writeback of each coordination outcome |

DataHub context is not decoration. It decides outcomes: the lineage graph produces the A/B
conflict, criticality decides which change needs human approval, and the schema/ownership/tag/
domain fingerprint decides whether a prepared proposal is still safe to commit.

## Technical execution

- Python 3.12, FastAPI, Pydantic (strict, `extra="forbid"`), SQLite, NetworkX.
- Nine-state transaction machine; every transition appends an audit event.
- Expiring leases with an injected clock, so an abandoned agent cannot strand a URN forever.
- **Fails closed everywhere.** An MCP error or an unrecognised response shape aborts the read.
  It never becomes an empty graph — an empty graph is indistinguishable from a graph with no
  conflicts, and would silently read as "safe to commit".
- **`COMMITTED` means proved, not attempted.** Mutation, mutation re-read, validation, writeback
  verification, writeback restoration, rollback, and receipts are tracked as seven independent
  signals.
- **Namespace isolation** on a shared instance, enforced on aspect payloads as well as entity
  addresses, so a lineage edge cannot smuggle in another project's URN.
- The deterministic coordinator owns every conflict and commit decision. **No model output
  participates in any of them.**

## Testing it

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
gtc-seed && gtc-api          # then open http://127.0.0.1:8105/
```

Press **Run four-agent scenario**. No DataHub instance, no cloud account, and no paid service is
required: the console runs against the recorded graph fixture and states on screen which context
source it used.

`examples/` holds the three agent proposals and a full transaction trace.

## Evidence status — please read

This project distinguishes what has been executed from what has been implemented.

**Executed and verified offline:** the conflict matrix including the zero-overlap lineage
conflict; the full state machine; lease expiry; pre-commit drift detection; real SQL artifact
mutation and rollback; the MCP client's wire protocol over real HTTP against a strict localhost
protocol double; the reversible writeback sequence; readiness; namespace isolation; deterministic
DataHub seed/reset/restore planning; and the judge console.

**Implemented but NOT yet verified against a live DataHub instance:** the MCP tool responses
themselves, and any writeback receipt from a real entity. All protocol-double results are
labelled **simulated** wherever they appear.

`docs/LIMITATIONS.md` is the authoritative list. Nothing in this submission claims a live
DataHub read or write that has not happened.

## Claims this project does not make

It does not provide distributed ACID transactions across data platforms, does not solve
multi-agent coordination in general, does not guarantee exactly-once effects in external systems,
and does not let an LLM decide whether a commit is safe.

## Licence and originality

Apache 2.0, in `LICENSE` at the repository root. Built new during the submission period. No
pre-existing code or assets are incorporated. Substantially different from every other submission
by this entrant: the transaction coordination model, implementation, and demo are its own.
