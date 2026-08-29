# Literal-matcher recall when the query does not match the Memory

Bead `mem-lbuvd`. Preregistration locked at commit `e9e29df` before any recall
number existed, sha256
`ee6f35bf3d79da65cf71b42b1db69223106b01d836e5f268f480e1ab2cf2fed1`.

## Why this experiment exists

The completed ordering experiment and its structural follow-up both report recall
of the primary Memory at 100%. That number is not a measurement. Corpus
construction (`membench/beads_ordering/corpus.py:600-645`) injects the literal
query string into exactly the labelled Memories, and the runtime gate
(`membench/beads_ordering/runner.py:262`) then raises unless the returned
candidate set equals the labelled set. Recall of 1.0 follows with probability 1,
on all 36 tasks. Every ordering conclusion is therefore conditional on the
matcher having already recalled the answer, and neither experiment can say
anything about candidate generation.

This experiment measures candidate generation as a first-class endpoint on a task
class where the gold Memory does not contain the query string.

## What was measured

Two task classes, 36 tasks each.

`lexical_miss`: a generated 680-Memory corpus, seed 5877, digest
`1e164b94fa52dd35266ea92b23d031689013af0c6b29bd896a1b9d825882aa45`. The query
goes into the distractors only; the gold Memory carries a different surface form
for the same thing. Four kinds, 9 tasks each: synonym, abbreviation,
renamed-concept, morphological variant. Construction is gated mechanically
against the materialized text: the query must be absent from the primary and
every entry point, present in at least one distractor (so a score of 0 means the
matcher found the wrong things rather than nothing), and absent from every
unlabelled Memory.

`lexical_hit_control`: the 36 unchanged ordering tasks over the existing frozen
corpus at sizes 50, 100 and 500. The equality gate is re-run on these rather than
assumed, because "recall is 1.0 by construction" is the claim this bead exists to
make explicit.

Three generators over identical document text (`stored_value`, the same string
Beads stores): the shipped case-insensitive substring matcher reached through the
real binary, SQLite FTS5 with `porter unicode61` ranked by bm25, and dense
retrieval by exact cosine over `nomic-embed-text` served locally.

The primary endpoint is recall of the primary Memory at matched-k, where k is the
size of the literal candidate set on the same task, so every generator spends the
same budget. Recall@10, unbounded recall, and candidate-set size are secondary.

Nothing in `beads_ordering` was modified. `candidate_parity`
(`membench/beads_ordering/client.py:114-145`) is an arm-versus-arm gate that
raises when two arms return different candidate sets, and it runs before the
label check. An experiment that varies candidate generation makes differing
candidate sets the expected outcome, and relaxing that symbol would weaken it for
`beads_ordering.runner` and `density_linkage_evidence`, which both call it and
both need it strict. The subset gate lives in a separate package instead.

## Results

Preregistered gates, both generators:

| Gate | Class | FTS | Embedding | Verdict band |
|---|---|---|---|---|
| G1 recovery (recall@matched-k) | miss | 0.000 | 0.056 | `does_not_recover` at ≤ 0.20 |
| G2 no regression (recall@matched-k) | control | 1.000 | 1.000 | `preserves_literal_recall` at ≥ 0.95 |
| G3 candidate cost, median ratio vs literal | control | 1.00x | 2.57x | descriptive, no threshold |
| G3 candidate cost, p90 ratio | control | 2.27x | 6.25x | |
| G3 candidate cost, median ratio vs literal | miss | 1.58x | 226.67x | |
| G3 candidate cost, p90 ratio | miss | 13.0x | 340.0x | |

`candidate_generation_earns_its_own_arm: false`. Neither generator meets both
gates, so on the preregistered primary endpoint this is a null. In the
preregistration's own words for that outcome: the shipped literal matcher is not
the bottleneck this experiment can demonstrate.

