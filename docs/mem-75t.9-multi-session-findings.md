# mem-75t.9 — Multi-session task history: join + cross-session metrics (PHASES 1 & 3)

Spike findings, 2026-06-11. PHASE 2 (store schema bump to multi-row
`record_agents`) is deferred to mem-75t.4; everything below is a SIDECAR over a
strictly read-only store + transcript corpus.

Artifacts (not committed; reproducible):

- `/home/ds/projects/mem/.mem/session-bead-join.json` — the join sidecar
  (`memory-bench/scripts/build_session_join.py`)
- `/home/ds/projects/mem/.mem/cross-session-metrics.json` — the metrics
  (`memory-bench/scripts/compute_cross_session.py`)

## Headline

**The within-task cross-session failure-recurrence rate is 16.3% at pair level
(33 of 202 eligible consecutive session pairs) and 18.3% at bead level (33 of
180 beads with an eligible pair). That is real dynamic range — neither ~0% nor
saturated — so the recurrence oracle REVIVES at within-task granularity.** The
mem-apg.3.1 NO_GO was at across-task granularity, where held-out failure
classes essentially never recur in fresh runs; within the same task, across
its sessions, the known failure class recurs in roughly one of six handoffs.
One caveat keeps this an upper bound: some recurring signatures are repo
baseline lint noise (e.g. the gascity `misspell` findings recur in every run
that touches `hooks.go`), not the agent re-hitting its own failure. A
baseline-noise filter (signatures present in N unrelated beads' sessions) is
the obvious next refinement before using this as a reward axis.

## PHASE 1 — the join

### Content scan (source a)

- Corpus: **116,922 jsonl transcripts** under `/home/ds/.claude/projects/*/`
  (recursive — includes nested subagent transcripts) plus
  `/home/ds/.claude-homes/*/.claude/projects/*/` (currently empty). The ~19k
  estimate was the flat per-project count; the recursive walk finds ~6x more
  files, mostly subagent sidecars.
- Scan wall-time: **71.3 s** (single pass, line-streamed; full corpus, no
  subset needed).
- Id grammar: 18 prefixes derived read-only from the store's 6,691 distinct
  work_ids (never hardcoded). Mentions extracted only from `bd` invocations in
  `tool_use` command inputs; `claim/update/close/comment/reopen` = strong,
  `show/list/...` = weak; `--assignee` values and ids embedded in larger
  hyphen runs (`mem-worker-gc-351177`) are rejected mechanically.
- Yield: **52,785 (session, work_id) link rows across 20,394 transcripts**;
  2,023 in-store beads have ≥1 strong-linked session.

### Calibration against the store's 874 assignee links

For each store `(work_id, trace_path)` link (the one-session-per-bead status
quo): did the content scan independently find that bead in that transcript?

| measure | value |
|---|---|
| store links | 874 (1 transcript missing on disk → 873 scannable) |
| found (any strength) | 329 → **37.7%** raw agreement |
| found (strong) | 321 → 36.8% |
| misses where the work_id NEVER appears in the transcript | 516 (59.1%) |
| misses where the id appears but the scan missed it | 28 |
| **agreement conditional on the id appearing at all** | **329/357 = 92.2%** |

The decomposition is the real story: the content scan recovers the link in
92% of transcripts that mention their bead at all. The 59% it cannot see are
predominantly gascity polecat sessions (gascity rig: 82/555 raw agreement)
that receive their bead via dispatch env/hooks and never type its id — their
linkage lives only in the assignee side channel. Interactive rigs are near
ceiling: mem 14/14, gascity_dashboard 151/172, EnterpriseBench 30/36.
Consequence for mem-75t.4: the productionized join MUST merge content scan
with the assignee/dolt source; neither alone covers the corpus.

### Dolt history (source b)

Read via a READ-ONLY client connection to the already-running shared city
dolt server (the `doltRunner` path: `dolt --host 127.0.0.1 --port 29620 ...
sql -r json -q`; no server was started). All 18 rig databases answered —
**0 gaps**; the per-rig aggregated history query takes ~3.5 s on the largest
rig (gascity).

- 1,704 beads carry ≥1 session-shaped assignee in `dolt_history_issues`;
  179 of them show ≥2 distinct session assignees (dolt's own view of
  multi-iteration work — smaller than the content scan's because history is
  compacted (`bd gc`/`flatten`) and re-dispatch often reuses one assignee row).
- Cross-validation vs content scan (strong, in-store): **1,083 beads seen by
  both; exact session-count match 77.7%; content-count ≥ dolt-count 98.3%**
  (mean diff +0.31 — content scan sees more sessions, as expected: weak-claim
  reuse + sessions that mention without being assigned). 621 beads are
  dolt-only (the silent-polecat population), 940 content-only (compacted or
  never-assigned-to-session history).

## PHASE 3 — cross-session metrics

Population: beads with ≥2 strong-linked in-store sessions, sessions ordered by
transcript start time. **432 beads, 1,119 bead-sessions, 687 consecutive
pairs** (636 distinct transcripts; coordinator sessions link to several beads).
Metrics computed in 42 s; failure signatures via the canonical
`mem extract-errors` extractor over each session's tool-observation output,
compared on the relaxed `tool:basename:error_class` key (`trace_score`).

### Coverage (beads with ≥2 sessions, per rig)

| rig | beads | sessions |
|---|---|---|
| gascity | 212 | 564 |
| gascity_dashboard | 57 | 166 |
| gpk | 36 | 94 |
| EnterpriseBench | 36 | 81 |
| zeldascension | 30 | 78 |
| scix_experiments | 26 | 60 |
| mem | 20 | 45 |
| GEO / gc / CodeScaleBench | 3 each | 6 each |
| agent_diagnostics | 2 | 5 |
| codeprobe | 2 | 4 |
| dec / code_intel_digest | 1 each | 2 each |
| **total** | **432** | **1,119** |

(Weak links included, 1,163 beads have ≥2 sessions — the strong-only 432 is
the conservative population.)

### Iterations distribution

mean 2.59, max 20: `{2: 310, 3: 71, 4: 28, 5: 12, 6: 3, 7: 1, 8: 2, 12: 2,
13: 1, 16: 1, 20: 1}` — 28% of multi-session beads took ≥3 sessions.

### Summed cost across sessions

Over the 432-bead population: 288,511 turns, 139,674 tool calls, 45.9M
fresh input tokens / 350.5M output tokens (usage-record sums; cache reads not
included in input). This is the budget multi-iteration work actually burns —
the warm/cold comparison in mem-0ut should report iteration count and this
summed cost, not per-session cost.

### Redundant-read overlap (session N+1 re-reading session N's files)

- 441 of 687 pairs have ≥1 structured file read in session N+1.
- **Mean redundant fraction 10.1%** of next-session reads were already read
  by the previous session; median 0, p90 33%, max 100%; 116/441 pairs (26%)
  have ≥1 redundant read.

Interpretation: re-exploration is concentrated, not uniform — a quarter of
handoffs re-read prior context (sometimes the entire read set), the rest start
fresh files. This is the axis a memory injection should move first.

### Failure recurrence (the oracle question)

- 202 of 687 pairs are eligible (session N surfaced ≥1 extractable failure).
- **33 recur → 16.3% pair-level; 33/180 beads → 18.3% bead-level.**
- Recurrences are concrete and class-stable: e.g. `tsc:server.ts:TS2353`
  (gascity-dashboard-fhj), `tsc:gc-client.ts:TS2305` ×2 files
  (gascity-dashboard-ucc), `eslint:gascity.js:no-unused-vars`
  (gascity-dashboard-rqv0/z8n7), plus the gascity `misspell` baseline cluster
  noted above.

**Verdict: the recurrence oracle revives at within-task across-session
granularity.** 16–18% positive rate over 200+ eligible pairs is usable signal
for a deterministic reward term (vs the across-task base rate that produced
the mem-apg.3.1 NO_GO), with enough headroom to detect a memory arm that
reduces it. Required refinement before adoption: subtract repo-baseline
signatures (recurring across unrelated beads) so the term measures the
agent's own failure persistence, not ambient lint.

## Limitations

- **Session ≠ worker iteration.** Strong links include coordinator/mayor
  sessions that claim/close several beads (636 distinct transcripts back
  1,119 bead-sessions); an "iteration" here is "a session that mutated the
  bead", not necessarily a full work attempt.
- **Error signatures are session-scoped, not bead-scoped.** A session working
  two beads attributes its failures to both; recurrence pairs inherit that.
- **Subagent transcripts share the parent sessionId**; dedupe keeps the
  transcript with the most strong mentions, which can pick a subagent slice
  of the session rather than the parent shell.
- **Baseline lint noise inflates recurrence** (upper bound; see headline).
- **The content scan cannot see silent sessions** (59% of assignee-linked
  transcripts never mention their bead) — source (b) covers part of that;
  the merged join is mem-75t.4's job.
- Inter-session gap time (open→close wall vs active time) was not computed in
  this spike; the join rows carry all timestamps needed to add it.
