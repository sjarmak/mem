---
name: mem-competitive-arms
description: >
  The memory arms under test in memory-bench: the uniform MemorySystem interface
  (reset/retrieve/write), the wired arm roster (none / oracle / filesystem /
  lexical / consolidating / retention_scheduled / ours / ours-issue-trigger /
  ours-live / builtin / mem0 / a-mem / nat / graphiti / nemo-embed), how the
  failure-triggered ours arm fires (oracle-triggered vs issue-triggered, D23),
  the SemanticMemoryClient seam and deterministic CI fakes for third-party
  adapters, launch-time arm selection (MEMBENCH_MEMORY_SYSTEM), and the
  checklist for adding a new arm. Load when wiring, debugging, comparing, or
  extending an arm, or when an arm raises at construction/first retrieve.
  NOT for running the eval or Harbor (mem-eval-harness-run), scoring/gates
  (mem-grading-and-validity-gates), the LOO substrate
  (mem-temporal-loo-and-leak-safety), or synthetic task authoring
  (mem-synthetic-world-generator).
---

# mem-competitive-arms: the retrieval systems under test

Everything in this skill was verified against the repo at commit `4e819e1`
(branch `main`) on 2026-07-07. Paths are relative to the repo root
(`/home/ds/projects/mem` on the authoring machine; the skill assumes only the
repo checkout).

**Jargon, defined once.**

- **Arm**: one memory system competing in the benchmark, behind the uniform
  `MemorySystem` interface. "Condition" is the run-level label (no_memory /
  oracle_memory / memory_enabled); an arm is what fills the memory_enabled slot.
- **Harness**: the Python eval half (`memory-bench/membench/`). It owns the
  record set, the scope, telemetry, and the leak boundary. Arms own nothing but
  their three methods.
- **LOO (temporal leave-one-out)**: the eval-validity contract (Decision 6):
  for a held-out query work `B`, an arm may only see records closed strictly
  before `B.started`, minus `B` itself, its convoy/PR/branch siblings, and its
  supersedes chain. Enforced in `memory-bench/membench/validity.py`.
- **D<N> / Decision N**: a numbered ruling in `docs/architecture-decisions.md`.
  Do not re-litigate them; changing one is a Stephanie call.
- **Trigger**: how a retrieval query is formed for the `ours` family (D23):
  `oracle` = from the held record's own stored trace errors; `issue-text` =
  from title/task-type text only.

## When NOT to use this skill

| You want to...                                                         | Use instead                                              |
| ---------------------------------------------------------------------- | -------------------------------------------------------- |
| Run replay / sequences / Harbor grids end-to-end                       | mem-eval-harness-run                                     |
| Understand scoring, the ablation curve, oracle soundness, safety gates | mem-grading-and-validity-gates                           |
| Change or reason about the LOO boundary itself                         | mem-temporal-loo-and-leak-safety                         |
| Author synthetic worlds/sequences the arms run over                    | mem-synthetic-world-generator                            |
| Rebuild the TS store the `ours` arm reads                              | mem-store-schema-and-rebuild / mem-ingest-and-provenance |
| Know which negative results are settled (do-not-retry)                 | mem-failure-archaeology                                  |

## 1. The uniform interface (the harness/arm contract)

Source of truth: `memory-bench/membench/memory_systems/base.py`. An arm
implements exactly three methods; the harness owns everything else.

```python
class MemorySystem(ABC):
    name: str
    backend: MemoryBackend            # filesystem | vector_db | kg | mcp | hybrid
    supports_write: bool = True
    uses_scope: bool = False          # True -> replay runs it under BOTH D7 tracks

    def reset(self, trial_id: str) -> None: ...
    def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult: ...
    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent: ...
```

`RetrievalRequest` carries both retrieval families in one signature:

| Family                     | Fields used                                                                     | Arms                                                                                                  |
| -------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| id-based / semantic        | `query_text` + `requested_ids`                                                  | oracle, filesystem, lexical, consolidating, retention_scheduled, all `AbstractSemanticArm` subclasses |
| failure-triggered (replay) | `query_work` (a `QueryWork`) + `scope` (`"cross_rig"` \| `"same_rig_temporal"`) | ours, ours-issue-trigger, ours-live                                                                   |

