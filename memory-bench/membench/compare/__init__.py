"""`ours` vs semantic-arm head-to-head, retrieval-quality only (mem-compare).

The replay runner (`replay.py`) drives `ours` over the work-audit graph via the
`query_work` path; the semantic arms (`mem0`, a-mem, …) consume the `query_text`
path and retrieve over whatever was *written* into their per-trial scope. The two
arm families therefore never met on one task surface. This package is the bridge:

1. compute the harness LOO-bounded ingest set for a query work `B`
   (`validity.loo_bounded`) — the single door to the corpus, identical for both
   arms;
2. **seed** the semantic arm with exactly that set (each prior bead's text), so it
   is not compared against an empty store (the `mem-bxhh.3` substrate trap);
3. run `ours` (failure-triggered) and the semantic arm (`query_text`) and score
   BOTH `retrieved` work_ids against ONE authored relevant set
   (`grading.retrieval_leg.score_retrieval_leg` — precision/recall/MRR/nDCG);
4. re-check BOTH arms' output against the LOO boundary (`validity.assert_no_leak`).

Retrieval-quality only — NO agent re-run, NO outcome lift, so it is free/local. The
outcome-lift comparison is the paid Harbor path and is deliberately out of scope.
"""

from membench.compare.retrieval_compare import (
    ArmComparison,
    ArmHarvest,
    ComparisonResult,
    compare_arms,
    harvest_ours,
    harvest_semantic,
    ours_replay,
    pool_candidates,
    score_harvest,
    seed_semantic_arm,
    semantic_replay,
)

__all__ = [
    "ArmComparison",
    "ArmHarvest",
    "ComparisonResult",
    "compare_arms",
    "harvest_ours",
    "harvest_semantic",
    "ours_replay",
    "pool_candidates",
    "score_harvest",
    "seed_semantic_arm",
    "semantic_replay",
]
