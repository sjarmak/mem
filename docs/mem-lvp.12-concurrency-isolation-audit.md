# Concurrency-isolation audit (mem-lvp.12)

Scope: does the membench harness share one process / backend state across trials
and arms, and where would concurrent execution (or a shared real backend)
cross-contaminate results? This audit **gates real-arm provisioning** of the
competitive arms (mem0, A-MEM, NAT, Graphiti — mem-lvp.5). It does *not* gate
their CI fakes, which are in-process, hermetic, and already green. Companion to
`docs/competitive-arms-integration.md` §5–6 and `ARCHITECTURE.md` Decision 11.

> **Bottom line.** Execution is single-process, single-threaded, fully sequential
> today, so no contamination is *currently* reachable. But isolation rests on a
> **soft contract** — `reset(trial_id)` + a `scope = ctx.trial_id` native-key
> partition that the harness *assumes* the backend honors. The moment a real arm
> points at shared infra (one Qdrant path, one Chroma dir, one FalkorDB,
> one Ollama daemon), that soft contract is the only thing standing between two
> trials and a silent cross-write. **Serialize per arm and make every real client
> own its store path / collection / group_id; do not parallelize trials until
> namespace isolation is proven per backend.**

> **Status update (2026-06-14).** The highest-priority gap below — the mem0
> store-path constant (rec. 2, failure modes 1 & 7) — **landed** at `43934c7`:
> `mem0_system.py` now injects a unique per-run store path
> (`default_mem0_store_path()`, `MEMBENCH_MEM0_STORE_DIR` override) instead of the
> shared `/tmp/membench-mem0-qdrant` constant. The async-arm gates also cleared:
> mem-lvp.10 (`AsyncClientBridge`, one loop per instance — rec. 5) and mem-lvp.11
> (Graphiti fresh-`group_id`-per-trial — failure mode 6) are both resolved. Rec. 4
> also landed at `b593ce8`: `AbstractSemanticArm.reset()` now records each
> `trial_id` and raises on reuse (a per-arm uniqueness guard, so a duplicate scope
> fails loud instead of silently merging two trials). The one gap remaining before
> a real mem0/A-MEM run is per-run namespace injection for the **A-MEM** Chroma
> collection (still `membench_<scope>`, not run-id-scoped — failure mode 7 across
> runs); mem0's equivalent already landed above.

## 1. Current execution model — single process, sequential, fresh instance per condition

One Python process, one thread, no async on the run path. Both entry points loop:

- **Sequence runner** (`membench/runner/conditions.py:108`): `for condition in
  conditions:` → `for step in seq.steps:` (`:115`). Conditions run in series,
  steps in series. A trial is one `(step × condition)` pair (`StepTrial`,
  `:26`).
- **Replay runner** (`membench/replay.py:121`): a single list comprehension
  `[replay_arm(arm, ...) for arm in arms for scope in _scopes_for(arm)]` — arms
  and D7 tracks run strictly in series.
- **CLI** (`membench/cli.py:57`, `:97`): one `run_sequence` / `run_replay` call
  per invocation, results written after the loop completes. No pool, no
  `asyncio.gather`, no subprocess-per-trial. (The only `ThreadPoolExecutor` in
  the tree is `oracle/consensus.py:283`, an LLM-judge fan-out unrelated to arm
  execution.)

**Instance lifecycle is per-condition, not per-trial.** `_system_for`
(`conditions.py:78`) calls `build_memory_system(...)` **once per condition**
(`:109`), then `system.reset(condition_root)` **once** (`:111`). The *same*
instance is then reused across every step in that condition; there is no
`reset()` between steps. Continuity across steps is the whole point — the store
*is* the cross-step channel (`conditions.py:1-7`). In replay, a fresh `reset`
runs per `replay_arm` call (`replay.py:89`), i.e. per `(arm × scope)`.

