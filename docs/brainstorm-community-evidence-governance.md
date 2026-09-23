# mem community + evidence governance

Status: converged
Idea target: 30
Problem: How should mem earn, triage, and convert Beads-community feedback into
a transparent roadmap without letting anecdotal requests distort the evaluation
program?

## Load-bearing invariants

- mem is the incubation/prototyping repo; gastownhall/beads is the intended
  eventual product and community home after upstream-readiness review.
- Community demand prioritizes research questions; it does not prove a design.
- A recommendation requires a named hypothesis, decision gate, provenance, and
  evidence strength.
- Negative and neutral results remain visible.
- Public artifacts are privacy-safe and name-agnostic; raw traces stay private.
- Discussions collect context; Issues own committed work; the Project is a view,
  not a second source of truth.

## Prior-art exclusion zones

1. **Open discussion forum** — categories, upvotes, answers, and maintainer replies
   expose demand before commitment. Source: https://github.com/gastownhall/beads/discussions
2. **Structured discussion forms** — typed prompts improve feedback completeness.
   Source: https://docs.github.com/en/discussions/managing-discussions-for-your-community/creating-discussion-category-forms
3. **Issue + sub-issue planning** — committed work is decomposed and linked through
   dependencies. Source: https://docs.github.com/en/issues/tracking-your-work-with-issues/learning-about-issues/planning-and-tracking-work-for-your-team-or-project
4. **GitHub Projects** — one item collection supports table, board, roadmap, fields,
   automation, and charts. Source: https://docs.github.com/en/issues/planning-and-tracking-with-projects
5. **RFC/FCP process** — substantial changes collect public design feedback before a
   bounded final-comment period. Source: https://rust-lang.github.io/rfcs/
6. **Enhancement proposal lifecycle** — proposal metadata and explicit graduation
   states preserve chain of custody. Source: https://github.com/kubernetes/enhancements/blob/master/keps/README.md
7. **Historical project insights** — burn-up and state-change charts expose delivery
   trends and bottlenecks. Source: https://docs.github.com/en/issues/planning-and-tracking-with-projects/viewing-insights-from-your-project/about-insights-for-projects

## Divergent ideas

Each MVP note names the smallest executable loop that distinguishes the idea's
shape. Ideas that collapsed to the same loop were rejected before recording.

1. **Structured field report** — a Discussion form captures workflow, scale,
   failure, workaround, and consent. MVP: one form, weekly dedupe, monthly themes.
2. **Maintainer listening post** — sample existing Beads Discussions and Issues
   rather than asking users to come to mem. MVP: ten coded threads per week.
3. **Adopter diary panel** — a fixed cohort records friction at the moment it occurs.
   MVP: five users, four weeks, one two-minute diary prompt per event.
4. **Workflow interview ladder** — interviews follow novice, regular, and power-user
   strata. MVP: three interviews per stratum with a shared critical-incident guide.
5. **Office-hours observation** — users bring real sessions and maintainers observe
   the work live. MVP: one monthly session with anonymized notes.
6. **Journey replay kit** — users submit a sanitized sequence that mem can replay.
   MVP: schema, validator, and one accepted community fixture.
7. **Counterexample bounty** — invite cases that falsify current recommendations.
   MVP: publish one claim and acceptance criteria for a disconfirming trace.
8. **Replication ladder** — independent contributors rerun sealed eval packages.
   MVP: one-command bundle plus three provenance tiers.
9. **Benchmark contribution lane** — community pain becomes held-out eval tasks.
   MVP: task template, leakage review, and two-reviewer admission gate.
10. **Opt-in telemetry pulse** — aggregate only coarse workflow counts and failures.
    MVP: local summary preview, explicit consent, one-month retention.
11. **Release cohort** — staged adopters test a candidate before general guidance.
    MVP: canary group, baseline period, rollback rule, post-period comparison.
12. **Shadow recommendation** — compute what mem would recommend without changing
    user behavior. MVP: log-only arm and counterfactual disagreement report.
