# Phase-2.5 plan — competitive baseline arms + the 5-axis memory-controller telemetry

> Status: **PLAN-READY, pending Stephanie review.** No implementation is slung
> until this is approved. Source: Phase-2.5 brief (`.gc/phase-2.5-brief.md`,
> Stephanie via mayor, 2026-06-05). This doc extends — does not replace — the
> locked eval/retrieval contract (`ARCHITECTURE.md` Decisions 1–10). On approval,
> the new decisions below (11–15) get promoted into `ARCHITECTURE.md`. Folds in
> Stephanie's controller reference architecture (Addendum) and the NVIDIA-stack
> incorporation findings (Addendum 2), both 2026-06-05.
>
> **GOVERNING DOC:** `.gc/memory-eval-harness-spec.md` (the 17-section *Agentic
> Memory Evaluation Harness* spec, Stephanie 2026-06-05) is now the **authoritative**
> spec for the eval work. This plan is reconciled to it in **§A**, which maps every
> concept and **flags divergences for Stephanie rather than silently resolving
> them** (per her instruction). Where §A and any older section below conflict, §A
> governs and the older section is the pre-spec draft kept for context.

## A. Conformance to the authoritative spec + divergence register

The spec (`.gc/memory-eval-harness-spec.md`) governs. This section reconciles the
pre-spec plan (controller arch + NVIDIA map + locked ARCHITECTURE.md D1–10) to it.
**Divergences are flagged, not silently resolved** — each `DIV-n` carries a
recommendation but is Stephanie's call.

### A.1 Concept map (this plan → spec)

| this plan / prior decisions | spec term (governing) |
|---|---|
| replay A/B: `none` vs arm | the **3 conditions**: `no_memory` / `oracle_memory` / `memory_enabled` (§4) — A/B is *subsumed* |
| arms `ours`/`a-mem`/`mem0`/`graphiti`/`nat` | `memory_systems/` under the **integrated** condition (§14) |
| `builtin` (Claude Code memory) | a memory_system + agent config (§5: Claude Code memory on/off) |
| — (new) | **`oracle`** condition = the memory-sensitivity gate (§4) |
| our thin harness (fork 6) | **Harbor** as execution substrate (§14, §1) — fork 6 resolved toward Harbor |
| 5-axis telemetry record (D12) | spec `memory_event` + `trace` + `metrics{task,efficiency,retrieval,retention,synthesis,action_impact}` (§6.2/§8/§12) |
| memory taxonomy (D13: semantic/episodic/preference/reflection) | spec **backend representations** {filesystem/semantic/kg/episodic/procedural} (§7) × **`candidate_memory.type`** {episodic/semantic/procedural/preference/entity/relationship/failure_pattern} (§8) |
| failure-triggered spine (D8) | `action_impact.memory_prevented_known_failure` + diagnostics (§12.6/§13) |
| precision guard (D10) | `retrieval_metrics` precision@k / distractor / stale rates (§12.3) |
| distill-once append-only (D9) | extractor + `candidate_memory.retention_policy` (§8) |
| replay closed beads (D5) | **one** dataset source feeding multi-session sequences (§9, §17: "do not depend only on Gas City beads") |

### A.2 Alignments (no conflict — the spec ratifies these)

- **ZFC boundary** matches spec §13 (heuristic-first where clean, LLM where fuzzy)
  and §11 (LLM blueprint/step gen → **deterministic** schema conversion).
- **Anti-gaming**: the oracle gate (§4) *strengthens* our precision guard — a task
  is rejected unless `oracle > no_memory`.
- **Distilled / append-only / failure-triggered** (D8/D9) map cleanly onto the
  extractor, retention policy, and action-impact metrics.

### A.3 Divergence register (flag — Stephanie decides)

- **DIV-1 — no-paid-API boundary vs the paid agent-under-test (BIGGEST).** Decision
  4's "no-paid-API" was applied to *memory* components. But the spec's agents under
  test are **paid Claude** (Claude Code, Opus 4.5–4.8, Sonnet, Haiku) run via
  Harbor `--model anthropic/…`. *Recommend:* scope no-paid-API to the **memory
  stack** (backends, embeddings, extractor, judge = OSS/self-hosted) and accept the
  **agent-under-test as paid Anthropic** (the only mandatory cost; Harbor itself is
  Apache-2.0 and adds none). *Why yours:* it's a budget/validity boundary, and it
  reinterprets a locked decision.
- **DIV-2 — eval object: bead-replay (D5/D6) vs multi-session sequenced workloads.**
  The spec's unit is a **multi-step sequence** (Step1→…→Goal, fresh context per
  step, memory persists), ≥10 sequences, 3–8 steps, ≥50% synthesis, ≥30%
  staleness/interference, multi-source incl. non-Gas-City. *Recommend:* bead replay
  becomes **one source** feeding sequence construction; the D6 temporal-LOO / no-leak
  discipline is preserved as the per-step context reset + "oracle is never a label
  the agent sees." *Why yours:* it changes what the headline number is *about*.
