# mem project management

The live planning surface is [mem — Research, Community, and Delivery](https://github.com/users/sjarmak/projects/4). It is an incubation project for work that may eventually move into `gastownhall/beads`; it is not an alternate Beads roadmap.

## Operating invariant

Community feedback chooses which questions deserve attention. It does not, by itself, settle a technical decision. Recommendations require a named research question, an auditable evidence stage, affected-task and neutral-task gates, costs and limitations, and a recorded decision. Negative and neutral results remain visible.

One object owns each kind of state:

| State                                       | Authority                                 |
| ------------------------------------------- | ----------------------------------------- |
| Public commitment, discussion, and decision | GitHub Issue                              |
| Cross-track status and portfolio view       | GitHub Project fields and views           |
| Local execution and dependencies            | `mem-*` bead                              |
| Result and provenance                       | Versioned, privacy-safe evidence artifact |

Do not copy narrative progress between these surfaces. Link them. A Project item is a projection of its Issue; the corresponding bead may carry the Issue URL as its external reference.

## Flow

1. **Listen.** Capture one workflow or problem per community-feedback issue. Record frequency, impact, workaround, and whether the reporter can help validate it. Count corroborating signals; do not count reactions as independent evidence.
2. **Triage.** Deduplicate, identify the affected population, and assign `Priority` separately from `Evidence confidence`.
3. **Frame.** Convert a promising signal into a falsifiable research question. Name the decision it could change, the baseline, affected tasks, neutral tasks, metrics, minimum effect or acceptance gate, and contamination/privacy risks. Prefer real held-out tasks; use authored or synthetic stress cases to isolate mechanisms, not to stand in for prevalence.
4. **Validate.** Preregister before confirmatory runs. Preserve frozen inputs, versions, seeds or schedules, raw-to-summary lineage, failures, cost, and stopping rules. A pilot may shape a confirmatory study but may not be relabeled as one. Establish output parity before comparing speed or cost, and record whether the sample can resolve the decision threshold.
5. **Decide.** Record adopt, more evidence, defer, reject, or supersede. Cite the evidence, alternatives, uncertainty, neutral-task result, operational cost, and rollback trigger.
6. **Deliver.** Track implementation with an Issue and local bead. Use the hvir-derived agent fields only when measured; missing means unknown, never zero.
7. **Audit.** Set `Outcome review date` before closing delivery. Compare the observed outcome with the claim that justified the work and open follow-up work for regressions or surprises.
8. **Upstream.** Move to `gastownhall/beads` only when the upstream gate below passes.

## Field semantics

`Status` is deliberately small: Todo, In Progress, Done. Research maturity belongs in `Evidence stage`; recommendation state belongs in `Decision`; transfer state belongs in `Upstream state`. Keeping these axes separate prevents “experiment finished” from being mistaken for “proposal accepted.”

- `Evidence stage`: None → Anecdotal → Observational → Pilot → Confirmatory → Replicated. It describes the strongest completed evidence, not intended work.
- `Evidence confidence`: judgment after considering design, sample, variance, provenance, and known threats. It is independent of effect size or community enthusiasm.
- `Validation coverage`: whether the preregistered affected and neutral surfaces were actually exercised.
- `Community signal count`: deduplicated, relevant reports. Document counting rules on the Issue.
- `Task source`: the provenance and realism of the evaluated work. A mixed benchmark must report each source separately rather than pooling away the distinction.
- `Contamination control`: the strongest applicable answer-leakage defense. “Private” is a status, not permission to publish the task or its data.
- `Precision status`: whether the design can resolve the registered decision threshold. “Inconclusive” is a valid result and must not be converted into “no effect.”
- `Agent difficulty`, model-route fields, token fields, time to first candidate, and first-pass outcome: an agentic delivery forecast and measurement ledger adapted from hvir. Leave unavailable measurements empty and mark coverage explicitly.

The complete machine-readable schema is [`.github/projects/mem-research-community.json`](../.github/projects/mem-research-community.json).

## hvir reference and adaptation

The template was derived from a live inventory of `jarmak-personal/hvir` rather than copied blindly. hvir's project keeps execution status small, groups epics and sub-issues, exposes feature and bug views, and tracks agent difficulty, delivery risk, estimate confidence, model routing, phase-token counts, time to first candidate, first-pass outcome, lifecycle tokens, and measurement coverage. Its repository-side project-management contract treats those fields as projections of authoritative issue history and distinguishes missing measurements from zero.

mem preserves those agentic-delivery properties and adds the dimensions hvir does not need for this research-led program: community signal, research question, evidence stage and confidence, validation coverage, explicit decision, outcome review, and upstream state. These are independent fields because delivery completion, evidentiary maturity, and upstream acceptance are different facts. Views reveal only the fields needed for each decision surface, keeping the full schema from becoming a single unusable table.

The reusable JSON manifest plays the same role as hvir's canonical project configuration without embedding installation-specific node IDs. The live Project plus the manifest are the prototype; if this workflow moves into `gastownhall/beads`, create a Beads-owned project/config and migrate issue links rather than preserving `sjarmak` IDs.

## Upstream gate

An item is `Ready` only when all applicable checks pass:

- a concrete Beads user or maintainer problem has multiple relevant signals or explicit maintainer sponsorship;
- the research question and decision gate were recorded before confirmatory analysis;
- affected tasks pass and preregistered neutral tasks show no unacceptable regression;
- artifacts identify the mem commit, Beads commit/binary, fixture or corpus, configuration, and analysis version;
- another operator can reproduce the conclusion from privacy-safe inputs;
- output/behavior parity is established before any efficiency claim, and task source, contamination control, and precision status are explicit;
- performance, operational, maintenance, migration, and failure-mode costs are stated;
- the proposed Beads-owned API/data surface is minimal and the mem-only incubation residue is excluded;
- private identities, raw traces, credentials, and infrastructure details are absent;
- an upstream maintainer agrees to sponsor or review the proposal.

`Ready` is not `Accepted`. Use `Socializing` while gathering upstream feedback, `Proposed` only after an upstream issue/PR exists, and link it in `Upstream link`.

## Views and cadence

- Review **Community intake** weekly for duplicates and question candidates.
- Review **Research questions**, **Active validations**, and **Decision queue** together; a decision must be traceable through all three.
- Review **Delivery** for execution, then **Outcome audits** for claims that need checking after release.
- Review **Upstream readiness** with Beads maintainers, not only mem contributors.
- Use **Agent efficiency** for measured delivery economics, never to rank people or reward raw token minimization.

A monthly project brief should report intake volume and deduplicated themes, questions opened/closed, validation coverage and failures, decisions by outcome, lead time, outcome-audit results, upstream-state changes, and missing measurement coverage. Never summarize only wins.

Recurring evaluations must name a pinned baseline (task-set hash, harness commit, model/version, configuration, seeds or schedule, and date) and append results as a series. Compare new runs with that baseline and prior variance; do not replace the prior result or promote a single point estimate as a trend.

## Reuse and audit

GitHub only supports marking organization-owned projects as organization templates. This project is user-owned, so reuse it by copying:

```sh
gh project copy 4 \
  --source-owner sjarmak \
  --target-owner TARGET_OWNER \
  --title "PROJECT — Research, Community, and Delivery"
```

After copying, link the destination repository, verify all fields and views against the JSON manifest, and inspect the built-in workflows. Node IDs are intentionally absent from the manifest because they change on every copy.

The live project should have 43 total fields (13 GitHub defaults plus 30 custom fields), 10 views, public visibility, a repository link to `sjarmak/mem`, and six enabled default workflows. Its description and README must state the decision rule and privacy boundary.

Issue forms in `.github/ISSUE_TEMPLATE/` provide the reusable intake contracts. At present, `sjarmak/mem` Issues are the incubation channel; broader Beads sentiment should be gathered in the upstream [Beads Discussions](https://github.com/gastownhall/beads/discussions) and linked rather than copied.
