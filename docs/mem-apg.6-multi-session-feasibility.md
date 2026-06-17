# mem-apg.6 — multi-session graded 3-arm grid: pool-construction result

Run 2026-06-13. Execution of the locked design (mem-r5y graded instrument +
mem-3sz clean-room/built-in-third-arm/multi-session direction, Stephanie
greenlit 2026-06-12; mem-p3w pilot harness; pool source mem-qw5). This report
covers **stage 1 of the bead's scope** (mining the multi-session bundle pool
via the mem-75t.7 pipeline) and the result that gates stages 2–4.

## Headline

**The alias-guarded multi-session candidate set does not yield a grid-ready
pool that can carry the gold-test-anchored graded signal vector, on the current
rig roster.** Admitted N after the full locked pipeline = **2 bundles,
both `codeprobe`**, and **neither can anchor the graded instrument**:

- `codeprobe-g1cp2`: gold diff touches one **docs-only** file
  (`docs/investigations/codeprobe-4cl6/cap90.md`); there is no gold *test* to
  reproduce, so no quality floor exists.
- `codeprobe-3l6tb`: gold diff includes a real test
  (`tests/adapters/test_claude_quota_detection.py`, Python), but the `codeprobe`
  rig has **no test-toolchain base image** and falls back to `ubuntu:24.04`
  (no python/pytest/deps), so the live gold-test repro and the CSB validity
  gate cannot run.

The single multi-session candidate that *was* gold-test-bearing **and**
toolchain-having (`gascity-dashboard-86kwb`, `node:22-bookworm`) was correctly
rejected by the locked issue-fanout scope guard (its issue bead fans out to 32
siblings; the issue leg over-describes a one-file slice). Relaxing that guard
to keep it would violate the design's fences (D6/admission integrity), and even
then yields N=1, not a headline.

**Conclusion:** a fair graded multi-session 3-arm grid is **not constructible**
on the current `DEFAULT_RIG_REPOS` + base-image roster. This is a feasibility
result about the corpus×harness, *not* an observed null effect of memory. No
agent runs were spent on the unconstructible pool (the bead forbids padding).

## The pipeline, stage by stage (alias-guarded multi-session restriction)

The live store carries the converged mem-qw5 expansion (verified before any
work: `swapped store records=7381 … multi_session=1974`; the gate fired).

| stage | tool | result |
|---|---|---|
| multi-session population | `record_agents`, `suspect=0`, ≥2 distinct agents | **1,974** / 7,381 records |
| bundle-eligible | `trace_path∧base_commit∧closed` | 229 |
| **candidate set** | eligible **∩** multi-session | **46** |
| SELECT rank | `select_rank.py --multi-session` | 46 ranked |
| non-zero mutation (replayable) | `top_candidates` (mut>0) | 23 processed |
| assemble | `assemble_batch.py` | **3 admitted** |
| oracle curation | `curate_bundle_oracles.py` (grep-only → gold_diff tier) | 3 curated |
| fanout scope guard | `admit_batch_guarded.py` | **2 admitted** |
| gold-test-anchorable under current infra | — | **0** |

### Where the 46 went (the binding constraints, in order)

1. **Rig-clone availability (14 rejected `NO_RIG_CLONE`).** The richest
   multi-session candidates sit in rigs absent from `DEFAULT_RIG_REPOS`:
   CodeScaleBench (8 with mut>0), scix_experiments (3), gpk (2),
   EnterpriseBench (1). They cannot be checked out / containerized, so they
   never reach assembly.
2. **SHARED_TRACE fanout (4 rejected).** The 4 `mem`-rig multi-session
   candidates (`mem-3d6zi/0tu17/fhtjw/na1pc`) share a single trace; the
   fanout guard excludes co-derived bundles (correct).
3. **base_predates_tree (2 rejected).** `gc-lv6nlk` (gascity),
   `gascity-dashboard-tx20s`: first mutation edits a file absent at the
   timestamp-approximate base commit.
4. **Issue-fanout scope mismatch (1 rejected).** `gascity-dashboard-86kwb`
   (fanout 32), the only gold-test-bearing + toolchain-having survivor, its
   issue spans far more than the one-file gold diff.
5. **Toolchain (the final 2).** Both survivors are `codeprobe`
   (ubuntu:24.04 fallback); one is docs-only. → 0 gold-test-anchorable.

Per-rig multi-session candidates with a replayable mutation signal: mem 4,
CodeScaleBench 8, scix_experiments 3, codeprobe 2, gascity_dashboard 2, gpk 2,
gascity 1, EnterpriseBench 1.

## The lever (what would unlock a real multi-session pool)

The clones for the richest unwired rigs **already exist on disk**, just not
registered in the harness:

- `/home/ds/projects/CodeScaleBench` (8 mut>0 candidates) ✓ clone present
- `/home/ds/projects/scix_experiments` (3) ✓ clone present
- `/home/ds/projects/EnterpriseBench` (1) ✓ clone present
- `/home/ds/projects/GEO`, `/home/ds/projects/codeprobe` already in roster
- `gpk` (2): no clone found on disk

Unlocking them requires **(a)** registering the clones in `DEFAULT_RIG_REPOS`
and **(b)** providing a test-toolchain base image per rig (the gold-test floor
needs the rig's `pytest`/build deps; ubuntu:24.04 cannot reproduce a Python or
build failure). That is a **harness-expansion effort, beyond "execute the
locked design"**: the locked pilot harness was scoped to the existing roster,
and several of these rigs are themselves benchmark repos (CodeScaleBench,
EnterpriseBench) whose work records warrant a methodology check before they
become OUR eval's rigs.

## Artifacts

- `.mem/select-ranking-ms.json`: 46 multi-session candidates ranked
- `.mem/bundles-ms/*.json`: 3 assembled + oracle-curated bundles
- `.mem/grid-ready-pool-ms.json`: 2-bundle admitted manifest + provenance
- `.gc/docs/mem-apg.6-{select-ranking,assemble,oracle-*,fanout}-ms.md`: per-stage reports

## Reproduction

```bash
cd memory-bench
uv run python scripts/select_rank.py --multi-session \
  --json-out ../.mem/select-ranking-ms.json \
  --report-out ../.gc/docs/mem-apg.6-select-ranking-ms.md
uv run python scripts/assemble_batch.py --ranking ../.mem/select-ranking-ms.json \
  --bundles-dir ../.mem/bundles-ms --report-out ../.gc/docs/mem-apg.6-assemble-ms.md --limit 50
# curate per-rig (one --clone each), then:
PYTHONPATH=. uv run python scripts/admit_batch_guarded.py \
  --bundles-dir ../.mem/bundles-ms --manifest ../.mem/grid-ready-pool-ms.json \
  --report-out ../.gc/docs/mem-apg.6-fanout-ms.md --write
```