- **DIV-3 — oracle condition is new and is the task-validity gate.** Adds a third
  arm we didn't have; `oracle ≈ no_memory ⇒ reject task` (§4, §15, §17). *Recommend:*
  adopt; every sequence must clear the oracle gate in a pilot run before it enters
  the set. *Why yours:* it's an eval-design acceptance rule.
- **DIV-4 — telemetry: spec schema canonical; privacy + interruption NOT in it.**
  Spec metrics (§12) are richer than my 5-axis sketch on 3 axes but **omit privacy
  and interruption-cost** — the two axes the controller north-star *requires* and
  our NVIDIA differentiation *claims as unique*. *Recommend:* adopt the spec's
  `memory_event`/`trace`/`metrics` as canonical, **serialized as OTel spans + ATIF
  derived**, and **extend** them with additive `privacy_metrics` +
  `interruption_metrics` groups (+ the `rerank_features` vector as the learned
  controller's inputs). *Why yours:* either we extend the authoritative spec with
  these two axes or we drop them from the north-star — that's your call, not mine.
- **DIV-5 — memory taxonomy (supersedes D13).** Spec separates **representation**
  (filesystem/vector/kg) from **type** (episodic/semantic/procedural/preference/
  entity/relationship/failure_pattern). *Recommend:* replace D13's flat 4-type list
  with the spec's two-level model; our "reflection" folds into procedural+episodic,
  "preference" stays. *Why yours:* it's a schema decision you already specified.
- **DIV-6 — arms → conditions + memory_systems.** `ours` = a `memory_systems/`
  entry (custom vector/filesystem); `builtin` = Claude Code memory; `a-mem/mem0/
  graphiti/nat` = additional `memory_systems/`; plus `none`/`oracle`/`filesystem/
  mcp/vector/kg` reference systems (§14). *Recommend:* adopt the remap. *Why yours:*
  confirms the arm set + which reference systems ship first.
- **DIV-7 — Harbor resolves fork 6 (substrate), with a filename correction.** Harbor
  (harbor-framework/harbor, Apache-2.0, Docker-per-task, Claude Code first-class) is
  the substrate; our harness logic becomes Harbor **adapters + scorers**, and NAT is
  an *arm*, not the harness. Correction to spec §14: Harbor's real extension shape is
  `adapter.py` + a Docker test writing reward to `/logs/verifier/reward.txt`, **not**
  `task_adapter.py`/`dataset_adapter.py`/`scorer.py`. *Recommend:* adopt Harbor +
  follow its real conventions. *Why yours:* confirms the substrate + supersedes fork 6.
- **DIV-8 — language/runtime: TS graph builder vs Python Harbor.** Phase-1 work-audit
  graph is **TypeScript**; Harbor + CodeScaleBench are **Python**. *Recommend:* the
  TS graph builder becomes a **data source** that exports dataset sequences/fixtures
  (JSON); the Harbor harness, scorers, and extractor are Python. *Why yours:* it's an
  architecture-boundary + integration-cost call (where the TS↔Python line sits).
- **DIV-9 — CodeScaleBench methodology = cross-rig reuse.** The spec's methodological
  base is CodeScaleBench (the `codeprobe` rig). *Recommend:* reuse the methodology
  and **read** codeprobe as reference (in-scope as data/reference); any *dispatch
  into* codeprobe's rig stays **mayor-owned**. *Why yours/flag:* keeps the cross-rig
  boundary explicit.
- **DIV-10 — roadmap re-baseline (scope).** The spec is a **5-phase** programme
  (skeleton → real dataset → metrics/diagnostics → synthetic generator → research
  loop, §16). What I called Phase 2 (retrieval-v1) + Phase 2.5 (arms + telemetry) are
  really components of the spec's **Phase 1 (skeleton)** + **Phase 3 (metrics)**.
  *Recommend:* re-baseline our roadmap onto the spec's 5 phases and treat retrieval-v1
  (`mem-di8`, in flight) as the first `ours` memory_system under that frame. *Why
  yours:* it's a scope/sequencing decision for the whole programme.

### A.5 Resolutions (Stephanie, 2026-06-05)

- **DIV-1 RESOLVED.** Paid agent-under-test is fine — it runs on **our Claude
  account via the OAuth subscription**. `no-paid-API` (Decision 4) stays scoped to
  the **memory stack** (backends / embeddings / extractor / judge = OSS /
  self-hosted); the agent-under-test (Claude Code / Opus / Sonnet / Haiku) uses our
  subscription.
- **DIV-4 RESOLVED.** Add **privacy + interruption** as metric groups extending the
  spec's §12 metrics.
- **DIV-2 + DIV-10 RESOLVED.** Eval object = **multi-session sequences**; the
  roadmap is **re-baselined onto the spec's 5 phases** (bead replay = one source
  feeding sequences).
