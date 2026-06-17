# N=9 graded headline: a weak positive for memory, a wash against native

The clean-room 3-arm graded grid ran to completion on 2026-06-17 over the native
sound-oracle pool. The result is a defensible null-to-weak-positive: retrieved
memory nudges the agent's work closer to the gold answer and costs fewer tokens,
but it does not beat the native built-in memory it was meant to beat, and on the
hard gold-test oracle no arm meaningfully solves these tasks. The artifact is
`.mem/grid-n8/summary-3arm-graded.json`.

## Setup

Eight gascity-dashboard bundles carried the 3-arm comparison. The ninth sound
oracle, `mem-us6j`, was held out of the arms: its repo has no native
project-memory surface at the base commit, so it cannot anchor the `builtin`
condition. All eight passed the pre-admission validity gate (gold reproduces,
empty fails). The three conditions:

- `none-clean`: native project memory (`CLAUDE.md`, `AGENTS.md`, `.claude`,
  `.agents`) stripped from the image, nothing injected.
- `ours`: retrieval-v1 citation plus distilled lessons injected, D6 LOO-bounded;
  bundles whose retrieval is empty reuse the `none-clean` run, since the task
  would be byte-identical and a fresh run would only measure sampling noise.
- `builtin`: native project memory present, our system off. The baseline to beat,
  run fresh under the same pins, not the cross-day cached relabel that confounded
  the earlier pilot.

Agent and judge were both Claude Sonnet 4.6, judge over three rounds. Retrieval
ran against the v6 store seeded with 236 distilled dashboard lessons, which gave
4 of the 8 bundles a non-empty `ours` payload.

## Result

The hard oracle, gold-test reproduction, is flat across arms:

| condition | gold tests reproduced |
|---|---|
| none-clean | 0 / 8 |
| ours | 0 / 8 |
| builtin | 1 / 8 |

The matched set is the four bundles where `ours` actually retrieved memory
(`4lf62`, `dh0gt`, `g0pff`, `i1bo2`); the other four had empty retrieval and
reuse their `none-clean` run by construction. Mean paired deltas on the matched
set:

| comparison | diff-sim | judge | output tokens | repro |
|---|---|---|---|---|
| ours vs none-clean | +0.046 (4/4 better) | +0.038 (1/4) | −307 | tie |
| ours vs builtin | +0.003 (2/4) | −0.025 (1/4) | −691 | tie |

Across the full eight, the `ours` advantage over `none-clean` dilutes to +0.023
diff-sim (4/8) as the four empty-retrieval pairs contribute zero, and `builtin`
is the strongest single arm: it took the one gold-test win and leads `none-clean`
on diff-sim (+0.028, 5/8), though it spends more tokens (+309).

## What it shows

Memory beats no memory, weakly and only on the structural signal. On all four
matched bundles `ours` moved the candidate diff closer to the gold diff, and it
did so while spending fewer tokens than both other arms. The rubric judge barely
moved (+0.038 mean, one of four bundles), so the effect lives in diff-similarity,
not in the quality judgment.

Our memory does not beat native built-in memory. Against `builtin` the quality
signals are a wash (near-zero diff-sim delta, a slightly lower judge score), and
the only consistent difference is that `ours` is cheaper. The native
project-memory baseline holds.

The hard oracle carries no signal here. Across 24 runs the gold tests reproduced
once, so the binary anchor cannot separate the arms: these dashboard features are
too large for a single agent run to land regardless of memory.

## What it does not show, and the levers

This is a read, not a win. The effective sample is four paired bundles, all from
one repo, with no confidence interval clearing zero. Two corpus-side constraints
cap the signal, and both are the forward work:

- **Lesson coverage.** Only four of eight bundles had any lesson to retrieve,
  because the distiller has run over the dashboard rig alone (236 of roughly 609
  error-bearing records corpus-wide). Distilling the remaining rigs is the
  largest lever on this headline.
- **Pool power and diversity.** Eight bundles from one repo, on tasks the binary
  oracle cannot discriminate, leave the comparison underpowered. A larger,
  multi-repo, less-saturated sound-oracle pool is what turns a directional read
  into a result, and reaching it is trace-substrate work (true per-worktree base
  commits so more bundles replay cleanly), not a harness patch.

The mechanism now runs end to end: validity gate, three fresh arms, LOO-bounded
retrieval, and graded scoring all execute on real Harbor runs. The next number
comes from feeding it a stronger corpus, not from changing the method.