Consequence for real arms: a real backend client is constructed once per
`build_memory_system` call and lives for the whole condition/replay loop. Its
embedder, LLM handle, connection pool, and (for A-MEM) in-process scope map are
**shared across every trial in that loop**. That is fine sequentially; it is the
contamination surface the moment anything runs concurrently or two loops share
one backend.

## 2. Isolation guarantees that exist today

| Layer | Mechanism | Enforced or assumed |
|---|---|---|
| Sequence trial | `condition_root = f"{seq}-{condition.value}"`, one `reset` per condition (`conditions.py:110-111`) | **Enforced** for fs (`filesystem_system.py:34` wipes the trial dir); **no-op** for `none`/`oracle`/`ours` (their `reset` returns `None`). |
| Replay trial | `trial_id = f"{work}-{arm}-{scope}"` + `reset` per call (`replay.py:84-89`) | Enforced as a call, but `reset` is a no-op for `ours` (`ours_system.py:114`) — isolation there comes from the **LOO boundary**, not reset. |
| Semantic arm scope | `scope = ctx.trial_id`; `store`/`query`/`clear` all pass `scope=ctx.trial_id` (`semantic_base.py:92,102,125`) | **Assumed.** The base *passes* the scope; whether two scopes are actually disjoint is the **client's** responsibility (`semantic_base.py:71-72`: "never touches other scopes"). |
| Native-key partition | mem0 `user_id` (`mem0_system.py:86,103,110`), NAT `user_id`, Graphiti `group_id`, A-MEM per-scope collection (`amem_system.py:88-94`) | mem0: `delete_all(user_id=scope)` "scrubs one user only" (`:13`) — **trusts** mem0's filter. A-MEM: holds **one native instance per scope** in `self._scopes` (`amem_system.py:84,115-119`) — isolation is the side map, not the DB. |
| LOO leak guard | `assert_no_leak(payloads, corpus, query)` re-checks every arm output (`replay.py:92`, `validity.py:127`) | **Enforced, independent of the arm.** Catches *temporal* future-leak; does **not** catch *trial-to-trial* cross-contamination within the eligible set. |

Key gap visible already: the LOO guard (`validity.py`) is the only *enforced*
post-hoc audit, and it only checks temporal leakage (work the arm shouldn't see
because it post-dates `B.started`). It would **not** flag a trial reading another
trial's writes if both live inside the LOO-eligible window — that class of bug is
entirely on the soft `scope` contract.

## 3. Contamination failure modes (ranked: likelihood × blast radius)