G3 is reported per class because the two differ by two orders of magnitude and a
reader of a miss-class experiment would otherwise take the control number as the
miss class's cost. The dense arm's miss-class figure is not a property of the
ranker: `EmbeddingGenerator.rank` returns every Memory, so its ratio is
`corpus_size / |literal set|` and nothing else. FTS's 1.58x is a real measurement.

### Why G1 is 0.000 for FTS, and what that number is not

`analysis.json` carries a `budget_diagnostic` next to every G1 verdict, because
the statistic alone is misleading. On the miss class each task ships `matched_k`
distractors carrying the query verbatim plus one entry Memory, so `matched_k + 1`
labelled documents can outrank the primary inside a budget of `matched_k`. That
holds on 36 of 36 tasks (`budget_saturable_on_every_task: true`).

For FTS the consequence is exact rather than approximate. Of 36 tasks it ranks the
primary at any depth on 9, and on all 9 the primary sits at `matched_k + 2`:
`primary_rank_minus_matched_k` is `{min: 2, median: 2, max: 2}`. FTS's G1 could not
have been anything but 0.000 for any stemmer, tokenizer or lexical weighting. It
is a fact about the fixture, not about lexical retrieval, and it is the same shape
of by-construction artifact this bead was filed to expose in the sibling ordering
experiment.

The endpoint is not unfalsifiable, only badly bounded: the dense arm clears the
budget on 2 of 36 tasks, so `primary_rank_minus_matched_k` reaches `-2` there.
Read G1 as "did any arm beat a budget that a ranker can fill with labelled noise",
and read recall@10 for how the arms actually compare.

Recall@10 on the miss class is where the signal is, per kind, 9 tasks each:

| Miss kind | Literal | FTS | Embedding |
|---|---|---|---|
| morphological | 0.000 | 1.000 | 1.000 |
| synonym | 0.000 | 0.000 | 0.556 |
| abbreviation | 0.000 | 0.000 | 0.222 |
| renamed-concept | 0.000 | 0.000 | 0.000 |
| all 36 | 0.000 | 0.250 | 0.444 |

Unbounded, the embedding arm ranks the primary somewhere in all 36 tasks (it
returns the whole corpus), FTS in 9 of 36. Three repeats of the embedding arm
produced identical top-20 rankings, each repeat re-embedding all 680 documents
from a fresh embedder rather than reusing a cache, so the check covers the
document side and not only the query.

**A renamed concept is invisible to both non-literal generators at every budget.**
Nine tasks, recall 0.000 for FTS and for the embedding arm at matched-k and at
10. A rename shares no stem with the retired name, and in this model it
shares no usable neighbourhood either. Retrieval is the wrong layer for it: what
recovers a rename is an explicit link from the old name to the new one, which is
the Memory-to-Memory link-type question raised on gastownhall/beads#6051.

**Morphological variants are a tokenizer problem with a cheap fix.** Both
non-literal arms recover all 9 at rank ≤ 10, FTS for the cost of a stemmer, and
the substring matcher recovers none of them.

**The literal matcher's control-class ordering is poor, and both other arms fix
it.** On the control class every arm clears matched-k on all 36 tasks. At 10 the
literal arm reaches 1 of 36 (0.028) while FTS and the embedding arm each reach 33
of 36 (0.917). That is a statement about the ordering of the `key` arm, which is
the ordering experiment's baseline and not its recommendation; the bm25f arm was
not run here. It is reported because it shows what a large matched-k budget hides:
G2 is satisfied by all three arms and separates none of them.

### Predictions, recorded before measuring

Graded at recall@10, the secondary endpoint, because at the primary endpoint the
budget bounds every arm near zero and all four predictions resolve to "fails".

| Prediction | Outcome at recall@10 |
|---|---|
| FTS recovers the morphological kind | confirmed, 1.000 |
| FTS fails synonym and renamed-concept | confirmed, 0.000 both |
| Embedding recovers synonym and renamed-concept | half falsified: synonym 0.556, renamed-concept 0.000 |
| Abbreviation is hard for all three | confirmed, FTS 0.000, embedding 0.222 |

