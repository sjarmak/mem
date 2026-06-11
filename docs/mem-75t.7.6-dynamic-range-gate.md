# mem-75t.7.6 — Dynamic-range GO/NO-GO gate

**Status: PROVISIONAL — verdict blocked at 5 clean pairs; ≥10 not yet met.**
The runner FIX demanded by the 2026-06-11 incident has landed and is validated;
the grid re-run needed to reach the ≥10-bundle bar is blocked on a fresh OAuth
token (see *Remaining work*).

## The decision this gate guards

`mem-apg.3.1` returned NO_GO because the recurrence oracle had **no dynamic
range** — a zero-memory agent already resolved everything, so the oracle rung
saturated against the floor. A new oracle does not guarantee headroom. Before
spending the P2 (consensus/curator) and P4 (dual-verifier) ports, this gate runs
a thin vertical slice — `none`-rung (issue statement only) vs a **cheap
upper-bound `oracle` rung** (the same issue + `gold_file_list` injected as "files
likely relevant") — over admitted bundles and measures the gap on **both** the
direct gold-diff score and the efficiency axis (tokens/turns).

- **GO**: a measurable gap (none floor < oracle ceiling) on `score_direct` **and**
  efficiency ⇒ headroom exists, proceed to `.3`/`.5`.
- **NO-GO**: none ≈ oracle ⇒ the bundle set lacks headroom; revisit a named lever
  (admission filter, SELECT rubric, rig scope) before porting anything.

## Runner FIX (incident 2026-06-11, prerequisite)

During the first grid run, the OAuth token expired mid-batch. The next runs
returned a 1-turn transcript with all-zero usage and an `is_error` /
`api_error_status: 401` result event. The runner **persisted those dead runs as
`combined: 0.0`** result files, and resumability would then have skipped them
forever — silently corrupting the gate (the dead 0.0s drag every gap statistic
toward zero).

Fix (`membench/harbor/probe_gate.py`, `scripts/run_gate_probe.py`):

- `detect_run_failure(stream)` flags a dead run by **either** an `is_error` /
  `api_error_status` terminal `result` event **or** zero billed output tokens. Turn
  count alone is *not* the test — a short but real run can be one turn, so a billed
  one-turn run is never falsely failed.
- `run_probe` raises `EmptyRunError` **before** the candidate harvest (no worktree
  is even created for a dead run).
- `run_probe_batch` treats `EmptyRunError` as **fatal**: it writes no result file
  (so a rerun re-executes the pair) and aborts the batch loudly — such failures
  cascade to every subsequent run in the session. Results already on disk survive.

Validated against the **6 real incident transcripts** still on disk
(tkhkg/ytvbs/zhy00 × none/oracle): all 6 caught with the exact 401 reason; a real
*good* transcript (e9y0d.none) is not flagged. Covered by unit + CLI tests
(`test_probe_gate.py`, `test_run_gate_probe.py`). The 6 corrupt `0.0` result files
were scrubbed; `summary.json` regenerated over the surviving valid pairs
(`summary.pre-fix-incident.json` keeps the pre-scrub snapshot for the record).

## Results so far (7 paired bundles: 5 informative + 2 confounded)

`oracle − none` deltas. Output-token Δ% is relative to the none-rung.

| bundle | none comb | oracle comb | Δcomb | none f1 | oracle f1 | Δf1 | none otok | oracle otok | Δtok% | Δturns |
|--------|----------:|------------:|------:|--------:|----------:|----:|----------:|------------:|------:|-------:|
| 4lf62  | 0.0758 | 0.0681 | −0.008 | 0.58 | 0.50 | −0.083 | 7251 | 5595 | **−22.8%** | −26 |
| 8n3to  | 0.0366 | 0.0424 | +0.006 | 0.53 | 0.37 | −0.158 | 5434 | 4564 | **−16.0%** | +5 |
| e9y0d  | 0.5066 | 0.4901 | −0.016 | 0.91 | 0.80 | −0.109 | 4911 | 4369 | **−11.0%** | −116 |
| j18zz  | 0.1588 | 0.3317 | **+0.173** | 0.93 | 1.00 | +0.067 | 2448 | 2444 | −0.2% | +8 |
| jai2y  | 0.0233 | 0.0142 | −0.009 | 0.40 | 0.43 | +0.029 | 2140 | 1319 | **−38.4%** | −24 |
| e29gw  | 0.0000 | 0.0000 | 0.000 | 0.00 | 0.00 | 0.000 | 4098 | 6718 | +63.9% | +73 |
| km0wj  | 0.0000 | 0.0000 | 0.000 | 0.00 | 0.00 | 0.000 | 5711 | 42330 | +641% | +63 |

**e29gw and km0wj are confounds, not signal**: both arms scored 0.0 — an
issue-scope mismatch (the agent produced zero gold-matching replayable mutations).
km0wj's oracle arm burned 42 330 output tokens for a 0.0 (215 turns, zero
replayable mutations) and single-handedly skews the aggregate `output_tokens`
mean to +5049. The honest read is **over the 5 informative pairs**, where the two
arms are otherwise comparable.