| # | Mode | Trigger | Blast radius | Current guard | Gap |
|---|---|---|---|---|---|
| 1 | **Shared physical vector store across instances** | Two `Mem0Memory` (or `AMemMemory`) instances built in one process — different conditions, or a future parallel sweep — both default to the same on-disk path. `LOCAL_CONFIG["vector_store"]["config"]["path"] = "/tmp/membench-mem0-qdrant"` is a **hardcoded constant** (`mem0_system.py:38`). | **Catastrophic, silent.** Both write into one Qdrant collection. `user_id`/`scope` filtering is the *only* separation; a filter bug or a non-unique trial_id merges two trials' memories. Corrupts every comparison. | Per-`user_id` filter inside mem0 (trusted, not verified by harness). | No per-instance store path; no harness assertion that two arms don't share a path; no check that trial_ids are globally unique across a sweep. |
| 2 | **Concurrent trials, one client, shared backend state** | Any future parallelization of the `for step`/`for arm` loops, or running two `membench` processes against the same `/tmp` path. | **Catastrophic.** mem0/Chroma/FalkorDB writes interleave; A-MEM's `self._scopes`/`self._minted` dicts (`amem_system.py:84-86`) are mutated without locks → lost updates, wrong minted-id mapping. | None — code assumes serial execution (`conditions.py:1-7` "fresh agent context … only continuity channel"). | No locking, no per-trial process/namespace boundary, no statement that trials *may not* run concurrently. |
| 3 | **Shared embedder / LLM client mutable state** | All trials in a condition share one mem0/A-MEM instance (§1), which shares one Ollama handle (`mem0_system.py:36-49`, `amem_system.py:128`). Under concurrency, or if the SDK caches per-call state, embeddings/extractions cross trials. | **High.** Wrong embeddings → wrong retrieval → quietly wrong precision/recall, no crash. | None. Ollama is a shared daemon (mem-lvp.5 provisions one). | No guarantee the embedder/LLM is stateless per call; no per-arm model isolation. |
| 4 | **A-MEM in-process note graph shared across trials** | A-MEM links/evolves notes *within* a native instance. The adapter holds **one instance per scope** (`amem_system.py:88-94`), so within a trial that is correct — but the `_AMemClient` object itself (the `self._scopes` map) is **shared across all trials in the condition**. | **Medium-high.** If two trial scopes ever collide (non-unique `ctx.trial_id`) they share a note graph and auto-link across trials. Single-instance-per-condition + reuse makes collision the live risk. | Per-scope keying in `self._scopes` (`:88`). | Relies entirely on `ctx.trial_id` global uniqueness; no assertion of it. `clear(scope)` drops the instance (`:115-119`) but is only called on `reset`, never between steps. |
| 5 | **Single held event loop shared across async arms** (NAT, Graphiti) | mem-lvp.10 `AsyncClientBridge` wraps an async SDK in one persistent loop inside the concrete client (`semantic_base.py:56-58`). If that loop/connection is shared across instances or entered re-entrantly under any concurrency, calls serialize incorrectly or deadlock. | **Medium** (these arms not yet wired — `_DEFERRED`, `__init__.py:56-60`). High once they are: one loop = a global serialization point and a shared connection-pool contamination vector. | The Protocol is sync by design so the loop is *internal* (`semantic_base.py:56`); not yet exercised. | No spec for *one bridge/loop per instance* vs shared; mem-lvp.10 must pin this. |
| 6 | **Graphiti `group_id` reset semantics** | Graphiti isolates by `group_id`; reset strategy is "fresh `group_id` per trial" (recommended, mem-lvp.11) but unbuilt. A reused `group_id` accumulates across trials. | **Medium** (deferred arm). | Recommendation exists (integration doc §6, mem-lvp.11); no code. | Decision still open; must land before Graphiti provisioning. |
| 7 | **`/tmp` path survives across runs** | mem0's Qdrant path is `on_disk` under a fixed `/tmp` dir (`mem0_system.py:38`). A second `membench` invocation reuses leftover state from the first. | **Medium, silent.** Stale memories from a prior run bleed into a new run's trials. | `clear(scope)` only scrubs scopes the run touches; never drops the collection (`mem0_system.py:13` notes `reset` does *not* drop the collection). | No run-scoped/ephemeral store dir; no teardown. |

## 4. The gap — what the harness does NOT guarantee that real arms need

1. **Global uniqueness of `ctx.trial_id`.** Every isolation mechanism (mem0
   `user_id`, A-MEM scope map, NAT `user_id`, Graphiti `group_id`) keys off it
   (`semantic_base.py:92,102,125`). Nothing asserts two trials can't produce the
   same `trial_id`. `condition_root` (`conditions.py:110`) is unique per
   `(sequence, condition)` and `trial_id` appends `step_id` — unique *within one
   run*, but **not** across concurrent runs sharing a backend (failure mode 1/7).
2. **Per-instance physical store isolation.** The store path / collection root is
   a **module constant** (`mem0_system.py:38`), not injected per instance. Two
   instances in one process, or two processes, collide.
3. **A serialization contract.** The runner's docstrings *assume* serial
   execution but never state trials **must not** run concurrently. There is no
   lock, no per-trial boundary — so the assumption is invisible to whoever wires
   the real backends.
4. **Backend teardown / ephemerality.** `reset` clears a *scope*, never the
   *collection* (`mem0_system.py:13`); there is no run-scoped store dir or
   teardown, so state survives across runs.