At the primary endpoint the third prediction is falsified outright, not halved:
embedding scores 0.000 on synonym and 0.000 on renamed-concept at matched-k. The
falsified line is the most useful one in the table, and it was falsifiable only
because it was written down first.

## Deviations from the preregistration

The preregistration requires deviations to be recorded with the direction of bias
they introduce.

1. **G3 was first computed on the control class only and printed without a class
   label.** Corrected here: it is reported per class. The original presentation
   understated the dense arm's miss-class cost by roughly 88x (2.57x printed
   against 226.67x actual). Direction of bias: flattered the dense arm.

2. **`matched_k`'s headroom is fixed by a free parameter chosen after the lock.**
   `corpus.py`'s `distractor_count = 2 + (index % 3)` sets matched-k to 2, 3 or 4.
   The preregistration named the budget threat but did not fix this constant, so
   it was chosen with the code. Direction of bias: against the non-literal arms,
   and therefore against the author's own hypothesis. Sensitivity check: at a
   budget of 10 the G1 statistics become 0.444 (embedding) and 0.250 (FTS), which
   both land inside the preregistered inconclusive band (0.20, 0.50). The verdict
   labels would move from `does_not_recover` to `inconclusive`;
   `candidate_generation_earns_its_own_arm` stays false either way.

3. **The preregistered runtime gate's third assertion was not implemented, and a
   fourth was added.** Not implemented: a re-check that the labelled set matches
   the frozen fixture at run time. Added: the subset gate's non-empty check, which
   separates "the matcher found nothing" from "the matcher found the wrong
   things". Direction of bias: none identified for the reported statistics; the
   missing assertion is redundant with the corpus digest, which is recorded.

4. **The embedding-repeat check originally re-embedded only the query**, because
   document vectors were cached, and reported the result as arm stability. Fixed
   before this run: each repeat now builds a fresh embedder and re-embeds all 680
   documents. The result is unchanged (identical top-20 rankings), so no reported
   number moves.

## What this cannot claim

Literal recall of 0.000 on the miss class is the definition of the class, enforced
by a construction gate. Reporting it as a finding would repeat the exact
by-construction error this bead was filed against.

The miss class is authored. Its difficulty is set by the author, and the four
kinds are weighted equally here, which is a design choice and not an observed
distribution. These numbers describe performance on a constructed distribution,
not on the queries agents issue.

G2 passing at 1.000 is a weak pass. Median matched-k on the control class is 40
against corpus sizes of 50, 100 and 500, so clearing the budget is nearly free.
Recall@10 is the informative view of that class.

The embedding arm is one small local model chosen for being free and offline. A
stronger embedder could move G1 in either direction, and the renamed-concept
result in particular is a claim about this model.

Corpus sizes are 680 and 500. Nine tasks per kind. Nothing here supports a claim
about a production-scale store.

## Reproducing

From `memory-bench/`:

    BEADS_BIN=<bd binary> uv run python results/lexical_recall/run.py \
      --workspace-root <scratch dir> \
      --out results/lexical_recall/analysis.json

Runtime 27.5s on the recorded run against a warm Ollama daemon. Provenance
pinned in `analysis.json`: `bd` binary sha256 `47aadd11…`,
`nomic-embed-text:latest` digest `0a109f42…` served by Ollama 0.30.10, FTS5 with
`porter unicode61` and bm25. Every provenance lookup raises on failure rather than
recording `unknown`, so a run that reports a digest actually resolved one.

The guard tests are checked by isolated revert rather than by reading them. From
`memory-bench/`:

    uv run python tools/isolated_revert_lexical_recall.py

It breaks 32 guards one at a time and asserts the named test goes red for each. A
guard test that stays green without its guard is measuring fixture geometry rather
than the defence it claims to cover.
