"""§12 — metric groups.

Core groups implemented now (bead scope): task / efficiency / retrieval / retention.
Privacy + interruption groups are ADDED per plan §A DIV-4 but their fields are
*stubbed* here (defaults / None) — full computation lands in the metrics phase
(mem-lvp). Synthesis (§12.5) and action-impact (§12.6) are likewise later-phase and
not modeled in the skeleton.
"""

from pydantic import BaseModel, Field


class TaskMetrics(BaseModel):
    """§12.1 — task outcome."""

    reward: float = 0.0  # 0-1
    pass_: bool = Field(default=False, alias="pass")
    final_goal_success: bool = False
    verifier_errors: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class EfficiencyMetrics(BaseModel):
    """§12.2 — efficiency."""

    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls_total: int = 0
    memory_tool_calls: int = 0
    non_memory_tool_calls: int = 0
    wall_clock_latency_ms: float = 0.0
    turns: int = 0


class RetrievalMetrics(BaseModel):
    """§12.3 — retrieval."""

    read_attempted: bool = False
    relevant_memory_available: bool = False
    relevant_memory_retrieved: bool = False
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    distractor_retrieval_rate: float = 0.0
    missed_required_memory_count: int = 0


class RetentionMetrics(BaseModel):
    """§12.4 — retention."""

    expected_memory_written: bool = False
    write_hit_rate: float = 0.0
    write_miss_rate: float = 0.0
    noise_write_rate: float = 0.0


class PrivacyMetrics(BaseModel):
    """Privacy axis — ADDED per plan §A DIV-4. Stubbed; measured, not acted on."""

    privacy_class: str | None = None  # none | internal | sensitive
    leakage_flags: list[str] = Field(default_factory=list)


class InterruptionMetrics(BaseModel):
    """Interruption axis — ADDED per plan §A DIV-4. Stubbed; measured, not acted on."""

    derailment_signal: float | None = None
    inject_timing: str | None = None  # on_failure | off_failure


class MetricsBundle(BaseModel):
    """All metric groups for one trial (one step under one condition)."""

    task: TaskMetrics = Field(default_factory=TaskMetrics)
    efficiency: EfficiencyMetrics = Field(default_factory=EfficiencyMetrics)
    retrieval: RetrievalMetrics = Field(default_factory=RetrievalMetrics)
    retention: RetentionMetrics = Field(default_factory=RetentionMetrics)
    privacy: PrivacyMetrics = Field(default_factory=PrivacyMetrics)
    interruption: InterruptionMetrics = Field(default_factory=InterruptionMetrics)
