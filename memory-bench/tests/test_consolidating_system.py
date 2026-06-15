"""S1 — the ConsolidatingMemory arm + the recombine-vs-dedupe ablation.

The one wave-1 frontier arm. Contract:

* ``write()`` is an O(1) wake-append — ZERO model calls on the hot path; all model
  cost is deferred to the offline ``consolidate()`` pass (M6 honesty foundation).
* the decisive ablation is ``mode in {recombine, dedupe_only}``: ``dedupe_only``
  removes near-duplicates and emits NO schema rows; ``recombine`` synthesises a
  schema row that abstracts the cluster (the latent pattern surviving across
  instances) — so the schema-induction signal differs by mode.
* subtractive ops are tombstone-only (soft, content retained, re-derivable) — there
  is no hard-delete primitive in the arm (a source-scan test enforces it).
* every item a retrieve returns carries non-empty ``source_trace_ids`` (M7).
* the cluster summariser is injected behind a Protocol, so CI runs a deterministic
  fake with no paid API.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.consolidating_system import (
    ClusterSummarizer,
    ConsolidatingMemory,
    SharedTokenSummarizer,
    SummaryResult,
)
from membench.memory_systems.consolidation import ConsolidationCapable
from membench.runtime import IdClock, StepContext


def _ctx(trial="t-1", step="s"):
    return StepContext(trial_id=trial, session_id="sess", step_id=step, clock=IdClock())


class _CountingSummarizer:
    """A summariser standing in for a model: records its call count and reports a
    positive background-token cost, so the meter and the write-path-model-free
    contract are both testable without a real model."""

    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, *, cluster_contents):
        self.calls += 1
        return SummaryResult(text="merged " + " ".join(cluster_contents), background_tokens=7)


# Three episodes that all instantiate one latent rule "snake_case for db columns",
# each with a distinct instance suffix → they share the rule tokens (cluster) but
# differ in surface.
_RULE_EPISODES = {
    "ep1": "convention snake_case db columns user_id table",
    "ep2": "convention snake_case db columns order_total table",
    "ep3": "convention snake_case db columns created_at table",
}


def _seed(arm: ConsolidatingMemory):
    ctx = _ctx()
    for mid, content in _RULE_EPISODES.items():
        arm.write(mid, content, ctx)


def test_arm_is_consolidation_capable():
    assert isinstance(ConsolidatingMemory(), ConsolidationCapable)
    assert isinstance(SharedTokenSummarizer(), ClusterSummarizer)


def test_factory_builds_the_arm_with_defaults_and_mode():
    arm = build_memory_system("consolidating")
    assert isinstance(arm, ConsolidatingMemory)
    assert arm.mode == "recombine"
    assert build_memory_system("consolidating", mode="dedupe_only").mode == "dedupe_only"


def test_invalid_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        ConsolidatingMemory(mode="merge_everything")


def test_write_path_makes_zero_model_calls():
    summ = _CountingSummarizer()
    arm = ConsolidatingMemory(mode="recombine", summarizer=summ)
    arm.reset("t-1")
    _seed(arm)
    # No summariser call happened on the write path — all cost is deferred.
    assert summ.calls == 0


def test_recombine_emits_a_schema_row_dedupe_does_not():
    summ_r = _CountingSummarizer()
    rec = ConsolidatingMemory(mode="recombine", summarizer=summ_r)
    rec.reset("t-r")
    _seed(rec)
    res_r = rec.consolidate(_ctx("t-r", "consolidate"))
    assert len(res_r.items) >= 1
    assert summ_r.calls >= 1  # recombine calls the summariser (offline)
    assert res_r.background_tokens >= 7  # metered from the summariser

    summ_d = _CountingSummarizer()
    ded = ConsolidatingMemory(mode="dedupe_only", summarizer=summ_d)
    ded.reset("t-d")
    _seed(ded)
    res_d = ded.consolidate(_ctx("t-d", "consolidate"))
    assert res_d.items == ()  # dedupe emits NO schema rows
    assert summ_d.calls == 0  # and never calls the summariser
    assert res_d.background_tokens == 0


def test_recombined_row_recovers_the_shared_rule_and_cites_sources():
    arm = ConsolidatingMemory(mode="recombine")  # default SharedTokenSummarizer
    arm.reset("t-1")
    _seed(arm)
    res = arm.consolidate(_ctx())
    row = res.items[0]
    # The shared rule tokens survive into the schema row; the instance-specific
    # tokens (user_id / order_total / created_at) do not.
    assert "snake_case" in row.content
    assert "user_id" not in row.content
    # Provenance: the row cites the cluster episodes (non-empty, M7).
    assert set(row.source_trace_ids) == {"ep1", "ep2", "ep3"}
    assert res.tombstoned_ids != ()


def test_tombstone_is_soft_content_stays_re_derivable():
    arm = ConsolidatingMemory(mode="recombine")
    arm.reset("t-1")
    _seed(arm)
    arm.consolidate(_ctx())
    # Episodes were tombstoned but remain present (re-derivable) — is_live True.
    for ep in _RULE_EPISODES:
        assert arm.is_live(ep) is True
    # A trace never written is not live (the reachability negative case).
    assert arm.is_live("never-written") is False


def test_retrieve_items_all_carry_provenance():
    arm = ConsolidatingMemory(mode="recombine")
    arm.reset("t-1")
    _seed(arm)
    arm.consolidate(_ctx())
    from membench.memory_systems.base import RetrievalRequest

    # Asking for a now-subsumed episode redirects to its schema row, with provenance.
    res = arm.retrieve(RetrievalRequest(query_text="rule", requested_ids=["ep1"]), _ctx())
    assert res.payloads, "expected the schema row that subsumes ep1"
    for mid in res.payloads:
        assert res.source_trace_ids.get(mid), f"item {mid} has no provenance"


def test_no_hard_delete_primitive_in_the_arm_source():
    # Tier-1 reversibility (structural, ZFC): the subtractive arm must not reach a
    # hard-delete primitive — tombstone (soft) is the only sanctioned disposition.
    src = (
        Path(__file__).resolve().parents[1]
        / "membench"
        / "memory_systems"
        / "consolidating_system.py"
    ).read_text(encoding="utf-8")
    for banned in ("os.remove", ".unlink(", "shutil.rmtree", "del self.", ".pop("):
        assert banned not in src, f"hard-delete primitive {banned!r} reachable in arm"