An arm called with the wrong family raises `ValueError` by design (e.g. `ours`
from the id-based sequence runner is a configuration error).

`RetrieveResult` fields beyond `payloads` + `event`:

- `total_matched`, `near_duplicate_top`, `fts_truncated`: the Decision-10
  precision-guard signal. Ranking arms populate them; exact-by-id arms leave
  defaults. Truncation is surfaced, never silent.
- `distractor_ids`: a reserved arm self-report seam the harness does NOT score
  (Confusion/Staleness score against authored ground truth, mem-zt1c).
- `source_trace_ids`: consolidating arms map each returned item to its source
  trace ids so the provenance gate can dereference citations (M7). Default
  empty is an honest absence, never fabricated provenance.

Two non-abstract hooks matter when you subclass:

- `seed(memories, ctx)`: harness-owned WORLD state injection (distractor noise)
  that must NOT emit telemetry. Default loops over `write` and discards events;
  no-ops when `supports_write` is False. An arm whose `write` raises but that
  sets `supports_write = True` MUST override `seed` (see `OursLiveMemory`).
- `close()`: idempotent teardown for process-lifetime resources (NAT/Graphiti
  event loops). No-op by default; the harness calls it once per arm.

**The leak rule that makes comparisons fair:** the harness bounds the corpus
(`validity.loo_bounded`) before any arm runs and re-audits every arm's output
(`validity.assert_no_leak`) after. A leak raises `LeakageError`; it is never
silently filtered. No arm picks its own boundary. Weakening anything in
`validity.py` (or the TS mirrors it tracks) invalidates every number: treat it
as HALT-branch-ready requiring Stephanie sign-off (PROVISIONAL pending
Stephanie, Q4 conservative-gating answer).

## 2. The wired arm roster

The single source of truth is `_systems_registry()` in
`memory-bench/membench/memory_systems/__init__.py`. Verified 2026-07-07:

```bash
cd memory-bench && python3 -c \
  "from membench.memory_systems import wired_memory_systems; print(wired_memory_systems())"
# ('a-mem', 'builtin', 'consolidating', 'filesystem', 'graphiti', 'lexical', 'mem0',
#  'nat', 'nemo-embed', 'none', 'oracle', 'ours', 'ours-issue-trigger', 'ours-live',
#  'retention_scheduled')
```