- **DIV-3 / DIV-5 / DIV-6 / DIV-7 adopted** per recommendation: oracle gate; the
  spec's representation×type taxonomy **supersedes D13**; arms → conditions +
  `memory_systems/`; **Harbor** substrate (its real `adapter.py` + `reward.txt`
  conventions, not spec §14's hypothesized filenames).
- **DIV-8 / DIV-9 acked:** the TS graph builder **exports data** to the
  Python/Harbor harness; reuse CodeScaleBench/codeprobe methodology **read-only**,
  cross-rig *dispatch* stays mayor-owned.
- **Deferred forks** (surface at their phase — none block the Phase-1 skeleton):
  §4.1 shared local stack/model, §4.2 raw-vs-composite metrics, §4.3 soft-axis defs
  (privacy_class / derailment_signal), §4.4 first-run arm scope, §4.5
  Nemotron-as-judge.

### A.4 What stays in flight / unchanged

`mem-di8` (retrieval-v1) continues — under the spec it's the first `ours`
memory_system. `mem-vr8` (replay A/B harness) is **reframed** as the Harbor-based
3-condition harness (skeleton, spec Phase 1) — its bead description will be updated
on approval, not before. Nothing new is slung.

---

## 0. North star (frames everything below)

We are building toward a **learned / evaluable memory controller** — a policy that
decides *when to retrieve, what to inject, and how much* — by explicitly trading
off **five objectives**:

1. **task performance** — outcome lift: success vs the merged-PR/CI oracle,
   iterations-to-green, agent cost on new work. (The headline; Decision 1.)
2. **token budget** — injected-context volume **plus the memory system's own
   token overhead** (its LLM-extraction cost).
3. **latency** — retrieval latency + end-to-end task-latency delta.
4. **privacy** — what is retained/exposed; cross-rig leakage; sensitive-content
   class.
5. **interruption cost** — cost of injecting at the wrong moment
   (derailment/distraction); failure-triggered vs retrieve-always (Decision 8).

The 5-axis tradeoff **is** the controller's objective function. The Phase-2
failure-triggered heuristic (Decision 8) and any LLM self-judge are **v1
stand-ins** for that controller, not the end state. The non-negotiable
consequence: **we instrument all five axes from the first lift run**, even the
two (privacy, interruption) the v1 heuristic cannot yet *act* on — they are still
*measured* so the training/eval signal exists when the learned controller does.
The bench's metrics **are** the controller's training data; we design them as a
durable versioned schema, not one-off eval prints.

### The controller loop (target design; v1 stands in for each learnable stage)

The controller is a **6-stage pipeline**. v1 fills each learnable stage with a
heuristic or LLM-judge; the bench logs each stage's decision as a measurable
feature so the learned policy has training signal:

1. **Need classification** — decide *whether* memory is needed this step (not
   retrieve-always; v1 = the Decision-8 failure-triggered spine). → logged:
   `need_classified`, `trigger`, `trigger_timing`.
2. **Query formation** — form a *targeted* query from current state (not a raw
   embedding of the prompt). → logged: `query_formed`, query source.
3. **Multi-type retrieval** — retrieve across the four memory **types** (§1a),
   each its own storage design. → logged: `memory_types_retrieved`.
4. **Reranking** — by **relevance · recency · importance · trust · task-fit**,
   *including beyond-lexical procedural/relational links* (this is why the
   work-audit GRAPH and a graph arm like Graphiti matter — pure cosine is
   insufficient). → logged: the full `rerank_features` vector (§2), which is
   exactly the learned controller's input.
5. **Minimal-useful injection** — inject only the minimal useful memory (ties to
   token-budget + interruption axes + the Decision-10 precision guard). →
   logged: `inject_tokens`, `n_injected`, `inject_what`.
6. **Post-task write decision** — after the task, decide whether to write a new
   memory or a reflection (the consolidation step; append-only per Decision 9,
   never iterative-rewrite). → logged: `write_decision`, `write_type` (§1b).

### Agent-type dependence (a conditioning + breakdown variable)

The controller is **agent-type / workflow dependent** — coding agents, personal
assistants, and research agents have different memory regimes; we do **not**
assume one regime generalizes. `agent_type` is therefore both a conditioning
variable in the policy and a field in every telemetry record, and every lift
number is reported **broken down by agent-type**. Honest scope note: city traces
are predominantly a **coding/orchestration** workflow — we report results as such
and treat generalization to PA/research workflows as a **distinct axis, not an
assumed property**.

---

## 1. Ask 1 — competitive baseline arms in the replay harness

Extend the P2.2 replay A/B harness (`mem-vr8`) so external memory systems run as
**additional retrieval arms**, head-to-head with ours, on the **same** closed-bead
replay tasks, **same** merged-PR/CI oracle, **same** `retrieval_scope` knob
(Decision 7: strict `cross_rig` + realistic `same_rig_temporal`), **same**
temporal-LOO bound (Decision 6), **same** precision guard (Decision 10), **same**
telemetry emission (§2).

### Arms

| arm | what it is | retrieval cost profile |
|---|---|---|
| `none` | control — no memory | zero |
| `ours` | retrieval-v1 (P2.1, `mem-di8`) — structured/keyword, deterministic | **zero-LLM at retrieve**, deterministic |
| `builtin` | Claude/Codex built-in memory — the `mem-whi` baseline-to-beat, run prospectively (injected-vs-withheld; retrospective path is dead per `mem-46i`) | opaque (vendor) |
| `a-mem` | A-MEM (OSS) | LLM at ingest + embedding retrieve |
| `mem0` | mem0 OSS / self-hosted (NOT the hosted platform) | LLM at ingest + embedding retrieve |
| `graphiti` | Graphiti OSS graph engine (NOT zep-cloud) | LLM at ingest + hybrid (semantic+BM25+graph) retrieve |
| `nat` | NeMo Agent Toolkit memory (Redis / custom local backend; local LLM) — framework-native arm, NOT a route to mem0/graphiti (§1c) | per backend |

### Uniform arm interface (the harness drives every arm identically)

Each arm implements the same two operations; the harness — not the arm — owns the
record set, the scope, the precision guard, and telemetry:

- `ingest(bounded_records)` — load a **harness-supplied, LOO-bounded** set of
  WorkRecords (see validity constraint V1). The arm may *not* read the store
  itself.
- `retrieve(query_ctx) -> ranked_payloads` — return ranked prior-work payloads
  for the held-out task context; the harness applies the precision guard and
  emits telemetry uniformly.

### Per-arm adapter (our substrate → each system's ingest format)

Grounded in the OSS sourcing pass (§5). One adapter per arm converts a WorkRecord
(+ its distilled `signal.semantic` lesson + citation, Decision 9) into that
system's native ingest shape:

- **a-mem** → `add_note(content, tags, category, timestamp)` — flatten the
  WorkRecord + distilled lesson into a content string + tags. Retrieve:
  `search_agentic(query, k)`.
- **mem0** → `add(messages, user_id=<scope>)` — map the record to role/content
  turns; `user_id`/`run_id` carries the `retrieval_scope`. Retrieve:
  `search(query, filters)`.
- **graphiti** → `add_episode(json_or_text, reference_time)` — emit the record as
  a structured episode; `reference_time` feeds Graphiti's bi-temporal model.
  Retrieve: hybrid search → edges/nodes.
- **ours** → native P2.1.
- **builtin** → vendor mechanism; prospective injected-vs-withheld only.

### Shared self-hosted stack (no-paid-API, Decision 4)

Research verdict (§5): **no arm is dropped.** All three external systems run with
zero paid APIs off a **single shared local Ollama** (one chat/instruct model +
one embedding model, e.g. `nomic-embed-text`) plus a per-arm store:

- a-mem → embedded **ChromaDB** + bundled sentence-transformers (lightest).
- mem0 → local **Qdrant / Chroma / FAISS**.
- graphiti → **FalkorDB** (Docker; lightest free graph DB) or Neo4j Community
  (heaviest arm).

### Validity constraints (these are the benchmark, not polish)

- **V1 — LOO enforced at the ingest layer, per arm, per task.** Decision 6/7 only
  holds if each arm ingests **only** WorkRecords closed strictly before
  `B.started`, with B's convoy siblings / supersedes-chain / shared-PR beads
  excluded. An external store that retains the whole corpus silently breaks the
  benchmark. The harness builds a fresh bounded ingest set per (task, scope) and
  the arm never touches the raw store. **This is the #1 validity risk.**
- **V2 — local-LLM extraction is a confound to control.** All three external arms
  use an LLM at ingest; a weak local model (esp. Graphiti's structured
  extraction) can make an arm lose on *its* model quality, not its design. We pin
  the model + version, record it in telemetry, and treat "lost due to small local
  model" as a controlled confound, not a result.
- **V3 — determinism / variance.** `ours` is deterministic; external arms are
  not. Fix temperature=0 / seeds where possible, run N seeds, report variance.
- **V4 — fairness is the point, not a problem.** The external arms incur
  LLM-extraction token + latency cost that `ours` does not. We do **not**
  hide this — we *measure* it (axis 2/3). The expected, honest shape: a heavier
  arm may win task-perf while losing token/latency — and that crossover **is** the
  controller's tradeoff surface.

### 1a. Memory taxonomy (first-class schema decision) + arm coverage

The controller retrieves across **four distinct memory types**, each with its own
storage/representation design (the representation is itself a lever). The store
must represent these distinctly — this is a schema decision (proposed Decision
13), not an implementation detail:

- **semantic facts** — distilled root-cause/resolution lessons (the `signal.semantic`
  field, Decision 9). Representation design is an explicit lever.
- **episodic traces** — the work-audit trace spine (bead↔agent↔trace↔outcome).
- **user/project preferences** — captured from interactions, supplied as *ambient
  context* (not query-triggered the same way).
- **reflections / lessons learned** — produced via configured learning loops OR a
  harness-level **write method** that modifies the stores (§1b, stage 6).

Each external arm covers a **subset** of these — which subset is part of the
comparison, logged per retrieval via `memory_types_retrieved`:

| arm | semantic | episodic | preference | reflection |
|---|---|---|---|---|
| `ours` (v1) | ✅ distilled lessons | ✅ trace spine | ⛔ (not yet) | ◢ write-method (§1b) |
| `a-mem` | ✅ notes | ◑ note-as-event | ⛔ | ◑ note evolution/links |
| `mem0` | ✅ extracted facts | ◑ message history | ◑ user-scoped | ⛔ |
| `graphiti` | ✅ entity facts | ✅ bi-temporal episodes | ⛔ | ◑ via re-ingest |
| `builtin` | opaque | opaque | ◑ | opaque |

(✅ first-class · ◑ partial/derived · ⛔ none · ◢ planned. Cells are hypotheses to
*confirm empirically* during adapter build, not asserted facts.) The taxonomy
coverage gap is itself a finding: e.g. **preference** memory is thin across all
OSS arms, and only graph-shaped arms (`ours`-graph, `graphiti`) carry the
**procedural/relational** links the reranker (stage 4) needs beyond lexical
similarity.

### 1b. Write / reflect interface (stage 6 — design now, even if v1 is heuristic)

The post-task write decision is the bridge to "configured learning loops or a
harness write-method." We design the interface now so reflection data is captured
from the first run:

- `write_decision(task_result) -> {write: bool, write_type, payload}` — after each
  replay task, decide whether to persist a new memory or a reflection. v1 = a
  heuristic/LLM-judge stand-in; the decision + its inputs are logged
  (`write_decision`, `write_type`) so a learned policy can later replace it.
- `write_type ∈ {semantic, episodic, preference, reflection}` — maps onto the §1a
  taxonomy.
- **Append-only (Decision 9).** The write-method may add memories/reflections; it
  **never iteratively rewrites** existing ones (iterative LLM rewriting degrades
  consolidated memory — see ARCHITECTURE.md lit grounding). Each write carries a
  citation (`bead_id` + `commit_sha`).
- Validity: in replay, writes go to a **per-run scratch store**, never back into
  the LOO-bounded corpus mid-eval (writing into the corpus would leak future work
  into past tasks — a V1-class break).

### 1c. NVIDIA-stack alignment (this work is NVIDIA agent-memory prep)

Consume OSS where it buys interop; contribute where we're differentiated; hold the
no-paid-API line (Decision 4). **Two grounded corrections to the source addendum
— following it verbatim would breach Decision 4:**

- **NAT's Zep backend is `zep-cloud` (paid SaaS), and its Mem0 backend defaults to
  the hosted platform.** NAT does **not** wrap the OSS Graphiti engine. So NAT is
  no-paid-API-clean **only** via its **Redis** backend or a **custom local
  `MemoryEditor`**. We therefore keep our **direct, self-hosted** mem0 and Graphiti
  arms (§1, §5) as the OSS path, and treat NAT as a *separate* bake-off arm — we do
  **not** source mem0/graphiti *through* NAT.
- **Nemotron-3 Nano is under the NVIDIA Open Model License, not Apache-2.0** —
  commercial-use-OK and self-hostable, but a custom license, not OSI-OSS. Fine to
  adopt; flag the distinction if the benchmark must claim OSI-only.

**ADOPT (verified self-hostable OSS / no-paid-API):**

- **NeMo Agent Toolkit (NAT) as a bake-off arm + optional unified harness.**
  Apache-2.0 (`NVIDIA/NeMo-Agent-Toolkit`, `pip nvidia-nat`). Point its LLM at a
  local OpenAI-compatible server (`_type: openai`, `api_base: http://localhost…`)
  — override the NIM default. Memory via the **Redis** backend / custom local
  `MemoryEditor` only. Adds a framework-native arm and an interop harness config.
- **Telemetry interop = OpenTelemetry spans primary + ATIF derived** (see §2,
  Decision 12). Each work-audit trace step (model-call / tool-call /
  retrieval-step) serializes to an **OTel GenAI span**; **ATIF** (*Agentic*
  Trajectory Interchange Format — NVIDIA's NAT eval schema) is a *derived* export.
  OTel stays primary to avoid single-vendor lock-in.
- **Latency / injection-cost axis adopts the BlueField-4 G1–G4 tier taxonomy as
  vocabulary only (no hardware):** hot semantic facts ≈ G1/G2, episodic traces ≈
  G3, cold reflections ≈ G4 — a clean way to model injection cost by which tier a
  memory lives in.
- **Expose the controller as an MCP server** (`retrieve` / `write` / `reflect`
  tools) so it drops into NAT and any agent loop unchanged — the controller
  interface (§1b) is defined as MCP tools.
- **Nemotron-3 Nano (30B-A3B, weights live on HF, vLLM/transformers/llama.cpp,
  NVIDIA Open Model License) as a self-hosted local judge / replay-agent
  candidate** — benchmark it against the current judge; satisfies no-paid-API.
- **Eval legibility:** report ≥1 metric through a **NeMo-Evaluator** (Apache-2.0)
  / **lm-evaluation-harness** (MIT) compatible config, both local-runnable; cite
  **RULER** (long-context, 4K→1M) as the retrieval yardstick our multi-type
  semantic memory complements.
- **Trace-curation filters** (from the Nemotron trajectory-quality work:
  submission integrity, tool-call hygiene, lost-in-exploration, edit-test thrash)
  as a pre-ingest checklist before traces enter the eval substrate.

**CONSUME-ONLY (don't reimplement):** Mem0/Zep, foundation models.
**AVOID for scix (GPU / NIM / paid-gated — vocabulary/reference only):** NeMo
Retriever, NIM, Dynamo/NIXL/CMX/DOCA-Memos/Grove, BlueField-4 hardware, the
Nemotron training recipe.

**CONTRIBUTE (our differentiation):** the **multi-agent-orchestration** memory
benchmark (bead↔agent↔trace↔PR↔outcome replay — NVIDIA's agentic data is
single-agent); the **5-axis eval** where **privacy + interruption-cost** are axes
NVIDIA's published material does not evaluate; **beyond-lexical
procedural/relational graph rerank** over a typed orchestration graph.

> Positioning: *a learned memory controller above the KV/context tier (G1–G4
> model), emitting ATIF/OTel-compatible traces, runnable inside NeMo Agent Toolkit
> as a memory provider, evaluated on a novel multi-agent-orchestration replay
> benchmark across 5 axes NeMo Evaluator doesn't cover.*

---

## 2. Ask 2 — the 5-axis telemetry schema (a decision in its own right)

One **versioned, reusable** telemetry record is emitted per replay run **and** per
future live-shadow event. Not eval prints — a durable schema; the bench's metrics
*are* the controller's training/eval signal.

### Record (v1)

```json
{
  "schema_version": "1",
  "task_id": "<held-out bead B>",
  "run_id": "<replay run>",
  "ts": "<ISO8601>",
  "arm": "none|ours|builtin|a-mem|mem0|graphiti|nat",
  "retrieval_scope": "cross_rig|same_rig_temporal",
  "agent_type": "coding|orchestration|pa|research",

  "controller_kind": "heuristic_failure_triggered|llm_self_judge|learned",
  "need_classified": true,
  "query_formed": "<targeted query / source state>",
  "trigger": "failure_signature|task_start|none",
  "trigger_timing": "on_failure|off_failure",
  "retrieved": true,
  "memory_types_retrieved": ["semantic", "episodic", "preference", "reflection"],
  "inject_what": "distilled_lesson|file_line_plus_commit|none",
  "n_candidates": 0,
  "n_injected": 0,

  "rerank_features": [
    {"item_id": "", "memory_type": "semantic|episodic|preference|reflection",
     "relevance": 0.0, "recency": 0.0, "importance": 0.0, "trust": 0.0,
     "task_fit": 0.0, "procedural_link": 0.0, "relational_link": 0.0,
     "rank": 0, "injected": false}
  ],

  "write_decision": false,
  "write_type": "semantic|episodic|preference|reflection|none",

  "outcome": "pass|fail",
  "iterations_to_green": null,
  "agent_cost_tokens": 0,

  "inject_tokens": 0,
  "arm_ingest_tokens": 0,
  "arm_query_tokens": 0,

  "retrieval_latency_ms": 0,
  "task_latency_delta_ms": 0,
  "arm_ingest_latency_ms": 0,
  "storage_tier": "G1|G2|G3|G4",

  "privacy_class": "none|internal|sensitive",
  "leakage_flags": [],

  "derailment_signal": null,

  "precision": null,
  "duplicate_audit_flag": false,
  "citations": [{"bead_id": "", "commit_sha": ""}],

  "arm_model": "<local LLM + embed model + version>",
  "arm_version": "<memory-backend version, e.g. mem0==0.x / graphiti==0.y / claude-code-mem-vN>"
}
```

**Backend-version as a first-class dimension.** Each arm pins and records its
backend **version** (`arm_version`); the harness supports a **version-swap eval
mode** — the same arm re-run across backend versions on the identical replay set —
so "did upgrading the memory backend help/hurt" is a measurable result, not a
guess. The `none` vs `builtin` pair is exactly *Claude Code with-vs-without
memory*; version-swap extends that to *which version of a backend* wins.

**Serialization (interop).** The record is the *logical* schema; the **primary
wire format is OpenTelemetry GenAI spans** — each model-call / tool-call /
retrieval-step is an OTel span and these fields are span attributes. **ATIF**
(NVIDIA's NAT eval format) is a **derived export**, not the source of truth. OTel
stays primary to avoid single-vendor lock-in (§1c, Decision 12).

### How each axis is measured per run

| axis | fields | how / source |
|---|---|---|
| 1 task-perf | `outcome`, `iterations_to_green`, `agent_cost_tokens` | fresh replay vs merged-PR/CI **oracle** (never a label the agent sees, Decision 6) |
| 2 token-budget | `inject_tokens` (Decision-10 guard), `arm_ingest_tokens`, `arm_query_tokens` | injected-context volume + the arm's own LLM-extraction tokens (ingest amortized per task) |
| 3 latency | `retrieval_latency_ms`, `task_latency_delta_ms`, `arm_ingest_latency_ms`, `storage_tier` | wall-clock around retrieve + end-to-end delta vs `none`; `storage_tier` models injection cost via the BlueField-4 **G1–G4** vocabulary (hot semantic ≈ G1/G2, episodic ≈ G3, cold reflection ≈ G4) — naming only, no hardware (§1c) |
| 4 privacy | `privacy_class`, `leakage_flags` | **model-classified** sensitivity of the injected payload (ZFC: judgment to the model); `leakage_flags` includes a cross-rig-in-strict check (a `cross_rig` run must never inject same-rig content) — **measured, not acted on in v1** |
| 5 interruption | `derailment_signal`, `trigger_timing` | proxy = added-iterations / abandonment conditioned on inject timing; lets us later compare failure-triggered (Decision 8) vs retrieve-always — **measured, not acted on in v1** |

Precision guard (Decision 10) spans axes 1–2: `precision` + `duplicate_audit_flag`
are emitted on **every** lift run, every arm — over-injection cannot game the
headline.

**Controller-loop features (the learned policy's inputs).** Beyond the five
axes, the record logs the per-stage controller decisions (`need_classified`,
`query_formed`, `memory_types_retrieved`, `write_decision`/`write_type`) and the
full **`rerank_features`** vector per candidate — relevance · recency · importance
· trust · task-fit **plus** beyond-lexical `procedural_link` / `relational_link`
(graph-derived). These are not scored into a weighted ranking in v1 (that weight
is the *learned* part, §4 fork 2); they are **logged as measurable features** so
the learned controller has training data from run one. `agent_type` conditions
every breakdown.

---

## 3. Proposed decisions (promote into ARCHITECTURE.md on approval)

> Reconciled by §A: D12 is restated onto the spec's `memory_event`/`trace`/`metrics`
> schemas (+ privacy/interruption extensions, DIV-4); **D13 is superseded** by the
> spec's representation×type taxonomy (DIV-5). Read §A before applying these.


- **Decision 11 — competitive-arm contract.** External memory systems run as arms
  behind one uniform `ingest`/`retrieve` interface, on identical replay
  tasks/oracle/scope/precision-guard/telemetry. The harness owns the
  LOO-bounded ingest set (V1); arms never read the store. OSS/self-hosted only
  (Decision 4) — an arm that can't run without a paid API is dropped and the drop
  is documented (per §5: none dropped). Per-arm token + latency overhead is
  reported, not hidden (V4).
- **Decision 12 — versioned 5-axis telemetry schema.** One durable, versioned
  telemetry record (§2) per replay run and per live-shadow event, measuring all
  five controller axes from the first lift run — including privacy and
  interruption, which v1 measures but does not act on. The record **also** logs
  the per-stage controller decisions and the full `rerank_features` vector
  (relevance/recency/importance/trust/task-fit + procedural/relational), plus
  `agent_type`, as measurable features — the learned controller's inputs, captured
  from run one. The schema is the controller's training/eval substrate, not eval
  scaffolding. **Wire format: OpenTelemetry GenAI spans primary + ATIF derived**
  (interop, no lock-in); the latency axis carries a `storage_tier` (G1–G4
  vocabulary, §1c).
- **Decision 13 — memory taxonomy is first-class.** The store represents four
  distinct memory types — semantic facts, episodic traces, user/project
  preferences, reflections/lessons — each with its own representation (a lever).
  Retrieval is multi-type; `memory_types_retrieved` is logged. Each external arm's
  type coverage (§1a) is part of the comparison, confirmed empirically during
  adapter build.
- **Decision 14 — controller-loop framing + write/reflect interface + agent-type
  conditioning.** The controller is the 6-stage loop (§0); v1 fills each learnable
  stage with a heuristic/LLM-judge and logs its decision. The post-task
  write/reflect interface (§1b) is designed now (append-only per Decision 9;
  replay writes go to a per-run scratch store, never the LOO corpus). `agent_type`
  is a conditioning variable and an eval-breakdown dimension; city traces are a
  coding/orchestration workflow and results are reported as such, with PA/research
  generalization treated as a distinct future axis.
- **Decision 15 — NVIDIA-stack posture (consume OSS / contribute / avoid).** Adopt
  the verified-self-hostable pieces (§1c): NAT as a bake-off arm + optional harness
  (local LLM via `_type: openai`; Redis/custom local memory backend **only** — its
  Mem0/Zep-cloud defaults are NOT no-paid-API-clean and are **not** our route to
  mem0/graphiti); OTel-primary + ATIF-derived telemetry (Decision 12); the G1–G4
  storage-tier vocabulary on the latency axis; the controller exposed as an **MCP
  server** (`retrieve`/`write`/`reflect`); Nemotron-3 Nano as a self-hosted local
  judge candidate (NVIDIA Open Model License, not Apache); eval legibility via
  NeMo-Evaluator / lm-eval-harness + RULER; trace-curation filters pre-ingest.
  **Avoid** all GPU/NIM/paid-gated NVIDIA components (vocabulary/reference only).
  **Contribute:** the multi-agent-orchestration benchmark, the privacy +
  interruption axes NVIDIA doesn't evaluate, and beyond-lexical graph rerank.

---

## 4. Open decisions for Stephanie (the forks I won't pick)

1. **Shared local stack.** Confirm one Ollama instance for all external arms.
   *Recommend:* a capable instruct model (`qwen2.5` or `llama3.1`, 8B+ — small
   models fail Graphiti's structured extraction) + `nomic-embed-text`; FalkorDB
   (Docker) for the graphiti arm. *Why yours:* it's an infra-weight + cost call
   (self-hosted compute), and the model choice is a validity confound (V2).
2. **Composite vs raw axes.** *Recommend:* report the **raw** 5-axis vector per
   arm now; do **not** define a weighted composite / objective function yet —
   hardcoding axis weights would be exactly the brittle heuristic the north-star
   rejects. The weighting is the *learned* part. *Why yours:* it's an eval-design
   call about what "winning" means.
3. **Soft-axis definitions (privacy_class, derailment_signal).** These are the
   least-defined fields. *Recommend:* v1 buckets `privacy_class ∈
   {none,internal,sensitive}` via model classification; `derailment_signal` as the
   iteration/abandonment proxy above — both measured, refined later. *Why yours:*
   they touch the validity/privacy axis you own.
4. **First-run arm scope.** *Recommend:* land the uniform interface + telemetry
   with `none`/`ours`/`builtin` first (de-risks the schema + oracle), then add
   `a-mem`/`mem0`/`graphiti` (de-risks the Ollama+graph-DB infra), then `nat` —
   without re-touching the harness contract. *Why yours:* it's a sequencing/scope
   call.
5. **Local judge / replay model.** Adopt **Nemotron-3 Nano** as a self-hosted
   judge/replay-agent candidate? *Recommend:* yes — benchmark it against the
   current judge; weights are live and it runs on vLLM/llama.cpp with no paid API.
   *Caveat that's yours:* it's under the **NVIDIA Open Model License, not OSI-OSS**
   — fine for commercial self-hosting, but if the benchmark must claim OSI-only,
   that's your call to make.
6. **NAT as harness vs our own.** *Recommend:* keep **our** thin harness as the
   SSOT and add NAT as *one more bake-off arm* + emit OTel/ATIF + expose the
   controller as MCP — interop without taking a hard NAT dependency. *Why yours:*
   it's an architecture/dependency call (taking NAT as the harness couples us to
   its release cadence and its cloud-leaning memory defaults).

---

## 5. OSS sourcing (grounded, no-paid-API verdict)

| system | license / repo | self-hosted store | LLM (local?) | embeddings (local?) | verdict (no-paid-API) |
|---|---|---|---|---|---|
| **A-MEM** | MIT · `agiresearch/A-mem` | ChromaDB (embedded) | required; **Ollama** backend | required; default **local** `all-MiniLM-L6-v2` (sentence-transformers) | **YES — cleanest**; defaults already local |
| **mem0** | Apache-2.0 · `mem0ai/mem0` (use `Memory`, not `MemoryClient`) | Qdrant/Chroma/FAISS/pgvector… (all self-hostable) | required; **Ollama** provider | required; **Ollama**/HF local embedder | **YES**; footgun — match `embedding_dims` to the local model & recreate the collection (issue #3441) |
| **Graphiti** | Apache-2.0 · `getzep/graphiti` (NOT zep-cloud) | graph DB **mandatory**: Neo4j Community / FalkorDB / Kuzu | required & heavy; local via `OpenAIGenericClient` → Ollama | required; Ollama / local embedder | **YES, with more infra**; heaviest, most sensitive to local-LLM extraction quality |
| **NAT** | Apache-2.0 · `NVIDIA/NeMo-Agent-Toolkit` (`pip nvidia-nat`) | **Redis** (OSS) or custom local `MemoryEditor` | required; `_type: openai` + `api_base` → local vLLM/Ollama (override NIM default) | per backend | **YES via Redis/custom only** — its **Mem0/Zep-cloud** plugins are hosted/paid; NOT a route to our mem0/graphiti arms |
| **Nemotron-3 Nano** (local judge candidate) | **NVIDIA Open Model License** (not Apache) · HF `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-*` | n/a (model) | self-host vLLM / transformers / llama.cpp (GGUF); no NIM dep | n/a | **YES** under no-paid-API; license is commercial-OK but not OSI |

**Shared infra:** one local **Ollama** (chat + embed model) serves a-mem/mem0/graphiti;
A-MEM and mem0 use embedded/local vector stores; Graphiti adds a self-hosted graph
DB (FalkorDB via Docker is lightest); NAT adds a local Redis. **No arm dropped on
the no-paid-API constraint** — but NAT only via its Redis/custom backend (§1c).

---

## 6. Sequencing after approval (nothing slung until then)

1. Promote Decisions 11–15 into `ARCHITECTURE.md`.
2. Land the memory-taxonomy store representation (D13), the uniform arm interface,
   the Decision-12 telemetry **serialized as OTel GenAI spans** (incl.
   `rerank_features` + `agent_type` + `storage_tier` + per-stage logging), and the
   write/reflect interface (D14) — exposed as an **MCP server** (D15) — in the P2.2
   harness (`mem-vr8`) with `none`/`ours`/`builtin`. Apply the trace-curation
   filters before traces enter the substrate.
3. Add `a-mem`/`mem0`/`graphiti` adapters + the shared Ollama/FalkorDB stack, then
   the `nat` arm (Redis/custom local backend); confirm each arm's memory-type
   coverage (§1a) empirically. Stand up the Nemotron-3 Nano local judge as a
   candidate and benchmark vs the current judge.
4. First competitive lift run: per-arm 5-axis report **broken down by agent-type**,
   under both `retrieval_scope` tracks, precision-guarded; report ≥1 metric through
   a NeMo-Evaluator / lm-eval-harness-compatible config, with RULER cited as the
   long-context yardstick.

Each becomes a routable `mem-worker` bead **after** Stephanie signs off on §3–§4.
Anything that reopens an eval-design fork (the §4 items) stays mine to surface,
not to sling.