### Over the 5 informative pairs

- **Efficiency — clear, consistent gap.** Oracle is cheaper in output tokens in
  **5/5** pairs (−0.2%, −11%, −16%, −22.8%, −38.4%; median ≈ −16%). Turns lower in
  3/5 (incl. −116). The gold-file-list measurably changes agent behavior: it
  reaches comparable output with less exploration.
- **Quality — flat, no headroom.** `combined` improves in only 2/5 (and 1 of those,
  j18zz +0.17, is the sole material lift); `file_f1` improves in only 2/5 and the
  mean Δ is negative. The none-rung already targets the right files from the issue
  text alone on this dashboard rig — the oracle file-list adds little, and the agent
  sometimes does *less* with it.

## Provisional verdict

**Leaning NO-GO on the quality axis the ports actually chase; GO only on
efficiency.** The original NO_GO failure mode (oracle fully saturates the floor)
does *not* reproduce — there is dynamic range here. But it is **on the efficiency
axis only**: the cheap oracle rung consistently cuts tokens while leaving the
gold-diff score flat. P4's *direct gold-diff* leg measures exactly the quality
axis that shows **no headroom** on this bundle set, so porting the full
consensus/curator (P2) + dual-verifier (P4) stack to chase a quality lift would
saturate — the very risk this gate exists to catch.

**Lever to revisit (NO-GO must name one): the admission filter / SELECT rubric.**
This bundle set is dominated by gascity-dashboard tasks where file targeting is
easy from the issue text alone (the none-rung floor is already high on
`file_f1`), plus 2/10 are scope-mismatch confounds. Select harder tasks with
genuine quality headroom — and tighten admission to reject scope-mismatch bundles
(both arms 0.0) — *before* porting P4. If the eval's value proposition is reframed
to include efficiency (tokens/turns to reach equivalent quality), the efficiency
gap is a legitimate GO signal on its own.

## Remaining work (to finalize at ≥10 admitted bundles)

1. **Re-run the 3 token-blocked pairs** (tkhkg, ytvbs, zhy00 × none/oracle) with a
   live `CLAUDE_CODE_OAUTH_TOKEN` — the credentials token expired 2026-06-11
   ~18:00Z. `uv run python scripts/run_gate_probe.py` is resumable: it skips the 7
   pairs already on disk and runs only the 3 missing. The FIX now guarantees a
   second token expiry aborts loudly instead of writing more 0.0s.
2. Recompute `summary.json` (automatic at end of the batch).
3. Decide the disposition of the 2 confounds (recommend: exclude from the gap
   stats as admission failures, and feed back into the SELECT/admission lever).
4. Finalize GO/NO-GO over the ≥10 with the gap statistic and the named lever.
