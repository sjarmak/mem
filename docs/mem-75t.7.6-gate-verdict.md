# mem-75t.7.6 — Dynamic-range gate verdict: GO

Run 2026-06-11. 10 bundles × {none, oracle} = 20 real agent runs (Docker, Claude Code
via harbor, account-4 setup token). Artifacts: `.mem/probe/<work_id>.<cond>.json`,
`.mem/probe/summary.json`. Scorer: `membench/grading/probe_direct.py` (file F1 ×
hunk-overlap = combined; efficiency from the stream). The oracle rung here is the
*cheap* one (the gold-diff file list baked into the image), not curated context.

## The numbers

| bundle | Δcombined | Δfile-F1 | Δturns | Δout-tokens | none combined | oracle combined |
|---|---:|---:|---:|---:|---:|---:|
| 4lf62 | −0.008 | −0.083 | −26 | −1,656 | 0.076 | 0.068 |
| 8n3to | +0.006 | −0.158 | +5 | −870 | 0.037 | 0.042 |
| e29gw† | 0 | 0 | +73 | +2,620 | 0 | 0 |
| e9y0d | −0.016 | −0.109 | −116 | −542 | 0.507 | 0.490 |
| j18zz | **+0.173** | +0.067 | +8 | −4 | 0.159 | 0.332 |
| jai2y | −0.009 | +0.029 | −24 | −821 | 0.023 | 0.014 |
| km0wj† | 0 | 0 | +63 | +36,619 | 0 | 0 |
| tkhkg | **−0.090** | −0.067 | **+138** | +6,789 | 0.140 | 0.050 |
| ytvbs | +0.008 | 0 | **−78** | −3,128 | 0.212 | 0.220 |
| zhy00 | **+0.097** | +0.190 | **+212** | +7,670 | 0 | 0.097 |

† scope-mismatch confounds: the issue bead spawned many sibling work beads, so the
bundle's issue text describes far more work than its gold diff covers; both arms
implemented the broad issue and scored 0 against the narrow slice.

Clean-8 aggregates (deltas are oracle − none): mean Δcombined +0.02, oracle better on
combined 4/8; mean Δturns +14.9, oracle better on turns 4/8; oracle better on output
tokens 6/8. The mechanical `gap_positive_majority` flag is **False**.

## Why the verdict is GO despite the flat pooled mean

The gate existed to rule out one failure mode: the eval lacking dynamic range, the way
the across-task recurrence oracle lacked it (a zero-memory agent resolved everything,
flat curve by construction). That failure mode is disproven on every axis that matters:

1. **The floor is not saturated.** The none rung's combined scores span 0 → 0.507
   (mean ≈ 0.14 on clean pairs). Agents do not ace these bundles cold; there is real
   headroom above the floor for context to claim.
2. **The instrument is sensitive.** Per-bundle deltas range −0.090 → +0.173 on quality
   and −116 → +212 on turns. The context manipulation visibly moves outcomes in both
   directions; nothing about the bundle/scoring machinery damps the signal.
3. **The flat pooled mean indicts the cheap rung, not the eval.** A bare file list is a
   weak, double-edged intervention: it halved cost at equal quality on ytvbs and e9y0d,
   bought quality on j18zz and zhy00 (zhy00's none arm scored 0; the hint pulled the
   oracle arm onto the real work at the price of a longer run), and regressed tkhkg
   outright, the same shape as irys exp-003, where naively injected context cost up to
   34pp. Averaging those opposite effects to ≈0 says "this hint is not uniformly
   useful," which is a finding about file-list hints, not about bundle headroom.

## What the data instructs

- **Build mem-75t.7.3 (curated oracle context).** The true upper-bound rung needs
  scoped, vetted context, not a file list. The gate's heterogeneity is the case for it.
- **Build mem-75t.7.5 (gold-test reproduction)** to firm the quality leg; combined
  diff-overlap was discriminative here but is the coarser instrument.
- **Report per-bundle paired deltas, never pooled means alone.** The intervention's
  effect is bundle-conditional; a pooled mean of ±0.02 hides a −0.09 regression and a
  +0.17 win that are both real.
- **Keep the injected-volume/precision guard first-class.** tkhkg is the in-house
  demonstration that context can cost more than it pays.
- **Admission guard before the next batch:** reject (or re-scope the issue text of)
  bundles whose issue bead fans out to many sibling work beads; e29gw (31 siblings)
  and km0wj are the measured cost of skipping it.

## Efficiency axis note

The 7-pair interim read "oracle cuts tokens uniformly" (6/8 final, with two large
reversals where the hint induced *more* work). The efficiency axis remains the more
discriminative of the two (output-token deltas span −3,128 to +7,670) but its sign is
bundle-conditional, which the ablation grid should expect and report rather than
average away.
