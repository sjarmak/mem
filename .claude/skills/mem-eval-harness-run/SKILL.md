---
name: mem-eval-harness-run
description: >
  Run the memory-bench eval end-to-end: the three conditions (no_memory /
  oracle_memory / memory_enabled), the two runners (run_sequence vs
  run_project), the bundle → runner → Harbor → grading → report flow, and the
  membench CLI (run-sequence, gen-tasks, replay, curate-ftp). Load when
  executing or debugging an eval run, emitting Harbor task dirs, replaying
  arms over the real store, or wiring the OAuth agent-under-test. NOT for
  grading/metric internals or the ablation curve — use
  mem-grading-and-validity-gates; NOT for the arm catalog or adding an arm —
  use mem-competitive-arms; NOT for LOO/leak mechanics — use
  mem-temporal-loo-and-leak-safety; NOT for building .mem/store.db — use
  mem-ingest-and-provenance; NOT for environment setup from scratch — use
  mem-build-test-env.
---

# mem-eval-harness-run — running the eval end-to-end

Verified against `/home/ds/projects/mem` at `main` @ `4e819e1` on 2026-07-07.
Every command below was executed or read from source this session.

## When NOT to use this skill

| You want                                                                                        | Go to                                                                     |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| How a run becomes a defensible number (ablation curve, validity_gate, safety gates, paired CIs) | `mem-grading-and-validity-gates`                                          |
| What the arms are / how `ours` fires / adding an arm                                            | `mem-competitive-arms`                                                    |
| Why retrieval is bounded, `closedBefore`, exclusions                                            | `mem-temporal-loo-and-leak-safety`                                        |
| Building or rebuilding `.mem/store.db` (incl. `--with-traces`)                                  | `mem-ingest-and-provenance` + the existing `ingest-trace-substrate` skill |
| Getting npm/pip/CI green                                                                        | `mem-build-test-env`                                                      |
| Authoring synthetic memory-dependent tasks                                                      | `mem-synthetic-world-generator`                                           |
| Before "fixing" replay/linkage/oracle recovery                                                  | `mem-failure-archaeology` (these are settled negatives)                   |

## Vocabulary (defined once)

- **Condition** — one of the three memory regimes a task runs under
  (`membench/schemas/conditions.py`). The _gap between conditions_ is the
  benchmark's signal.
- **Sequence** — an ordered multi-session task (Step 1 → … → Step N); each
  step starts with fresh agent context, only the memory store persists
  (`membench/schemas/sequence.py`, fixtures under
  `memory-bench/fixtures/sequences/`).
- **Arm** — a memory system under test behind the uniform `MemorySystem`
  interface (`membench/memory_systems/`). Details: `mem-competitive-arms`.
- **Bundle** — an admitted real-corpus eval object: a WorkRecord + its
  replay-reconstructed gold diff + frozen LOO exclusion set
  (`membench/bundle/assemble.py`).
- **LOO** — temporal leave-one-out: retrieval sees only records closed
  strictly before the query work started, minus siblings. Details:
  `mem-temporal-loo-and-leak-safety`.
- **Harbor** — the execution substrate: `harbor-framework/harbor`
  (Apache-2.0), a framework that runs agent evaluations in Docker containers.
  Optional dependency `harbor>=0.3` (pyproject constraint; installed 0.13.1
  in the repo venv as of 2026-07-07). The harness supplies datasets, adapters, and scorers; Harbor
  runs the containers. The **only paid-adjacent path in the whole harness is
  the agent under test**: Claude Code on the OAuth _subscription_ (Decision
  16 — explicitly not a paid-API cost fork; the memory stack itself stays
  OSS/self-hosted).
- **ATIF** — Harbor's trajectory format (RFC-0001); what a real run emits and
  what the harvester projects back into a scoreable transcript.

## The three conditions

Enum values (exact strings, `membench/schemas/conditions.py`):

| Condition        | What the agent gets                                             | Role                                                                                                   |
| ---------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `no_memory`      | current step only, no memory tooling                            | stateless floor                                                                                        |
| `oracle_memory`  | the exact relevant memory, harness-injected                     | ceiling + task-validity probe: if oracle ≈ no_memory the task doesn't discriminate and gets redesigned |
| `memory_enabled` | the full arm through its normal retrieve/write/consolidate path | the real system's score                                                                                |

