# Competitive memory-system arms: integration design

Scope: how mem0, Zep, A-MEM, and the NVIDIA **NeMo Agent Toolkit (NAT)** plug
into the membench harness as competitive arms (mem-lvp), how they compare to
`ours` (retrieval-v1), and the order to build them in. Companion to
`ARCHITECTURE.md` (Decision 11, the uniform-arm contract) and
`.gc/memory-eval-harness-spec.md` §14.

> **The seam already exists.** Every arm implements one 3-method contract
> (`MemorySystem`: `reset` / `retrieve` / `write`) and the harness owns the
> record set, the leave-one-out boundary, scope, and telemetry. The factory's
> `_DEFERRED` registry already names `nat`, `mem0`, `a-mem`, `graphiti` as
> pending arms. Adding one is a ~90-line class + a factory entry, with
> `filesystem_system.py` as the template — **not** a re-architecture.

## 1. The contract an arm implements

```python
class MemorySystem(ABC):
    name: str
    backend: MemoryBackend
    supports_write: bool = True
    uses_scope: bool = False          # True → harness runs it under D7 dual-track

    def reset(self, trial_id: str) -> None: ...
    def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult: ...
    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent: ...
```

`RetrievalRequest` carries **both** retrieval families so one signature serves
all arms:

- `query_text` + `requested_ids` — the **id/semantic** path (oracle, filesystem,
  and every competitive arm's native `search(query, top_k)`).
- `query_work` + `scope` — the **failure-triggered** path `ours` uses, under the
  harness-owned D6/D8 LOO boundary.

Every arm's output is re-checked against the boundary by
`validity.assert_no_leak`, so an arm cannot leak future work even if its own
index would have returned it. This is what makes a third-party system
(mem0/NAT/Zep) measurable on the *same* fairness terms as `ours`.

`RetrieveResult` already carries the Decision-10 precision-guard fields
(`total_matched`, `near_duplicate_top`, `fts_truncated`); competitive arms that
rank populate them, id-based arms leave them at default.

## 2. Two integration directions — both are real work, opposite arrows

### Inbound — benchmark them (the mem-lvp arms)
Wrap each external system as a `MemorySystem` arm so the harness measures
NAT/mem0/Zep/A-MEM head-to-head against `ours` and the reference baselines under
identical conditions. **`retrieve` → their `search`; `write` → their `add`;
`reset` → their `delete_all`/new-namespace.** This answers: *does a
general-purpose memory layer beat our failure-triggered, zero-API retrieval on
agentic dev-work?*

### Outbound — publish ours (adoption / distribution)
NAT is explicitly extensible ("Adding a Memory Provider"). We can ship
retrieval-v1 as a `nvidia-nat-mem` provider plugin implementing NAT's
`nat.memory.interfaces`, so NAT users get our failure-triggered memory as a
drop-in backend next to mem0/redis/zep. This is the inverse adapter — our store
behind *their* interface — and is the lowest-cost way to get the framework in
front of an existing agent-framework audience.

The two share a translation layer (our `MemoryEvent`/`RetrievalRequest` ↔ NAT's
`add_memory`/`get_memory`/`delete_memory` + `nat.memory.models`), so building the
inbound NAT arm de-risks the outbound provider and vice versa.

## 3. Per-system comparison

| System | What it is | Native API → our contract | Retrieval model | Infra to self-host | no-paid-API verdict |
|---|---|---|---|---|---|
| **mem0** | LLM-extracted fact store over a vector DB | `add`→`write`, `search`→`retrieve`, `delete_all`→`reset` | LLM salience-extract on write + vector top-k on read | vector store (Qdrant/in-mem) + **LLM + embedder** | OK **only** with a local embedder/LLM (Ollama/HF); OpenAI default ✗ |
| **Zep** | Temporal knowledge-graph memory (Graphiti core) | `memory.add`→`write`, `graph.search`→`retrieve` | KG fact extraction + temporal validity + graph search | Zep Cloud = SaaS (✗). Graphiti OSS = **Neo4j/FalkorDB + LLM** | Cloud ✗. Graphiti self-host OK but heaviest infra |
| **A-MEM** | Zettelkasten agentic memory; notes auto-linked + evolved | `add_note`→`write`, `search`→`retrieve` | embeddings + LLM-generated keywords/links, memory evolves over time | **embedder (sentence-transformers) + LLM** | OK self-hosted, moderate infra |
| **NAT** | NVIDIA agent framework; memory is a plugin layer | `add_memory_tool`/`get_memory_tool`/`delete_memory_tool` → `write`/`retrieve`/`reset` | delegates to its backend (mem0 / redis / zep) | the chosen backend's infra | depends on backend: NAT+redis+local-embedder OK; NAT+zep-cloud ✗ |

### Where `ours` sits in this table
`ours` is the **deterministic, zero-API, failure-triggered** point in the design
space: no LLM on the read path, no embedder, ranking is explicit tiered
arithmetic (signature → error-class → FTS). Every system above is
**semantic/embedding** retrieval with an LLM in the write path. So the headline
comparison is not "which retriever is better" in the abstract — it is **"does a
heavyweight semantic memory layer earn its cost over a cheap deterministic one on
recurring build/test/lint failures?"** That framing is the contribution; the
arms exist to make it measurable, and the precision/efficiency guards (D10) are
what keep "just retrieve everything" from winning.

## 4. The real blocker is infra, not adapter code

Every competitive arm needs a **local embedding model** (and usually a local
LLM) to honor the scix no-paid-API constraint — which is exactly why the mem-lvp
scope says "direct self-hosted" and "Redis/custom local backend." That shared
dependency, not the ~90 lines per adapter, is the bulk of the work.

**Idiomatic way to land arms before the infra exists** — mirror how
`distill/distiller.ts` injects its `DistillRunner` and how `filesystem_system.py`
defaults to an in-process dict:

> Build each arm with an **injectable client**. The `MemorySystem` subclass owns
> the contract translation, telemetry, and LOO re-validation; the actual
> mem0/NAT/Zep client is constructor-injected behind a narrow protocol. Tests run
> against a deterministic fake client (no network, no model), so the arm wiring +
> event normalization + no-leak revalidation all land and are CI-green **today**.
> The real client + local embedder plug in behind the seam when provisioned,
> with zero change to the harness or the contract.

This keeps the no-paid-API constraint intact (CI never calls a paid API), makes
the arm mergeable immediately, and isolates the heavy infra into one swappable
dependency per arm.

## 5. The reconciled client Protocol (mem-lvp.1, LANDED)

A spec-research pass over the four systems' *real* current APIs (mem0 main, NAT
`MemoryEditor`, `graphiti-core`, A-MEM) forced three corrections to the naive
seam before any arm was built — the shipped `SemanticMemoryClient`
(`membench/memory_systems/semantic_base.py`) is:

```python
class SemanticMemoryClient(Protocol):              # sync; async backends hold a loop inside
    def store(self, *, scope: str, content: str, memory_id: str) -> str: ...   # returns BACKEND id
    def query(self, *, scope: str, query_text: str, top_k: int) -> Sequence[SemanticHit]: ...
    def clear(self, *, scope: str) -> None: ...
```

- **`scope` (= `ctx.trial_id`)** — every backend isolates trials by a native key
  (mem0 `user_id`, NAT `user_id`, Graphiti `group_id`, A-MEM collection), so the
  arm scopes instead of dropping the store between trials.
- **`store` returns the backend-minted id** — mem0/A-MEM/Graphiti mint their own
  UUIDs and ignore the caller's `memory_id`; payloads key off the returned id, and
  the write event records `target_ids=[requested]` vs `written_ids=[assigned]`.
- **`SemanticHit.score` is `float | None`** — direction varies (cosine vs L2
  distance); the *client* normalizes to higher-is-better, and the base trusts list
  order when score is `None` (Graphiti).

`AbstractSemanticArm` implements `reset`/`retrieve`/`write` once against this; an
arm is pure construction-time wiring (which client + `top_k`). The harness keeps
the LOO boundary (`validity.assert_no_leak`).

### 5b. Graphiti reset strategy — DECIDED (mem-lvp.11)

Graphiti has no native group-wipe. Two ways to give it a per-trial clean slate
were on the table: (a) a Cypher `DETACH DELETE` over the trial's `group_id`,
which would force a driver `execute_query` hook onto the `SemanticMemoryClient`
Protocol; or (b) mint a fresh, unique `group_id` per trial and never reuse it.

**Decision: adopt (b) — fresh unique `group_id` per trial, never reused.**
Rationale:

1. **No Protocol widening** — keeps the seam at `store`/`query`/`clear` only; no
   driver `execute_query` hook leaks onto `SemanticMemoryClient`.
2. **Zero cross-trial leakage** — each trial writes into a brand-new `group_id`
   namespace, so no prior-trial nodes are reachable by construction.
3. **Simplest fake** — the CI fake just keys by `group_id` exactly like the real
   client; no destructive-delete path to emulate.

The per-trial namespace is `group_id = <run_id>:<ctx.trial_id>`, consistent with
the mem-lvp.12 concurrency-isolation audit: rec.2 (inject the store/namespace
per-run rather than sharing process-global state) and rec.4 (`trial_id` must be
globally unique). **Consequence for mem-lvp.4:** Graphiti's `clear(scope)` is a
no-op (or, equivalently, mints the next fresh `group_id`) — there is no
destructive purge, because isolation comes from the never-reused namespace, not
from deletion.

### 5c. AsyncClientBridge — the sync↔async seam (mem-lvp.10, LANDED)

The Protocol in §5 is **sync** on purpose, so the seam and the deterministic
fakes never go async. But two backends are async-native — NAT's `MemoryEditor`
and `graphiti-core` — so they need a sync adapter. That adapter is
`AsyncClientBridge` (`membench/memory_systems/async_bridge.py`): it holds one
persistent asyncio event loop and exposes `run(coro)`, so a concrete async client
calls `bridge.run(self._editor.add_items(...))` and satisfies the sync
`store`/`query`/`clear` contract.

Three design choices are load-bearing, and all trace back to the mem-lvp.12
concurrency audit:

- **One loop per bridge instance**, created with `asyncio.new_event_loop()` and
  held for the instance's lifetime — never a module-global or shared loop (audit
  failure mode #5: a shared loop is both a global serialization point and a
  shared-connection contamination vector across trials/arms).
- **Never `asyncio.set_event_loop()`.** The loop stays off the thread-global
  current-loop slot, so it can't be picked up by unrelated `get_event_loop()`
  callers. This is also why the bridge does **not** delegate to `asyncio.Runner`,
  which would otherwise be the obvious fit: `Runner.__init__` calls
  `set_event_loop()`, reintroducing exactly the global mutation the audit forbids.
- **Warm connection lifecycle.** Reusing one loop across many sequential `run`
  calls keeps the backend's Redis/graph-driver connection pool warm;
  `asyncio.run` per call would tear it down every time. `close()` drains async
  generators and the default executor before closing the loop (the same teardown
  `asyncio.run`/`Runner` perform) so streaming backends don't leak pending tasks
  or daemon threads.

The bridge is sequential-use only (matching the audit's serialize-per-arm
contract); an optional per-call timeout to bound a hung backend coroutine is
tracked as a follow-up (mem-lvp.13).

## 6. Build order & parallel front

`mem-lvp.1` (base + Protocol) was the only true serialization point. It, both
sync arms, and the async infra have since **landed and closed**; the two async
arms are the remaining frontier (✓ = closed):

```
mem-lvp.1 ✓ (base) ─┬─> .2  mem0      ✓ (sync, lightest, independent)
                    ├─> .10 ✓ AsyncClientBridge (infra) ─┬─> .3 NAT      ○ (async)
                    │                                      └─> .4 Graphiti ○ (async; + .11 reset decision ✓)
                    ├─> .9  A-MEM     ✓ (sync)
                    └─> .5  shared local model/embedder stack ✓ (consumed by all real clients — §7)
.6 metrics · .7 synthetic ○ · .8 dataset   — independent workstreams, parallel anytime
```

Sub-beads carved from the research, now resolved: **.9** (A-MEM, split out of the
old `.4`) ✓, **.10** (`AsyncClientBridge`, unblocks the two async arms — see §5c)
✓, **.11** (Graphiti reset-strategy decision — fresh `group_id` per trial, §5b) ✓,
**.12** (concurrency-isolation audit — gates *real-arm* provisioning of mem0/A-MEM,
not their CI fakes) ✓. Every arm's CI is model-free/network-free via the fake
client, so the Ollama/Qdrant/Redis/FalkorDB/Chroma infra only gates *real-arm
provisioning*, downstream of green CI.

