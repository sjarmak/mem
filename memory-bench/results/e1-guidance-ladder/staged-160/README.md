# E1 guidance ladder: first staged fire (R0, R4), 160 legs

**Status: PRELIMINARY.** Design {R0, R4} x {necessary, unnecessary}, T=8 tasks per
variant, R=5 repeats, 32 cells, 160 legs, all 160 measured (0 timed out, 0 errored).
Model `claude-sonnet-4-6`, Claude Code CLI 2.1.258 pinned across both sessions,
`corpus_fingerprint` `f78c508a7886fd56`, `surface_fingerprint` `c09cd475f30bb545`,
channel `recalled`. Fired 2026-09-02 in two sessions: 100 legs (20 cells), then a quota
halt at cell 21, then 60 legs (12 cells) resumed after the account reset. Bead:
`mem-eg850`. The Phase B defects in `mem-zfm0m` bound this result (see below); a
comparable result needs a fresh 160-call fire after they land.

## Headline

R4 buys saturation, not discrimination. The R4 guidance block (recall plus capture, 54
words) lifts the raw call rate on both halves of the corpus, necessary +0.600 and
unnecessary +0.750, and the primary endpoint, the discrimination margin
d(rung) = P(call | necessary) - P(call | unnecessary), collapses from +0.175 at R0 to
+0.025 at R4. That 0.025 is not a small effect waiting for more data: with
P(call | unnecessary) = 0.975, d(R4) is bounded above by 1.000 - 0.975 = 0.025, and the
observed value is the ceiling. No sample size makes the {R0, R4} design informative about
discrimination at R4, because R4 saturates the instrument. That is the empirical argument
for the interior rungs R1 to R3, which remain unauthorized.

## Pooled table

Pooled means sum of calling legs over sum of measured legs across the eight task cells
of each (rung, variant), never a mean of per-cell rates. Recomputed from
`summary.json` on 2026-09-02; every number below matches the record on `mem-eg850`.

| rung / variant   | measured | calling | P(call) | blocks | read verbs | write verbs |
| ---------------- | -------: | ------: | ------: | -----: | ---------: | ----------: |
| R0 / necessary   |       40 |      16 |   0.400 |     16 |         16 |           0 |
| R0 / unnecessary |       40 |       9 |   0.225 |     14 |         26 |           0 |
| R4 / necessary   |       40 |      40 |   1.000 |     40 |         40 |           0 |
| R4 / unnecessary |       40 |      39 |   0.975 |     43 |         51 |           1 |

| statistic                             |   value | test                                                         |
| ------------------------------------- | ------: | ------------------------------------------------------------ |
| d(R0)                                 | +0.1750 | Fisher exact two-sided p = 0.147                             |
| d(R4)                                 | +0.0250 | Fisher exact two-sided p = 1.000                             |
| difference-in-differences d(R4)-d(R0) | -0.1500 | permutation p = 0.242 (N = 200k, as reported on `mem-eg850`) |

Fisher p-values recomputed here with `scipy.stats.fisher_exact` on the 2x2 tables
[[16, 24], [9, 31]] (R0) and [[40, 0], [39, 1]] (R4): p = 0.1471 and p = 1.0. The
permutation p is cited from the bead record; it was not recomputed for this README.

Gate block (from `summary.json`): monotonicity over the necessary half is clean
(R0 0.400 -> R4 1.000, no violations); the R0 rate is reported as the tool-affordance
floor, not a zero, because R0 carries no guidance text but still allowlists the memory
tool surface; guidance words per rung (R0 0, R4 54) are reported, never subtracted.

## Underpowered

n = 40 legs per arm. Neither margin is distinguishable from zero (d(R0) p = 0.147,
d(R4) p = 1.000), and neither is the collapse between them (p = 0.242). d(R0) = +0.175 is
the larger point estimate, but the correct reading is that the design cannot yet separate
these, not that R0 discriminates. The structural ceiling argument above does not depend
on power; the point estimates do.

## The unnecessary arm is a working control