| Arm                   | Class (module in `membench/memory_systems/`)                 | Role                                                                                                                                                                                                                                                                       | `supports_write` | `uses_scope`    | `backend`      |
| --------------------- | ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | --------------- | -------------- |
| `none`                | `NoneMemory` (`none_system.py`)                              | No-memory control (condition A)                                                                                                                                                                                                                                            | no               | no              | filesystem     |
| `oracle`              | `OracleMemory` (`oracle_system.py`)                          | Memory-sensitivity ceiling (condition B); harness-injected via `load()`, task-validity gate (`oracle ≈ no_memory` rejects the task)                                                                                                                                        | no               | no              | filesystem     |
| `filesystem`          | `FilesystemMemory` (`filesystem_system.py`)                  | Exact-by-id Markdown-file reference baseline; in-process dict when no `base_dir`                                                                                                                                                                                           | yes              | no              | filesystem     |
| `lexical`             | `LexicalTopKMemory` (`lexical_system.py`)                    | Deterministic token-overlap top-k ranker; the Confusion/Staleness probe (surfaces seeded distractors + superseded v1s that exact arms never touch)                                                                                                                         | yes              | no              | filesystem     |
| `consolidating`       | `ConsolidatingMemory` (`consolidating_system.py`)            | Two-speed arm (S1): O(1) hot write, offline `consolidate()` clusters by salience; `mode="recombine"` abstracts a schema row, `mode="dedupe_only"` is the no-abstraction control. Tombstone-only subtraction (M7 reversibility; a source-scan test enforces no hard delete) | yes              | no              | filesystem     |
| `retention_scheduled` | `RetentionScheduledMemory` (`retention_scheduled_system.py`) | Scheduled-disposition arm (S3): class-at-write, offline sweep applies `RETENTION_POLICY` (permanent/review/destroy/archive), legal-hold pins, unknown class RETAINED. Archive crosses the irreversibility boundary; the wrongful_destruction gate scores exactly that      | yes              | no              | filesystem     |
| `ours`                | `OursMemory` (`ours_system.py`)                              | Retrieval-v1 over the work-audit graph via `mem retrieve --json`; replay-only, ORACLE-triggered (D23). See §4                                                                                                                                                              | no               | yes             | kg             |
| `ours-issue-trigger`  | `OursIssueTriggerMemory` (`ours_system.py`)                  | D23 separable trigger control: same surface, query formed WITHOUT trace errors (`--no-trace-query`)                                                                                                                                                                        | no               | yes             | kg             |
| `ours-live`           | `OursLiveMemory` (`ours_live_system.py`)                     | `ours` READ + forward-capture WRITE through the firewalled `mem memory-event record` CLI. See §5                                                                                                                                                                           | yes              | yes (inherited) | kg (inherited) |
| `builtin`             | `BuiltinMemory` (`builtin_system.py`)                        | The agent's native memory as the baseline-to-beat (Decision 22 D-F); deliberately a no-store arm, NOT a relabelled `none`. See §6                                                                                                                                          | no               | no              | filesystem     |
| `mem0`                | `Mem0Memory` (`mem0_system.py`)                              | mem0ai vector-store adapter (`infer=False`: 1 write = 1 memory)                                                                                                                                                                                                            | yes              | no              | vector_db      |
| `a-mem`               | `AMemMemory` (`amem_system.py`)                              | A-MEM Zettelkasten adapter (ChromaDB + local LLM; L2 distance normalized)                                                                                                                                                                                                  | yes              | no              | vector_db      |
| `nat`                 | `NatMemory` (`nat_system.py`)                                | NeMo Agent Toolkit `MemoryEditor` adapter (async, via `AsyncClientBridge`)                                                                                                                                                                                                 | yes              | no              | vector_db      |
| `graphiti`            | `GraphitiMemory` (`graphiti_system.py`)                      | Graphiti temporal-KG adapter (async; `clear` is a no-op, isolation = fresh `group_id` per trial, §5b decision)                                                                                                                                                             | yes              | no              | kg             |
| `nemo-embed`          | `NemoEmbedMemory` (`nemo_embed_system.py`)                   | Plain dense NeMo embedder + exact in-process cosine top-k; a second neural BASELINE next to mem0, explicitly NOT an `ours` upgrade (mem-sikg)                                                                                                                              | yes              | no              | vector_db      |

`build_memory_system(name, **kwargs)` is the factory; an unknown name raises
`ValueError` listing the wired set (never a silent default).

Design fence: embeddings are never folded into the deterministic `ours`
retriever (`docs/architecture-decisions.md` Decision 21, Stephanie 2026-06-23:
"NeMo dense embedder is a BASELINE retrieval arm, not an `ours` upgrade";
the full feasibility ADR `docs/mem-nemo-retriever-agentic-feasibility.md`
exists only on the unmerged spike branch `mem-i54s-nemo-spike` @ `ead1055`,
NOT on `main`); the
agentic LLM-refinement loop and ColBERT/vision-language backends are
deliberately deferred. Do not "upgrade" `ours` with a semantic tier; add a new
arm instead.

## 3. How the harness drives arms (three surfaces)

Run mechanics live in mem-eval-harness-run; this section covers only what an
arm sees.

**a) Convention-sequence runner** (`membench/runner/conditions.py`
`run_sequence`): per condition, `reset(condition_root)` once, then per step:
seed distractors (memory_enabled only, telemetry-discarded), `retrieve` only
when the step declares `expected_memory_reads` (no phantom events), agent
runs, then `write` each performed write (plus `assign_class` for
`Classifiable` arms). After all steps, a `ConsolidationCapable` arm gets one
offline `consolidate()` pass. A crashed step aborts loudly; partial sequences
are never recorded as trials.

