# Candidate-density and linkage agent evidence

5254 usable observations from 21 base tasks; 2 embedded infrastructure failures.

Repeated density and linkage variants are paired within base task and graph family. Intervals are 90% hierarchical cluster bootstraps (family, then task).

## Registered decision gates

- candidate density behaviorally material: **False**
- pagerank benefit link dependent: **False**
- structural default supported: **False**
- query specific beads ownership supported: **False**

## Navigation, page size 5

| candidates | links | policy | success [90% CI] | page-one useful | pages p50/p90 | compact tokens p50/p90 |
|---:|---|---|---:|---:|---:|---:|
| 10 | enriched | bm25f | 0.667 [0.476, 0.841] | 1.000 | 1.0/1.0 | 604/627 |
| 10 | enriched | key | 0.746 [0.603, 0.889] | 0.667 | 1.7/2.0 | 649/1253 |
| 10 | enriched | pagerank | 0.619 [0.460, 0.762] | 1.000 | 1.0/1.3 | 618/625 |
| 10 | native | bm25f | 0.619 [0.429, 0.794] | 1.000 | 1.0/2.0 | 604/627 |
| 10 | native | key | 0.683 [0.476, 0.857] | 0.667 | 2.0/2.0 | 649/1255 |
| 10 | native | pagerank | 0.667 [0.492, 0.825] | 0.857 | 1.0/2.0 | 608/1234 |
| 10 | sparse | bm25f | 0.651 [0.444, 0.841] | 1.000 | 1.0/1.0 | 604/627 |
| 10 | sparse | key | 0.762 [0.603, 0.905] | 0.667 | 2.0/2.0 | 649/1255 |
| 10 | sparse | pagerank | 0.746 [0.571, 0.905] | 0.857 | 1.0/2.0 | 616/1247 |
| 40 | enriched | bm25f | 0.540 [0.333, 0.746] | 1.000 | 1.0/1.0 | 569/576 |
| 40 | enriched | key | 0.571 [0.429, 0.714] | 0.667 | 2.3/3.7 | 649/2108 |
| 40 | enriched | pagerank | 0.492 [0.286, 0.683] | 0.714 | 1.3/3.7 | 618/2132 |
| 40 | native | bm25f | 0.571 [0.381, 0.746] | 1.000 | 1.0/1.0 | 569/576 |
| 40 | native | key | 0.476 [0.286, 0.683] | 0.667 | 2.7/4.0 | 649/2527 |
| 40 | native | pagerank | 0.635 [0.460, 0.810] | 0.619 | 1.7/3.0 | 620/1708 |
| 40 | sparse | bm25f | 0.667 [0.492, 0.825] | 1.000 | 1.0/1.0 | 569/576 |
| 40 | sparse | key | 0.508 [0.365, 0.651] | 0.667 | 2.7/4.3 | 649/2676 |
| 40 | sparse | pagerank | 0.667 [0.524, 0.794] | 0.810 | 1.0/4.3 | 597/1190 |
| 150 | enriched | bm25f | 0.603 [0.397, 0.810] | 1.000 | 1.0/1.3 | 569/576 |
| 150 | enriched | key | 0.476 [0.317, 0.651] | 0.667 | 2.0/3.3 | 649/1904 |
| 150 | enriched | pagerank | 0.349 [0.190, 0.508] | 0.714 | 1.0/3.3 | 620/2081 |
| 150 | native | bm25f | 0.651 [0.460, 0.825] | 1.000 | 1.0/1.0 | 569/576 |
| 150 | native | key | 0.476 [0.270, 0.683] | 0.667 | 2.0/4.0 | 649/2496 |
| 150 | native | pagerank | 0.603 [0.429, 0.778] | 0.571 | 1.7/4.0 | 620/2265 |
| 150 | sparse | bm25f | 0.619 [0.413, 0.825] | 1.000 | 1.0/1.0 | 569/576 |
| 150 | sparse | key | 0.444 [0.286, 0.603] | 0.667 | 2.0/4.0 | 649/2325 |
| 150 | sparse | pagerank | 0.619 [0.460, 0.762] | 0.571 | 1.0/3.3 | 624/1719 |

