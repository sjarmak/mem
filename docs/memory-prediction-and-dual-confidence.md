# Design note — write-gate as `P(improvement | memory)` + dual confidence

> Status: proposal (additive). Extends `docs/architecture-decisions.md` Decisions 12 & 14 and the
> spec's `candidate_memory` schema (§ candidate memories). Nothing here changes the
> v1 retrieval-v1 (`mem-di8`) path or the LOO discipline; both items are designed
> now and filled with a heuristic until there is data to learn from.

## Why

The current design already captures the right *inputs* — the full `rerank_features`
vector "captured from run one" (Decision 12) and a stage-6 "post-task write
decision" in the controller loop (Decision 14) — but it never names the **objective**
those inputs serve, and `candidate_memory.confidence` is a single scalar that
conflates two independent questions. This note closes both gaps without adding new
machinery: it makes the write decision an explicit learnable predictor over features
that already exist, and it splits one confidence field into two.

## A. The post-task write decision is `P(improvement | memory)`

Stage 6 of the controller (Decision 14) decides whether an extracted
`candidate_memory` is committed to the corpus. Frame that decision as a scored
prediction:

```
score = P(a future task's outcome improves | this memory is retrievable)
```

estimated from the features the telemetry record already logs (Decision 12):
`relevance / recency / importance / trust / task-fit + procedural / relational`,
plus the storage-decision signals the spec's retention filters imply — access
frequency, novelty (distance to nearest existing memory), retrieval success on
replay, user-correction events, and downstream task completion.

- **v1 is the heuristic / LLM-judge** already mandated for every learnable stage —
  the spec's four retention filters ("useful beyond this session? specific enough?
  scoped right? stale/superseded/transient?") *are* this gate, run by a judge. No
  new code beyond logging the gate's score and decision.
- **The label is free.** Each retention metric the spec already defines
  (`write_hit_rate`, `over_retention_rate`, `noise_write_rate`,
  `supersession_correct`) is exactly the supervision signal for the predictor. The
  gate is trainable the moment replay produces enough labeled write decisions; until
  then the judge stands in. This is the same "fill the learnable stage with a
  heuristic, log the decision, learn later" posture as the rest of the controller.
- **ZFC boundary holds.** The *features* are mechanical (recency, frequency, novelty
  distance, retrieval-success counts — arithmetic over logged events). The
  *judgment* ("is this worth keeping?") is the model's, via the score. We do not
  hardcode semantic thresholds; we learn the gate or delegate it.
- **Eval hook:** report the gate as a precision/recall curve against the retention
  metrics, and tie its operating point to the Decision-10 Precision Guard — a
  write-gate that over-retains shows up as injected-context volume creep on lift
  runs, so the two guards bracket the same failure from both ends (write-time and
  read-time).

This is the predictive framing the search found missing: not a new subsystem, a
*name and an objective* for the stage-6 decision that already exists.

## B. Split `candidate_memory.confidence` into retrieval vs truth confidence

`candidate_memory.confidence` is currently one scalar. It answers two unrelated
questions that move independently, so it should be two fields:

```yaml
candidate_memory:
  # ... existing fields ...
  retrieval_confidence: number   # how strongly this should surface for matching tasks
  truth_confidence:     number   # how likely the content is still correct
```

- **`retrieval_confidence`** is a *ranking* signal — it rises with reinforcement
  (frequency, retrieval success, task-fit). It feeds reranking and the write-gate's
  `score`.
- **`truth_confidence`** is a *correctness* signal — it rises only with verification
  (CI pass, merged diff, a later record confirming the same root cause) and **decays
  with age** and on contradiction. It never rises from popularity.

The two must stay separate because **a frequently-retrieved memory can still be
wrong**: high `retrieval_confidence` + low `truth_confidence` is the precise
signature of a stale-but-popular fact, and collapsing them into one scalar makes
that state unrepresentable. It also gives the existing diagnostics real inputs —
"High stale-read rate" (spec §13) becomes *detectable* as retrievals of records
whose `truth_confidence` has decayed below a floor.

### Contradiction handling (already half-specified)

The spec's `retention_policy: supersede` + `supersedes: [memory_id]` and the
append-only lessons invariant already say a superseded memory is **state-changed,
not overwritten**. This note adds the resolution rule that uses the two confidences:

When a new candidate contradicts an existing memory (same scope + `task_family`,
incompatible content):

1. The older record's state moves to `superseded` (state change, not delete — the
   append-only invariant and the audit trail are preserved).
2. The winner is chosen by a lexicographic preference: **verified** (higher
   `truth_confidence`) > **newer** (recency) > **more reinforced** (higher
   `retrieval_confidence`). Truth beats recency beats popularity — so a heavily-used
   but unverified memory loses to a fresh verified one, which is the whole point of
   keeping the axes apart.
3. `contradiction_resolution_success` (spec § synthesis metrics) scores whether the
   agent then retrieved the winner, not the superseded record.

## Surface area

- **Spec:** add `retrieval_confidence` / `truth_confidence` to `candidate_memory`;
  keep `confidence` as a deprecated alias mapping to `truth_confidence` for one
  version.
- **Telemetry (Decision 12):** log the stage-6 gate `score` and both confidences
  alongside `rerank_features` — they are more learned-controller inputs, captured
  from run one.
- **No change** to retrieval-v1, the LOO reader, exclusions, or the ablation
  headline. Both items are conditioning/representation, not new eval objects.
