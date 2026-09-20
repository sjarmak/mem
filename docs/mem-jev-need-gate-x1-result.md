# X1 result: the Jev need-gate is killed at gate 3 (`mem-xh9vb.1`)

Fired 2026-09-19 against the pre-registration in
`docs/mem-jev-need-gate-prereg.md`. Nothing in that document was changed after
the first call. **X1 fails. X2 (`mem-xh9vb.2`) stays halted and is now closed
out by the pre-registered kill order, not merely un-triggered.**

## What was run

128 labelled cases over 32 seeds, 32 per class, 3 repeats.

| Row                        | Model              | Calls | Route            |
| -------------------------- | ------------------ | ----- | ---------------- |
| Primary (prompt + titles)  | `typesafe-ai/jev`  | 384   | gateway, metered |
| Secondary (prompt alone)   | `typesafe-ai/jev`  | 384   | gateway, metered |
| Comparator                 | `claude-cli/haiku` | 384   | OAuth, unmetered |
| Comparator                 | heading regex      | 128   | deterministic    |

770 metered calls against the 5,000 hard cap, about three cents. The ledger is
`.mem/jev-call-budget.json`; it was charged before each call, never after.

## Results

| Row                | Brier  | Accuracy | Repeat stability | Cutoff | Coverage | Accepted accuracy |
| ------------------ | ------ | -------- | ---------------- | ------ | -------- | ----------------- |
| Jev, with titles   | 0.0276 | 0.9948   | 0.992            | 0.91   | 0.516    | 1.000             |
| Jev, prompt alone  | 0.0267 | 0.9896   | 0.992            | 0.88   | 0.549    | 1.000             |
| Haiku              | 0.0099 | 0.9870   | 0.961            | 1.00   | 0.586    | 1.000             |
| Heading regex      | 0.5    | 0.5000   | n/a              | n/a    | 1.0      | n/a               |

Per-class accuracy, primary row: necessary 1.000, partial 1.000,
unnecessary 0.979, unnecessary-by-absence 1.000. The regex scores 1.000 on the
two old classes and 0.000 on both new ones, which is the corpus extension
working exactly as designed.

## The gates, in their fixed order

1. **Accepted-answer accuracy at the cutoff — PASS.** 1.000 against a floor of
   0.9, on the 198 answers above confidence 0.91.
2. **Repeat stability — PASS.** 0.992 against a floor of 0.95.
3. **Better than Haiku on the primary row — FAIL.** Jev 0.9948, Haiku 0.9870.
   Jev is numerically ahead, but its 95% Wilson interval runs
   [0.981, 0.999] and does not clear Haiku's point estimate. The gate asked for
   a separation inside the interval and there is none.
4. **Better than the regex on the partial class — PASS.** Jev 1.000, regex
   0.000.

The first failure ends the line, so the verdict is a kill at gate 3.

## What this means

The gate 3 failure is not a statement that Jev is a bad classifier. Both models
sit against the ceiling: 382 of 384 versus 379 of 384. At that separation, 128
cases cannot distinguish them, and no amount of repeats will, because the
repeats measure stability rather than adding independent cases.

So the finding is about the corpus, not the model. The four classes broke the
lexical tell, which was the point of the extension, and the new partial class
is real work that the regex cannot touch. But the classes are still easy enough
that a small general model solves them nearly perfectly, which leaves nothing
for a typed confidence gate to buy.

This is the gate 4 failure mode the pre-registration anticipated, arriving one
gate earlier and pointing the same way: **the next move is corpus work, not
another model.** A corpus that separates these two models needs cases where the
need decision is genuinely ambiguous, not merely unstated. Candidates: values
the prompt states but that the store supersedes, partial coverage across
several values at once, and prompts whose stated values are stale rather than
absent.

X2 is not fired. Under the pre-registered kill order it cannot be, and a
decision to revisit the line is a new pre-registration.

## Artifacts

- Cases, labels, per-call rows and scores:
  `memory-bench/results/jev-need-gate-20260919/{primary,no-titles}/`
- Scorer: `memory-bench/jev_gate/score.py`, thresholds pinned by
  `memory-bench/tests/test_jev_gate_score.py`
- Call cap: `memory-bench/jev_gate/call_budget.py` and its TypeScript mirror
  `~/projects/omni-experiments/jev-need-gate/budget.mts`
