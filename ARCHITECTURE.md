# mem — Architecture

`mem` turns the dolt bead spine plus agent transcripts into a queryable
**work-audit graph**, then uses that graph as the corpus for a **memory
benchmark**: does retained/retrieved memory measurably improve an agent's success
rate, iterations, and cost on new work?

This document is the synthesized current-state overview. Two companions go deeper:

- **The *why* behind every choice** — [`docs/architecture-decisions.md`](docs/architecture-decisions.md), the chronological decision log (Decisions 1–22 + literature grounding).
- **The authoritative eval contract** — `.gc/memory-eval-harness-spec.md` (the *Agentic Memory Evaluation Harness* spec). Where it and the decision log conflict, the spec governs.

The thesis in one line: **work records beat session prose as a memory corpus**,
because every record carries a real, verifiable outcome label, and the one
proven, model-free reuse signal — *the same `file:line` failure recurring across
tasks* — is computable directly from trace output.

---

## Status at a glance

| Layer | State | Where |
|---|---|---|
| Work-audit graph builder (ingest → parse → store) | **Built** | `src/{ingest,parse,store}/` |
| SQLite + FTS5 sidecar store, append-only lessons | **Built** | `src/store/`, `.mem/store.db` |
| Retrieval v1 — failure-triggered, structured/keyword | **Built** | `src/retrieve/` |
| `mem` CLI (`--json` envelope, progressive-disclosure retrieve) | **Built** | `bin/mem`, `src/cli/` |
| memory-bench harness — replay, oracle bundles, metrics, arms | **Built, evolving** | `memory-bench/membench/` |
| Temporal leave-one-out + exclusions + dual-track + precision guard | **Built (eval contract)** | `src/store/reader.ts`, `src/retrieve/exclusions.ts` |
| Synthetic corpus SHARE schema — records ARE WorkRecords; one firewall/reader/LOO | **Phase-0 contracts merged (@bbc4f9a); wiring in progress** | Decision 19, `mem-ifm2`/`mem-3zos` |
| Learned 6-stage memory controller (MCP server) | **Designed; v1 = heuristic/judge per stage** | spec §1, Decision 14 |
| Competitive arms (mem0 / A-MEM / NAT / Graphiti) | **Built** | `memory-bench/membench/memory_systems/` |
| NeMo-embed dense **baseline** arm (not an `ours` upgrade) | **Branch-ready (`mem-sikg`)** | Decision 21 |
| Builtin no-store native-memory arm + forward-capture firewall | **Branch-ready (`mem-mor1`)** | Decision 22 (D-E/D-F) |
| OpenRath projecting read-model over `memory_events` | **Phase-0 merged; adapter in progress** | Decision 20 |
| Multi-session sequence eval object | **Planned** | spec / Decision 16 |
| Fine-tuning / RL reranker + retrieval behavior | **Research track** | `research/` PRDs |

"Built" means the code path exists and is exercised; it does not mean the science
is final — the headline metric itself is still being pinned (see *Eval harness*).

---

## Data model — the WorkRecord

The atomic unit is a **WorkRecord**, keyed by a bead id, joining the whole audit
trail. The store is a *projection* of this JSON — every queryable column is
rebuilt from `work_records.record` on upsert; projections are never written
directly.

```
WorkRecord {
  work_id:    "gascity-dashboard-tnqw"      # bead id (anchor)
  rig:        "gascity-dashboard"
  title, labels, metadata, priority
  lifecycle:  { created, started, closed, status, status_history[] }
  agents:     [ { agent_id, account, trace_ref } ]            # → JSONL path
  trace:      { jsonl_path, n_turns, tool_calls[], tool_outcomes[],
                errors[ {tool, file, line, message, severity} ] }
  outcome:    { pr, merged|closed, commit_sha, ci: pass|fail }   # the label
  provenance: { work_dir, repo, base_branch,                     # the env baseline
                base_commit, history_state: commit-by-date|unresolved }
  signal:     { deterministic: {...}, semantic: {...} }          # the learned bit
  links:      { deps[], convoy_id, supersedes[] }
}
```

Two fields carry the benchmark's weight:

- **`outcome`** — the verifiable label (closed / merged / CI). This is what makes
  the corpus a benchmark substrate rather than a log. *Caveat:* outcome linkage is
  sparse in the real corpus (only a handful of records carry PR/commit metadata),
  which is why the headline is ablation-based rather than merged-PR success — see
  *Eval harness*.
- **`provenance`** — the *environment baseline*, distinct from outcome: the repo +
  commit a session started from, so a record can be replayed as a git-checkout
  task. gc never records the exact base SHA, so `base_commit` is the newest commit
  on `base_branch` at/before `started_at` (`history_state: commit-by-date`).
  Resolving against the work_dir's HEAD would walk the agent's own feature branch —
  a train/test leak — so an absent base branch is terminal `unresolved`, never
  guessed.

---

## Pipeline