Read the gaps: `oracle > memory > no_memory` → retrieval/ranking leaves gains
on the table; `memory < no_memory` → memory injects noise or stale state.
`ExperimentConfig.conditions` defaults to all three
(`membench/schemas/config.py`).

## The two runners

Both live in `membench/runner/` and share `_execute_step` (identical step
semantics); the only difference is store scope.

|             | `run_sequence` (`runner/conditions.py`)                   | `run_project` (`runner/project.py`)                                               |
| ----------- | --------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Store reset | per (sequence × condition) — scope `<seq_id>-<condition>` | once per condition across ALL sequences — scope `<project_id>-<condition>`        |
| Measures    | within-sequence convention memory                         | **cross-task continuity**: a later task reads what an earlier task wrote          |
| Oracle pool | one sequence's writes                                     | union across sequences (cross-sequence id conflict with different content raises) |

A project whose later tasks depend on an earlier task's memory passes under
`run_project` and fails under `run_sequence` — that gap IS the continuity
signal. The synthetic-world track's shared-store continuity result (isolated
0.062 → shared 0.188, per `memory-bench/README.md`) came from `run_project`.
Do not quote that or any headline number as publishable: the `mem-0rrf`
publication freeze is in force; numbers appear only with their validity
caveats. (Freeze scope stated conservatively — PROVISIONAL pending Stephanie,
discovery Q4.)

Runner guards worth knowing before you blame your fixture:

- An oracle `memory_id` written by two steps with _different_ content raises
  (`_oracle_pool` — prevents a within-sequence future leak).
- A `superseded_memory_ids` entry that was never written by an EARLIER step
  raises (`_assert_superseded_written` — otherwise the staleness signal would
  silently read 0).
- A crashed agent step aborts the whole run loudly; partial sequences are
  never scored (incomparable condition gaps).

### Arm selection at launch (memory_enabled only)

`MEMBENCH_MEMORY_SYSTEM` overrides `experiment.memory.system` at the process
boundary (`runner/conditions.py`). Pilot values: `none | ours | ours-live |
builtin`; any value is validated against the wired factory set — a typo
raises at launch, never a silent substitution. `no_memory`/`oracle_memory`
are fixed controls and ignore it. The `mem`-CLI-backed arms additionally
require:

```bash
export MEMBENCH_MEMORY_SYSTEM=ours          # or ours-live
export MEMBENCH_MEM_BIN=/abs/path/to/mem/bin/mem
export MEMBENCH_MEM_STORE=/abs/path/to/.mem/store.db
```

A missing var is a loud LAUNCH error, not a deferred first-use failure.
`MEMBENCH_AGENT_MODEL` selects the model for the headless real agent
(`runner/headless_agent.py`).

## The pipeline: bundle → runner → Harbor → grading → report

Two lanes share this shape:

**Lane 1 — in-process, deterministic, free** (no Docker, no OAuth): fixture
JSON → `run_sequence`/`run_project` with `ScriptedAgent` + reference arms →
`compute_metrics` per step → `report/comparison.py` 3-condition table. This
is what `membench run-sequence` and CI exercise.

**Lane 2 — real, containerized, OAuth-metered**: eval object → Harbor task
dirs → `harbor run` (Claude Code in Docker) → harvest → grading → report.
Concretely, for the real-corpus grid (`harbor/grid.py`):

```
WorkRecord/bundle --adapter--> per-rung task dirs --inject memory-->
  agent run per rung (HarborRunner shells `harbor run --config <cfg> -q -y`)
  --harvest--> RunTrace --score_run--> RewardRecord(work_id, rung, repeat_idx)
```

- **Bundle admission** (`bundle/assemble.py`) gates what may enter Lane 2 at
  all: bead closed with clean trace tail, not a shared multi-bead trace, env
  anchor (repo + base_commit) present, base does not predate the tree,
  non-empty gold diff, replay fidelity above threshold. Every rejection is a
  typed `RejectionReason`, never a silent drop. The bundle freezes its LOO
  exclusion ids so enforcement is mechanical per run.
