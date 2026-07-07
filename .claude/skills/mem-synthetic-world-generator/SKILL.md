---
name: mem-synthetic-world-generator
description: >
  Author, freeze, verify, and evaluate synthetic enterprise worlds — the
  memory-bench track that produced the first measurable memory lift. Load this
  skill when working on anything under memory-bench/membench/generators/ or
  memory-bench/fixtures/worlds/: the NeMo world builder (offline NIM), the
  enterprise_workflow materializer (facts / distractors / supersession authored
  in code), the memory-necessity gate (rejects oracle≈no_memory tasks), the
  world freeze + determinism manifest, opaque memory ids, adding a decision
  subject, regenerating or verifying a frozen world.json, or debugging a
  verify_worlds failure. NOT for running the eval harness end-to-end — use
  mem-eval-harness-run. NOT for grading, the ablation curve, or validity gates
  on runs — use mem-grading-and-validity-gates. NOT for the memory arms under
  test — use mem-competitive-arms. NOT for the ZFC boundary in the TypeScript
  parse layer — use mem-deterministic-extraction-zfc. NOT for loading synthetic
  records into the store / temporal LOO — use mem-store-schema-and-rebuild and
  mem-temporal-loo-and-leak-safety.
---

# mem synthetic world generator

The synthetic-world track manufactures eval tasks where memory-dependency is
true **by construction** — the answer the real corpus cannot give (real-trace
track: no measurable lift, N=8/407 sound oracles; see
mem-failure-archaeology). This track produced the project's first measurable
lift: a cross-task continuity gap between isolated and shared-store runs.
Everything here lives in `memory-bench/` (Python). Run all commands from
`/home/ds/projects/mem/memory-bench` unless stated otherwise.

Numbers note: every number in this skill is a diagnostic observation, dated,
and NOT publishable — the `mem-0rrf` publication freeze is in force and
release of any headline number is a Stephanie call.

## Definitions (jargon, defined once)

| Term                      | Meaning                                                                                                                                                                                                                                                                          |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **World**                 | An `EnterpriseWorld` + `Project` (pydantic, `membench/schemas/world.py`, `world.v1`): a synthetic org's cast and setting — teams, personas, channels, repos, PRD summary. Surface only; carries NO facts and NO oracle.                                                          |
| **NeMo / Data Designer**  | NVIDIA's `data_designer` SDK (verified against 0.6.1) that fills the world's natural-language surface (org/team/persona names, PRD prose) from category samplers + LLM-text columns. Runs offline against a local NIM; never in CI.                                              |
| **NIM**                   | A local NVIDIA Inference Microservice exposing an OpenAI-compatible endpoint (default `http://localhost:8000/v1`, model `meta/llama-3.1-8b-instruct`). No paid API — mem's no-paid-API stance (Decision 16) covers the memory stack; generation is offline and one-time.         |
| **Materializer**          | `membench/generators/enterprise_workflow.py` (`enterprise-workflow.v3` as of commit `30733d2`, 2026-07-07): pure Python that turns a world into N memory-dependent `BenchmarkSequence`s. Every fact, value, distractor, and supersession is authored in code, seed-reproducible. |
| **Necessity gate**        | `membench/generators/memory_necessity_gate.py`: admits a sequence only if the ORACLE_MEMORY arm beats NO_MEMORY by more than `EPSILON` (0.05, `membench/report/comparison.py`). A task the agent solves without memory measures nothing and is rejected.                         |
| **Freeze / manifest**     | `write_world` serializes a world to `fixtures/worlds/<seed>/`; `membench/generators/world_manifest.py` (`world-manifest.v1`) records SHA-256 hashes + provenance so the fixture proves it reproduces its tasks with no model call.                                               |
| **Confusion / Staleness** | Retrieval-quality signals the materializer seeds: distractors (plausible-but-wrong values, same template) and supersession chains (stale versions the goal must not state).                                                                                                      |
| **ScriptedAgent**         | The deterministic reference agent (`membench/runner/agent.py`) used by the gate and the CI-safe arm evals — no Docker, no model, no spend.                                                                                                                                       |

