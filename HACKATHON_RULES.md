# DataHub Agent Hackathon Rules and Compliance Checklist

Reviewed against the official rules on **July 24, 2026**. The official rules control if this summary differs or the rules change: <https://datahub.devpost.com/rules>

## Key dates

- Registration and submission opened July 6, 2026 at 9:00 a.m. Eastern Time.
- Final submission deadline: **August 10, 2026 at 5:00 p.m. Eastern Time**.
- Judging period: August 17 through August 31, 2026.
- Winners are expected to be announced around September 8, 2026.

Do not rely on a local timezone conversion without checking it again near the deadline.

## Eligibility and teams

- Eligible adults may enter individually, as a team, or through an eligible organization.
- An individual may join more than one team or organization and may also enter individually.
- A team or organization must appoint an authorized representative.
- Geographic, sponsor-affiliation, judge-employment, and conflict-of-interest exclusions apply. Every participant should verify personal eligibility in the official rules.

## Multiple submissions

- An entrant may submit more than one project.
- Every submission must be **unique and substantially different** from that entrant's other submissions.
- Each eligible submission may win only one prize.
- Only one feedback-survey submission is allowed per entrant.

For this portfolio, do not turn Graph Traffic Control into a shared shell or minor reskin of another entry. Its multi-agent transaction workflow, implementation, repository, demo, and claims must stand on their own.

## What the project must be

- A working software application newly created during the submission period.
- Built on open-source DataHub.
- Must also use at least one of the DataHub MCP Server, Agent Context Kit, DataHub Skills, or Analytics Agent.
- Must solve one challenge category or intentionally combine categories.
- Must install and run consistently on its intended platform.
- Must work as shown in the video and described in the submission.
- Standard tools, libraries, frameworks, templates, and AI coding assistants are allowed.
- Disclose other pre-existing code or work incorporated into the project.
- Third-party SDKs, APIs, data, trademarks, and assets must be used with authorization and in compliance with their licenses.

## Challenge categories

1. **Agents That Do Real Work:** Agents understand connected data through DataHub, take action, and write results back.
2. **Metadata-Aware Code Generation & Development:** Agents generate production-grade data code using real schemas, lineage, and rules; generated artifacts belong in a Git repository and should be mergeable.
3. **Production ML Agents:** Agents use end-to-end ML lineage to prevent or respond to production ML failures.
4. **Open / Wildcard:** Creative applications built on DataHub's open-source stack.

## Required submission package

- A project URL that gives judges easy access to test the application.
- A public code repository containing all source, assets, and complete setup instructions.
- An **Apache 2.0 license file**, visible and detectable at the top of the repository page, including the repository About area.
- A clear text description of features, behavior, technologies, and data.
- A publicly visible YouTube, Vimeo, or Youku demonstration video.
- The video must be **under three minutes**; judges need not watch beyond three minutes.
- The video must show the project actually functioning on its intended device.
- Do not use third-party copyrighted music, marks, or other material without permission.
- English submission materials, or English translations.

Recommended:

- Include sample outputs such as plans, reports, queries, transformations, or generated code in an `examples/` directory.
- Make the demo reproducible without requiring judges to purchase services.

## Testing availability

- Provide a website, functioning demo, or test build.
- If access is private, include working credentials in the testing instructions.
- Keep the project available free of charge and without testing restrictions through the judging period.
- Judges may judge only the description, images, and video, so those artifacts must explain the value even if they never run the app.

## Judging process

Stage one is a pass/fail screen for theme fit, baseline viability, and reasonable use of the required tools.

Stage two uses five core criteria:

1. **Use of DataHub:** Meaningful use of the context graph and eligible agent tooling. Reading and writing context is stronger than shallow metadata lookup.
2. **Technical Execution:** Quality, robustness, end-to-end functionality, and truthful claims.
3. **Originality:** A novel extension or composition beyond DataHub's out-of-the-box capabilities.
4. **Real-World Usefulness:** Clear value to practicing data, ML, or AI platform teams.
5. **Submission Quality:** A concise demo, understandable description, and reproducible README.

Bonus consideration is available for a meaningful open-source DataHub contribution such as a connector, skill, fix, RFC, or documentation improvement.

The rules permit expert review, peer review, automated AI-driven analysis, or combinations of those methods. Make every criterion explicit in the README and demo instead of expecting a reviewer to infer it.

## Graph Traffic Control scoring checklist

- [ ] Multiple agents submit structured proposals concurrently.
- [ ] DataHub lineage reveals at least one indirect conflict.
- [ ] A non-conflicting proposal visibly proceeds in parallel.
- [ ] Prepare, approval, commit or abort, and verification states are shown.
- [ ] Graph drift or a stale version causes a safe failure.
- [ ] The deterministic coordinator—not an unvalidated LLM answer—controls execution.
- [ ] Outcomes and context are written back to DataHub.
- [ ] The README distinguishes semantic graph coordination from file locking.

## Pre-submission audit

- [ ] Project was created during the permitted period.
- [ ] Repository is public.
- [ ] Apache 2.0 `LICENSE` is present and visible.
- [ ] Setup succeeds from a clean checkout.
- [ ] No secrets, private data, or unlicensed assets are committed.
- [ ] All simulated actions are labeled; all claimed real actions are verifiable.
- [ ] Public test URL or complete local test instructions work.
- [ ] Video is public, under three minutes, and shows the application running.
- [ ] Description states the problem, DataHub usage, technical proof, originality, and value.
- [ ] Entry is substantially different from the other portfolio submissions.
- [ ] Final rules and deadline are rechecked on Devpost.
