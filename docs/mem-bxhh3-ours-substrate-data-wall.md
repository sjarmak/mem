# mem-bxhh.3 (D-G, Option A) — codeprobe ours-substrate is a data-availability NULL

**Verdict: the LOCAL/FREE build pieces are complete and sound, but the `ours`
retrieval arm is provably inert (0/6) on the only available codeprobe corpus.
A paid graded run would compare `none` vs `builtin` vs an empty `ours`. HALTED
before any spend per directive.**

Date: 2026-06-23. Branch `mem-bxhh.3` @ worktree `/home/ds/projects/mem-bxhh.3`.
Free/local only — no agent, no paid tokens.

## What Option A asked for

Build the REAL `ours` substrate (not the wiring-only paid smoke that the
2026-06-22 note correctly stopped): "targets as failure-signature work-records
over a matching codeprobe corpus, LOO-honored." Plus the three decompose pieces
(.3a base image, .3b bundles, .3c verification wiring) and the firewall design
choice (D-E = B, post-close value re-scan). HALT before any paid graded run.

## The three local pieces (done, verified free)

- **.3a base image** — `codeprobe-base:py3.11` built (deps baked); graded runs
  skip reinstall. ✓
- **.3b bundles** — 6 codeprobe landing-commit bundles materialized into
  `.mem/bundles-codeprobe/` with curated fail-to-pass oracles + `env` (repo,
  base_commit, base_image). ✓
- **.3c verification** — free validity gate (`run_grid_3arm_ftp --validity-only`)
  is 6/6 sound (gold_repro=True, empty_repro=False) via Docker pytest. The
  scoring leg (`FtpReproRunner`) discriminates. ✓

These exercise scoring and validity. **None of them exercises `ours`
retrieval** — that is the gap Option A targets.

## How `ours` retrieval actually works (so the NULL is grounded, not assumed)

`run_grid_3arm_ftp.py` injects, per bundle, the payload its `ours` arm would
inject: `mem retrieve <anchor> --scope same-rig`, keep only items that carry a
**lesson** (`resolve_payloads` in `run_grid_3arm.py` — the arm's information
content is the lesson payload, D9; a bare citation carries none).

`retrieve()` (`src/retrieve/retrieval.ts`) is **entirely failure-signature
driven**:
1. `queryFromRecord` builds the query from the target record's `trace.errors`.
   `trigger_count == 0` (no errors) ⇒ no trigger ⇒ 0 items (D8).
2. Candidate corpus records match on tiers signature → tool+error_class → FTS
   message. Crucially, the match loop iterates **`record.trace.errors` from the
   candidate's record JSON** — the `trace_errors` table + FTS only *locate*
   candidates and supply FTS positions. A corpus record with no
   `record.trace.errors` can never match.
3. Each matched item's lessons come from the `lessons` table (`lessonsFor`).

So a non-empty `ours` payload requires, simultaneously: (a) the anchor carries
real `trace.errors`; (b) a corpus record carries matching `trace.errors` in its
record JSON; (c) that record carries a lesson; (d) it closed strictly before the
anchor and is not LOO-excluded.

## The substrate that exists

- The v8 store built for this bead (`.mem/store.db`, 6961 records incl. 258
  codeprobe) is **spine-only**: only 10/6962 records carry `record.trace.errors`,
  **0 lessons, 0 trace_errors projected**. It can serve no `ours` arm at all
  (same-rig or cross-rig).
- Across **every** store on disk, only **3 codeprobe records carry both
  trace_errors AND a lesson**: `codeprobe-0zjex`, `-u6slc` (pytest
  *collection-time* failures — FileNotFound/import in test_release_gate,
  test_mine_profiles, test_bare_invocation_matrix) and `-v0q4x` (**ruff lint** —
  I001/E501/UP030/F401 in `src/codeprobe/cli/mine/`). All three closed
  2026-06-15.