5. **A no-cross-trial-read audit.** `assert_no_leak` checks temporal leak only
   (`validity.py:127`); nothing audits that trial A never surfaced trial B's
   writes.

## 5. Recommendation for real-arm provisioning (mem-lvp.5)

Actionable contract for whoever provisions the real backends:

1. **Serialize per arm. Do not parallelize trials within an arm.** Keep the
   existing single-process sequential loops. The continuity-across-steps design
   (`conditions.py:1-7`) and the shared-instance lifecycle (§1) are only safe
   serially. If throughput is needed, parallelize **across arms** (one process
   per arm), never across trials inside one arm's backend.

2. **One physical store per arm instance, injected — not the module constant.**
   Make the store path / collection / `group_id` root a **constructor argument**
   to each concrete client, derived from a run id, e.g.
   `/tmp/membench-<run_id>-mem0-qdrant`, A-MEM `collection_name=membench_<run_id>_<scope>`
   (already scope-suffixed at `amem_system.py:134` — add the run id), Graphiti
   `group_id = <run_id>:<trial_id>`. This removes failure modes 1 and 7 outright.
   Replace the hardcoded `LOCAL_CONFIG[...]["path"]` (`mem0_system.py:38`)
   accordingly.

3. **Process-per-arm for the real run; fresh-namespace-per-trial within it.**
   Real-arm isolation strategy = **process-per-arm** (isolates embedder/LLM/loop
   state, failure modes 3/5) **× per-trial native-key namespace** (`scope =
   ctx.trial_id`, already wired). Do **not** require a fresh container per
   trial — too heavy and unnecessary if (a) the store path is per-run and (b)
   trial_ids are unique. A fresh container **per arm** is the clean option if
   process isolation is insufficient for a given SDK.

4. **`reset(trial_id)` must guarantee an empty scope, and trial_ids must be
   globally unique.** For real arms, `reset`/`clear(scope)` must leave that scope
   readback-empty (mem0 `delete_all` ✓; A-MEM drop-instance ✓; Graphiti needs a
   real per-`group_id` purge or the fresh-`group_id`-per-trial decision,
   mem-lvp.11). Add a **harness-level uniqueness assertion** on `trial_id` per run
   so collision (the root cause behind modes 1/4) fails loud instead of merging
   silently.

5. **No Protocol change needed** — the `SemanticMemoryClient` seam
   (`semantic_base.py:46-72`) already scopes every call by `ctx.trial_id` and
   `clear` is already specced as "reset one scope only … never touches other
   scopes" (`:71-72`). The gap is **enforcement of that contract inside the real
   clients**, plus the injected store path (rec. 2). Pin the AsyncClientBridge
   (mem-lvp.10) to **one loop per client instance**, never a shared/global loop
   (failure mode 5).

6. **Add a run-scoped teardown** (drop the per-run store dir / collection /
   group_ids on completion) so state never survives across runs (failure mode 7).

### Verdict for the gate
**Real-arm provisioning is safe to proceed for the two sync arms (mem0, A-MEM)
only after** rec. 2 (injected per-run store path) and rec. 4 (trial_id
uniqueness assertion) land — both are small, harness-side changes, no Protocol
churn. The async arms (NAT, Graphiti) additionally gate on mem-lvp.10 pinning
one-loop-per-instance (rec. 5) and mem-lvp.11's `group_id` reset decision. The
mem0 store-path fix — formerly the highest-priority blocker, when two real-arm
instances shared `/tmp/membench-mem0-qdrant` — **landed at `43934c7`** (see the
status update at the top); rec. 4 (`trial_id` uniqueness) landed at `b593ce8`, and
rec. 5 + the Graphiti decision are also resolved. The one remaining gate before a
real mem0/A-MEM run is per-run namespace injection for A-MEM's Chroma collection
(run-id-scoped, not just `membench_<scope>`).