- **Harvest** (`harbor/harbor_exec.py`): prefers `*/agent/trajectory.json`
  (Harbor only writes it when the agent result is empty), falls back to the
  Claude Code stream `*/agent/claude-code.txt`; neither present raises —
  a missing transcript is never scored as a clean trace. `files_read`/
  `files_written` are derived only from structured file tools (Read/Write/
  Edit/MultiEdit/NotebookEdit); Bash/Grep/Glob are deliberately not guessed.
- **Grading and report** consume the harvested `RunTrace`; scorer semantics,
  the ablation curve, and gate doctrine live in
  `mem-grading-and-validity-gates`. `harbor/bundle_grid.py` scores the grid
  from _cached_ probe runs under `.mem/probe/jobs/` (real 2026-06-11
  Docker/OAuth executions) — re-harvesting transcripts locally, **no new
  agent runs**. Prefer that lane when a question is answerable from cache.

### Harbor OAuth wiring — the traps that cost real runs

1. **`CLAUDE_CODE_OAUTH_TOKEN` must be in the harbor PROCESS env, never in
   `agent.env`.** Harbor does not expand `${VAR}` and merges `agent.env`
   OVER the adapter's env, so a token reference there overwrites the real
   token with a literal string → `401 Invalid bearer token`.
   `build_job_config` refuses the key outright. `ANTHROPIC_API_KEY` stays
   unset so Claude Code uses subscription auth (D16).
2. **Pin `agent_version`.** Without it every container installs whatever
   Claude Code is latest — silent instrument drift across runs executed on
   different days (mem-p3w).
3. **`harbor run --config <cfg> -q -y`**: `-y` auto-confirms the
   host-env-var prompt that otherwise blocks on stdin; `-q` suppresses the
   live UI. Non-zero exit raises.
4. Real Harbor execution needs Docker and consumes OAuth subscription
   capacity; treat any new spend-bearing run (agent runs, distill/judge) as
   gated — PROVISIONAL pending Stephanie (discovery Q4): default to
   sign-off before new real-run campaigns.
5. Heavy local agent execution (the headless `claude -p` path) must be
   wrapped in `scix-batch` by the caller so a runaway agent cannot OOM-kill
   the host orchestrator (`runner/headless_agent.py` docstring; operational
   precondition of this machine).
6. Headless `claude -p` boots project MCP servers and hangs the batch;
   `--strict-mcp-config` (default on in `HeadlessClaudeAgent`) prevents it.

## The membench CLI

Console script `membench` (pyproject `[project.scripts]`) or
`python3 -m membench.cli`. Run from `memory-bench/` (activate `.venv` or
`pip install -e ".[dev]"` — see `mem-build-test-env`). Four subcommands
(verified via `--help` and smoke runs, 2026-07-07):

### `run-sequence` — Lane 1, free, seconds

```bash
cd memory-bench
python3 -m membench.cli run-sequence \
  fixtures/sequences/gascity_backend_conventions.json --out reports/
```

Runs one fixture under all three conditions in-process with the
deterministic `ScriptedAgent` (no Docker, no paid API). Writes per-trial
`traces/<trial>.trace.json` + `.otel.json` + `.atif.json`, plus
`report.json` and `report.md` (the 3-condition comparison table with the §4
interpretation line), and prints the markdown. `--fs-dir` relocates the
filesystem-arm store (default `<out>/memory_store`).

### `gen-tasks` — emit Harbor task dirs (build step, free)

```bash
python3 -m membench.cli gen-tasks \
  fixtures/sequences/gascity_backend_conventions.json --out tasks/out/
```

