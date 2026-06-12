# mem-qw5 — corpus expansion: more rigs + longer window; TRUE multi-session count

2026-06-12. Per Stephanie (Slack, 2026-06-12): widen the ingest corpus beyond
current scope — more rigs + a longer time window — as durable substrate, then
report the TRUE alias-guarded multi-session task count.

## Headline numbers (alias-guarded, 2026-06-12 ~22:10 UTC)

| population | multi-session | of | criterion |
|---|---|---|---|
| merged join (all session-linkable beads) | **2,133** | 3,575 | ≥2 non-suspect entries after UUID alias collapse |
| store work records (expanded store) | **1,967** | 7,309 | ≥2 non-suspect `record_agents` rows |

Both counts are the post-fix, alias-guarded population: entries are collapsed
on the Claude session UUID (`merge_join._collapse_aliases` — one Claude session
reachable via two FS namespaces counts ONCE), and `suspect` assignee links are
excluded. The pre-fix inflated within-task figures (the 26.4% recurrence class,
235 false multi-session beads — see mem-75t.10) were alias contamination; that
class is excluded here by construction.

**Honest residual:** an entry with NO resolvable transcript passes through the
alias collapse untouched (two distinct gc session ids that were actually one
Claude session cannot be unified without a transcript), so these counts are an
upper bound along that axis. Suspect filtering removed 10 entries (join level).

## Rig axis: already maximal — finding, not a change

Rig enumeration is dynamic (every dolt database with an `issues` table; no
hardcoded list). Verified against the running city server: 22 databases = 18
real rigs (all in the store) + `information_schema`/`mysql` (system) +
`__gc_probe` (probe) + `beads` (federation shell, `issues` table empty).
There are no rigs left to add; "more rigs" is satisfied vacuously and will stay
satisfied automatically as new rigs appear on the server.

## Window axis: two levers landed

The bead spine carries no time bound (records back to 2026-03-31). The binding
constraints were (a) the ~6-week rolling Claude transcript retention and
(b) — discovered during this work — dolt spine compaction.

### 1. Archive-restore: pruned transcripts re-enter the corpus

`membench.transcript_archive.restore_pruned()` decompresses every archived
transcript whose live source no longer exists into
`.mem/transcript-archive/restored/<digest>/<uuid>.jsonl`;
`build_merged_join.py` (new `extend_corpus_with_restored`, opt-out
`--skip-restore`) adds them to the content scan + uuid→path map (live wins on
collision; subagent sidecars excluded by ORIGINAL path). Restored paths flow
into `record_agents.trace_ref` / `trace.jsonl_path` through the existing
artifact contract — no TS changes.

Current contribution (attribution run: identical join with `--skip-restore`,
~20 min apart, ambient city drift caveat): 18 transcripts already pruned, 10
top-level restored into the corpus, **+43 transcript-resolved session entries,
+1 multi-session bead**. Small today because the archive began before the
retention cliff ate much; it grows daily and is now permanent — the nightly
cron runs the same driver with restore on.

### 2. Cron seeding: the spine no longer follows dolt compaction

The nightly `ingest-trace-substrate.sh` built a FRESH scratch store and swapped
it over the live one. The dolt spine compacts: this morning's fresh build had
6,972 records; a fresh build this evening had **6,445** (~530 beads compacted
away in one day) vs the live store's 7,064. Every nightly swap silently shed
the records — and trace axes — of every compacted bead.

Fix: the script now seeds the scratch from the live store before the ingest
(the writer upserts, so the build converges to live+current instead of
dolt-current-only). Verified by this run: seeded build = **7,309 records**
(+245 over live) vs 6,445 unseeded.

## Verification build

`.mem/store-expanded.db` (the live `store.db` is in active use by the mem-p3w
pilot and was NOT touched; the nightly cron converges it with both levers):

| axis | live before | expanded | delta |
|---|---|---|---|
| records | 7,064 | 7,309 | +245 |
| with_trace | 3,359 | 3,554 | +195 |
| trace_errors | 5,558 | 5,620 | +62 |
| trace_runs | 3,346 | 3,543 | +197 |
| with_base_commit | 191 | 228 | +37 |
| multi_session | 1,747 | **1,967** | +220 |
| with_task_type | 6,839 | 7,213 | +374 |

Lessons table carried by the seed copy (17 rows, verified). 18 rigs; created
span 2026-03-31 → 2026-06-12.

## Files

- `memory-bench/membench/transcript_archive.py` — `restore_pruned`, `RestoredTranscript`, `RESTORED_SUBDIR`
- `memory-bench/scripts/build_merged_join.py` — `extend_corpus_with_restored`, `--skip-restore`, restored-aware archival exclusion, params provenance
- `.gc/scripts/ingest-trace-substrate.sh` — live-store seeding
- `.gc/cron/ingest-trace-substrate.md` — doc updated
- tests: `tests/test_transcript_archive.py` (+4), `tests/test_build_merged_join.py` (+1)
