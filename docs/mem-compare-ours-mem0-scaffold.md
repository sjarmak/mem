# `ours` vs `mem0` retrieval-quality comparison — scaffold

Status: **scaffold, free/local lane only.** Code + tests land; a real run is gated on
provisioning (below). No agent re-run, no outcome lift, no spend.

## The problem this solves

`ours` and the competitive semantic arms (`mem0`, a-mem, graphiti, nat) were wired
behind one `MemorySystem` interface but never met on a single task surface — they
consume **different `RetrievalRequest` fields and run in different runners**:

| | request field | runner | corpus | ground truth |
|---|---|---|---|---|
| `ours` | `query_work` + `scope` | `replay.py` | work-audit graph (failure-signature match) | `assert_no_leak` + signature matches |
| `mem0` | `query_text` | `runner/conditions.py` | what was *written* into its per-trial scope | authored `expected_memory_reads` |

Each arm **raises** if handed the other family's request
(`ours_system.py:129`, `semantic_base.py:149`). So there was no driver that scored
them head-to-head.

## The bridge (`membench/compare/`)

Put both arms on one surface, reusing existing primitives (no reimplementation):

1. **One corpus, one boundary.** `validity.loo_bounded(corpus, B)` is the single
   door to the corpus for both arms.
2. **Seed the semantic arm** with exactly that LOO set (`seed_semantic_arm`) — each
   prior bead's text written into its per-trial scope — so it is not compared
   against an empty store (the `mem-bxhh.3` substrate trap).
3. **Run both:** `ours` via the existing `replay.replay_arm`; the semantic arm via
   its `query_text` path with a `query_text` derived from `B`.
4. **Score both** retrieved id sets against ONE authored relevant set
   (`grading.retrieval_leg.score_retrieval_leg` → precision / recall / MRR / nDCG).
5. **Re-check both** with `validity.assert_no_leak`.

### The id-translation wrinkle

A semantic backend mints its **own id** per write and keys its hits off it
(`semantic_base.AbstractSemanticArm.write` / `retrieve`), not the work_id. Seeding
captures a `backend_id → work_id` map; retrieval is translated back through it before
any scoring or leak-check. An unmapped id stays as-is so it surfaces as an
"unknown id" leak rather than being silently dropped.

## Validity disciplines baked in

- **Relevant set is explicit input** (authored ground truth), never derived here
  from either arm's mechanism. Deriving it from `ours`'s failure-signature match or
  from `mem0`'s embedding would silently bias the comparison toward that arm — the
  single most important benchmark-design hazard for this lane. **Open decision:** who
  authors the relevant set and against which target (raw / source / canonical).
- **Relevant ∩ LOO.** An authored relevant id the boundary withholds is dropped from
  the recall denominator (`_relevant_within_loo`), mirroring
  `retrieval_leg.gold_relevant_ids`.
- **Empty relevant set ⇒ `None` metrics**, never a fabricated `0.0`.
- **Injected-context volume reported** alongside recall (Decision-10 precision
  guard): retrieval quality is gameable by over-injection.
- **Pinned model identity** (`LocalModelStack.telemetry_dict()`) recorded per run —
  the V2 local-LLM-quality confound control.

## Provisioning required for a real run

1. **SDK:** `uv add mem0ai qdrant-client` (absent from `uv.lock` today; only mypy
   import-silencing overrides exist).
2. **Local models:** `ollama serve`; `ollama pull nomic-embed-text`;
   `ollama pull llama3` (defaults in `local_stack.py`; override via `MEMBENCH_*`).
   `preflight` fails loud with the exact pull command otherwise.
3. **A populated store** the `ours` arm reads (built `mem` CLI + a real work-audit
   store with the corpus actually ingested).

Run: `python scripts/run_compare_ours_mem0.py --store <db> --corpus c.json
--queries q.json --relevance r.json --out out.jsonl`.

## Not in scope (deliberately)

- **Outcome lift** (does the agent solve `B` better with each arm's memory) — that is
  the paid Harbor agent path and needs explicit sign-off.
- The other competitive arms (a-mem / graphiti / nat). The bridge is arm-agnostic
  (any `AbstractSemanticArm`), so adding them is a driver-construction change only.
