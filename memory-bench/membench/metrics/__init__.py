"""Deterministic metric scorers (§12).

Pure mechanism (ZFC / patterns.md §ZFC): set arithmetic, ranking math, and counting
over what a trial actually produced. No semantic judgment lives here — fields that
need a judge (rubric_score, completion_quality, action-impact booleans,
derailment_signal magnitude) are left at their None/default seams by the callers.
"""

from membench.metrics.scorers import (
    InterruptionInputs,
    PrivacyInputs,
    RetentionInputs,
    RetrievalInputs,
    SynthesisInputs,
    score_efficiency,
    score_interruption,
    score_privacy,
    score_retention,
    score_retrieval,
    score_synthesis,
)

__all__ = [
    "InterruptionInputs",
    "PrivacyInputs",
    "RetentionInputs",
    "RetrievalInputs",
    "SynthesisInputs",
    "score_efficiency",
    "score_interruption",
    "score_privacy",
    "score_retention",
    "score_retrieval",
    "score_synthesis",
]
