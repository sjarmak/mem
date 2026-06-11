# mem-75t.5 — irys-stateful-swarms blackboard assessment

**Decision: cross-check-only.** The public blackboard is within-task working
state, not a memory system. It has no load path, no retrieval API, no work-unit
key, and no temporal key — wrapping it as a membench `ours` arm would mean
rebuilding all four around a dataclass that adds nothing our store lacks, and
populating it requires paid Gemini calls. Its value to mem is methodological
(it independently validates D17's empty-state control) plus two narrow schema
ideas worth a low-priority follow-up bead. Receipts below are from a read-only
clone at the repo's default branch, 2026-06-11 (clone deleted after this spike).

## a. What the blackboard actually is in their code

### Typed entries with provenance

`Entry` (`src/swarm/models.py:118-133`) is the knowledge unit: `type`
(observation / analysis / calculation / strategy / contradiction / gap),
`content` (free text), `confidence` (0–1), `status` (active / disputed /
superseded), and three kinds of metadata:

- **created_by provenance** — `WorkerRecord{worker_id, description, iteration}`
  (`models.py:111-114`). Note: iteration counter, not a wall-clock timestamp.
- **source attribution** — `EntrySource{document, section, evidence}`
  (`models.py:104-107`). Anchors to a document section, not to a work unit.
- **dependency edges** — `supports_entries`, `contradicts_entries`,
  `supersedes_entries`, `addresses_signals` (`models.py:130-133`).

`Signal` (`models.py:157-165`) is the open-question companion row: typed
(question / contradiction_resolution / convergence_gap), prioritized, with a
status lifecycle (open / addressed / expired) and `addressed_by` back-pointer.

### The blackboard container

`Blackboard` (`src/swarm/blackboard.py:34-47`) holds `entries`, `signals`,
per-document read state, and token accounting. `add_entry`
(`blackboard.py:89-93`) runs two side-effect passes:

- `_extract_signals` (`blackboard.py:159-171`) — promotes an entry's
  `opens_questions` to Signal rows and closes signals it `addresses_signals`.
- `_propagate_effects` (`blackboard.py:173-232`) — hardcoded confidence
  arithmetic: +0.05 boost per supporting entry (+0.02 when >2 same-source,
  `blackboard.py:184`), −0.12 contradiction penalty plus a synthesized
  `contradiction` entry and a critical signal (`blackboard.py:194-214`),
  supersession flips the target to `superseded` and re-opens signals it had
  addressed (`blackboard.py:223-232`).

Access is id-based only: `find_entry` / `get_entries_by_ids`
(`blackboard.py:104-117`) and an LLM-facing `get_summary`
(`blackboard.py:119-142`). **There is no search, ranking, or query API.**
Selection of which entries a worker sees is done by the orchestrator LLM
naming entry ids in `reads_from_blackboard`
(`src/swarm/orchestrator.py:30`, consumed at
`src/swarm/worker_dispatch.py:342-344`).

### The coordination loop

`run_swarm` (`src/swarm/__init__.py:100-496`) is the whole lifecycle: build a
**fresh** `Blackboard` (`__init__.py:115`, after `reset_id_counters()` at
`__init__.py:112`), structural profiling, seed planning, parallel initial
reading, then the iteration loop (`__init__.py:218-279`): orchestrator LLM
proposes 1–5 workers or `converge`; workers run in parallel and append
entries; convergence is LLM-checked; budget kill at 85%. Post-loop: supervisor
review, state-conversion, coverage repair, curation, synthesis. Every phase is
an LLM call — the loop does not run without a model provider (Gemini,
`src/providers/gemini.py`).

### Snapshot/replay

`save_snapshot` (`blackboard.py:234-250`) serializes the full state to
`<output_dir>/swarm/blackboard_iter_{N}{_label}.json` at ~20 labeled points
through the run (`pre_N`/`post_N` per iteration, `seed`, `converged`,
`post_state_conversion`, … `final`). This is what makes their reasoning trace
auditable, and it is genuinely good diagnostics. But it is **write-only**:
there is no loader, no resume path, and no code anywhere in `src/` that reads
a snapshot back into a `Blackboard`. The whitepaper's "persists across
sessions by default" (`whitepaper.md:114`) describes the JSON files on disk,
not a code path. The benchmark bridge confirms it: `IrysSwarmBackend._run_sync`
(`src/bench.py:174-188`) creates a `tempfile.TemporaryDirectory` per task and
calls `run_swarm` fresh — state is discarded with the tempdir.

### The seam to the closed platform

Cross-session persistence is explicitly delegated to **MapU** — Postgres +
pgvector, entity graphs, conflict-aware supersession, 14 MCP tools — which is
a separate, closed-platform project: `README.md:391-399` ("the persistence
layer that makes stateful swarms practical in production"), `whitepaper.md:358`
("Provenance-backed knowledge memory systems … are a necessary complement to
the swarm pattern"). The public repo contains no client, no schema, and no
integration point for it beyond those prose pointers. The seam is exactly
where a memory system would have to exist — and it is on the closed side.

## b. Wrap-cost analysis

What an `IrysBlackboardMemory(MemorySystem)` adapter
(`membench/memory_systems/base.py:67-89`) would need, and why each piece is a
rebuild rather than a wrap:

**1. Population (corpus → blackboard).** Our arm consumes the LOO-bounded
lessons substrate (`ours_system.py:1-16`). The repo's only way to populate a
blackboard is to run the swarm — every entry is born from an LLM call. A
no-API population path means writing our own `WorkRecord/lessons → Entry`
converter. The mapping itself is mechanical (lesson payload → `Entry.content`,
`type="analysis"`; rig/agent → `created_by.worker_id`; `trace_path` →
`source.document`; citation → `source.evidence`) but it exercises none of
their code: `add_entry`'s side-effect passes need `supports/contradicts` edges
our lessons don't carry, so the converter would feed inert entries into a
container that then behaves like a list.

**2. Retrieval.** `retrieve(RetrievalRequest{query_work, scope})`
(`base.py:32-46`) must return ranked payloads for a query work's failure
context. The blackboard has no query surface (receipts above) — entry
selection in irys is the orchestrator LLM's job, which is a paid call per
retrieval. We would write the retrieval ourselves, against a JSON blob,
re-implementing what retrieval-v1 already does over SQLite+FTS
(`ours_system.py:1-16` — the existing arm delegates to `mem retrieve --json`).

**3. LOO compatibility — fails structurally.** The harness boundary is
temporal-plus-sibling: `loo_boundary = query_from_record(record).started`
(`workrecord_adapter.py:112`), enforced by `validity.loo_bounded`
(`validity.py:117`) and the bundle's `loo_excluded_work_ids`
(`schemas/bundle.py:110`, never empty). Filtering blackboard entries by
excluded work ids requires each entry to carry (i) its originating `work_id`
and (ii) a wall-clock timestamp. `Entry` has neither — `created_by.iteration`
is a per-run counter (`models.py:114`) and there is no time field anywhere on
`Entry` or `Signal`. We would extend their schema with exactly the two keys
our `work_records` table already indexes (`src/store/schema.ts:23-48`:
`work_id` PK, `started_at`/`closed_at` indexed). At that point the "wrap" is
our schema wearing their field names.

**4. Injection payload — the only cheap part.** Rendering entries as
`dict[str, str]` payloads through `RetrieveResult.payloads` into
`inject_context` (`memory_inject.py:125-141`) is trivial; the probe-gate
build-context constraint (`probe_gate.py:176-188`: Harbor's build context is
`environment/` only, so `MEMORY.md` must be COPY'd into the image) is already
solved generically and is payload-agnostic.

**Sum:** of the four pieces, three are from-scratch builds and the fourth is
already built. The wrap inherits a dataclass and ~80 lines of confidence
arithmetic whose inputs (dependency edges, contradictions between concurrent
workers) our corpus does not produce. Cost is not justified by what is
inherited. If a structured-accumulating arm is ever wanted, building it
natively over `lessons` + `trace_errors` is strictly cheaper than adapting
this one.

A second, independent blocker: even if wrapped, the arm could not be
*exercised* as designed without the swarm loop, and the swarm loop is
Gemini-priced per iteration — the no-paid-API constraint ([[mem-paid-stance-scope]])
rules out the only configuration in which the blackboard is more than a typed
list.

## c. What their methodology confirms — and what their numbers do not establish

### Confirms for mem-apg D17

- **The empty-state control is the honest baseline.** "Every task starts from
  an empty blackboard with zero prior state — the hardest possible condition
  for a stateful system, and the only honest way to benchmark" (`README.md:33`).
  This is D17's ablation-first stance arrived at independently: isolate the
  state mechanism's contribution by stripping it, not by demoing it warm.
- **Architecture-over-model as the headline axis.** Gemini Flash at 0% strict
  all-pass under Harvey's agentic scaffolds vs 17.75% under theirs
  (`README.md:59`) is the same claim shape as our none→ours→oracle ladder:
  the lift lives in the scaffold/state, not the weights.
- **Disciplined negative-result ablations.** Their experiments ledger runs
  go-gates and abandons failures: exp-003 (`experiments/EXPERIMENTS.md`)
  found that injecting ~29 strategy entries into the blackboard *regressed*
  extraction tasks by up to 34pp — injected prior context diluted focus. That
  is independent support for our Decision-10 injected-volume precision guard:
  more memory is not monotonically better, and the harness must measure the
  payload volume it injects.

### What their numbers do NOT establish for us

- **0% → 17.75% is not a memory lift.** The 0% is Gemini under *Harvey's*
  agentic evaluations; the 17.75% is Gemini under *their* swarm — different
  scaffolds, different harnesses, different judges (theirs is Gemini 3.1 FL,
  not the recommended Sonnet judge; `README.md:53`), public split vs Harvey's
  private holdout. It is an architecture comparison across systems, not an
  ablation within one. No number in the repo isolates the blackboard's
  *persistence* contribution — there is no warm-vs-cold run anywhere, and
  there cannot be, because the public code has no warm path (receipts in §a).
- **The 7→2400-entry growth is within-task, not cross-task.** The curve
  (`README.md:228-230`, `whitepaper.md:229`) measures information accumulation
  across 12 iterations *inside one task*. Our score-vs-information curve
  (mem-apg .3c) is across the none→ours→oracle rungs — varying *prior*
  cross-task information at fixed task. Their curve has no x-axis we can
  map onto ours; treating it as validation of cross-session memory lift would
  be a category error. The cross-session claims ("the tenth question about
  the same deal costs a fraction of the first", `README.md:395`) are
  projections about MapU, with no public measurement attached.
- **Their criteria are LLM-judged end-task rubrics; ours are deterministic
  trace-error signatures** (`score_run`, grid.py:215-217) with a separate
  judge axis. Their pass rates and our reward components are not comparable
  quantities; only the *shape* of the methodology transfers.

## d. The decision

**Cross-check-only**, with a schema-delta note for a follow-up bead.

1. **There is no memory system to wrap.** The public blackboard is per-task
   working state: created fresh per run (`__init__.py:112-115`,
   `bench.py:174-188`), snapshot write-only, no loader, no retrieval API. The
   thing that would make it a memory system — MapU — is the closed platform,
   reachable only as prose (`README.md:391-399`).
2. **The wrap rebuilds more than it reuses.** Population, retrieval, and LOO
   keys are all from-scratch (§b items 1–3); the only reusable piece, the
   injection path, is already generic in `memory_inject`/`probe_gate`. Adding
   `work_id` + timestamps to `Entry` to satisfy `loo_excluded_work_ids`
   reconstitutes our own schema inside theirs.
3. **Exercising it faithfully violates the cost constraint.** Entry creation,
   entry selection, convergence, and curation are all LLM calls on a paid
   Gemini key; a no-API rendition reduces the blackboard to a typed list and
   would test our converter, not their architecture.

What we keep (the cross-check value): independent convergence on the
empty-state ablation control and architecture-over-model headline (D17); the
exp-003 negative result as external support for the Decision-10 injected-volume
guard; and the per-iteration labeled-snapshot discipline as a pattern worth
copying into any future long-horizon membench runner diagnostics.

**Schema ideas worth a follow-up bead (adopt-schema-only material, not now):**
per-lesson `confidence`, and typed conflict edges (`contradicts` /
`supersedes`) *between lessons* with a `disputed`/`superseded` status
lifecycle. Today `lessons` is append-only with no FK and no conflict state by
design (D9; `src/store/schema.ts:158-170`), and supersession exists only at
the work-record level (`record_links`, `schema.ts:74-79`). If retrieval ever
starts surfacing contradictory lessons for the same failure signature, the
irys `_propagate_effects` lifecycle (dispute both, synthesize a conflict
record, force resolution) is the maturer reference — but adopting it must be
weighed against D9's never-re-distilled contract, which a status mutation
arguably violates. That trade is its own bead, gated on observing actual
lesson conflicts in retrieval output, not on this spike.

**Recommended follow-up bead:** "lessons conflict-state schema: evaluate
per-lesson confidence + contradicts/supersedes edges against the D9
append-only contract (irys `_propagate_effects` as reference)" — P3, blocked
on first observed contradictory-lesson retrieval in a real grid run.