**Frontier:** the two async arms, `.3` (NAT) and `.4` (Graphiti), both now
unblocked by `AsyncClientBridge` — each lands as a concrete `SemanticMemoryClient`
over the bridge plus fake-client tests. Real-arm provisioning of the sync arms has
largely cleared its mem-lvp.12 gates: the mem0 per-run store path (`43934c7`) and
the per-arm `trial_id` uniqueness guard (`b593ce8`) both landed; the one remaining
real-run gate is per-run namespace injection for A-MEM's Chroma collection.

## 7. The shared local model/embedder stack (mem-lvp.5, LANDED)

§4 named the real blocker: every semantic arm needs a **local embedder** (and
usually a **local LLM**) to honor the scix no-paid-API constraint, and that shared
dependency — not the per-adapter code — is the bulk of the work. mem-lvp.5 lands it
as **one source of truth** so the arms can't drift to different (or paid) models:
`membench/memory_systems/local_stack.py` →
[`LocalModelStack`](../memory-bench/membench/memory_systems/local_stack.py).

The phase-2.5-plan verdict (§"Shared self-hosted stack") is **one shared local
stack, two embedder modalities**:

| Field | Default | Consumed by | Modality |
|---|---|---|---|
| `chat_model` | `llama3` | mem0, A-MEM | Ollama-served instruct LLM (ingest) |
| `ollama_embedding_model` | `nomic-embed-text` | mem0 | Ollama-served embedder |
| `sentence_transformer_model` | `all-MiniLM-L6-v2` | A-MEM, NAT | in-process sentence-transformers |
| `ollama_base_url` | `http://localhost:11434` | mem0, A-MEM | daemon address |

Every field is env-overridable (`MEMBENCH_LOCAL_CHAT_MODEL`,
`MEMBENCH_LOCAL_EMBED_MODEL`, `MEMBENCH_LOCAL_ST_MODEL`,
`MEMBENCH_OLLAMA_BASE_URL`) so a run pins a stronger/weaker model without a code
change. Each arm's **real-client factory** maps the stack onto its backend config
via a small pure function, unit-tested with no SDK installed:

- **mem0** → `build_mem0_config(store_path, stack=…)` — Qdrant + Ollama embedder +
  Ollama LLM, the model names sourced from the stack (not hardcoded).
- **A-MEM** → `build_amem_kwargs(scope, stack=…)` — pins `llm_backend="ollama"`.
  **This closes a real no-paid-API leak:** A-MEM defaults `llm_backend="openai"`, so
  the pre-mem-lvp.5 factory silently routed ingest through a *paid* OpenAI call.
- **NAT** → its `RedisEditor` already embeds locally via sentence-transformers (no
  paid API by construction); the stack's `sentence_transformer_model` is the
  reference identity to pin when NAT's real RedisEditor is provisioned.

### Two reasons this is a module, not three hardcoded configs

1. **No-paid-API enforcement.** `LocalModelStack.preflight()` checks the Ollama
   daemon is up and the pinned models are pulled, and raises
   `LocalStackUnavailableError` with the exact `ollama pull …` to run. A real run
   **fails fast at the boundary** instead of a backend degrading to a paid API.
2. **V2 confound control.** The phase-2.5-plan Validity §V2 requires pinning the
   model + version and recording it in telemetry (a weak local model is a
   controlled confound, not a result). `LocalModelStack.telemetry_dict()` is that
   pinned identity, identical across every arm.

The module is **config only** — no SDK import, no network at construction — so
importing it and the whole suite stays model-free (CI never calls a paid API).
`preflight` is the one network method, called explicitly before a real run with an
injectable fetcher so the readiness logic is itself unit-tested with no live daemon.

### Provisioning a real run (operator steps)

```bash
# 1. Bring up the shared local stack (one Ollama daemon for all arms)
ollama serve &
ollama pull llama3            # chat/instruct (mem0, A-MEM ingest)
ollama pull nomic-embed-text  # mem0 embedder
# sentence-transformers (A-MEM/NAT) is a pip dep, pulled on first arm use

# 2. (optional) pin different models for this run
export MEMBENCH_LOCAL_CHAT_MODEL=qwen2

# 3. Each arm's preflight then verifies readiness before any trial; a missing
#    daemon/model is a loud LocalStackUnavailableError, never a paid-API fallback.
```
