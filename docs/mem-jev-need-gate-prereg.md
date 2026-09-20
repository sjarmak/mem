# Pre-registration: Jev as a need-gate for memory beads (`mem-xh9vb`)

Status: **pre-registered 2026-09-19, before any call is made.** Grilled and confirmed
by Stephanie the same day. Thresholds, the cutoff rule, the kill order and the paid
exception below are fixed; a change to any of them after X1 starts is a new
pre-registration, not an edit.

Beads: epic `mem-xh9vb`; X1 `mem-xh9vb.1` (offline, no agent runs); X2 `mem-xh9vb.2`
(paid fire, blocked by X1 and HALT-gated on the explicit fire trigger).

## Question

Does a Jev confidence-gated need-classifier, deciding per task whether to tell the
agent to consult memory, beat no gate, always-gate, a coin flip at matched coverage, a
regex, and a Haiku judge? Jev is the TypeSafe typed decision model, gateway id
`typesafe-ai/jev`. This is the R8 recommendation from beads#5877, "selective
retrieval, no automatic body loading", made concrete. The E0 report records that the
R8-conformant counterfactual cannot be measured from historical transcripts, so it is
measured here by construction.

## What exists today

- No judge anywhere in the adoption path. The need decision is the agent under test;
  need is supplied by the twin corpus design (`memory-bench/membench/runner/toolreq_corpus.py`).
  The write classifier is a verb-plus-result rule (`tool_surface.py`). The only LLM
  judge in the repo is the local Nemotron endpoint in
  `memory-bench/membench/grading/judge.py`, which refuses paid hosts by marker.
- Rung guidance R0..R4 is prompt text prepended to the goal prompt before spawn
  (`e1_grid.py`, `rung_step`). The store is reachable only through the `bd` shim; no
  index is auto-loaded. R4 saturated: P(call | unnecessary) = 0.975 bounds
  d(R4) <= 0.025 (`results/e1-guidance-ladder/staged-160/README.md`).
- Labeled cases: 32 twins over 8 seeds, 16 necessary and 16 not. The unnecessary twin
  is the necessary prompt plus a block headed `Current state:` with the needed values
  inlined; store contents are identical across the twin. **This is a lexical tell**: a
  regex on the heading classifies the existing set perfectly.
- Prior Jev evidence: the omni-experiments retrieval canary (`artifacts/jev_canary`,
  2026-09-18) scored 85% raw on 53 cases, 97% on the 55% it accepted at confidence
  >= 0.9, 96% repeat stability, Brier 0.094 on `evidenceRelevant`. The mtg draft-pick
  probe (`mtg-h76gz`) was negative at n = 4 and hit the free-tier limit after 5
  back-to-back calls; pacing was untested.

## Paid-API exception

The no-paid-API rule covers the memory stack and a Jev gate is memory stack. Ruling
(Stephanie, 2026-09-19): Jev calls are in scope as paid, under a **hard cap of 5,000
calls** counted in a file the caller checks before every call. Market cost is about
$0.00004 per call, so the cap is about twenty cents. The gate is a standalone script
outside membench; the judge module's paid-host refusal is not modified. The call
counter is deterministic and does not depend on the gateway cost field, which
reported zero on the free tier in the mtg probe.

## Corpus extension (done before X1)

Two new seed-driven, offline twin classes in the tool-requiring generator
(`scripts/generate_worlds.py --offline --tool-requiring`), beside necessary and
unnecessary:

| Class                   | Prompt                                                       | Need  |
| ----------------------- | ------------------------------------------------------------ | ----- |
| necessary (existing)    | goal only                                                    | true  |
| unnecessary (existing)  | goal + `Current state:` block, all values inlined            | false |
| unnecessary-by-absence  | goal that needs no prior information, no state block         | false |
| partial                 | goal + state block with one value the store holds **omitted** | true  |

32 seeds, 128 cases, 32 per class. The partial class is the one carrying information:
the prompt looks answered but is not, and no regex settles it. Store contents stay
identical across every class within a seed, so the gate input carries no label.

## X1: offline decision quality

- **Gate input.** Exactly what the agent sees at leg start: the goal prompt plus the
  memory store titles (`bd memories` on the cell store). Never bodies, never the label.
  Secondary row: prompt alone.
- **Question.** One boolean: "completing this task needs information not in the prompt
  that a prior session could have recorded." Logged secondary: a choice over index
  entries naming which memory. Only the boolean feeds the verdict.
- **Harness.** A new case set in omni-experiments `jev-test`, which already routes
  Jev and baselines, prices with `--plan`, resumes, stops on first error, and scores
  with Brier plus five reliability bins (`src/omni_experiments/jev_score.py`). Cases
  are exported from the mem generator as jsonl; results are copied under
  `memory-bench/results/jev-need-gate-<date>/`. 3 repeats. 384 Jev calls planned.
- **Comparators.** (1) A regex on the state-block heading, reported as the mechanical
  ceiling on the easy classes. (2) Haiku through the headless Claude CLI on OAuth
  (`lib.mts` `claude-cli/<model>` route, 180 s timeout, serial). (3) Jev without
  titles.
- **Cutoff rule.** The lowest confidence giving at least 0.5 coverage on the primary
  row. X2 uses that value. It is chosen once, by this rule, on the X1 data.
- **Kill gates, in order. Any one ends the line before a leg is paid for.**
  1. Accepted-answer accuracy under 0.9 at the cutoff.
  2. Repeat stability under 0.95 across the 3 repeats.
  3. Jev not better than Haiku within the confidence interval on the primary row.
  4. Jev not better than the regex on the partial class.

## X2: outcome lift on the E1 grid (only if X1 passes)

- **Gate placement.** Pre-spawn, beside `rung_step`: the standalone gate script reads
  the goal prompt plus store titles, asks Jev at the X1 cutoff, and selects the
  existing R4 guidance prefix on yes or the empty R0 prefix on no. The arm differs
  from the rungs by selection policy alone. No new hook, no index injection: E0b showed
  delivery dominates, so changing delivery and selection together is uninterpretable.
- **Arms, one same-day fire.** R0 refired, R4 refired, Jev-gated, random-gated at the
  coverage X1 measured. Refiring the controls removes model and CLI drift since the
  2026-09-03 staged-160 fire.
- **Task set.** The 8 staged tasks plus the partial-class twins, 5 repeats,
  `bd_context` on to match staged-160. About 480 legs at $0.05 to $0.07 per leg,
  roughly $30. One Jev call per leg.
- **Primary metric.** Discrimination margin d = P(call | necessary) minus
  P(call | unnecessary), with the Fisher exact and permutation difference-in-differences
  tests the E1 grids already use. Secondary: memory calls on the partial class; cost
  and tokens per leg.
- **Kill.** Jev-gated d indistinguishable from the random gate at matched coverage.
- **Halt protocol.** Stage with `--plan`, record the priced `planned_call_count` on
  `mem-xh9vb.2`, then HALT for the explicit fire trigger. Never fire without it.

## What a pass and a fail each mean

A pass on X1 and X2 says a typed confidence gate, at the cost of one sub-cent call
per task, recovers a discrimination margin that guidance text alone could not, on
cases a regex cannot solve. A fail on gate 4 says the corpus still carries no case
where a model beats a heading match, and the next move is corpus work, not another
model. A fail on X2 alone says the offline quality did not survive the agent's own
reach for memory, which is the E0b finding again and closes this line.