## Candidate-density control

Primary-first, unbounded visibility. Positive success/failure values mean the 150-candidate condition was worse than 10 candidates.

| links | mode | success drop [90% CI] | correct-use failure increase | retrieval-token growth p50 |
|---|---|---:|---:|---:|
| enriched | navigation | -6.3% [-20.6%, +7.9%] | -6.3% | +14987.33 |
| enriched | search-only | -3.2% [-12.7%, +3.2%] | -3.2% | +15002.00 |
| native | navigation | +6.3% [-4.8%, +19.0%] | +6.3% | +14957.00 |
| native | search-only | +0.0% [-9.5%, +7.9%] | +0.0% | +14984.00 |
| sparse | navigation | +6.3% [+0.0%, +15.9%] | +6.3% | +15002.00 |
| sparse | search-only | +1.6% [-6.3%, +12.7%] | +1.6% | +14928.00 |

## PageRank versus key

Navigation with five-result pages. Positive values favor the contender.

| candidates | links | page-one gain | pages saved p50 | compact-token saving p50 | success delta [90% CI] |
|---:|---|---:|---:|---:|---:|
| 10 | enriched | +33.3% | +0.67 | +4.4% | -12.7% [-30.2%, +3.2%] |
| 10 | native | +19.0% | +0.00 | +4.3% | -1.6% [-14.3%, +12.7%] |
| 10 | sparse | +19.0% | +0.00 | +4.2% | -1.6% [-17.5%, +14.3%] |
| 40 | enriched | +4.8% | +0.00 | +4.3% | -7.9% [-31.7%, +15.9%] |
| 40 | native | -4.8% | +0.00 | +8.0% | +15.9% [-7.9%, +38.1%] |
| 40 | sparse | +14.3% | +0.00 | +8.4% | +15.9% [-1.6%, +33.3%] |
| 150 | enriched | +4.8% | +0.00 | +4.8% | -12.7% [-38.1%, +11.1%] |
| 150 | native | -9.5% | +0.00 | +8.2% | +12.7% [-14.3%, +39.7%] |
| 150 | sparse | -9.5% | +0.67 | +6.0% | +17.5% [+3.2%, +31.7%] |

## BM25F versus PageRank

Navigation with five-result pages. Positive values favor the contender.

| candidates | links | page-one gain | pages saved p50 | compact-token saving p50 | success delta [90% CI] |
|---:|---|---:|---:|---:|---:|
| 10 | enriched | +0.0% | +0.00 | +1.6% | +4.8% [-6.3%, +15.9%] |
| 10 | native | +14.3% | +0.00 | -0.5% | -4.8% [-14.3%, +4.8%] |
| 10 | sparse | +14.3% | +0.00 | +3.4% | -9.5% [-27.1%, +7.9%] |
| 40 | enriched | +28.6% | +0.00 | +8.2% | +4.8% [-20.6%, +34.9%] |
| 40 | native | +38.1% | +0.00 | +8.1% | -6.3% [-20.6%, +6.3%] |
| 40 | sparse | +19.0% | +0.00 | +4.4% | +0.0% [-14.3%, +15.9%] |
| 150 | enriched | +28.6% | +0.00 | +8.1% | +25.4% [-0.0%, +52.4%] |
| 150 | native | +42.9% | +0.00 | +8.2% | +4.8% [-15.9%, +25.4%] |
| 150 | sparse | +42.9% | +0.00 | +8.0% | +0.0% [-20.6%, +20.6%] |

## Linkage interaction

Enriched-minus-sparse change in PageRank's advantage over key order, under navigation with five-result pages.

| candidates | page-one change | pages-saved change p50 | compact-saving change p50 | success-advantage change |
|---:|---:|---:|---:|---:|
| 10 | +14.3% | +0.00 | +0.0% | -11.1% |
| 40 | -9.5% | +0.00 | -2.0% | -23.8% |
| 150 | +14.3% | -0.67 | -2.9% | -30.2% |

## Graph-family tails

Decision-edge cells only: 150 candidates, navigation, five-result pages. Positive values favor the contender.