## The ZFC line (the track's one non-negotiable)

**No oracle lives in model output.** NeMo supplies only the cast and prose
(who/where/what-domain); Python writes the script. A reviewer must be able to
read the entire ground truth — subjects, values, dependencies, distractors,
supersession — in `enterprise_workflow.py` without consulting any model
output. Enforced structurally:

- `schemas/world.py` deliberately has no fact or oracle fields.
- `data_designer` is lazy-imported inside functions (`world_builder.py`,
  `model_provider.py`) and `pytest.importorskip`-gated in
  `tests/test_nemo_world_builder.py` — CI imports and tests everything
  without the SDK or a model.
- `records_to_world` validates NeMo output against bounded vocabularies
  (`column_spec.py`: DOMAINS, PERSONA_ROLES, CHANNEL_KINDS, REPO_LANGUAGES)
  and requires org-level fields constant across rows; drift raises loudly.

Do NOT add an LLM-text column whose output becomes reward-bearing, and do not
let a generated string decide pass/fail. That is the same ZFC boundary the
TypeScript parse layer holds — see mem-deterministic-extraction-zfc.

## Module map

| Concern                                                      | File                                                                                                                                 | Model call?                   |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- |
| Column spec (samplers + prompts, SDK-free)                   | `membench/generators/nemo/column_spec.py`                                                                                            | no                            |
| NIM provider/config helpers                                  | `membench/generators/nemo/model_provider.py`                                                                                         | lazy SDK                      |
| Build config, generate rows, parse rows → world, freeze/read | `membench/generators/nemo/world_builder.py`                                                                                          | only `generate_world_records` |
| World/Project schemas + integrity validators                 | `membench/schemas/world.py`                                                                                                          | no                            |
| Materializer (facts, distractors, supersession, charter)     | `membench/generators/enterprise_workflow.py`                                                                                         | no                            |
| Opaque agent-visible memory ids                              | `membench/generators/opaque_ids.py`                                                                                                  | no                            |
| Necessity gate (runs the pilot)                              | `membench/generators/memory_necessity_gate.py`                                                                                       | no (ScriptedAgent)            |
| Admission arithmetic only                                    | `membench/generators/pilot_filter.py`                                                                                                | no                            |
| Determinism manifest + verify                                | `membench/generators/world_manifest.py`                                                                                              | no                            |
| Synthetic sequence → WorkRecord projection (D19)             | `membench/generators/synthetic_corpus.py`                                                                                            | no                            |
| Operator scripts                                             | `scripts/generate_worlds.py`, `scripts/verify_worlds.py`, `scripts/eval_synthetic_arms.py`, `scripts/eval_synthetic_outcome_lift.py` | only generate_worlds          |

Adjacent, NOT covered here: `external_anchor.py` / `anchor_adaptation.py` /
`ftp_shapes.py` (real-anchor calibration, mem-bxhh.5), `synthetic_task.py` /
`schema_induction.py` / `factorial_dag.py` / `interruption.py` (the older
authored-blueprint generators the enterprise track mirrors).

## Pipeline

```
OFFLINE, operator-only (one-time, seeded, local NIM):
  generate_world_records (NeMo)  →  records_to_world  →  write_world
                                                          fixtures/worlds/<seed>/world.json + project.json

DETERMINISTIC, CI-safe (no model, byte-reproducible):
  read_world → materialize_world / materialize_project → BenchmarkSequences
             → memory_necessity_gate (admit/reject)
             → build_manifest / write_manifest → manifest.json
             → verify_world (re-hash + re-materialize)                 ← the determinism proof
             → runner (run_sequence isolated / run_project shared store) → arm lift
             → synthetic_corpus.materialize_record → WorkRecord (origin="synthetic")
```

