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

## 6. Build order & parallel front

`mem-lvp.1` (base + Protocol) was the only true serialization point — **it has
landed and is closed**. The remaining beads run on a wide parallel front:

```
mem-lvp.1 (base) ─┬─> .2  mem0      (sync, lightest, independent)
                  ├─> .10 AsyncClientBridge (infra) ─┬─> .3 NAT      (async)
                  │                                   └─> .4 Graphiti (async; + .11 reset decision)
                  ├─> .9  A-MEM     (sync; real arm gated by .12 concurrency audit)
                  └─> .5  score-norm helpers (consumed by all, no blocker)
.6 metrics · .7 synthetic · .8 dataset   — independent workstreams, parallel anytime
```

New sub-beads carved from the research: **.9** (A-MEM, split out of the old
`.4`), **.10** (`AsyncClientBridge`, blocks the two async arms), **.11** (Graphiti
reset-strategy decision — DECIDED: fresh `group_id` per trial, see §5b), **.12**
(concurrency-isolation audit — gates *real-arm* provisioning of mem0/A-MEM, not
their CI fakes). Every arm's CI is model-free/network-free via the fake client, so
the Ollama/Qdrant/Redis/FalkorDB/Chroma infra (`.5`/`.5`-adjacent) only gates
*real-arm provisioning*, downstream of green CI.

The natural next workflow: a parallel **worktree** build of `.2` (mem0) and `.9`
(A-MEM) — both sync, both independent of the AsyncClientBridge — each landing as
subclass + fake-client tests.
