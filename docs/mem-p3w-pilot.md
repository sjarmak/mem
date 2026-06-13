# mem-p3w — 3-arm clean-room pilot: none-clean / ours / builtin

Run 2026-06-12. The mem-apg OURS rung executed under the clean-room control
Stephanie confirmed (2026-06-12, Slack): Claude's native project memory is
**disabled in both the `none-clean` and `ours` arms** (so our memory system is
the only variable between them), and **native memory becomes a third labeled
arm (`builtin`)** — the baseline-to-beat (mem-whi). Scoring: the existing
binary gold-test reproduction metric as the quality guard, efficiency
(tokens / turns / tool calls) as per-bundle paired deltas — never pooled means
alone (the mem-75t.7.6 instruction). Feeds mem-apg.4.

Pool: the 9 fanout-guard-admitted bundles (`.mem/grid-ready-pool.json`).
Artifacts: `.mem/grid/<work_id>.{none-clean,ours}.json`,
`.mem/grid/summary-3arm.json` (per-bundle metrics, all three pairings,
provenance). Driver: `memory-bench/scripts/run_grid_3arm.py` (resumable).

## What each arm concretely is

In this harness the agent runs `claude --print` in a fresh container (fresh
`CLAUDE_CONFIG_DIR`, empty user-level memory), so "native memory" reduces to
the repo-tracked project surface `git archive` bakes into the image:
`CLAUDE.md`, `AGENTS.md`, `.claude/`, `.agents/`. Every admitted bundle's
base commit tracks that surface (per-bundle `ls-tree` evidence in
`summary-3arm.json:builtin_surface_evidence`).

- **`builtin`** — the gate probe's cached `none` runs (2026-06-11): native
  project memory present at `/app` by construction, our system off. Relabeled
  from the existing `.mem/grid/<id>.none.json` scores at zero new agent cost.
  (Labeling note for mem-apg.3: its "none" arm was de facto
  project-memory-ON; its none-vs-oracle deltas are unaffected — both sides
  carried the same surface — but the floor it measured is `builtin`, not a
  clean room.)
- **`none-clean`** — 9 NEW runs; byte-identical task except a Dockerfile layer
  strips the native-memory surface from `/app`.
- **`ours`** — clean room + retrieval-v1's citation+lessons payload (D9,
  17 distilled lessons in store, D6 LOO enforced and re-asserted) injected at
  `/memory/MEMORY.md`. Retrieval returned payloads for **2/9** bundles
  (4lf62, km0wj — 10 lesson-bearing items each); those got real runs. For the
  other 7 the constructed task is byte-identical to `none-clean`, so the arm
  **shares that run** — their ours deltas are **zero by construction**
  (retrieval injected nothing). That is a retrieval-coverage finding about
  the system under test, NOT an observed null effect of memory.

Instrument pinned across arms: `claude-sonnet-4-6` on claude-code `2.1.173`
(the cached runs' uniform stream-init values; new runs pinned via harbor agent
kwargs and verified post-run per stream — `assert_run_pins`). Residual
confound: the builtin runs executed one day earlier than the clean arms.

## Quality guard: gold-test reproduction is FLAT across all three arms

**1/9 pass in every arm, the same bundle (e9y0d), Δ = 0 on every pairing.**
Native project memory neither buys nor costs gold-test quality on this pool;
neither does our injected payload. This reproduces mem-apg.3's flat-quality
finding under the clean-room control — the efficiency deltas below are not
purchased with quality anywhere, and no quality gain hides behind them.

## Per-bundle paired deltas (output tokens, the headline axis)

Negative = first arm cheaper. `ours−none-clean` is 0 by construction for the
7 shared bundles (marked †).