**The frozen `world.json` is the durable artifact, not the seed.** NeMo
generation is NOT byte-reproducible (the LLM surface varies run to run); what
is deterministic is everything downstream of a frozen world. Never plan to
"regenerate from the seed" — re-running NeMo with the same seed gives a
different world. The seed only fixes the org's domain/org_size draw and the
materializer's RNG.

## Environment

CI installs the package (`pip install -e ".[dev]"` in `memory-bench/`); tests
also work uninstalled via the root `conftest.py`. The operator scripts assume
the uninstalled layout — run them as:

```bash
cd /home/ds/projects/mem/memory-bench
PYTHONPATH=. python3 scripts/<script>.py ...
```

No NeMo SDK, Docker, GPU, or API key is needed for anything below except
Runbook 4.

## Runbook 1 — verify frozen worlds (start here; CI-safe, seconds)

```bash
cd /home/ds/projects/mem/memory-bench
PYTHONPATH=. python3 scripts/verify_worlds.py fixtures/worlds
```

Per world dir with a `manifest.json`, this re-hashes the frozen
`world.json`/`project.json` (detects edited fixtures) and re-materializes the
sequences from the manifest's recorded `seed`/`n_tasks`/`facts_per_task`
(detects materializer drift or non-determinism), comparing every SHA-256 to
the manifest (canonical sorted-key JSON hashing). Exit 1 on any failure.

**Known state as of 2026-07-07 (branch `main`, HEAD `4e819e1`):**
`fixtures/worlds/0` (org "Nexarion Systems", seed 0) FAILS with
`sequences_sha256 ... (materialiser is non-deterministic or drifted)`.
This is expected drift, not corruption: the manifest was written by
`enterprise-workflow.v1`, and the materializer is now
`enterprise-workflow.v3` (v2 = commit `88d85e4`, mem-z3gi: opaque ids,
unified templates, reward-bearing staleness; v3 = commit `30733d2`,
mem-31vl, 2026-07-07: tool-requiring apply-current-value variant). The
world/project hashes still verify — the world itself is intact and NO NeMo
re-run is needed to repair it.

