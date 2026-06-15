"""§9.2 — the multi-session benchmark sequence and its steps.

The eval object is a *sequenced workload* (plan §A, DIV-2): Step1→…→Goal, fresh
context per step, with the persistent memory store the only continuity channel
(except under the oracle condition). Bead replay is one source feeding sequence
construction; this module is the construction target.
"""

from typing import Any

from pydantic import BaseModel, Field


class OutcomeCheck(BaseModel):
    """A deterministic check on a step's outcome (§9.3)."""

    check_id: str
    description: str = ""
    # Memory ids whose availability this check depends on. Empty => the check does
    # not require memory (passes statelessly). Non-empty => the step is
    # memory-sensitive: the agent must have the listed memory available to pass.
    requires_memory: list[str] = Field(default_factory=list)


class MemoryProbe(BaseModel):
    """A probe asserting a specific memory was used/available (§9.3)."""

    probe_id: str
    expected_memory_id: str
    description: str = ""


class SequenceStep(BaseModel):
    step_id: str
    user_request: str
    available_tools: list[str] = Field(default_factory=list)
    environment_state: dict[str, Any] = Field(default_factory=dict)
    # Memory the step is expected to establish (id → content) and to depend on.
    expected_memory_writes: dict[str, str] = Field(default_factory=dict)
    expected_memory_reads: list[str] = Field(default_factory=list)
    outcome_checks: list[OutcomeCheck] = Field(default_factory=list)
    memory_probes: list[MemoryProbe] = Field(default_factory=list)
    # Distracting-but-irrelevant memories (§10 interference). Defined for schema
    # completeness; SEEDING into the store is a Phase-2 stressor and is NOT wired
    # into the skeleton runner yet (distractor_retrieval_rate stays 0 until then).
    distractor_memories: dict[str, str] = Field(default_factory=dict)
    # Staleness/supersession marker (§10.C). Memory ids written by an EARLIER step
    # that this step makes stale by establishing a newer value under a *distinct*
    # id (the runner's oracle pool rejects same-id/different-content rewrites, so
    # supersession is modeled as v1→v2 distinct ids, with later reads depending on
    # v2 only). This is a metadata annotation for dataset analysis and the
    # stale_memory_used diagnostic; the skeleton runner does not act on it yet,
    # and it defaults empty so existing fixtures stay valid.
    superseded_memory_ids: list[str] = Field(default_factory=list)


class BenchmarkSequence(BaseModel):
    sequence_id: str
    title: str
    domain: str = ""
    goal: str = ""
    steps: list[SequenceStep]
    final_goal_check: dict[str, Any] = Field(default_factory=dict)
    # S2 schema-induction oracle (additive): the abstract rule every episode
    # INSTANTIATES without stating it verbatim — the answer the final probe must
    # induce. None for non-schema sequences, so existing fixtures stay valid. The
    # source-trace set is the written episode ids (expected_memory_writes across
    # steps); it is not duplicated here.
    latent_rule: str | None = None
