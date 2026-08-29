# Literal-matcher recall when the query misses the gold Memory

Bead: `mem-lbuvd`. Preregistration:
`memory-bench/fixtures/lexical_recall/preregistration.json`
(locked before any recall number was computed; sha256 recorded in the locking
commit message).

## The question

When the gold Memory does not literally contain the query, does any non-literal
candidate generator recall it, and what does the extra recall cost in candidates?

## Why this exists

The completed ordering experiment and its structural follow-up both pin recall of
the primary Memory at 1.0 **by construction**.

`validate_rank_truth` (`memory-bench/membench/beads_ordering/runner.py:262`)
raises unless the literal candidate set equals the labelled set
`{primary_relevant, acceptable_entry_points, distractors}`. The authoring path
guarantees it will: `beads_ordering/corpus.py:600-645` injects the literal query
string into exactly the labelled Memories and nowhere else. Across all 36 tasks
there is not one primary miss, and there cannot be one.

So neither experiment says anything about candidate-generation quality. Every
ordering conclusion is conditional on the matcher having already recalled the
answer. This experiment measures that conditioning event.

## What stays untouched

The ordering corpus, its equality gate, and its published result do not change.
Folding candidate generation into that grid would destroy the single-variable
isolation that makes the ordering result worth believing.

That isolation has a concrete mechanical consequence. `candidate_parity`
(`beads_ordering/client.py:114-145`) is an arm-versus-arm gate: it raises when two
arms return different candidate sets, and it runs at `runner.py:258`, before the
label check ever executes. An experiment that varies candidate generation makes
differing candidate sets the expected outcome. Relaxing that shared symbol would
weaken the gate for `runner.py` and `density_linkage_evidence.py` too, which both
call it and both need the strict behaviour.

The new gate is therefore a new function in a new package
(`memory-bench/membench/lexical_recall/`), and it is a subset assertion rather
than an equality one: the literal candidate set must be a subset of the labelled
set, and the primary must be absent from it.

## The task class

A lexical-miss task has a query `Q` such that:

- `Q` does not appear as a case-insensitive substring of the primary Memory's key
  or stored value, nor of any acceptable entry point's, and
- `Q` does appear literally in at least one distractor.

The second condition matters as much as the first. Without it the literal matcher
returns nothing and recall is 0 for a degenerate reason. With it, the literal arm
returns a non-empty and wrong candidate set, which is a real miss.

Four kinds, nine tasks each:

| kind | query | primary's surface form |
| --- | --- | --- |
| synonym | lease renewal | tenancy extension |
| abbreviation | time to live | TTL |
| renamed concept | worker pool | executor group |
| morphological | renewing leases | lease renewal |

Nine per kind is too small to gate on. Per-kind recall is reported as a stratum
with its `n`, so a reader can see which capability drives the aggregate, and it is
not used to reach a verdict.

The control class is the 36 existing ordering tasks, reused unchanged.

## The three generators

All three index exactly the string `MemoryFixture.stored_value()` produces. No arm
sees text another arm does not.

**Literal** is Beads' shipped case-insensitive substring matcher over key and
complete stored value, reached through
`bd --json memories <query> --experimental-order key --page-size all`. Candidate
generation in the pinned experimental binary is identical to stock: BM25F reorders
the map `memoryops.List` already returned and cannot add or remove a candidate.
The ordering experiment and this one therefore see the same candidate sets.

**FTS** is SQLite FTS5 with the `porter unicode61` tokenizer, ranked by `bm25()`.
The query is split on non-alphanumerics, each term quoted, joined with `OR`. A
phrase query would require adjacency and would sink the morphological and synonym
kinds for a reason that has nothing to do with the tokenizer. Bag-of-words `OR` is
what a search index actually does. That choice changes the recall number, so it is
locked in the preregistration rather than tuned once results exist.

**Embedding** is dense retrieval with exact in-process cosine and no ANN, over
`nomic-embed-text` served by the local Ollama daemon at 768 dimensions. It is
local, offline, already present, and free, which is why it was chosen. It is not
the strongest available embedder, and the result generalizes to this model only.