To repair: re-materialize `sequences.json` from the frozen world with current
code and write a fresh manifest (mirror the tail of
`scripts/generate_worlds.py`: `read_world` → `materialize_world` →
`build_manifest` → `write_manifest`). **Do not just do it**: a frozen
world fixture is eval substrate; changing it is HALT-branch-ready — Stephanie
sign-off, tests ship with the change (PROVISIONAL pending Stephanie, Q4:
conservative gating treats anything touching the eval substrate as
sign-off-gated). Note the fixtures are NOT in git: `.gitignore:36` ignores
`memory-bench/fixtures/worlds/` wholesale ("local generation artifacts, not
committed" until curated freezing lands in Phase 4, bead `mem-ge51`), and
`git ls-files` under it returns nothing — the frozen worlds exist only on
the machine that generated them, versioned by the manifest hashes + seed,
not by git.

Interpretation table:

| Mismatch                          | Meaning                                                     | Action                                                                                                                                                                                                            |
| --------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `world_sha256` / `project_sha256` | frozen files edited or corrupted                            | fixtures are gitignored (`.gitignore:36`) — there is no git copy; restore from a local backup or regenerate from the same seed (Runbook 4) and re-freeze under change control; investigate who wrote to fixtures/ |
| `sequences_sha256` only           | materializer drifted (version bump) or is non-deterministic | if you just changed `enterprise_workflow.py`: expected — re-freeze under change control and bump `GENERATOR_VERSION`; if you changed nothing: a real non-determinism bug, stop and bisect                         |
| `no manifested worlds`            | wrong `base` path or world never manifested                 | check the path; a world without `manifest.json` predates Phase 4                                                                                                                                                  |

## Runbook 2 — reproduce the continuity signal (CI-safe, seconds)

```bash
cd /home/ds/projects/mem/memory-bench
PYTHONPATH=. python3 scripts/eval_synthetic_arms.py fixtures/worlds/0
# options: --arms none oracle filesystem lexical   --tasks 3   --facts 3
```

Reads the frozen world, materializes tasks both independently
(`materialize_world`) and as a charter-linked project
(`materialize_project`), and runs the arms under all three conditions with
the ScriptedAgent. Three report blocks print; the continuity signal is the
gap between blocks 2 and 3:

- **Cross-task project, ISOLATED (`run_sequence`)** — store reset per task;
  later tasks require a charter they never wrote → low reward.
- **Cross-task project, SHARED store (`run_project`)** — one store across
  tasks; the task-0 charter write is visible later → reward recovers.

Observed 2026-07-07 on `fixtures/worlds/0` (then-current v2 materializer —
the materializer is v3 as of commit `30733d2`, so expect different numbers
on re-run; default arms;
diagnostic only, non-publishable under mem-0rrf): oracle-condition reward
0.053 isolated vs 0.158 shared — the gap IS the continuity lift. (The
first-lift result recorded in project history, 0.062 → 0.188, was produced on
the earlier materializer; it lives in bead threads, not repo docs — treat the
mechanism, not either number pair, as the durable fact.)

Reading the columns (`membench/report/synthetic_arms.py`): `lift` =
arm − none; `oracle_gap` = oracle − arm; `confusion`/`staleness` = mean
distractor/stale retrieval rate over read-attempted MEMORY_ENABLED trials
(`rate_n` is the denominator — small by design, only goal steps retrieve).
Known ceiling (documented in that module, mem-zt1c): the ScriptedAgent
retrieves by exact id, so `oracle`/`filesystem` hit oracle-level reward with
0 confusion/staleness by construction; the `lexical` top-k arm DOES surface
distractors and stale versions (non-zero rates) but still matches oracle on
reward at default top-k. Reward-level differentiation on these axes needs a
supersession-aware/semantic arm or a real agent — do not claim it from this
script.

## Runbook 3 — large-N outcome lift with NO NeMo (CI-safe)

```bash
cd /home/ds/projects/mem/memory-bench
PYTHONPATH=. python3 scripts/eval_synthetic_outcome_lift.py --seed 0 --tasks 40
# --facts N, --arms ..., see --help
```

The §4.4 outcome-lift driver (mem-lvp.27): builds an AUTHORED world in code
(`authored_world` inside the script — no NeMo, no fixture needed), gates
every task through `memory_necessity_gate`, and reports memory-vs-no-memory
outcome lift at an N the real corpus cannot reach (default 40 tasks). Use
this when you need task volume and do not care about NeMo surface diversity.

## Runbook 4 — generate and freeze a NEW world (operator-only, needs a NIM)

Precondition: a local NIM serving an OpenAI-compatible endpoint. Setup lives
in bead `mem-3453` (`bd show mem-3453`). Never wire this into CI.

```bash
cd /home/ds/projects/mem/memory-bench
PYTHONPATH=. python3 scripts/generate_worlds.py \
  --seed 1 --personas 4 --tasks 2 --facts 3 \
  --nim-endpoint http://localhost:8000/v1 \
  --nim-model meta/llama-3.1-8b-instruct \
  --out fixtures/worlds
```

Flags: `--seed` (fixes the org's domain/org_size draw and the materializer
RNG), `--personas` (NeMo rows — one per persona), `--tasks`/`--facts`
(materialized sequences and subjects per task), `--nim-endpoint`/`--nim-model`
(must match the RUNNING NIM — the script docstring's example uses port 8001;
the code default is 8000). Output: `<out>/<seed>/world.json`, `project.json`,
`sequences.json`, `manifest.json`, plus a printed per-task ADMIT/REJECT
summary from the necessity gate.

Post-flight, always:

```bash
PYTHONPATH=. python3 scripts/verify_worlds.py fixtures/worlds   # must print OK for the new seed
```

Failure modes `records_to_world` raises on (all mean the NeMo run is bad —
regenerate, do not hand-patch rows): org-level field not constant across rows
("rows do not describe one organization"); out-of-vocabulary sampler value
("NeMo output drifted from the column spec"); missing required columns.
Committing a new frozen world = adding eval substrate → HALT-branch-ready,
Stephanie sign-off (PROVISIONAL pending Stephanie, Q4).

## The materializer contract (read before editing enterprise_workflow.py)

`materialize_world(world, project, n_tasks=, facts_per_task=, seed=)` returns
independent sequences; `materialize_project(...)` adds a shared charter
established in task 0 and required by EVERY task's goal (the continuity
lever; `drop_charter=True` is the Recovery variant — charter required but
never written, even the oracle pool lacks it). Determinism: same
(world, project, args, seed) ⇒ byte-identical sequences; per-task RNG is
`random.Random((seed << 16) ^ (task_index * 2654435761))`.

