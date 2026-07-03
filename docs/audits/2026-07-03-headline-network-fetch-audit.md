# Retroactive network-fetch audit of the headline runs (mem-hp9o)

**Date:** 2026-07-03
**Question:** did any agent run behind the n=8/n=9 headline numbers consult the
rig's origin repository (WebFetch/WebSearch, `git clone|fetch|pull`, `gh` CLI,
`curl`/`wget`/`pip` against the origin or its `raw.githubusercontent.com`
mirrors)? Any arm that consulted the origin saw post-hoc answer material and
invalidates its run.

**Verdict: CONTAMINATED — one run.** The mem-apg.4 ablation headline
(`docs/mem-apg.4-ablation-headline.md`) contains one origin-consulting run:
bundle `zhy00`, **oracle** arm, which successfully WebFetched the origin PR it
was being asked to reproduce (two fetches, title/description/full file-level
change detail returned into context). Three further runs *attempted* `gh pr
view` against the origin and got nothing (`gh` is not installed in the task
image). **The mem-n9 graded headline (`docs/mem-n9-graded-headline.md`) is
CERTIFIED CLEAN**, as are the probe-gate runs (probe-ce, probe-graded,
probe-n9).

## Rig origins

Per `src/ingest/rig-repo-map.ts` (lines 48, 51):

| rig | origin repo |
| --- | --- |
| `gascity_dashboard` | `gastownhall/gascity-dashboard` |
| `codeprobe` | `sjarmak/codeprobe` |

Every audited bundle is from one of these two rigs (60 of 62 streams are
gascity-dashboard; 2 are codeprobe gate probes).

## Corpus inventory

All harvested Harbor job directories with agent streams live under
`/home/ds/projects/mem/.mem/`. Each job carries the stream twice: the
stream-JSON `agent/claude-code.txt` and the Claude Code session JSONL under
`agent/sessions/projects/-app/` (plus subagent JSONLs where the run spawned
agents). Both forms were scanned.

| jobs dir | jobs | run window | feeds |
| --- | ---: | --- | --- |
| `.mem/probe/jobs` | 31 | 2026-06-11 → 06-12 | **mem-apg.4** ablation headline: 9 bundles × {none, oracle} scored via `.mem/grid/summary.json` (the "cached gate-probe agent runs"); plus none-clean runs, 2 `ours` runs (4lf62, km0wj), and an e29gw pair not in the headline table |
| `.mem/probe-n8L/jobs` | 20 | 2026-06-16 → 06-17 | **mem-n9** graded 3-arm headline (`.mem/grid-n8/summary-3arm-graded.json`): 8 bundles × {none-clean, none(=builtin fresh), ours(×4)} — 20 executed, 24 scored (4 empty-retrieval `ours` rows reuse none-clean by construction) |
| `.mem/probe-ce/jobs` | 5 | 2026-06-14 | corpus-expansion gate probes (2a7lh, 4lf62) |
| `.mem/probe-graded/jobs` | 5 | 2026-06-14 | graded gate probes (codeprobe-3l6tb, codeprobe-g1cp2, dashboard 2a7lh/4lf62/6yy76, all none-clean) |
| `.mem/probe-n9/jobs` | 1 | 2026-06-16 | n9 gate probe (4lf62 none-clean) |

File inventory scanned: **62 `claude-code.txt` streams + 73 session/subagent
`.jsonl` files = 135 files**, containing **10,298 tool_use blocks** (6,632
Bash, 2,382 Read, 1,150 Edit, 62 Write, 22 Agent, 10 ToolSearch, 14
TaskCreate, 22 TaskUpdate, **4 WebFetch, 0 WebSearch**). Every line parsed as
JSON with zero parse errors, so no stream content was skipped.

The builtin arm of mem-n9 is the `none` condition run fresh
(`memory-bench/scripts/run_grid_3arm_graded.py` lines 91–92); job dirs named
`.none` in probe-n8L are the builtin arm.

## Method

Scanner: structured stream parse (session-scoped scratch script; logic
reproduced here for auditability). For every line of every file above:

1. **Tool-invocation pass (authoritative).** Extract each `tool_use` block and
   flag: (a) any `WebFetch`/`WebSearch` call; (b) any Bash `command` matching
   `git (clone|fetch|pull|ls-remote|remote add)`, `gh (pr|issue|api|repo|release|search|run|gist)`
   at command position, `curl`, `wget`, `pip install|download` from a URL,
   `npm|pnpm|yarn` install from git/URL, `apt/apk` fetch; (c) any tool input
   (including Write/Edit payloads) containing `gastownhall/gascity-dashboard`,
   `sjarmak/codeprobe`, or `raw.githubusercontent.com`.
2. **Supplementary URL sweep.** Every `https?://` URL occurring in any Bash
   command across all 62 streams was enumerated: **zero URLs appear in any
   Bash command in the corpus.**
3. **Incidental-text pass.** The same patterns matched against `tool_result` /
   `text` content, to classify non-invocation mentions rather than let a clean
   verdict rest on an unexamined grep.

Result: 16 raw tool-invocation flags → 6 unique network events (each event
appears in both the `claude-code.txt` and the session `.jsonl` copy; 4 flags
were one non-network false positive, see below).

## Findings — per run, per arm

### Successful origin consultation (contamination)

**`gascity-dashboard-zhy00` / oracle arm / mem-apg.4 headline.** Task:
"Polish + ship PR #91 (dash-ygj unavailable tier)…" — the issue title names
the origin PR, and the agent loaded WebFetch via ToolSearch and fetched it
live. Evidence
(`.mem/probe/jobs/gascity-dashboard-zhy00.oracle/gascity-dashboard-zhy00.oracle__oYDTRXi/agent/`):

| file:line | event | outcome |
| --- | --- | --- |
| `claude-code.txt:644` | `ToolSearch {"query": "select:WebFetch"}` | WebFetch schema loaded |
| `claude-code.txt:650` | `WebFetch https://github.com/gastownhall/gascity-dashboard/pull/91` | **SUCCEEDED** — result at line 651 returns PR title, full description, key changes |
| `claude-code.txt:655` | `WebFetch https://github.com/gastownhall/gascity-dashboard/pull/91/files` | **SUCCEEDED** — result at line 656 returns the per-file change detail (file list, function-level changes) |
| `claude-code.txt:927` | agent's own retro note: "Eventually used `WebFetch` on the GitHub PR URL to discover the PR description" | confirms deliberate use |

Same events in the session copy
`sessions/projects/-app/99215c8b-4ebd-4ec9-966c-dc3d61cb3902.jsonl:332,335`.