At R0 the same eight tasks draw 0.225 on the unnecessary half against 0.400 on the
necessary half, so the variant labels move the instrument in the expected direction with
no guidance at all. The R4 collapse is therefore attributable to the guidance, not to
mislabeled tasks. This holds even though `mem-zfm0m` item 1 says the unnecessary twin is
still partly memory-necessary; that defect attenuates the margin toward zero, so the
observed d(R0) is a lower bound on what a clean control would show.

## The write half is zero

`summary.json` reports `write_calls = 1` across 160 legs (cell R4 / unnecessary /
`world-seed2-task0`). The persisted stream for that leg
(`legs/R4__unnecessary__world-seed2-task0__1.json`) shows the call was
`bd remember list 2>&1 | head -100`, which bd refused: "looks like a command, not
something to remember. Hint: Did you mean 'bd list'?". Nothing was stored. True endogenous
writes are 0/160, including 0/80 at R4, the rung whose guidance explicitly instructs both
recall and capture. The agent follows the recall half 40/40 on the necessary tasks and the
capture half 0/40. The classifier defect (a `remember` verb token scored as a write on
the token alone, so a refused call counts as capture) is filed as `mem-8fv4t`; fixing it
moves one verb occurrence and does not touch any call rate or margin.

## Counter units: blocks versus verb occurrences

Do not pool these two columns. `memory_calls` counts call blocks: one structured tool call
whose command invokes at least one memory verb, or one native-memory file access
(`e1_grid.run_rung_cell`: `endogenous_memory_tool_calls(calls) + len(native)`;
`tool_surface.endogenous_memory_tool_calls` is documented "blocks, not verb occurrences").
`read_calls` and `write_calls` count verb occurrences: every memory invocation parsed out
of a command line (`memory_invocations`, split on `MEMORY_READ_VERBS = (recall, memories)`
and `MEMORY_WRITE_VERBS = (remember,)`) plus every native access (`native_read` /
`native_write`). One Bash line chaining two verbs is one block carrying two verbs.

Across the grid 113 blocks carried 134 verb occurrences. Verified on R4 / unnecessary: 39
native blocks plus 4 bd blocks = 43 blocks, carrying 39 `native_read` + 12 `memories` +
1 `remember` = 52 verbs. `calling_runs` (the rate numerator) is a third unit: legs with at
least one block.

## Surface: the agent reaches for the native memory file

Verb histogram over all 134 occurrences, recomputed from `summary.json`:

| verb          | count | where                                                                                      |
| ------------- | ----: | ------------------------------------------------------------------------------------------ |
| `native_read` |   103 | every cell that called at all                                                              |
| `memories`    |    30 | 18 in R0 / unnecessary / `world-seed2-task0`, 12 in R4 / unnecessary / `world-seed2-task0` |
| `remember`    |     1 | the refused `bd remember list` above                                                       |
| `recall`      |     0 |                                                                                            |

103 of the 133 read verbs are `native_read`, and every bd-shim read came from a single
task (`world-seed2-task0`, both rungs, unnecessary variant only). The bead record states
"133 of 134 read verbs are native_read; only 12 memories"; that line undercounts the `memories`
count by 18 and overcounts `native_read` by 30, and is superseded by the table above. The
finding it supports stands unchanged: the agent overwhelmingly reaches for the native
memory file, not the provisioned bd shim, which is why "both surfaces count" (mem-gj0pc)
is load-bearing. A counter that scored only the bd shim would see 10 blocks in this grid.

## Known defects that bound this result (mem-zfm0m, Phase B)

1. The unnecessary twin still withholds 2 of the 3 subjects the request names, so the
   unnecessary half is still partly memory-necessary (the largest one).
2. d counts capture writes as calls; R4's capture clause drives P(call | unnecessary) up by
   instruction. The margin should be computed on read calls with the write rate separate.