```
ingest/  ->  parse/  ->  store/  ->  retrieve/  ->  bench/
 (IO)       (signal)    (graph)    (failure-      (with-vs-without
                                    triggered)     memory eval)
```

1. **`ingest/`** — readers per source (dolt bead store, JSONL traces, gh PRs).
   Pure IO → raw WorkRecords. Trace resolution shells `gc session logs`, which
   loads `city.toml` from the cwd — full rebuilds run from `/home/ds/gas-city`.
2. **`parse/`** — two extractors, kept strictly separate:
   - *Deterministic* (mechanical, in code): tool exit states + `file:line`
     build/test/lint errors, with cross-task recurrence confidence
     (`unique_traces / total`). This is the ZFC-clean signal — no keyword
     heuristics live here.
   - *Semantic* (judgment, via model): root-cause + resolution approach, run
     **batched at ingest, once per record, append-only**.
3. **`store/`** — the WorkRecord graph in a SQLite + FTS5 sidecar (`.mem/store.db`,
   schema in `src/store/schema.ts`). The **`lessons` table is append-only** —
   deliberately no FK to `work_records`; citations are snapshotted at append time.
   A schema bump means rebuilding from the bead spine, so lessons (the one thing a
   rebuild can't regenerate) are round-tripped via `export-lessons` /
   `import-lessons`.
4. **`retrieve/`** — failure-triggered query (see below).
5. **`bench/`** + `memory-bench/` — the replay eval harness.

---

## Retrieval

Retrieval v1 is deliberately the cheapest thing that captures the proven signal;
embeddings are gated behind it underperforming.

- **Failure-triggered, not retrieve-always.** Memory fires when an agent hits a
  build/test/lint error, keyed on the deterministic failure signature (normalized
  `file:line` + error-class). This is rig-agnostic by construction, so it works
  cross-rig; the claim is *cuts iterations-to-green and raises eventual pass rate*,
  not *prevents the first failure*. Task-start semantic retrieval is a Phase-2 item
  (needs embeddings + respects the no-paid-API constraint).
- **Distilled payload, never the raw trace.** The default payload is the
  model-extracted root-cause + resolution + dep links, **plus a citation**
  (`mem://lesson/<work_id>[/<commit_sha>]`) so it is auditable and the agent can
  recover full detail. `mem retrieve --format index` lists ranked items with
  citations so the agent sees token cost before hydrating context. Same-rig runs
  may additionally attach the literal `file:line` + commit, still leading with the
  distilled note.

### The validity discipline (load-bearing)

The eval is only honest if retrieval cannot see the answer. Four mechanisms
enforce that, and weakening any one leaks:

- **Temporal leave-one-out** — when evaluating bead B, the retrievable set is only
  records **closed strictly before `B.started`** ("memory as it existed when the
  work began"). Enforced by the reader's strict `closedBefore`.
- **Exclusions** — also drop B's convoy siblings, supersedes-chain, and any bead
  sharing B's PR/branch (same work that dodges the timestamp filter).
- **Dual-track, report both** — *strict/headline* = cross-rig retrieval only
  (leak-proof, measures transfer); *realistic/secondary* = same-rig temporal-LOO +
  a duplicate audit (how memory is actually used). Same-rig ≫ cross-rig is a
  finding, not a flaw.
- **Precision guard** — returning the whole store gets recall 1.0 and can pass
  answer-quality evals, so outcome-lift alone is gameable by over-injection. The
  bench measures injected-context volume + retrieval precision as a first-class
  guard on every lift run.

---

## The memory controller (planned; v1 stubbed per stage)

Beyond retrieval-v1, memory is framed as a **6-stage controller loop**, exposed as
an **MCP server** (`retrieve` / `write` / `reflect` tools) so it drops into any
agent loop unchanged:

```
need-classification -> query-formation -> multi-type retrieval
   -> reranking -> minimal-useful injection -> post-task write decision
```

v1 fills each learnable stage with a heuristic or LLM-judge and **logs its
decision**, so the controller is trainable the moment replay produces labels.
Every stage's inputs are captured from run one as the `rerank_features` vector
(relevance / recency / importance / trust / task-fit + procedural / relational),
alongside `agent_type` and `storage_tier`.

**Memory types** are two-level: *representation* {filesystem / vector / kg} ×
*type* {episodic / semantic / procedural / preference / entity / relationship /
failure_pattern}. The store represents these distinctly and logs which types a
query retrieved; representation is itself an experimental lever.

**Telemetry** is one versioned record per run, serialized as OpenTelemetry GenAI
spans (primary) + ATIF (derived), covering five axes (task-perf, token-budget,
latency, privacy, interruption). Privacy and interruption are **measured but not
acted on** in v1.

---

## The memory record & write decision

A separate extractor proposes a `candidate_memory` (type, content, scope,
evidence, `proposed_backend`, `retention_policy`, `supersedes`) that passes the
spec's retention filters before being committed. Two refinements
([`docs/memory-prediction-and-dual-confidence.md`](docs/memory-prediction-and-dual-confidence.md))
make the framing explicit:

- **The write decision is a prediction.** The controller's stage-6 gate is scored
  as `P(a future task improves | this memory is retrievable)`, estimated over the
  features already logged. v1 is the heuristic/judge; the spec's retention metrics
  (`write_hit_rate`, `over_retention_rate`, `noise_write_rate`) are the free
  supervision label. It brackets the read-time precision guard from the write side.
- **Retrieval confidence != truth confidence.** A memory carries two scalars, not
  one: `retrieval_confidence` (a ranking signal that rises with reinforcement —
  frequency, retrieval success) and `truth_confidence` (a correctness signal that
  rises only with verification and **decays with age / on contradiction**). A
  frequently-retrieved memory can still be wrong; keeping the axes separate is what
  makes "stale but popular" representable and "High stale-read rate" diagnosable.
- **Contradiction = supersede in place.** A superseded memory is **state-changed,
  not overwritten** (the append-only invariant + audit trail are preserved). On
  conflict within the same scope, the winner is chosen **verified > newer >
  reinforced** — truth beats recency beats popularity.

---

## The eval harness

The harness runs an agent under three conditions and reads the gaps:

| Condition | Meaning |
|---|---|
| `no_memory` | stateless baseline |
| `oracle` | memory ceiling + the task-validity gate (`oracle ≈ no_memory ⇒ reject the task`) |
| `memory_enabled` | the integrated system under test |

Metrics stack in three levels:

- **L1 — retrieval:** precision@k, recall@k, MRR, nDCG, distractor-rate, stale-rate.
- **L2 — utilization:** citation rate + `action_impact` (did memory change the
  tool choice / plan / output, prevent a known failure, improve verification).
- **L3 — end-to-end:** task completion, efficiency (tokens, tool calls, latency,
  cost, retries), correction rate.

**The headline is ablation-based, not merged-PR/CI.** Because outcome linkage is
structurally sparse in this corpus, the headline is a *reward-vs-information curve*
(where does adding more retrieved context stop helping — saturation point +
minimum-useful combination). The agent is its own control across an information
ladder, so this needs neither a reconstructed environment nor a ground-truth
outcome label. Per-rung `reward` is a deterministic check on data the corpus *has*
(did the run avoid/resolve the held-out task's known `trace_error`), plus a
calibrated OSS LLM-judge `rubric_score` for semantic quality. The merged-diff
oracle is opportunistic validation on the few beads that carry PR/commit metadata,
never the headline.

**Competitive arms** (mem0, A-MEM, NAT, Graphiti) run behind one uniform
`ingest`/`retrieve` interface on identical tasks / oracle / scope / precision-guard
/ telemetry. The harness owns the LOO-bounded ingest set; arms never read the
store. Per-arm token + latency overhead is reported, not hidden.

---

## Constraints that shape every choice

- **ZFC boundary.** Deterministic signal (build/test/lint outcomes, `file:line`
  extraction, recurrence math) is *mechanical, in code*. Semantic work (root-cause,
  task-type classification, quality judgment) is *delegated to a model*. No keyword
  heuristics in the deterministic layer.
- **No-paid-API — memory stack only.** Backends, embeddings, extractor, and judge
  must be OSS / self-hosted. This does **not** cover the agent-under-test, Harbor,
  or Docker: the agent runs on a flat-rate Claude OAuth subscription with no
  per-run marginal cost, so running it through Harbor is in-scope mechanism work,
  not a paid-infra fork.
- **The work-audit graph is the source of truth.** Projections rebuild from the
  record JSON; lessons are append-only; a version bump means a rebuild from the
  spine.

---

## Failure modes the design targets

- **Quality (wrong memory)** — distilled-not-literal payload; precision guard;
  truth_confidence decay; contradiction supersession; noise-write-rate diagnostics.
- **Scale (unbounded growth)** — the write gate (`P(improvement|memory)`) is the
  retention valve; `retention_policy: ttl|discard|supersede`.
- **Freshness (stale facts)** — truth_confidence decays with age; stale-read-rate
  is a first-class signature; supersede-in-place.
- **Economics (embedding everything)** — batched/deferred extraction; v1 uses zero
  embedding (deterministic FTS + ranking); embeddings gated behind v1 underperforming.
- **Leakage (cross-task / future)** — temporal LOO + exclusions + per-run scratch
  store for replay writes (never the LOO-bounded corpus).

---

## Roadmap

- **Phase 1 — work-audit graph builder** *(built):* ingest beads/traces/outcomes/
  provenance → WorkRecord spine; SQLite+FTS5 store; deterministic parse; `mem` CLI.
- **Phase 2 — retrieval + replay eval** *(in progress):* failure-triggered
  retrieval-v1 as the first `ours` system; the memory-bench harness with the
  three-condition contract; oracle-bundle assembly + curation.
- **Phase 3+ — the research loop** *(planned):* the full learned controller, the
  competitive-arm bake-off, multi-session sequence tasks, and the fine-tuning / RL
  reranker track (`research/`).