| bundle | none-clean | ours | builtin | ours−none-clean | builtin−none-clean | ours−builtin |
|---|---:|---:|---:|---:|---:|---:|
| 4lf62 | 3,435 | 3,679 | 7,251 | +244 | +3,816 | **−3,572** |
| 8n3to† | 4,909 | 4,909 | 5,434 | 0 | +525 | −525 |
| e9y0d† | 2,787 | 2,787 | 4,911 | 0 | +2,124 | −2,124 |
| j18zz† | 2,832 | 2,832 | 2,448 | 0 | −384 | +384 |
| jai2y† | 2,456 | 2,456 | 2,140 | 0 | −316 | +316 |
| km0wj | 6,680 | 3,598 | 5,711 | **−3,082** | −969 | −2,113 |
| tkhkg† | 3,020 | 3,020 | 2,710 | 0 | −310 | +310 |
| ytvbs† | 4,279 | 4,279 | 5,307 | 0 | +1,028 | −1,028 |
| zhy00† | 2,044 | 2,044 | 3,262 | 0 | +1,218 | −1,218 |

Aggregates (medians; per-bundle table above is the headline shape):

- **ours vs builtin** (the baseline-to-beat): ours cheaper on **6/9**
  bundles; median Δout-tokens **−1,028**, median Δturns **−23**, at flat
  quality. The claim "beats Claude's native memory on efficiency" holds on
  this pool — but see the mechanism caveat below.
- **builtin vs none-clean** (what native memory does): builtin MORE expensive
  on 5/9 (median Δout-tokens **+525**; 4lf62 paid +3,816 and e9y0d +2,124
  for zero quality change). On this pool the repo-shipped CLAUDE.md/AGENTS.md
  mostly cost tokens without buying gold-test quality.
- **ours vs none-clean** (our system's own lift, clean room): zero on 7/9 by
  construction. On the 2 payload-bearing bundles the effect is **mixed in
  sign**: km0wj **−3,082** out-tokens / −89 turns (lessons made the run
  substantially cheaper), 4lf62 **+244** / +11 turns (payload slightly
  increased work). n=2 — anecdote, not a claim.

## Honest read (the caveats ARE the findings)

1. **Quality is flat everywhere.** Reproduced under clean-room control: this
   pool has no quality headroom to demonstrate memory value on the gold-test
   axis; what moves is where the effort goes (tokens/turns).
2. **The ours-beats-builtin efficiency win is mostly the clean room.** 7/9
   ours legs are byte-identical to none-clean, so `ours−builtin` largely
   measures "not loading native memory" rather than "loading our lessons".
   The defensible statement: *our system's policy (strip native memory,
   inject retrieved lessons only when relevant) is cheaper than Claude's
   native project memory at equal quality on this pool* — with the lesson
   payload itself contributing on exactly the 2 bundles retrieval covers.
3. **Retrieval coverage (2/9) is the binding constraint** on saying anything
   stronger about the lessons themselves. The lever is distillation breadth
   (`mem distill-lessons --rig gascity_dashboard` is idempotent) and
   retrieval recall, not more agent runs on the current store.
4. **Input-token deltas are unreliable** on this instrument (cache-accounting
   noise; e.g. e9y0d none-clean billed 40k input vs builtin's 864 for a
   cheaper run overall). Output tokens / turns / tool calls are the
   trustworthy efficiency axes.
5. **Cross-day confound:** builtin = cached 2026-06-11 runs; clean arms ran
   2026-06-12 on the same pinned model + CLI version. Single run per
   (bundle, arm) — per-bundle deltas carry sampling noise; signs and
   magnitudes above are pilot-grade, not powered estimates.

## Reproduction

```bash
cd memory-bench
export CLAUDE_CODE_OAUTH_TOKEN=...   # setup token; harbor reads process env
uv run python scripts/run_grid_3arm.py --dry-run   # construct + leak-validate only
uv run python scripts/run_grid_3arm.py             # resumable; ~11 agent runs
```

The driver aborts loudly (nothing persisted) on dead runs (`EmptyRunError`),
instrument drift (`PinMismatchError`), missing cached builtin legs, an empty
native-memory surface (would falsify the relabel), or a D6 LOO violation in
the retrieval payload; died job dirs are scrubbed on the next invocation.