3. Bash-mediated native reads (`cat` / `echo >>` under the config dir's memory path) are
   counted by neither recognizer; the undercount is rung-correlated.
4. A timed-out leg's partial stdout carries its events and is discarded instead of scored
   and marked truncated.
5. R0 is not silent: `CLAUDE_CONFIG_DIR` is minted empty, so the CLI's own auto-memory
   system prompt is on; the floor is attributed to the tool name but the data says otherwise.
6. The native-recognizer constants and the config-dir pin are not folded into
   `surface_fingerprint`.
7. The sandbox is cwd-only with unrestricted Bash; a leg can forage the host and find the
   corpus.
8. Timeout SIGKILLs only the direct child; Bash grandchildren orphan and race tempdir cleanup.
9. `e1_necessity_preflight`'s paid path has never been run and, as built, is blind to
   defect 1.

Every one of these changes what a leg scores, so landing them invalidates resume against
this artifact; a comparable result requires a fresh 160-call fire under a fresh
authorization. All nine attenuate the margin toward the null, so the observed d is a lower
bound. The first 100 legs (the 20 R0 cells and the first 8 R4 / necessary cells) have no
persisted streams: leg persistence landed in Phase A (`mem-1qmoo`) and is forward-only,
so those legs cannot be re-scored by a counter fix, only re-bought. The 60 legs bought
after Phase A are re-scorable.

## Reproduction (no paid calls)

Artifact layout, all siblings of this README:

- `summary.json`: the grid summary; `cells[]` holds one row per (rung, variant, work_id)
  with `metrics` and the per-leg `verbs` list; `call_rate_gates` rides outside `metrics`;
  `identity_backfilled` records the CLI-version and corpus-fingerprint backfill on the first
  20 cells and the evidence for it.
- `preflight.json`: the one-leg R4 preflight that cleared the resume session (1 call,
  `native_read`).
- `legs/`: the 60 persisted leg streams, `<rung>__<variant>__<work_id>__<leg>.json`
  (20 R4 / necessary, 40 R4 / unnecessary), each with `status`, `memory_calls`,
  `read_calls`, `write_calls`, `verbs`, `cli_version`, and the credential-redacted `stream`.

Re-derive the pooled table, margins, verb histogram, and gate block from the artifact
with the harness's own functions (every command in this section runs from `memory-bench/`):

```sh
uv run python - results/e1-guidance-ladder/staged-160/summary.json <<'EOF'
import collections, json, sys
from membench.runner.e1_grid import RungCell, call_rate_gates, discrimination_margins, pooled_rates
cells = [RungCell.from_row(r) for r in json.load(open(sys.argv[1]))["cells"]]
pooled = collections.defaultdict(lambda: [0, 0, 0, 0, 0]); verbs = collections.Counter()
for c in cells:
    p = pooled[(c.rung, c.variant)]
    p[0] += c.measured_runs; p[1] += c.calling_runs; p[2] += c.memory_calls
    p[3] += c.read_calls; p[4] += c.write_calls; verbs.update(c.verbs)
for k, (n, calling, blocks, reads, writes) in sorted(pooled.items()):
    print(k, n, calling, f"{calling / n:.3f}", blocks, reads, writes)
print(pooled_rates(cells, "necessary"), pooled_rates(cells, "unnecessary"))
print(discrimination_margins(cells), dict(verbs))
print(json.dumps(call_rate_gates(cells)["monotonicity"]))
EOF
```

Fisher p-values (scipy is not a membench dependency, so pull it in for the one call):

```sh
uv run --with scipy python -c 'from scipy.stats import fisher_exact as f; print(f([[16, 24], [9, 31]])[1], f([[40, 0], [39, 1]])[1])'
```

Locate the refused write in the persisted streams:

```sh
grep -l remember results/e1-guidance-ladder/staged-160/legs/*.json
grep -o 'looks like a command[^"]*' results/e1-guidance-ladder/staged-160/legs/R4__unnecessary__world-seed2-task0__1.json | head -1
```

Nothing above spawns `claude`. The paid paths (`--preflight`, `--fire-staged`) refuse to run
without an explicit flag, a pinned `--model`, and an OAuth token, and `--staged` only prices
the plan.
