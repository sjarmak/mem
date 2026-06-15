"""End-to-end schema-induction headline (S1+S2 integrated, on the sequence runner).

Ties the wave-1 Track-2 pieces together:

* the CI admission test (premortem M-alpha): a ConsolidationCapable arm MUST be
  reachable from the headline (sequence-runner) path;
* the decisive recombine-vs-dedupe contrast driven through ``run_sequence`` over an
  S2 sequence — recombine recovers the rule, dedupe does not;
* the **never-synthetic-only** discipline as an executable assertion: the summary
  REFUSES to produce a headline without a real-anchor leg (B-1 — the corpus can't
  carry the signal, so SEA-Eval-as-BenchmarkSequence is the real anchor);
* the headline reported as the per-mode ``(Δrecall, confabulation_rate)`` PAIR;
* confabulation wired into the run-level safety_gates (flag-and-quarantine, B-2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from membench.generators.external_anchor import load_external_schema_sequences
from membench.generators.schema_induction import (
    GENERATOR_VERSION,
    generate_schema_induction_sequence,
)
from membench.grading.safety_gates import compute_safety_gates
from membench.memory_systems import build_memory_system
from membench.memory_systems.consolidating_system import ConsolidatingMemory, SummaryResult
from membench.memory_systems.consolidation import ConsolidationCapable
from membench.metrics.schema_scorers import confabulation_findings, episode_source_texts
from membench.report.schema_induction import (
    run_schema_induction_modes,
    score_schema_induction_run,
    summarize_schema_induction,
)
from membench.runner.conditions import run_sequence
from membench.schemas.conditions import Condition
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig

ANCHOR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sequences" / "sea_eval_schema_anchor.jsonl"
)


def _exp():
    return ExperimentConfig(
        experiment_id="exp-schema",
        agent=AgentConfig(agent_config_id="scripted-ref", runtime="scripted"),
        memory=MemoryConfig(memory_config_id="consolidating", system="consolidating"),
        dataset_id="schema-induction",
    )


# --------------------------------------------------------------------------- #
# Real anchor (B-1)
# --------------------------------------------------------------------------- #
def test_external_anchor_loads_as_sequences():
    seqs = load_external_schema_sequences(ANCHOR)
    assert seqs, "the real-anchor fixture must yield at least one sequence"
    for s in seqs:
        assert s.latent_rule
        writes = [m for st in s.steps for m in st.expected_memory_writes]
        assert len(writes) >= 2
        assert set(s.steps[-1].expected_memory_reads) == set(writes)


# --------------------------------------------------------------------------- #
# CI admission (premortem M-alpha)
# --------------------------------------------------------------------------- #
def test_consolidation_arm_is_reachable_from_the_headline_path():
    arm = build_memory_system("consolidating")
    assert isinstance(arm, ConsolidationCapable)
    seq = generate_schema_induction_sequence(seed=1, n_episodes=4)
    run = run_sequence(seq, _exp(), conditions=[Condition.MEMORY_ENABLED], memory_system=arm)
    # The headline path actually dispatched consolidate() to it.
    assert run.consolidations.get(Condition.MEMORY_ENABLED.value) is not None


# --------------------------------------------------------------------------- #
# The decisive contrast
# --------------------------------------------------------------------------- #
def test_recombine_beats_dedupe_end_to_end():
    seq = generate_schema_induction_sequence(seed=1, n_episodes=4)
    scores = run_schema_induction_modes(seq, _exp())
    assert scores["recombine"].schema_recall > scores["dedupe_only"].schema_recall
    assert scores["dedupe_only"].schema_recall == 0.0  # no schema rows ⇒ no recall
    assert scores["recombine"].confabulation.rate == 0.0  # faithful summary


def test_score_run_reads_the_consolidation_output():
    seq = generate_schema_induction_sequence(seed=2, n_episodes=4)
    arm = ConsolidatingMemory(mode="recombine")
    run = run_sequence(seq, _exp(), conditions=[Condition.MEMORY_ENABLED], memory_system=arm)
    score = score_schema_induction_run(seq, run)
    assert score.n_schema_rows >= 1
    assert score.schema_recall > 0.0


# --------------------------------------------------------------------------- #
# never-synthetic-only + the pair
# --------------------------------------------------------------------------- #
def test_summary_refuses_synthetic_only():
    seq = generate_schema_induction_sequence(seed=1, n_episodes=4)
    synth = run_schema_induction_modes(seq, _exp())
    with pytest.raises(ValueError, match="synthetic-only"):
        summarize_schema_induction(
            synthetic=synth,
            real_anchor=None,
            generator_version=GENERATOR_VERSION,
            success_threshold=0.5,
        )


def test_summary_reports_the_pair_threshold_and_both_legs():
    seq = generate_schema_induction_sequence(seed=1, n_episodes=4)
    synth = run_schema_induction_modes(seq, _exp())
    anchor = run_schema_induction_modes(load_external_schema_sequences(ANCHOR)[0], _exp())
    out = summarize_schema_induction(
        synthetic=synth,
        real_anchor=anchor,
        generator_version=GENERATOR_VERSION,
        success_threshold=0.5,
    )
    assert out["generator_version"] == GENERATOR_VERSION
    assert out["success_threshold"] == 0.5
    for leg in ("synthetic", "real_anchor"):
        pair = out[leg]
        assert {"delta_recall", "recombine_confab", "dedupe_confab", "recombine_wins"} <= set(pair)
    assert isinstance(out["headline_win"], bool)


# --------------------------------------------------------------------------- #
# confabulation → safety_gates (B-2 flag-and-quarantine)
# --------------------------------------------------------------------------- #
class _FabricatingSummarizer:
    """A summariser that injects a token absent from every source episode — the
    confabulation the deterministic proxy must catch."""

    def summarize(self, *, cluster_contents):
        return SummaryResult(text="snake_case columns FABRICATED_CLAIM", background_tokens=3)


def test_confabulation_quarantines_via_safety_gates():
    seq = generate_schema_induction_sequence(seed=1, n_episodes=4, rule=None)
    arm = ConsolidatingMemory(mode="recombine", summarizer=_FabricatingSummarizer())
    run = run_sequence(seq, _exp(), conditions=[Condition.MEMORY_ENABLED], memory_system=arm)
    items = run.consolidations[Condition.MEMORY_ENABLED.value].items
    findings = confabulation_findings(items, episode_source_texts(seq))
    assert findings.rate > 0.0  # the fabricated token is flagged

    gates = compute_safety_gates(
        must_retain=[],
        live_ids=[],
        tombstoned_with_provenance=[],
        confabulation_rate=findings.rate,
        unverified_claim_ids=findings.unverified_claim_ids,
        calibration_path=None,  # no κ set ⇒ flag, never void (B-2)
    )
    assert gates.confabulation.authority == "flag"
    assert gates.win_eligible is False
    assert gates.run_void is False