13. **Randomized interface trial** — compare two control surfaces on matched tasks.
    MVP: preregister assignment, primary metric, stopping rule, and exclusions.
14. **Longitudinal retention panel** — measure whether benefit survives weeks, not
    one session. MVP: weekly repeated task with within-user change estimates.
15. **Failure-cluster watch** — cluster deterministic error signatures and alert on
    growing recurrence. MVP: weekly top-new and top-growing clusters.
16. **Decision premortem** — collect predicted failure modes before implementation.
    MVP: five independent narratives converted to measurable guardrails.
17. **Evidence challenge review** — a skeptic reviews provenance, leakage, and
    alternate explanations. MVP: red-team checklist and written disposition.
18. **Neutral-task budget** — every affected-task experiment includes tasks expected
    not to move. MVP: preregister a 1:1 affected/neutral allocation.
19. **Experience-report RFC** — substantial choices require user stories and actual
    experience reports, not only design arguments. MVP: one proposal template.
20. **Evidence escrow** — freeze hashes, analysis code, and gates before paid runs.
    MVP: signed manifest checked before aggregation.
21. **Independent adjudication** — disputed qualitative labels are blinded and
    resolved by a third reviewer. MVP: double-code 20 cases and record agreement.
22. **Sensitivity envelope** — report how conclusions change across reasonable
    thresholds. MVP: one parameter grid and conclusion-stability plot.
23. **Sequential evidence gate** — move from smoke to pilot to confirmatory only when
    preconditions pass. MVP: explicit entry/exit criteria for three stages.
24. **Research-question auction** — community members allocate a limited token budget
    to questions, forcing priorities. MVP: quarterly 100-token ballot.
25. **Rotating user council** — a time-bounded representative group reviews themes,
    not individual implementation choices. MVP: six seats, two-month term.
26. **Minority report** — every roadmap decision preserves the strongest dissenting
    interpretation. MVP: mandatory dissent field or “none submitted.”
27. **Abandonment interview** — specifically recruit people who stopped using Beads
    or memory tools. MVP: five exit interviews and barrier taxonomy.
28. **Ecosystem compatibility matrix** — test workflows across agents, shells,
    worktrees, and Beads versions. MVP: one risk-weighted matrix refreshed monthly.
29. **Public evidence ledger** — each claim links to hypothesis, run, result,
    confidence, limitations, and supersession. MVP: one machine-readable index.
30. **Decision-to-outcome audit** — revisit shipped decisions after a fixed horizon.
    MVP: 30/90-day review comparing predicted and observed outcomes.

## Convergence ratings

Scores are Feasibility / Novelty / Impact (1–5).