Load-bearing invariants (each has a test; breaking one invalidates the
Confusion/Staleness numbers):

1. **One subject per task is superseded** through a chain of
   `SUPERSESSION_DEPTH` (= 3) versions under distinct ids; each superseding
   step marks its predecessor; the goal requires the FINAL version only and
   FORBIDS stating any earlier value (`forbidden_values` on the
   `OutcomeCheck`) — staleness is reward-bearing, not diagnostic-only
   (mem-z3gi). The chain's position among the task's subjects is seed-varied
   so position cannot stand in for the label.
2. **No agent-visible string separates the classes.** Truth, stale, and
   distractor all render through the SAME `_fact` template; all establishing
   steps use the single `_RECORD_REQUEST` template ("initial"/"corrected"
   wording would leak); every agent-visible memory id is
   `opaque_memory_id(namespace, label)` = `m-` + 16 hex chars of SHA-256 —
   deterministic, content-keyed, zero class information
   (`OPAQUE_ID_PATTERN`). Labels live only in harness-side fields (step ids,
   probe descriptions) the runner never shows the agent.
   `tests/test_label_leak.py` enforces all of this — run it after ANY
   materializer edit.
3. **Distractor values are distinct from the current AND every stale value**,
   so surfacing a distractor is never mis-scored as staleness; the goal query
   deliberately omits the values, so a naive top-k retriever cannot rank
   truth above distractor — that hardness is the point.
4. **Subject-bank rules** (`_SUBJECTS`): every subject needs
   `> SUPERSESSION_DEPTH` distinct values (chain + a distractor always
   exist) — enforced at import time; no value may be a word-boundary
   substring of another (the `states_value` grading contract in
   `membench/metrics/scorers.py` — e.g. `v2` must not appear inside
   `checkout_v2`); values must be disjoint across subjects
   (`_assert_no_forbidden_value_leak` raises at materialization otherwise).
   To add a subject: append a `_Subject(key, prompt, values)` obeying all
   three, run `python3 -m pytest tests/test_enterprise_workflow.py
tests/test_label_leak.py -q`, and bump `GENERATOR_VERSION` — every frozen
   manifest pins it, and existing fixtures will (correctly) start failing
   `verify_worlds` until re-frozen under change control.

## The necessity gate contract

`memory_necessity_gate(seq)` runs the sequence under exactly two conditions —
NO_MEMORY and ORACLE_MEMORY — with the deterministic ScriptedAgent, and
admits only when `oracle_reward − no_memory_reward > EPSILON` (0.05, shared
with reporting so generation and reporting agree on "beats"). Rejection
reason is carried in the returned `NecessityResult.verdict` (a
`PilotVerdict`: rewards, delta, epsilon, reason). MEMORY_ENABLED is
deliberately NOT run: necessity is a property of the task, independent of any
arm under test. No model is called; the gate is CI-safe.

Do not weaken this gate or admit rejected tasks "for volume" — it is the
construct-validity precondition for every synthetic number (PRD Phase 0: a
task where oracle ≈ no_memory measures nothing about memory). If a batch of
generated tasks is rejected wholesale, the materializer's dependency
structure regressed; fix the generator, not the gate.

