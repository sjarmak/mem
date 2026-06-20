"""Synthetic-data realism metric (mem-ovi).

Quantifies how realistic a synthetic eval corpus is versus real agent traces, on
three independently-reported axes:

* STRUCTURAL  — mechanical distributional match of task shapes (``distance``).
* SEMANTIC    — a model judge's per-task realism rating (``semantic``).
* CONSTRUCT   — rank-correlation of memory-arm performance, N-flagged (``construct``).

``report.assess_realism`` assembles the three into a `RealismReport` whose
``defensible`` flag is a transparent AND over the per-axis gates — never an opaque
composite. See ``report.py`` for the gating policy and the publication-freeze note.
"""

from membench.realism.construct import (
    ConstructVerdict,
    FlatSampleError,
    construct_validity,
    construct_validity_from_arms,
    spearman_rho,
)
from membench.realism.distance import (
    StructuralReport,
    ks_statistic,
    structural_realism,
)
from membench.realism.features import (
    FEATURE_NAMES,
    TaskFeatures,
    TraceStep,
    features_from_sequence,
    features_from_trace_steps,
)
from membench.realism.report import (
    PerTaskRealism,
    RealismReport,
    assess_realism,
)
from membench.realism.semantic import (
    SemanticAggregate,
    SemanticVerdict,
    aggregate_semantic,
    build_semantic_prompt,
    parse_semantic_verdict,
    score_semantic_realism,
    task_text_for_sequence,
)

__all__ = [
    "FEATURE_NAMES",
    "ConstructVerdict",
    "FlatSampleError",
    "PerTaskRealism",
    "RealismReport",
    "SemanticAggregate",
    "SemanticVerdict",
    "StructuralReport",
    "TaskFeatures",
    "TraceStep",
    "aggregate_semantic",
    "assess_realism",
    "build_semantic_prompt",
    "construct_validity",
    "construct_validity_from_arms",
    "features_from_sequence",
    "features_from_trace_steps",
    "ks_statistic",
    "parse_semantic_verdict",
    "score_semantic_realism",
    "spearman_rho",
    "structural_realism",
    "task_text_for_sequence",
]