The fetched PR #91 is the work the bundle's gold answer derives from
(`.mem/bundles/gascity-dashboard-zhy00.json`: `issue_title` names PR #91;
`oracle_context.oracle_answer` is the PR's file list). The oracle arm's design
budget is the gold-diff *file list* only; this run additionally received the
PR description, rationale, and per-file change detail from the live origin.
The run exceeded its arm's information budget with post-hoc answer material:
**invalid**.

### Attempted, failed (no information transferred)

`gh` is not installed in the task image (proven at
`gascity-dashboard-tkhkg.oracle__AYzehsm/agent/claude-code.txt:14`:
`/bin/bash: line 1: gh: command not found`). All `gh` attempts returned
nothing:

| run (arm) | file:line | command | result |
| --- | --- | --- | --- |
| e9y0d (oracle), mem-apg.4 | `probe/jobs/gascity-dashboard-e9y0d.oracle/…__5KjYrfN/agent/claude-code.txt:9` (session `fec976d9…jsonl:10`) | `gh pr view 72 --json … \|\| echo "gh not available…"` | no output — failed |
| tkhkg (none), mem-apg.4 | `probe/jobs/gascity-dashboard-tkhkg.none/…__XgxVbd2/agent/claude-code.txt:9` (session `2bdeddd6…jsonl:10`) | `gh pr view 73 …` | "gh not available or PR not found" — failed |
| tkhkg (oracle), mem-apg.4 | `probe/jobs/gascity-dashboard-tkhkg.oracle/…__AYzehsm/agent/claude-code.txt:9,13` (session `f67dd8a0…jsonl:10,12`) | `gh pr view 73` (twice) | `gh: command not found` — failed |

These runs remain valid (attempt without data transfer), but they demonstrate
intent: agents given "ship PR #N"-shaped tasks reach for the origin.

### False positives, examined and discharged

- `probe-n8L` acda2 (none and none-clean arms): Edit tool inputs matched the
  origin slug — the edited source comment cites bead ids
  (`gascity-dashboard-nyln / -4bol`). Text, not network.
- 97 incidental text matches across 61 files: all are repo file contents
  surfaced by Read/Grep (test fixtures with synthetic
  `github.com/gastownhall/...` URLs, `package.json` repository fields, README
  links, an issue body containing the word `curl`). None accompany a network
  tool invocation.

### Per-arm summary

| jobs dir (headline) | none | none-clean | oracle | ours | builtin (`none` fresh) |
| --- | --- | --- | --- | --- | --- |
| probe (mem-apg.4, n=9) | clean (1 failed `gh` attempt: tkhkg) | clean | **1 CONTAMINATED (zhy00)** + 2 failed `gh` attempts (e9y0d, tkhkg) | clean (2 runs) | — |
| probe-n8L (mem-n9, n=8) | — | clean (8) | — | clean (4) | clean (8) |
| probe-ce / probe-graded / probe-n9 (gate probes) | clean | clean | — | clean | — |

## Blast radius (mem-apg.4 only)

- One of the 18 headline runs (9 none + 9 oracle) is invalid: `zhy00.oracle`
  (combined reward 0.095). Its `none` counterpart is clean.
- Recomputed over the 8 clean pairs, the headline table becomes: none mean
  0.343, oracle mean 0.323, reward span −0.020 (published: 0.305 / 0.297 /
  −0.007). The qualitative read — the curve is flat-to-negative in reward and
  the headline lives on the efficiency axis — is unchanged, but the published
  n=9 table includes a contaminated run and must be footnoted or re-issued as
  n=8 pairs.
- The efficiency-leg paired deltas and the merged-diff footnote in the same
  doc also include the zhy00 pair and inherit the same footnote.
- **mem-n9 graded headline: unaffected.** zhy00 is not in the n=8 pool, and
  all 20 probe-n8L runs are clean.

## Certification

- `docs/mem-n9-graded-headline.md` (n=8 graded 3-arm): **CERTIFIED CLEAN** —
  no WebFetch/WebSearch invocation, no network-verb Bash command, no URL in
  any Bash command, across all 20 executed runs including subagent
  transcripts.
- `docs/mem-apg.4-ablation-headline.md` (n=9 ablation): **CONTAMINATED** at
  `zhy00.oracle`; clean at the other 17 headline runs.
- Gate probes (probe-ce, probe-graded, probe-n9): clean.

Vector status: the `gh` vector is closed by the image (`gh` not installed);
the WebFetch vector was **open** — the agent could ToolSearch-load WebFetch
and reach github.com. Future runs should disallow WebFetch/WebSearch in the
harness agent config (and ideally run the task container network-restricted),
since "ship PR #N" task titles actively invite origin consultation.

## Limitations

- **Validity-gate runs have no agent streams.** `.mem/grid-n9/*.validity.json`
  and `.mem/truebase-gate/*.json` are verifier-side (gold-repro/empty-repro
  test executions); no agent ran, so there is nothing to audit there.
- **Scored-artifact dirs carry no streams.** `.mem/grid`, `.mem/grid-n8`,
  `.mem/grid-n9`, `.mem/grid-ce`, `.mem/grid-72sj` hold scores only; the
  streams for every scored headline run were located in the probe dirs above
  (grid ↔ probe linkage confirmed via `run-grid-3arm-graded.log` /
  `grid-n8L-run.log` and the mem-apg.4 doc's "cached gate-probe agent runs").
- **Worktrees `~/projects/mem-apg.10/.11/.12`:** no job/stream artifacts
  (apg.10 and apg.12 have no `.mem`; apg.11's `.mem` holds store DBs and
  truebase-gate JSONs only). Nothing under `memory-bench/` outside `.venv`.
- The audit sees what the transcripts recorded. WebFetch's transport route
  (in-container fetch vs API-side fetch) is not distinguishable from the
  stream, and is irrelevant to the verdict: the origin PR content demonstrably
  entered the agent's context.
- `probe/jobs` also contains an `e29gw` none/oracle pair absent from the
  headline table (no scored artifact in `.mem/grid/`); both streams were
  scanned and are clean.