## Downstream: synthetic records in the real corpus (pointer)

`membench/generators/synthetic_corpus.py::materialize_record` projects a
gated sequence into a WorkRecord with `origin="synthetic"` — one schema, one
reader, one temporal-LOO path (Decision 19: a parallel synthetic loader was
explicitly rejected; two code paths hide leaks). The only outcome label it
carries is the deterministic necessity verdict, never a real commit/PR; an
optional high-entropy `outcome_sentinel` is routed into the firewall-scanned
`outcome.commit_sha` so leak-safety stays mechanically checkable end-to-end.
Details of the store side and the LOO guard: mem-store-schema-and-rebuild,
mem-temporal-loo-and-leak-safety.

## Tests (the safety net)

```bash
cd /home/ds/projects/mem/memory-bench
python3 -m pytest tests/test_world_schema.py tests/test_nemo_world_builder.py \
  tests/test_enterprise_workflow.py tests/test_memory_necessity_gate.py \
  tests/test_pilot_filter.py tests/test_label_leak.py tests/test_world_manifest.py \
  tests/test_synthetic_arms.py tests/test_synthetic_corpus_contract.py \
  tests/test_eval_synthetic_outcome_lift.py -q
```

35 tests collected across the first five files alone (verified 2026-07-07).
`test_nemo_world_builder.py` contains two `pytest.importorskip("data_designer")`
smoke tests that only run where the SDK is installed — a skip there is by
design, not a failure. Any change to the materializer, opaque ids, or the
gate ships its test in the same commit (house rule).

## Change control summary

HALT-branch-ready (Stephanie sign-off + tests ship with the change —
PROVISIONAL pending Stephanie, Q4 conservative gating): committing or
re-freezing anything under `fixtures/worlds/`; changing `EPSILON` or the gate
logic; changing `GENERATOR_VERSION` semantics, the `_fact`/`_RECORD_REQUEST`
templates, opaque-id derivation, or `forbidden_values` behavior; any change
that alters what an agent can see (label-leak surface). Free to proceed:
running Runbooks 1–3, adding tests, adding a subject on a branch (the commit
itself is gated).

## Provenance and maintenance

Authored 2026-07-07 against branch `main`, HEAD `4e819e1`
(sjarmak/mem checkout at /home/ds/projects/mem; checkout was on `main`).
Volatile facts pinned to that state: `enterprise-workflow.v3` (v2 at HEAD
`4e819e1`; v3 landed later the same day, commit `30733d2`), `world.v1`,
`world-manifest.v1`, `EPSILON = 0.05`, `SUPERSESSION_DEPTH = 3`,
data-designer 0.6.1, the `fixtures/worlds/0` verify FAILURE, and the Runbook-2
numbers. Re-verify with:

```bash
cd /home/ds/projects/mem/memory-bench
grep -n GENERATOR_VERSION membench/generators/enterprise_workflow.py        # materializer version
grep -n "WORLD_SCHEMA_VERSION\|WORLD_MANIFEST_VERSION" membench/schemas/world.py membench/generators/world_manifest.py
grep -n "^EPSILON" membench/report/comparison.py                            # gate threshold
grep -n "SUPERSESSION_DEPTH =" membench/generators/enterprise_workflow.py
ls fixtures/worlds/                                                          # frozen world inventory
PYTHONPATH=. python3 scripts/verify_worlds.py fixtures/worlds                # determinism status
PYTHONPATH=. python3 ../.claude/skills/mem-synthetic-world-generator/scripts/world_fixture_status.py  # version-drift report
python3 -m pytest tests/test_label_leak.py -q                      # leak invariants still hold
```

Freeze status (`mem-0rrf`) and the release call (`mem-1fl8`) are bead-side:
`bd show mem-0rrf`, `bd show mem-1fl8`.