| Idea | F | N | I | Disposition |
|---|---:|---:|---:|---|
| 1 Structured field report | 5 | 2 | 4 | Shortlist |
| 2 Maintainer listening post | 5 | 3 | 4 | Shortlist |
| 3 Adopter diary panel | 3 | 4 | 4 | Pilot later |
| 4 Workflow interview ladder | 4 | 2 | 4 | Shortlist |
| 5 Office-hours observation | 4 | 3 | 4 | Pilot later |
| 6 Journey replay kit | 3 | 4 | 5 | Shortlist |
| 7 Counterexample bounty | 4 | 4 | 5 | Shortlist |
| 8 Replication ladder | 3 | 3 | 5 | Shortlist |
| 9 Benchmark contribution lane | 3 | 3 | 5 | Shortlist |
| 10 Opt-in telemetry pulse | 2 | 3 | 4 | Defer: privacy/design cost |
| 11 Release cohort | 3 | 2 | 5 | Pilot later |
| 12 Shadow recommendation | 3 | 4 | 5 | Research backlog |
| 13 Randomized interface trial | 3 | 2 | 5 | Research backlog |
| 14 Longitudinal retention panel | 2 | 3 | 5 | Research backlog |
| 15 Failure-cluster watch | 4 | 3 | 4 | Research backlog |
| 16 Decision premortem | 5 | 2 | 3 | Use for high-risk choices |
| 17 Evidence challenge review | 4 | 3 | 5 | Shortlist |
| 18 Neutral-task budget | 5 | 3 | 5 | Shortlist |
| 19 Experience-report RFC | 4 | 2 | 5 | Shortlist |
| 20 Evidence escrow | 4 | 3 | 5 | Shortlist |
| 21 Independent adjudication | 3 | 2 | 4 | Research standard |
| 22 Sensitivity envelope | 4 | 3 | 5 | Research standard |
| 23 Sequential evidence gate | 5 | 3 | 5 | Shortlist |
| 24 Research-question auction | 3 | 5 | 3 | Experiment later |
| 25 Rotating user council | 2 | 3 | 4 | Defer until community grows |
| 26 Minority report | 5 | 3 | 4 | Decision standard |
| 27 Abandonment interview | 3 | 4 | 5 | Shortlist |
| 28 Compatibility matrix | 3 | 2 | 5 | Research backlog |
| 29 Public evidence ledger | 4 | 4 | 5 | Shortlist |
| 30 Decision-to-outcome audit | 4 | 4 | 5 | Shortlist |

## Recommended operating model

Use a dual-track funnel with a hard join gate:

1. **Listen:** structured Discussions, upstream listening, interviews, and exit
   interviews produce problem statements.
2. **Triage:** deduplicate, identify affected personas/workflows, and assign evidence
   need. No delivery promise yet.
3. **Frame:** convert a supported problem into an experience-report RFC with explicit
   hypothesis, non-goals, alternatives, risks, and decision gate.
4. **Validate:** admit sanitized replay tasks; preregister smoke, pilot, confirmatory,
   neutral-task, sensitivity, and replication requirements.
5. **Decide:** publish evidence ledger entry, strongest dissent, and disposition:
   adopt, experiment more, defer, or reject.
6. **Deliver:** only adopted decisions become implementation issues and sub-issues.
7. **Audit:** review predicted versus observed outcomes after 30 and 90 days.

## Incubation-to-upstream boundary

Prototype in `sjarmak/mem` until all of the following hold:

- the problem statement is supported by Beads-user evidence, including dissent or
  abandonment evidence where available;
- the recommendation passes its preregistered affected-task and neutral-task gates;
- the sanitized eval or replay package is independently reproducible;
- the public design removes mem-specific paths, identities, infrastructure, and
  private trace assumptions;
- the proposal names the smallest Beads-owned surface and its maintenance cost;
- an upstream maintainer sponsors the proposal and chooses the correct Beads
  Discussion, Issue, or enhancement/RFC-shaped path.

After those gates, open the upstream proposal in `gastownhall/beads`, link back to
the sealed evidence package in `sjarmak/mem`, and track upstream disposition as a
separate item. Do not duplicate implementation state across both repositories.

## GitHub Project shape

Project: `mem — Research, Community, and Delivery`

Fields:

- Status: Inbox / Triage / Framed / Validating / Decision / Ready / In progress /
  Measuring / Done / Not planned
- Track: Community / Research / Eval infrastructure / Experiment / Product / Docs
- Evidence stage: None / Anecdotal / Observational / Pilot / Confirmatory / Replicated
- Decision: Undecided / Adopt / More evidence / Defer / Reject / Superseded
- Community source, research question, owner, priority, target iteration, confidence,
  privacy review, upstream link, and evidence-artifact link

Views:

- Community inbox
- Research questions
- Active validations
- Decision queue
- Delivery board
- Roadmap
- Outcomes due (30/90-day audits)

Progress measures:

- response time and triage time for community inputs
- conversion from input → framed research question
- time spent in each evidence stage
- confirmatory and replication rate
- negative/neutral-result publication rate
- decision cycle time and implementation lead time
- 30/90-day predicted-versus-observed outcome accuracy
