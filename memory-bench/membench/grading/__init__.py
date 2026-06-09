"""Outcome/grading sources + the coverage probe (mem-apg.1).

The verifier oracle is a FAMILY of sources (Stephanie, 2026-06-08): `merged_diff`
(the locked headline, where constructible) and `ablation` (env-independent, always
feasible). `OutcomeSource` is the uniform feasibility contract they share; the
coverage probe surveys which source can grade each held-out task before any Docker
or paid run. Named `grading` (not `oracle`) to avoid colliding with the existing
`OracleMemory` memory-ceiling condition (architect finding H3).
"""

from membench.grading.ablation import DEFAULT_RUNGS, AblationDesign, AblationSource
from membench.grading.base import Feasibility, OutcomeSource
from membench.grading.coverage import (
    CoverageSummary,
    SourceCount,
    SourceCoverage,
    coverage_table,
    recommend_source,
    summarize,
)
from membench.grading.leak_guard import (
    OutcomeLeakError,
    assert_no_outcome_leak,
    outcome_labels,
)
from membench.grading.merged_diff import MergedDiffSource

__all__ = [
    "DEFAULT_RUNGS",
    "AblationDesign",
    "AblationSource",
    "CoverageSummary",
    "Feasibility",
    "MergedDiffSource",
    "OutcomeLeakError",
    "OutcomeSource",
    "SourceCount",
    "SourceCoverage",
    "assert_no_outcome_leak",
    "coverage_table",
    "outcome_labels",
    "recommend_source",
    "summarize",
]