| links | comparison | family | pairs | pages saved p50/p90 | compact saving p50/p90 | success delta p50/p90 |
|---|---|---|---:|---:|---:|---:|
| enriched | key→pagerank | data-schema-dependency-dag | 3 | +1.00/+1.53 | +9.0%/+52.8% | +0.0%/+0.0% |
| enriched | key→pagerank | distributed-system-clustered-components | 3 | +0.00/+0.53 | -88.8%/-3.1% | +0.0%/+26.7% |
| enriched | key→pagerank | incident-runbook-sparse-authority | 3 | +0.00/+0.80 | +6.7%/+43.9% | +0.0%/+53.3% |
| enriched | key→pagerank | migration-correction-temporal-chain | 3 | +0.00/+1.87 | +8.3%/+58.3% | +0.0%/+53.3% |
| enriched | key→pagerank | platform-documentation-hub-spoke | 3 | -0.33/+0.20 | -180.7%/-42.6% | -100.0%/+6.7% |
| enriched | key→pagerank | release-engineering-branching-playbooks | 3 | +1.00/+1.27 | +8.9%/+50.4% | +0.0%/+0.0% |
| enriched | key→pagerank | security-policy-cross-team-network | 3 | +0.00/+1.07 | +3.9%/+48.2% | +0.0%/+80.0% |
| enriched | pagerank→bm25f | data-schema-dependency-dag | 3 | +0.00/+0.00 | +8.3%/+8.4% | +33.3%/+86.7% |
| enriched | pagerank→bm25f | distributed-system-clustered-components | 3 | +2.33/+2.87 | +72.9%/+75.6% | +100.0%/+100.0% |
| enriched | pagerank→bm25f | incident-runbook-sparse-authority | 3 | +0.00/+1.07 | +6.4%/+7.4% | -33.3%/-6.7% |
| enriched | pagerank→bm25f | migration-correction-temporal-chain | 3 | +0.00/+1.07 | +7.6%/+8.1% | +0.0%/+0.0% |
| enriched | pagerank→bm25f | platform-documentation-hub-spoke | 3 | +2.33/+3.13 | +72.5%/+77.1% | +100.0%/+100.0% |
| enriched | pagerank→bm25f | release-engineering-branching-playbooks | 3 | +0.00/+0.00 | +3.0%/+7.1% | +0.0%/+0.0% |
| enriched | pagerank→bm25f | security-policy-cross-team-network | 3 | +0.00/+1.87 | +7.6%/+8.6% | +0.0%/+80.0% |
| native | key→pagerank | data-schema-dependency-dag | 3 | +0.00/+0.80 | +4.9%/+30.7% | +0.0%/+80.0% |
| native | key→pagerank | distributed-system-clustered-components | 3 | +0.00/+0.80 | -81.7%/+10.0% | +0.0%/+80.0% |
| native | key→pagerank | incident-runbook-sparse-authority | 3 | +0.67/+1.47 | +8.0%/+35.0% | +0.0%/+26.7% |
| native | key→pagerank | migration-correction-temporal-chain | 3 | +0.00/+0.27 | +4.2%/+15.3% | +66.7%/+93.3% |
| native | key→pagerank | platform-documentation-hub-spoke | 3 | -1.33/+1.07 | -252.3%/-9.7% | -66.7%/+66.7% |
| native | key→pagerank | release-engineering-branching-playbooks | 3 | +3.00/+3.80 | +13.1%/+68.6% | +0.0%/+80.0% |
| native | key→pagerank | security-policy-cross-team-network | 3 | +1.00/+2.07 | +8.6%/+59.1% | +0.0%/+80.0% |
| native | pagerank→bm25f | data-schema-dependency-dag | 3 | +0.00/+0.80 | +8.2%/+43.8% | +0.0%/+26.7% |
| native | pagerank→bm25f | distributed-system-clustered-components | 3 | +2.00/+3.87 | +67.0%/+78.6% | +0.0%/+80.0% |
| native | pagerank→bm25f | incident-runbook-sparse-authority | 3 | +0.00/+1.07 | +4.2%/+50.1% | -33.3%/+46.7% |
| native | pagerank→bm25f | migration-correction-temporal-chain | 3 | +1.00/+1.53 | +8.2%/+52.6% | +0.0%/+26.7% |
| native | pagerank→bm25f | platform-documentation-hub-spoke | 3 | +1.33/+3.20 | +75.1%/+78.1% | +33.3%/+86.7% |
| native | pagerank→bm25f | release-engineering-branching-playbooks | 3 | +0.00/+0.00 | -1.4%/-1.0% | -33.3%/-6.7% |
| native | pagerank→bm25f | security-policy-cross-team-network | 3 | +0.00/+0.00 | +4.6%/+6.2% | +0.0%/+26.7% |

