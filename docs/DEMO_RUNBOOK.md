# Demo runbook: 2:35–2:45

The hackathon rules cap the video at **three minutes** and judges need not watch past it.
`DEMO_AND_SUBMISSION.md` targets **2:35–2:45**, leaving margin for a title card without risking
the cap. This runbook is the shot list that fits that budget.

Every timing below is a **cumulative end time**. If a segment overruns, take the slack from
segment 6, never from segment 2 — segment 2 is the project's entire thesis.

---

## Before recording

```bash
gtc-reset && gtc-seed              # deterministic; repeated runs are byte-identical
gtc-api                            # http://127.0.0.1:8105/
```

Checklist:

- [ ] Browser at `http://127.0.0.1:8105/`, zoomed so proposal text is legible at 1080p.
- [ ] Window sized so the lineage graph and the agent panel are both visible without scrolling.
- [ ] `gtc-demo` run once beforehand to warm the page; then **reload**, so recording starts from
      the unpressed state.
- [ ] No terminal showing a token, hostname, or absolute home directory in frame.
- [ ] No third-party music. Narration only, or silence.
- [ ] Screen recorder at 1080p or better, 30 fps.

Rehearse once with a timer before the take. The button-press-to-render round trip is roughly a
second; the script below assumes you keep talking through it.

---

## Shot list

### 1 — The setup — **0:00–0:20** (20s)

**On screen:** the console, unpressed. Press **Run four-agent scenario** at about 0:08 and keep
narrating while it renders.

> Three data agents want to change a data platform. One renames a revenue column. One publishes a
> metric. One updates an unrelated support model. They touch different files, so every file lock
> and worktree says all three are safe to run in parallel.

### 2 — DataHub reveals the collision — **0:20–0:55** (35s) — *the critical shot*

**On screen:** the lineage graph with the highlighted conflict path, then scroll to Agent B's
blocked card so the lineage evidence line is legible. Hold on it.

> They are not safe. Agent A writes the revenue fact. Agent B writes the metric. They declare no
> asset in common — nothing overlaps. But DataHub lineage shows the metric is derived from the
> column A is renaming. The coordinator reads that path out of DataHub, blocks B, and quotes the
> exact chain as evidence.

**Do not rush this.** If a judge takes one thing away, it is that the two proposals share no
declared URN and the graph found the conflict anyway.

### 3 — Safe work is not serialised — **0:55–1:20** (25s)

**On screen:** Agent C's card, `COMMITTED`, next to B's `BLOCKED`. Point out the lease panel.

> Agent C is lineage-disjoint from both, so it is not queued behind them. It takes a lease,
> prepares, and commits while the revenue conflict is still being resolved. Unrelated work is not
> globally serialised — that is the difference between a graph-aware coordinator and a global
> lock.

### 4 — Approval, recheck, and verified commit — **1:20–1:55** (35s)

**On screen:** Agent A's card. Show `approval` (`release-manager`), the two graph fingerprints,
and the row of verification flags.

> A's change has a high blast radius, so DataHub criticality routes it to a human approver.
> Immediately before commit, the coordinator re-reads the graph and compares fingerprints — any
> drift aborts. Then it applies the real SQL change, reads the file back off disk, writes the
> outcome to DataHub, and reads *that* back too. Committed means proved, not attempted.

### 5 — Failing closed — **1:55–2:20** (25s)

**On screen:** Agent D's `ABORTED` card and its stale-version reason; then B re-analysed after A
commits.

> A fourth agent submits work based on a stale snapshot. Its expected version no longer matches,
> so it fails closed rather than committing something built on a graph that has moved. And once A
> is terminal, B is re-analysed against the new state and gets a concrete path forward.

### 6 — Evidence and close — **2:20–2:40** (20s) — *absorb overruns here*

**On screen:** the append-only audit log, then click one commit receipt to show the verification
block.

> Every transition is in an append-only audit log, and every commit leaves a receipt recording
> what was actually verified — including whether the DataHub writeback was restored. Graph Traffic
> Control gives autonomous data agents a semantic two-phase commit, and leaves evidence for the
> next agent.

**End at 2:40.** Budget 2:35–2:45; hard ceiling 3:00.

---

## Timing budget

| # | Segment | Length | Ends at | If you must cut |
|---|---|---|---|---|
| 1 | Setup | 20s | 0:20 | to 15s |
| 2 | DataHub reveals the collision | 35s | 0:55 | **never cut** |
| 3 | Safe work is not serialised | 25s | 1:20 | to 20s |
| 4 | Approval, recheck, verified commit | 35s | 1:55 | to 30s |
| 5 | Failing closed | 25s | 2:20 | to 18s |
| 6 | Evidence and close | 20s | 2:40 | to 12s |

Full cuts applied: **2:10**. Nominal: **2:40**.

---

## What must be visible on screen

Straight from the `HACKATHON_RULES.md` scoring checklist:

- [ ] Three (four) agents' proposals, legible.
- [ ] The DataHub lineage path causing the indirect conflict — **visually obvious**.
- [ ] Agent C proceeding without waiting.
- [ ] Prepare, approval, commit, and abort states.
- [ ] A stale-version failure.
- [ ] The verification flags on a commit.
- [ ] The audit log and a receipt.
- [ ] No secrets, hostnames, or copyrighted music.

## If the demo is run against live DataHub

The recorded demo is fixture-backed and needs none of this. A live run against the shared instance
does, and it must happen in this order — capture first, always. A capture taken *after* a seed
records this project's own rows as the state to return the instance to, and the shared catalogue
never gets clean again.

```bash
gtc-datahub-capture --allow-absent   # first run: the traffic. namespace is absent, so record that
gtc-datahub-seed --apply             # inspect seed_plan.json first; it is inert and fingerprinted
# ... demo, writeback, receipts ...
gtc-datahub-restore --apply          # soft-deletes them again, then re-reads and proves it
```

Every artifact lands under `APP_STATE_DIR/datahub/`:

| File | What it is |
|---|---|
| `pre_seed_capture.json` | The pre-seed state of every allocated entity, each recorded `present` or `absent`. Restore reads this exact path and no other. |
| `seed_plan.json` | The inert seed plan. Inspect before `--apply`; its fingerprint is printed by the command. |
| `reset_plan.json` | Namespace-scoped soft-delete plan. |
| `restore_plan.json` | The return-to-captured-state plan. |
| `ingestion_recipe.yaml` | Namespace-scoped recipe, stale-entity removal disabled. |

If `--apply` fails part way through, the error states how many operations were applied before the
failure. That number is real: those operations are already in the shared instance. Run
`gtc-datahub-restore --apply` before retrying.

## Honesty rules for the narration

Non-negotiable, from `AGENTS.md` and the rules' truthful-claims requirement:

- The console states its context source on screen. **Say what it says.** If it reads `fixture`,
  do not narrate "reading from our live DataHub instance".
- Do not show a DataHub UI screenshot implying a live writeback unless one has actually been
  performed and its receipt exists.
- "Simulated" belongs on anything produced against the protocol double.
- Do not claim distributed ACID transactions, general multi-agent coordination, or exactly-once
  effects.

## After recording

- [ ] Under three minutes. Check the final file, not the editor timeline.
- [ ] Uploaded public (not unlisted-only if the rules require public) to YouTube or Vimeo.
- [ ] Watched end to end once, muted, to confirm nothing sensitive is on screen.
- [ ] `gtc-safety-scan` passes before the repository is made public.