## The endpoint and the budget

The headline is recall of `primary_relevant` in the generator's candidate set **at
matched-k**, where `k` is the size of the literal candidate set for that same task.

Recall without a size control is gameable: a generator that returns the whole
corpus scores 1.0. Matched-k holds the budget at what the shipped matcher already
costs. Because matched-k varies across tasks, `recall@10` is reported alongside it
as a fixed absolute budget, together with unbounded recall as a ceiling and the
candidate-set sizes each generator actually returns.

## The gates

**G1, recovery.** Primary recall@matched-k on the lexical-miss class, per
non-literal generator. At or above 0.50 the generator recovers; at or below 0.20 it
does not; between the two the result is inconclusive and no recommendation is made.

**G2, no regression.** Primary recall@matched-k on the control class. At or above
0.95 the generator preserves literal recall; at or below 0.80 it regresses. A
generator that recovers misses while dropping Memories the literal matcher already
found is not an improvement.

**G3, candidate cost.** Median and p90 of the candidate-set size ratio against the
literal arm, unbounded. Descriptive, no threshold.

Candidate generation earns its own arm in future ordering work if and only if some
generator both recovers under G1 and preserves literal recall under G2.

## Predictions, recorded before measuring

FTS should recover the morphological kind, because the porter tokenizer stems both
surface forms to the same token and the substring matcher cannot. FTS should fail
synonym and renamed-concept, where no lexical overlap survives stemming. The
embedding arm should recover exactly those two. Abbreviation should be hard for all
three, since an initialism shares no stem with its expansion.

If FTS beats the embedding arm on synonyms, or the embedding arm fails
renamed-concept, that is the finding, and it gets the same prominence as a
confirmation would.

## What this cannot claim

The literal arm's recall on the lexical-miss class is 0 because the construction
gate forces it to be. That is the definition of the class, not a measured result.
Reporting it as a finding would repeat the exact by-construction error this bead
was filed against.

The miss class is authored, so its difficulty is set by the author rather than
observed. These numbers estimate performance on a constructed distribution, not on
the queries agents actually issue. That is the mirror image of the defect the
experiment exists to name, and it belongs in the writeup at the same prominence as
the headline.

## Outcome

The run landed. Numbers, gate verdicts and provenance are in
`memory-bench/results/lexical_recall/` (`report.md`, `analysis.json`,
`manifest.json`).

Headline: neither non-literal generator meets the preregistered primary endpoint,
so `candidate_generation_earns_its_own_arm` is false. In the preregistration's
words for that outcome, the shipped literal matcher is not the bottleneck this
experiment can demonstrate.

The signal sits in the secondary endpoint the threat list called out in advance.
At recall@10 on the miss class the embedding arm reaches 0.444 and FTS 0.250. The
literal arm's 0.000 is not a comparator: it is the definition of the class,
enforced by a construction gate, and reporting it as a baseline would repeat the
by-construction error this bead was filed against. The arms that can be compared
are FTS and the dense arm, and the per-kind split is where the design pays off:
morphological variants are recovered by both, synonyms only by the dense arm, and
renamed concepts by neither at any budget.

Read the primary endpoint with its `budget_diagnostic`. On all 36 miss tasks the
matched-k budget can be filled by labelled non-primary documents, and for FTS the
primary sits at exactly `matched_k + 2` on all 9 tasks where it is retrieved at
all, so that arm's G1 of 0.000 is a fact about the fixture rather than about
lexical retrieval. `report.md` §Deviations records the sensitivity check: at a
budget of 10 both arms land in the inconclusive band and the recommendation is
unchanged.

One prediction was falsified. The embedding arm was predicted to recover both
synonym and renamed-concept; at recall@10 it recovers synonyms at 0.556 and
renamed concepts at 0.000, and at the primary endpoint it recovers neither. A
renamed concept needs an explicit link, not a better retriever.