- The 6 anchors are real codeprobe landing commits whose failures are
  **behavioral test assertions** in the scoring / executor / analysis domains
  (test_stats quota exclusion, test_executor scoring projection, test_run_explorer,
  test_no_bare_usage_errors, test_experiment_cmd token rollups, test_comparison_viewer).
  Real commit dates: 2026-04-30 → 2026-06-16.

The lesson-bearing corpus (3 records, ruff-lint + collection-failure domains) is
disjoint from the anchors' failure domains. There is no signature, error-class,
or message-token bridge between "Import block un-sorted" / "FileNotFoundError at
collection" and "AssertionError in paired-score quota exclusion".

## Empirical measurement (maximally generous, wiring-validated)

`memory-bench/scripts/bxhh3_ours_substrate_probe.py` builds the best-possible
substrate and runs the real `ours` retrieval, all free:

- corpus = v8 codeprobe spine + **every** available codeprobe `trace_errors`
  (union of `store-lobt.db` v7 + `store.db` v5) + **every** available codeprobe
  lesson, mirrored into the records' JSON so they are matchable;
- anchors patched to their **real landing-commit dates** (widest honest LOO
  window) and carrying a faithful failure-signature query rendered from the
  curated ftp oracle's failing tests.

Result:

```
OURS RETRIEVAL COVERAGE: 0/6 anchors with a non-empty lesson-bearing payload
```

Per anchor, `trigger_count` fires (1–70) and `total_matched` is non-zero only
because the six synthetic anchors self-match each other on the injected pytest
tokens — and **every one of those matches has 0 lessons**. The 3 real
lesson-bearing corpus records never surface.

**Positive control** (proves the 0 is substrate barren-ness, not a wiring bug):
a probe query mirroring `codeprobe-v0q4x`'s ruff `I001` signature retrieves
`codeprobe-v0q4x` with its lesson — `trigger=1 matched=1 items=1 lessons=1
match=signature`. Retrieval works the instant a matching lesson-bearing record
exists; for these anchors, none does.

## Why this is an ingest wall, not a fixable wiring gap

The codeprobe corpus was never trace-resolved / lesson-distilled at scale. Per
the trace-substrate ingest design, the transcript corpus is a ~6-week rolling
window (Claude Code prunes old session jsonl); codeprobe sessions were largely
not captured, so the failure-signature substrate that `ours` needs does not
exist and cannot be regenerated for these commits. This is the same
data-availability wall recorded for the flat dashboard pool (Gate-0 = DATA
availability, the real issue is the INGEST gap), now confirmed for the codeprobe
ftp anchors specifically.

This empirically resolves the build-substrate-vs-wiring fork (escalated as
gc-404592): the blocker is **substrate ingest**, not driver wiring. The wiring
is sound (positive control passes).

## Firewall design (D-E = B): post-close value re-scan

For the eventual case where a valid `ours` substrate exists, the firewall is
**post-close value re-scan** (option B), not structural+LOO alone: after the LOO
exclusion set is applied, re-scan each retrieved payload for the target's
outcome values (`commit_sha` / `pr` / `base_commit`) and reject the item if any
appears, allow-list (RAISE on unrecognized fields), not deny-list. This composes
with — does not replace — the strict `closedBefore` cut and the sibling
exclusions. On this run the firewall is moot: an inert `ours` arm produces no
payload to scan, so no firewall assertion is exercised.

## Disposition

- HALTED before AC#3 (the paid 1-smoke graded run). Running it now would spend
  tokens to compare `none` vs `builtin` vs an `ours` arm known to be empty.
- Branch-ready. The decompose pieces stand; the headline `ours` arm is blocked
  on substrate ingest, not on this bead's code.
- Recommended next lever (for the mayor / Stephanie): the codeprobe ftp corpus's
  productive use is **use #2 from the feasibility doc** — calibration data for
  synthetic-task design — not a real `ours` eval anchor, because the matching
  prior-work substrate for these commits does not exist.

## Reproduce

```
cd /home/ds/projects/mem-bxhh.3
python3 memory-bench/scripts/bxhh3_ours_substrate_probe.py
```