**b) Replay path** (`membench/replay.py` + `membench replay` CLI):
failure-triggered arms over the real store under the LOO guard. Scope-using
arms run under BOTH Decision-7 tracks (`cross_rig`, `same_rig_temporal`),
reported separately. Per arm: `reset`, `retrieve(query_work, scope)`,
`assert_no_leak` on the output, and an `ArmReplayResult` carrying the D10
guard fields, `injected_context_chars`, and `eligible_count` (so an empty
result is distinguishable from an empty corpus).

```bash
# from memory-bench/ ; store must be built --with-traces for ours to fire
python3 -m membench.cli replay \
  --store ../.mem/store.db --work-id <work_id> --arms none,ours --out reports/
# flags: --store (required) --work-id (required) --arms (default none,ours)
#        --mem-bin (default ../bin/mem) --limit --out (default reports/)
```

CLI wiring caveat (verified at `4e819e1`): `_build_arm` in `membench/cli.py`
special-cases only `ours` (giving it `store_path`/`mem_bin`/`limit`); every
other name goes through the bare factory. So `--arms ours-issue-trigger` or
`--arms ours-live` constructs an unwired arm that raises
`"OursMemory needs a store_path"` on first retrieve. The issue-trigger control
runs through the bundle/probe-gate grid path (§4), not `membench replay`.

**c) Harbor bundle grid** (`membench/harbor/probe_gate.py`): the ours-family
conditions are realized as baked task images, not live arm calls:
`none` / `oracle` / `none-clean` / `ours` / `ours-issue-trigger` / `shuffled`
(volume-matched donor-payload placebo, mem-hhto) / `raw-trajectory` /
`full-context`. The caller resolves `ours_payloads` via the mem CLI and the
gate bakes them in; `CONDITION_TRIGGER` persists `trigger` into task.toml
metadata. Grading of these conditions: mem-grading-and-validity-gates.

### Launch-time arm selection (env contract)

From `membench/runner/conditions.py`, verified:

| Env var                      | Meaning                                                                                                                                                                         | Failure mode                                                                             |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `MEMBENCH_MEMORY_SYSTEM`     | Overrides `experiment.memory.system` for the memory_enabled condition at the process boundary. Pilot values: `none`, `ours`, `ours-live`, `builtin`; any wired name is accepted | Unwired name raises at launch, never a silent fallback                                   |
| `MEMBENCH_MEM_BIN`           | Path to the `mem` CLI; required when the override selects `ours` or `ours-live`                                                                                                 | Missing/blank raises a loud LAUNCH error (mem-ymxp #4), not a deferred first-use failure |
| `MEMBENCH_MEM_STORE`         | Path to the LOO-bounded store; required for `ours` / `ours-live`                                                                                                                | Same loud-launch semantics                                                               |
| `MEMBENCH_TRIAL_TIMEOUT_SEC` | Per-coroutine wall-clock budget for async backends (`AsyncClientBridge`); unset = unbounded                                                                                     | Non-numeric or non-positive raises `ValueError` at the boundary                          |

NO_MEMORY and ORACLE_MEMORY are fixed controls and ignore the override. When
the override changes the arm, telemetry reports the arm name instead of the
now-wrong `memory_config_id`. Arms the factory cannot build from config alone
(e.g. `ConsolidatingMemory` with an injected summarizer) use the
`memory_system=` injection seam on `run_sequence` instead.

## 4. The ours family and D23 (trigger labeling)

`ours` delegates to the TypeScript retrieval-v1 surface through
`mem retrieve <work_id> --scope cross-rig|same-rig --store <db> --json`
(one substrate, no second store; consumes append-only `lessons`, D9). It is
replay-only (`supports_write = False`; the post-task write interface is D14,
out of scope here) and raises if called without `query_work` + `scope`.

**Decision 23 (2026-07-04, bead mem-tnyo), the fact to internalize:** in
replay, the default query is built by `queryFromRecord` from the held record's
OWN stored trace errors, i.e. failures the fresh agent has not yet produced.
That is an ORACLE trigger, not the deployed failure-triggered flow, and the
arm is relabelled `ours-oracle-triggered` everywhere it is REPORTED (the
condition key stays `ours` for existing readers; a `trigger: "oracle"` field
rides run conditions, arm provenance, and summaries as `arm_trigger`).

|                 | `ours`                                                | `ours-issue-trigger`                                          |
| --------------- | ----------------------------------------------------- | ------------------------------------------------------------- |
| Class attr      | `OursMemory.trigger == "oracle"`                      | `OursIssueTriggerMemory.trigger == "issue-text"`              |
| Query source    | held record's stored trace errors (`queryFromRecord`) | title/task-type text only (fields available at dispatch time) |
| CLI flag        | (default)                                             | `mem retrieve --no-trace-query`                               |
| Everything else | identical surface, strip, injection, leak guards      | identical                                                     |

The subclass sets `no_trace_query = (trigger == "issue-text")` on the
`OursQuery`; nothing else differs, so the trigger-information contribution is
measurable on its own. D23 also extends the relaxed-signature self-leak scan
to both ours payloads as a persisted covariate (`signature-overlap.json`),
report-only, while the oracle payload keeps its hard guard. Held signatures
are the canonical TS-computed strings surfaced via the retrieval envelope
(`query_signatures` / `query_signatures_relaxed`), never recomputed in Python.

Failure semantics: a failed `mem retrieve` (missing binary, malformed
envelope, non-zero exit) RAISES through `mem_cli.run_mem_json`; it is never
treated as "no memory".

Settled negative (do not retry): running the ours arm on the codeprobe corpus
is HALTED; its trace substrate is unrecoverable
(`docs/mem-bxhh3-ours-substrate-data-wall.md`). Details:
mem-failure-archaeology.

## 5. ours-live (forward capture)

`OursLiveMemory` subclasses `OursMemory`: READ is byte-identical (LOO-bounded
replay retrieval); WRITE emits a `memory_event` tagged
`source='forward-capture'` through the canonical `mem memory-event record` CLI
only. It never writes SQLite from Python and never ports the TS schema, so
`MemoryEventSchema.strict()` governs every captured field. A failed emit
raises. `seed()` is overridden to a no-op because harness distractor seeding
must never masquerade as an agent capture (mem-zt1c).

Construction contract: `build_memory_system("ours-live")` with no kwargs
builds an arm that RAISES on first use, by design. The runnable paths are the
`memory_system=` injection seam or `MEMBENCH_MEMORY_SYSTEM=ours-live` +
`MEMBENCH_MEM_BIN` + `MEMBENCH_MEM_STORE` (fail-fast at launch). The scope
stays at failure-triggered capture (the same events replay `ours` is scored
on), deliberately not every tool call (YAGNI, per the module header).

## 6. builtin (the native-memory baseline)

`BuiltinMemory` is a no-store arm: `retrieve` returns no payloads,
`supports_write = False`, and the event carries the `builtin` label so
telemetry attributes results to the right condition. It is NOT `none`: under
`builtin` the agent's own Claude/Codex native memory is the continuity
channel (opaque to mem, enabled at agent launch); under `none` no memory
exists at all. The two encode different conditions and are deliberately
separate classes.

Operational precondition (internal orchestration; PROVISIONAL pending
Stephanie, Q1 placement answer): enabling the agent's native memory is the
RUN's job, done at launch by the orchestrator's pool/agent config (the code
comment names `gc agent add`), not by anything in this repo. Fleet mechanics:
mem-git-and-dispatch-workflow.

## 7. Third-party adapters and the CI fake discipline

### The seam

All semantic arms are `AbstractSemanticArm` subclasses
(`membench/memory_systems/semantic_base.py`) that set `name` + `backend` only.
The base translates the uniform contract onto an injected client:

```python
class SemanticMemoryClient(Protocol):   # sync on purpose
    def store(self, *, scope: str, content: str, memory_id: str) -> str: ...  # returns BACKEND-minted id
    def query(self, *, scope: str, query_text: str, top_k: int) -> Sequence[SemanticHit]: ...
    def clear(self, *, scope: str) -> None: ...
```

Load-bearing seam rules (all decided, do not re-litigate):

- `scope` = `ctx.trial_id`, mapped to each backend's native isolation key
  (mem0/NAT `user_id`, Graphiti `group_id`, A-MEM per-trial collection). The
  base asserts trial ids are globally unique (mem-lvp.12); a reuse raises.
- `store` returns the backend-assigned id (mem0/A-MEM/Graphiti mint their
  own); write events record `target_ids=[requested]` vs `written_ids=[assigned]`.
- `SemanticHit.score` is normalized higher-is-better by the CLIENT, or `None`
  (Graphiti) in which case list order is trusted. L2 distances go through
  `l2_distance_to_similarity` which rejects NaN/negative/inf loudly.
- No Protocol widening (§5b of `docs/competitive-arms-integration.md`): the
  seam stays at `store`/`query`/`clear`. Resource-holding clients additionally
  implement the separate `ClosableClient` protocol.
- Graphiti reset strategy (mem-lvp.11): `clear` is a NO-OP; isolation comes
  from a never-reused per-trial `group_id`, not a destructive purge.
- Async backends (NAT, Graphiti) wrap one `AsyncClientBridge` each: one
  private event loop per bridge, never `set_event_loop`, warm connections
  across calls, idempotent `close()`. Per-call budget via
  `MEMBENCH_TRIAL_TIMEOUT_SEC` (`trial_timeout()`).

### CI runs fakes, never SDKs

Every SDK import is lazy (module import needs no SDK; mypy overrides in
`memory-bench/pyproject.toml` silence the unstubbed imports). Arm tests inject
`tests/semantic_fakes.FakeSemanticClient`, a deterministic in-memory client
that faithfully models the two traits that bit the real backends: it mints its
own ids and isolates by scope. Note the mechanism: the arm tests do NOT use
`pytest.importorskip` (that pattern belongs to the Harbor/NeMo tests); they
simply never construct a real client. The `ours` integration tests skip
gracefully without the TS build (`tests/paths.py require_mem_cli`: node,
`dist/`, `bin/mem`, `node_modules`).

### The real (self-hosted) stack

`membench/memory_systems/local_stack.py` (`LocalModelStack`,
`STACK_VERSION = "2"`) is the single source of truth for the no-paid-API local
models, now THREE embedder modalities plus one chat model (the §7 table in
`docs/competitive-arms-integration.md` predates the third; the code is
authoritative):

| Pin                         | Env override                      | Consumed by                                                                                                                                               |
| --------------------------- | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| chat/instruct model         | `MEMBENCH_LOCAL_CHAT_MODEL`       | mem0, A-MEM ingest (Ollama)                                                                                                                               |
| Ollama embedder             | `MEMBENCH_LOCAL_EMBED_MODEL`      | mem0                                                                                                                                                      |
| sentence-transformers model | `MEMBENCH_LOCAL_ST_MODEL`         | A-MEM, NAT (in-process)                                                                                                                                   |
| NeMo dense embedder         | `MEMBENCH_LOCAL_NEMO_EMBED_MODEL` | nemo-embed (default pin is the permissive `nvidia/llama-nemotron-embed-1b-v2`, a redistribution-clean license choice; the NC backend is a one-line repin) |

`LocalModelStack.preflight()` is the only network call: it verifies the
Ollama daemon + pulled models and raises `LocalStackUnavailableError` with the
exact `ollama pull` to run. A real run fails fast at the boundary; a backend
never silently degrades to a paid API (the A-MEM factory pins
`llm_backend="ollama"` precisely because its upstream default is OpenAI).
`telemetry_dict()` records the pinned identity per run (V2 confound control).
No-paid-API scope is Decision 16: memory stack only; the OAuth
agent-under-test, Harbor, and Docker are not paid infra.

## 8. How to add an arm (checklist)

Read `docs/competitive-arms-integration.md` first (note two dated spots,
verified 2026-07-07: its "`_DEFERRED` registry" mention is stale, the registry
is now `_systems_registry()` with all arms wired; and its §7 stack table
predates the third embedder modality).

1. **Pick the family.** Query/top-k over harness-provided text: subclass
   `AbstractSemanticArm`. Failure-triggered over the work-audit graph: model
   on `OursMemory` (delegate to the mem CLI; do not reimplement retrieval or
   open a second store). Everything else: implement `MemorySystem` directly
   with `filesystem_system.py` as the template.
2. **Semantic arm = client + two attrs.** Write a `_YourClient` adapting the
   SDK to `SemanticMemoryClient` (return the backend-minted id from `store`;
   normalize scores higher-is-better or return `None`; `clear` one scope
   only). The arm subclass sets `name` + `backend` and NO retrieval logic.
   Async SDK: hold an `AsyncClientBridge` inside the client, implement
   `ClosableClient.close()`, thread `trial_timeout()` through.
3. **Lazy real client.** Build the real SDK client only inside a
   `default_<arm>_client()` factory; module import must need no SDK, no
   network. Map model names from `LocalModelStack` (never hardcode), and add
   the SDK to the mypy `ignore_missing_imports` override block in
   `memory-bench/pyproject.toml`.
4. **Register.** Add one entry to `_systems_registry()` in
   `membench/memory_systems/__init__.py` (plus the import and `__all__`).
   `wired_memory_systems()`, the factory error message, and the
   `MEMBENCH_MEMORY_SYSTEM` validation all follow automatically. If the arm
   needs launch-path construction kwargs, mirror the `_LIVE_OURS_SYSTEMS`
   resolution in `runner/conditions.py` (fail loud at launch), or rely on the
   `memory_system=` injection seam.
5. **Tests ship with the arm** (same commit): a deterministic fake client
   (reuse `tests/semantic_fakes.FakeSemanticClient` or add a dedicated fake
   modeling your backend's id/score quirks), `tests/test_<arm>_system.py`
   mirroring `test_mem0_system.py`, and a construction test in
   `tests/test_memory_systems.py`. CI must stay network-free and model-free.
6. **Gates before claiming done** (both halves if you touched TS):
   `cd memory-bench && ruff check membench tests && black --check membench
tests && mypy --strict membench && python3 -m pytest -q`.
7. **Fences.** Never let the arm own or relax the LOO boundary
   (`validity.py` and its use in `replay.py`/`runner` are harness territory;
   changes there are HALT-branch-ready, PROVISIONAL pending Stephanie, Q4).
   Never widen `SemanticMemoryClient`. Never add semantic/keyword heuristics
   to make an arm's plumbing "smarter" in the deterministic layers (ZFC:
   mem-deterministic-extraction-zfc). Real-backend provisioning
   (Ollama/Qdrant/Chroma/Redis/FalkorDB) is downstream of green CI, never a
   CI dependency.

The outbound direction (shipping retrieval-v1 as a NAT memory-provider
plugin, `docs/competitive-arms-integration.md` §2) is a documented candidate,
not built; treat it as open, not planned work you can assume.

## 9. Results status (as of 2026-07-07)

No arm-comparison number from this repo is publishable: the `mem-0rrf`
publication freeze is in force and headline/real-corpus numbers are a
Stephanie release call. When you read arm results, read them with their
validity caveats attached; e.g. the graded 3-arm grid
(`docs/mem-apg.9-graded-3arm-grid.md`) reports its own headline as NULL,
underpowered (N=2 admitted, 1 retrieval-fired). The settled real-corpus
context (oracle-validity wall, replay-engine null) lives in
mem-failure-archaeology; the metrics and gates that turn a run into a
defensible number live in mem-grading-and-validity-gates.

## Provenance and maintenance

Authored 2026-07-07 against `/home/ds/projects/mem`, branch `main`, HEAD
`4e819e1` (checkout was on main). Facts most likely to drift: the wired arm
roster, the pilot-values list, the replay CLI flags, and the local-stack pins.
Re-verify with:

```bash
git -C . log -1 --format='%h %s'                              # compare against 4e819e1
cd memory-bench && python3 -c "from membench.memory_systems import wired_memory_systems; print(wired_memory_systems())"
python3 -m membench.cli replay --help                          # flag surface
grep -n 'trigger' membench/memory_systems/ours_system.py       # D23 labels
grep -n '_PILOT_SYSTEMS\|ENV_MEM' membench/runner/conditions.py
grep -n 'STACK_VERSION\|ENV_' membench/memory_systems/local_stack.py
python3 -m pytest --collect-only -q tests/test_memory_systems.py | tail -1
python3 ../.claude/skills/mem-competitive-arms/scripts/arm-roster.py   # full trait table
```
