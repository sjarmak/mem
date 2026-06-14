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

## 5. Recommended build order

1. **`AbstractSemanticArm`** — a shared base over `MemorySystem` that does the
   `RetrievalRequest.query_text` → `client.search(query, top_k)` →
   `RetrieveResult` + `MemoryEvent` translation once, with the injectable-client
   protocol. Every semantic arm subclasses it; this is the DRY core (and the
   thing the per-arm beads depend on).
2. **mem0 arm** first — its `add`/`search`/`delete` is the cleanest contract fit
   and it self-hosts with a local embedder, so it validates the base.
3. **NAT arm** second — wraps NAT's memory tools; because NAT itself delegates to
   mem0/redis, the mem0 arm de-risks it. Pair with the **outbound NAT provider**
   (publish `ours`) since they share the translation layer.
4. **A-MEM** and **Graphiti/Zep-OSS** — heavier infra (graph store / evolving
   notes); schedule after the base + first two prove the seam and the local-model
   stack exists.

Each step is one child bead under mem-lvp (see the decomposition committed
alongside this doc).
