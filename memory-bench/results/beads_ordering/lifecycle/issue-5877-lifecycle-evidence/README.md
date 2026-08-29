# R6 lifecycle and control addendum

Status: **sealed, decision-ready, additive to the existing R6 evidence package**.

This addendum asks two follow-up questions without changing literal candidate generation:

1. How fresh would a query-independent reverse-PageRank order need to be under mutations?
2. Do frozen semantic, strategy-selection, or raw-rank operator controls justify a Beads-owned control surface?

## Decision

R6 should **not** standardize persistent structural-rank maintenance, periodic rank refresh, a specialized rank service, raw numeric rank, strategy-selection controls, or semantic pin/boost/demote controls from this evidence.

If a consumer continues experimenting with structural rank, the evidence supports only exact recomputation at the read boundary with candidate/order/rank-epoch-bound continuations that fail closed. That is consumer policy, not a Beads storage or discovery contract. The earlier R6 requirements remain the portable surface: compact bounded discovery, deterministic documented order, stable positions, truthful completeness, an explicit unbounded control, state-bound continuation, and explicit recall.

## Operator-control result

The completed control analysis contains 1,424 observations averaged into 672 equally weighted task/mode/page/policy cells. The 94 frozen disagreement groups contributed 752 repeat observations; 376 cells have three observations and the remaining 296 have one. The independent task count remains 21 across seven graph families—repeats estimate run variance and do not inflate N.

The preregistered control decision is ready, but the gate fails:

- Semantic controls repaired page-one placement in every bounded affected cell, so the mechanism can move the intended Memory.
- The neutral-task non-regression check cleared only search-only page sizes 10 and 20. It failed the other six mode/page cells, including every navigation cell.
- Therefore the all-cell semantic gate is false.
- Semantic task success was not equivalent to raw numeric rank within the registered ±5 percentage-point margin in any of eight mode/page cells.
- At navigation page size 5, success estimates were automatic 0.444 [0.270, 0.619], semantic 0.603 [0.460, 0.730], and raw 0.635 [0.508, 0.762]. Semantic placement was cheaper than automatic (compact tokens p50/p90 624/654 versus 665/1,890), but an isolated favorable cell cannot override the failed neutral and equivalence gates.

This is evidence against standardizing the interface, not proof that every correct intervention is useless. The frozen controls sometimes help targeted failures; the sample does not show a safe, portable control contract across neutral work and modes.

## Freshness and cost result

The mutation replay contains 4,200 task-policy snapshots across seven families, corpus sizes 50/100/500, 40 chronological mutations, and five registered policies.

| policy | refresh p50 / p90 | amortized refresh / mutation | top-10 overlap mean / min | useful-page parity | worse useful page | registered surrogate gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| exact global | 10.8 / 324.1 ms | 105.3 ms | 1.000 / 1.0 | 100.0% | 0.0% | pass |
| periodic-5 | 11.0 / 339.1 ms when run | 20.9 ms | 0.844 / 0.0 | 97.9% | 1.4% | fail |
| periodic-20 | 12.3 / 343.2 ms when run | 5.2 ms | 0.718 / 0.0 | 97.3% | 1.8% | fail |
| exact on read | 10.8 / 324.1 ms | workload-dependent | 1.000 / 1.0 | 100.0% | 0.0% | pass |
| incremental-if-feasible | unsupported | — | — | — | — | not evaluated |

Periodic-5 and periodic-20 save compute but both violate the registered minimum top-10 overlap of 0.9. The failure is not just an aggregate tail: at 500 Memories, useful-page parity falls to 93.6% and 91.8%, respectively. Exact and exact-on-read preserve retrieval behavior and explicitly invalidate changed continuations. Because the replay reads after every mutation, exact-on-read cannot coalesce writes in this measured workload and has the same observed compute cost as exact-after-update.

`workload-rate-model.json` therefore reports a separate illustrative capacity model, not a deployment measurement. Under independent Poisson updates/reads, a lazy exact-on-read refresh rate is `U*R/(U+R)`. At 10 updates/s and 1 read/s, the measured mean refresh cost projects to about 0.096 compute cores for lazy versus 1.053 for exact-after-every-update. This can inform a consumer experiment, but it does not relax the failed freshness gates or establish a real Beads workload budget.

## Specialized plumbing result

The comparable aggregated-dangling-mass implementation scales from compute p50/p90 0.58/0.88 ms at 50 Memories to 131.5/159.6 ms at 10,000. It is a compute-only curve, not a behavior-preserving replacement: across the pinned 50/100/500 sizes, top-10 overlap with the reference update order has mean 0.819 and minimum 0.0. The preregistered plumbing gate requires retrieval parity and a measured operational budget miss, neither of which is present. No daemon, dependency, persistent rank field, or specialized incremental structure is justified.

## Provenance and validity boundary

The sealed deterministic replay and scaling runs used:

- mem `377b82199d20c55584d7320389ddd41f2dae6a45`, clean;
- Beads `3302247cb4cd3a9a05227819029269c331245d05`, clean;
- experimental Beads binary SHA-256 `47aadd114f3afe16918ff5eff27f2e7d85bafc4003101ee7a4939c04252853de`;
- fixture manifest SHA-256 `fd6c88e687dcbbe8feaebb67ed512bf9063f4d9db39edb272f0b7ee6f0a20682`;
- preregistration SHA-256 `b7c83507ce771bd7c35c6ed4611c8eb3bdfe7e52506b84ed09abcb8d9354ade5`.

The agent matrix spans the implementation commits recorded in its manifest and one model (`claude-haiku-4-5-20251001`) across CLI versions 2.1.246 and 2.1.247. Of 1,424 observations, 1,120 recorded an empty mem diff. During the still-running repeat process, 88 observations recorded an `AGENTS.md`-only failure-prevention diff and 216 later observations recorded that same documentation diff plus the project-governance README link. Their exact hashes are preserved in the control manifest. No Python, TypeScript, fixture, prompt, Beads checkout, or binary changed in those two diffs; the evaluated agent ran in isolated experimental workspaces, not against those documentation files. The variation is disclosed rather than hidden or rerun after outcomes were known.

The experiment holds candidate membership fixed and cannot estimate candidate-generation recall. Its held-out authored operational tasks isolate rank/control mechanisms but do not establish population prevalence, usability of a human control interface, or production workload rates. The decision is therefore narrow: the registered evidence does not justify making these mechanisms part of R6.

## Privacy-safe artifacts

- `data/control-agent-analysis/analysis.json` — registered control comparisons and gates
- `data/control-agent-analysis/cell-estimates.jsonl` — repeat-balanced cells
- `data/control-agent-analysis/sanitized-observations.jsonl` — per-run metrics with queries, model text, failures, credentials, and local paths excluded
- `data/control-agent-analysis/manifest.json` — hashes and provenance
- `data/mutation-replay/analysis.json` and `manifest.json` — freshness, cursor, compute, and strata
- `data/rank-scaling-optimized/rank-scaling-analysis.json` and `rank-scaling-manifest.json` — compute-only curve and parity boundary
- `data/workload-rate-model.json` — explicitly illustrative workload model
- `ISSUE_COMMENT.md` — concise upstream-ready conclusion
- `REPRODUCE.md` — privacy-safe reanalysis commands
- `CHECKSUMS.sha256` — package and source-artifact digests

Raw agent streams, prompts/answers, failure diagnostics, credential paths, account identities, and experimental workspaces are intentionally excluded.