## Task-level tails

The bottom task is the least favorable observed paired result for the contender. Full bottom/top-three records for every metric are retained in `analysis.json`.

| links | comparison | metric | p10/p50/p90 | negative tasks | bottom task |
|---|---|---|---:|---:|---|
| enriched | key→pagerank | pages_saved | -0.33/+0.00/+1.33 | 4 | ordering-followup-02-500-cache-owner |
| enriched | key→pagerank | compact_token_reduction_fraction | -180.7%/+4.8%/+60.8% | 5 | ordering-followup-02-500-cache-owner |
| enriched | key→pagerank | task_success_delta | -100.0%/+0.0%/+66.7% | 7 | ordering-followup-02-50-token-scope |
| enriched | pagerank→bm25f | pages_saved | +0.00/+0.00/+2.33 | 1 | ordering-followup-06-50-index-build |
| enriched | pagerank→bm25f | compact_token_reduction_fraction | +3.1%/+8.1%/+72.9% | 0 | ordering-followup-05-100-provenance-gate |
| enriched | pagerank→bm25f | task_success_delta | -33.3%/+0.0%/+100.0% | 4 | ordering-followup-04-50-outbox-cursor |
| native | key→pagerank | pages_saved | -1.33/+0.00/+2.33 | 4 | ordering-followup-04-500-clock-skew |
| native | key→pagerank | compact_token_reduction_fraction | -252.3%/+8.2%/+51.0% | 4 | ordering-followup-04-500-clock-skew |
| native | key→pagerank | task_success_delta | -66.7%/+0.0%/+100.0% | 6 | ordering-followup-02-50-token-scope |
| native | pagerank→bm25f | pages_saved | +0.00/+0.00/+2.00 | 1 | ordering-followup-01-50-lease-fence |
| native | pagerank→bm25f | compact_token_reduction_fraction | -0.9%/+8.2%/+75.1% | 3 | ordering-followup-05-100-provenance-gate |
| native | pagerank→bm25f | task_success_delta | -33.3%/+0.0%/+66.7% | 7 | ordering-followup-05-100-provenance-gate |

## Provenance profiles

- observations=2646; infrastructure failures=2; mem=bdacf881f45c8aaa5c43818b788ca0cb17da56f1 (dirty=True, diff=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855); Beads=c89168fc0942f18dd08fd35296c6fdebdd3e1dd7 (dirty=True, diff=afb24525b9be6d70bbca433d906aeec78188f24d8bea19928ba700e619aa2627); binary=47aadd114f3afe16918ff5eff27f2e7d85bafc4003101ee7a4939c04252853de; model=claude-haiku-4-5-20251001; agent CLI=2.1.245
- observations=2610; infrastructure failures=0; mem=f78a6642d1638a553327a0a23508feb099465ccb (dirty=True, diff=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855); Beads=c89168fc0942f18dd08fd35296c6fdebdd3e1dd7 (dirty=True, diff=afb24525b9be6d70bbca433d906aeec78188f24d8bea19928ba700e619aa2627); binary=47aadd114f3afe16918ff5eff27f2e7d85bafc4003101ee7a4939c04252853de; model=claude-haiku-4-5-20251001; agent CLI=2.1.246

## Targeted repeats

- infrastructure failures: 2 groups
- policy task success disagreements: 369 groups
- density endpoint disagreements: 39 groups

## Machine-readable evidence

The machine-readable density, policy, linkage-interaction, and repeat-trigger tables, including all graph-family and task-level tails, are in `analysis.json`. Raw model text is deliberately excluded from the shareable observation projection.