Emits one task dir per (step × condition) — a 3-step fixture yields 9 dirs —
each containing `task.toml`, `instruction.md`, `environment/Dockerfile`,
`tests/test.sh` (verifier writes reward ∈ [0,1] to
`/logs/verifier/reward.txt`, Harbor's canonical path), and
`solution/solve.sh` (oracle solution scoring 1.0). The condition is
materialized the way an agent experiences it: oracle memory inline in
`instruction.md`; memory_enabled gets a persistent `~/memory/` dir +
instructions. Network is off by default — internet is a task-level property
so only memory varies across conditions. Existing dirs raise
`FileExistsError` unless `--overwrite`. Running the emitted dirs
(`harbor run`) is the paid path.

### `replay` — arms over the REAL store, LOO-guarded (free)

```bash
# Prereqs: TS build + a trace-carrying store (see the two traps below).
python3 -m membench.cli replay \
  --store ../.mem/store.db --work-id <work_id> --arms none,ours --out reports/
```

Loads the corpus via `mem query --json` (the TS reader — no second store
schema in Python), bounds it with the harness-owned LOO guard, runs each arm
on the caller-named query work (the harness never curates the eval target),
and writes `replay_report.{json,md}` (per-arm raw 5-axis vector — never a
weighted composite) + `replay_spans.json` (OTel GenAI spans). Flags:
`--arms` (default `none,ours`), `--mem-bin` (default resolves to the repo's
`bin/mem`), `--limit` (max items `ours` returns).

Two prerequisites that fail confusingly if skipped:

- **`bin/mem` runs `dist/`, not `src/`** — run `npm run build` at the repo
  root first or you exercise stale compiled code.
- The `ours` arm only fires on a store built `--with-traces`, and that build
  must run from the gas-city city directory (wrong cwd = exit 0 with zero
  traces, silently). Operational precondition of this machine — PROVISIONAL
  pending Stephanie (discovery Q1); full runbook in
  `mem-ingest-and-provenance` / `ingest-trace-substrate`.

### `curate-ftp` — fail-to-pass oracle curation for a rig (local, free)

```bash
python3 -m membench.cli curate-ftp codeprobe \
  --store ../.mem/store.db --out reports/ftp-codeprobe.json
```

Derives landing SHAs from the store via `mem link-outcomes <rig> --json`
(the acceptance path — results are derived, not handed in; `--commits` is a
DEBUG-only bypass, `--linked-json` replays a saved derivation), then curates
each landing commit's fail-to-pass tests in a container. Flags: `--linkages`
(default `canonical`), `--base-image` (default `python:3.11-bookworm`),
`--worktree-root` (default `/tmp`). The rig must have a checkout in
`DEFAULT_RIG_REPOS` (`harbor/env_recon.py`) — a hardcoded map of
machine-local paths (`/home/ds/...`), so this subcommand only runs on a host
with those rig clones. Needs Docker.

## Preconditions checklist (before any run)

```bash
bash .claude/skills/mem-eval-harness-run/scripts/check-harness-env.sh
```

or by hand:

- [ ] `cd memory-bench && python3 -c "import membench"` (venv active or
      `pip install -e ".[dev]"`)
- [ ] Lane 1 needs nothing else. Smoke: the `run-sequence` command above.
- [ ] `replay` / `ours`: `npm run build` at repo root; `.mem/store.db`
      exists and was built `--with-traces` (77 MB as of 2026-07-07 — size is
      volatile).
- [ ] Lane 2: `harbor --help` works, Docker daemon up,
      `CLAUDE_CODE_OAUTH_TOKEN` exported in the process env, spend
      sign-off obtained.
- [ ] Suite sanity: `python3 -m pytest --collect-only -q | tail -1`
      (2,121 tests as of 2026-07-07). Optional-dep skips (harbor,
      data_designer, mem0/graphiti/…) are by design, not failures.

## Provenance and maintenance

Pinned to `/home/ds/projects/mem` branch `main`, HEAD `4e819e1`
(2026-07-06). Facts dated 2026-07-07. Re-verify before trusting drift-prone
claims:

```bash
git -C /home/ds/projects/mem log -1 --format='%h %ci'          # has the pin moved?
cd /home/ds/projects/mem/memory-bench && python3 -m membench.cli --help  # subcommand set
grep -n "class Condition" membench/schemas/conditions.py        # condition strings
grep -n "ENV_MEMORY_SYSTEM\|_PILOT_SYSTEMS" membench/runner/conditions.py  # launch env vars
grep -n '"run", "--config"' membench/harbor/harbor_exec.py      # harbor invocation shape
grep -rn "membench =" pyproject.toml                            # console script
python3 -m pytest --collect-only -q | tail -1                   # test count
ls -la ../.mem/store.db                                         # store presence/size
```
