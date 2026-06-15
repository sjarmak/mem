"""S2 — schema-induction generator + confabulation/recall metrics.

The generator emits a ``BenchmarkSequence`` (reusing the existing schema per B-0,
NOT a new task type): N episode-writing steps that each INSTANTIATE a latent rule
without stating it verbatim, plus a final probe step whose answer is the rule
itself — not any single episode's content. The oracle (the sequence-level
``latent_rule`` + the written episode ids) is authored in pure Python (Tier 0):
deterministic, seed-reproducible, no LLM in CI.

The metrics are deterministic too: ``schema_recall`` (did the consolidation recover
the rule?) and the ``confabulation`` token-re-derivability proxy (does any schema
row assert a token absent from its cited sources?) — the no-judge Tier-2 floor that
ships day 1.
"""

from __future__ import annotations

from membench.generators.schema_induction import (
    GENERATOR_VERSION,
    generate_schema_induction_sequence,
)
from membench.memory_systems.consolidation import ConsolidatedItem
from membench.metrics.schema_scorers import (
    confabulation_findings,
    episode_source_texts,
    schema_recall,
)
from membench.signals import SalienceSignals


def test_sequence_carries_latent_rule_and_writes_episodes():
    seq = generate_schema_induction_sequence(seed=1, n_episodes=4)
    assert seq.latent_rule, "the sequence must carry the latent-rule oracle"
    writes = [mid for step in seq.steps for mid in step.expected_memory_writes]
    assert len(writes) == 4
    # The final step is the probe — it reads the episodes, writes nothing.
    probe = seq.steps[-1]
    assert probe.expected_memory_writes == {}
    assert set(probe.expected_memory_reads) == set(writes)
    assert probe.outcome_checks  # the rule-recovery check


def test_probe_answer_is_the_rule_not_any_single_episode():
    seq = generate_schema_induction_sequence(seed=1, n_episodes=4)
    episodes = [c for step in seq.steps for c in step.expected_memory_writes.values()]
    # The latent rule is NOT stated verbatim by any episode (it must be induced).
    for ep in episodes:
        assert seq.latent_rule not in ep


def test_every_episode_instantiates_the_rule_signature():
    # Entailment self-check: each episode contains the rule's signature tokens
    # (instantiates it) even though it never states the rule sentence.
    seq = generate_schema_induction_sequence(seed=3, n_episodes=5)
    sig = SalienceSignals()
    rule_tokens = sig.tokenize(seq.latent_rule)
    for step in seq.steps[:-1]:
        for content in step.expected_memory_writes.values():
            ep_tokens = sig.tokenize(content)
            shared = rule_tokens & ep_tokens
            assert shared, f"episode does not instantiate any rule token: {content!r}"


def test_deterministic_seed_is_byte_reproducible():
    a = generate_schema_induction_sequence(seed=7, n_episodes=4)
    b = generate_schema_induction_sequence(seed=7, n_episodes=4)
    assert a.model_dump_json() == b.model_dump_json()
    # A different seed yields a different sequence (not a constant).
    c = generate_schema_induction_sequence(seed=8, n_episodes=4)
    assert c.model_dump_json() != a.model_dump_json()


def test_generator_version_is_recorded():
    assert GENERATOR_VERSION.startswith("schema-induction")


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def test_schema_recall_rewards_a_rule_bearing_row_and_zeroes_an_empty_set():
    seq = generate_schema_induction_sequence(seed=1, n_episodes=4)
    sig = SalienceSignals()
    rule_tokens = list(sig.tokenize(seq.latent_rule))
    good = [
        ConsolidatedItem(memory_id="schema-0", content=seq.latent_rule, source_trace_ids=("x",))
    ]
    assert schema_recall(seq.latent_rule, good) == 1.0
    # dedupe_only emits no schema rows ⇒ recall 0.0 (the mode contrast).
    assert schema_recall(seq.latent_rule, []) == 0.0
    assert rule_tokens  # sanity: the rule has scorable tokens


def test_confabulation_proxy_flags_a_fabricated_token():
    sources = {"ep1": "snake_case db columns", "ep2": "snake_case db tables"}
    faithful = [
        ConsolidatedItem(memory_id="s0", content="snake_case db", source_trace_ids=("ep1", "ep2"))
    ]
    f = confabulation_findings(faithful, sources)
    assert f.rate == 0.0
    assert f.unverified_claim_ids == ()

    fabricated = [
        ConsolidatedItem(
            memory_id="s1",
            content="snake_case db PascalCase",  # PascalCase appears in NO source
            source_trace_ids=("ep1", "ep2"),
        )
    ]
    g = confabulation_findings(fabricated, sources)
    assert g.rate > 0.0
    assert g.unverified_claim_ids == ("s1",)


def test_episode_source_texts_extracts_the_written_episodes():
    seq = generate_schema_induction_sequence(seed=2, n_episodes=3)
    texts = episode_source_texts(seq)
    writes = {mid: c for step in seq.steps for mid, c in step.expected_memory_writes.items()}
    assert texts == writes
