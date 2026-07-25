# Builder Instructions: Graph Traffic Control

## Mission

Build a working, judge-ready vertical slice of Graph Traffic Control: a DataHub-powered coordinator that prevents semantic collisions between autonomous data-agent changes.

## Read first

Before modifying code, read these files completely:

1. `HACKATHON_RULES.md`
2. `PROJECT_BRIEF.md`
3. `BUILD_PLAN.md`
4. `DEMO_AND_SUBMISSION.md`

## Non-negotiable product behavior

- Read real lineage, schemas, ownership, and governance context from open-source DataHub through an eligible integration.
- Demonstrate a real DataHub writeback using a supported API or SDK.
- Accept structured change proposals with agent identity, read set, write set, expected versions, intent, and evidence.
- Detect direct conflicts and hidden lineage-mediated conflicts.
- Implement a visible prepare and commit/abort lifecycle.
- Permit a demonstrably unrelated proposal to proceed concurrently.
- Recheck graph state immediately before commit and fail closed on drift.
- Verify committed changes and record the result for future agents.

## Engineering principles

- The deterministic coordinator owns conflict and commit decisions; the LLM explains or proposes resolutions.
- Use leases with expirations or optimistic versions so abandoned work cannot block the system forever.
- Make commit operations idempotent and audit every state transition.
- Require approval for high-blast-radius changes.
- Never let the demo depend on uncontrolled concurrent timing; use deterministic barriers and fixtures.
- Keep secrets in environment variables and provide `.env.example`.
- Test conflict matrices, indirect lineage collisions, expiration, drift, idempotency, aborts, and parallel safe work.
- Maintain `docs/DECISIONS.md` as architectural decisions are made.

## GitHub publishing

- Canonical repository: `https://github.com/amathias/graph-traffic-control`.
- Configured origin: `git@github-datahub-graph-traffic-control:amathias/graph-traffic-control.git`.
- While this chat is the project's primary writer, it may commit and intermittently push verified
  milestone changes to `origin/main`.
- Inspect the complete diff, run relevant checks, stage only intended paths, and keep
  `COORDINATOR_HANDOFF.md` current before pushing.
- Never change the remote, force push, delete remote refs, use another project's SSH alias, or add
  secrets, private keys, `.env` files, runtime receipts, or private evidence to Git.
- If `origin` is absent or differs from the exact value above, stop and escalate to the portfolio
  coordinator.

### Commit cadence

- Commit and push at coherent, verified milestones or independently reviewable sub-milestones, not
  after every edit or test run.
- Keep implementation, tests, documentation, and handoff updates for one logical change together;
  separate unrelated work.
- Use meaningful imperative Conventional Commit subjects such as `feat:`, `fix:`, `test:`, or
  `docs:`; never use `update`, `changes`, `fix stuff`, `WIP`, or `checkpoint`.
- For non-trivial changes, add a commit body covering why, key safety or compatibility decisions,
  and checks performed.
- Do not create an unverified work-in-progress commit solely because work pauses or a chat ends.

## Definition of done

A reviewer can launch three demo agents, inspect their proposals, watch one unrelated change commit while two semantically conflicting changes are sequenced, observe a stale proposal get rebased or aborted, confirm verification, and inspect the resulting DataHub audit context.

## Submission guardrails

- The repository must be public and contain an Apache 2.0 `LICENSE`.
- The work must be newly built during the submission period.
- Disclose any meaningful pre-existing code or assets.
- Keep the title independent: “Graph Traffic Control,” described as DataHub-powered.
