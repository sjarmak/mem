# mem-apg.9 — native convoy/epic-carved graded 3-arm grid (Decision C headline EXECUTION)

Execution of the Decision-C headline (mem-cg9h, Stephanie 2026-06-14): the
convoy/epic-carved **gas-city-native** pool run as a clean-room 3-arm grid
(`none-clean` / `ours` / **fresh** `builtin`) and scored with the graded instrument
(mem-r5y / mem-g6a) — per-signal paired per-bundle deltas, no pooled composite. This
is an execution of a locked design, not a new eval-design call. CSB/EB excluded
(mem-apg.8); scix is a separate, held robustness arm (mem-e3h2), NOT folded here.

**Driver:** `scripts/run_grid_3arm_graded.py` over the CE-anchorable pool
(`.mem/bundles-ce` / `grid-ready-pool-anchorable.json`), scored into `.mem/grid-ce/`.
Pins (one instrument across all three fresh arms): agent + judge `claude-sonnet-4-6`,
CLI `2.1.173`, `judge_rounds=3`, `builtin_arm=fresh` (NOT the cached 2026-06-11
relabel that confounded the mem-p3w pilot).

## Headline: HONEST NULL — the native pool is underpowered for a defensible read

The CSB oracle-validity gate admitted only **N=2 of 5** carved candidates, and only
**1 of those 2 fired a non-empty `ours` retrieval**. On the binary gold-test repro
anchor (the untouchable floor) **all three arms score 0 on both bundles** — no
condition solved either task to gold-test-passing. The graded sub-signals show at
most a small `ours`-over-`builtin` edge on the single retrieval-fired bundle, at
higher turn/token cost. **N=1 retrieval-fired cannot support a headline.**

Per this bead's scope item 4, that underpowered result is the explicit trigger to
fold the scix robustness arm via **mem-e3h2** — surfaced here, not actioned here.

## Admitted set & validity exclusions (reported, never silent)

CSB validity gate (gold diff must reproduce → 1.0; empty diff must fail → 0.0),
run on the live repro runner — 5 checked, **2 admitted**, 3 excluded:

| work_id | gold_repro | empty_ratio | admitted | reason |
|---|---|---|---|---|
| gascity-dashboard-2a7lh | ✓ (1.0) | 0.0 | **yes** | gold reproduces, empty fails |
| gascity-dashboard-4lf62 | ✓ (1.0) | 0.0 | **yes** | gold reproduces, empty fails |
| gascity-dashboard-6yy76 | ✓ (1.0) | 0.571 | no | a gold test passes WITHOUT the fix (empty leaks) |
| gascity-dashboard-yz7x1 | ✗ (0.25) | 0.0 | no | gold diff did not reproduce |
| gascity-dashboard-km0wj | ✓ (1.0) | 0.6 | no | a gold test passes WITHOUT the fix (empty leaks) |

Three of five carved candidates have **broken oracles** (gold non-reproducing, or
gold tests that pass on the empty diff). This — not agent quality — is what collapses
the native pool to N=2. Retrieval coverage: **1/2** (`4lf62` fired; `2a7lh` empty).

## Per-bundle signal vectors (graded; repro=0 everywhere)

`combined`/`file_f1` are `None` because the binary repro anchor failed for every
arm (the combined metric is anchored on repro). `diff_sim` (S2, bounded) and the
Sonnet-4.6 rubric `judge` (S3, median of 3 rounds) carry the sub-binary resolution.

**gascity-dashboard-2a7lh** — `ours` retrieval EMPTY, so `ours` ≡ `none-clean` (Δ=0):

| arm | repro | diff_sim | judge | turns |
|---|---|---|---|---|
| none-clean | 0.0 | 0.023 | 0.5 | 107 |
| builtin | 0.0 | 0.064 | 0.5 | 158 |
| ours (=none-clean) | 0.0 | 0.023 | 0.5 | 107 |

**gascity-dashboard-4lf62** — `ours` retrieval FIRED:

| arm | repro | diff_sim | judge | turns |
|---|---|---|---|---|
| none-clean | 0.0 | 0.055 | 0.6 | 137 |
| builtin | 0.0 | 0.057 | 0.5 | 149 |
| ours | 0.0 | 0.087 | 0.6 | 180 |

`ours` vs `builtin` on 4lf62: diff_sim **+0.030**, judge **+0.1**, but turns **+31**
and output_tokens **+1513** — a small quality edge bought with more work.

## Paired-delta gap stats (N=2, the headline shape — no pooled composite)

| comparison | diff_sim mean Δ | judge mean Δ | repro mean Δ | output_tok mean Δ | turns mean Δ |
|---|---|---|---|---|---|
| ours vs none-clean | +0.016 (1/2 >) | 0.0 (0/2) | 0.0 | +1044 | +21.5 |
| builtin vs none-clean | +0.022 (2/2 >) | **−0.05** (0/2) | 0.0 | +1436 | +31.5 |
| ours vs builtin | −0.005 (1/2 >) | +0.05 (1/2 >) | 0.0 | −392 | −10 |

Reading (with the N=2 caveat front and center):
- **No arm moves the binary anchor** — repro Δ = 0 across every comparison.
- **`builtin` (native project memory ON) raises diff_sim on both bundles** (2/2) but
  the rubric judge rates it *lower* on average (−0.05) — native memory nudges the
  surface diff toward the gold files without improving judged quality.
- **`ours` vs `builtin` is flat/mixed**: a small judge edge (+0.05, 1/2) and a small
  diff_sim deficit (−0.005), with `ours` cheaper on tokens in aggregate (−392) —
  driven entirely by the 2a7lh empty-retrieval leg, not a real win.

## Verdict & next step

The native CE-anchorable pool, after honest validity gating, is **N=2 admitted /
1 retrieval-fired with a zero binary-repro floor** — underpowered for a defensible
headline. No arm earns the binary anchor; the graded signals show only small,
cost-bearing, direction-mixed movements that N=1 retrieval cannot support.

Per scope item 4, this is the trigger to **fold the scix robustness arm (mem-e3h2)**
to reach a powered read — surfaced here as the recommendation, held separately as
that bead's scope. The dominant lever to recover native power is **oracle repair /
re-carving** (3/5 candidates failed validity), not more arms.

**Fences honored:** gold-test repro stayed the binary floor; ZFC (verdict prose here
is the orchestrator's, the summary JSON is pure arithmetic over model outputs);
D6 LOO unchanged; no outcome label in agent input; judge (Sonnet) separate from any
calibrator; corpus strictly gas-city-native (no CSB/EB/scix). Branch-ready — not
pushed. Raw: `.mem/grid-ce/summary-3arm-graded.json`.
