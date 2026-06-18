"""Tests for the workload calibrator: measuring prompt shape from sequences and
deriving a runnable TrialConfig. Pure — no engine, no GPU."""

import pytest

from membench.autotune.calibrate import (
    ShapeStats,
    calibrated_config,
    measure_sequences,
)
from membench.schemas.sequence import BenchmarkSequence, SequenceStep


def _seq(seq_id: str, steps: list[SequenceStep]) -> BenchmarkSequence:
    return BenchmarkSequence(sequence_id=seq_id, title=seq_id, steps=steps)


def test_measure_resolves_prefix_from_accumulated_writes() -> None:
    # Step 0 writes mem "a" (5 words); step 1 reads "a" → its prefix is those 5 words.
    seq = _seq(
        "s",
        [
            SequenceStep(
                step_id="0",
                user_request="do the first thing",  # 4 words tail
                expected_memory_writes={"a": "one two three four five"},  # 5 words
            ),
            SequenceStep(
                step_id="1",
                user_request="do the second thing now",  # 5 words tail
                expected_memory_reads=["a"],
            ),
        ],
    )
    stats = measure_sequences([seq])
    assert stats.n_sequences == 1
    assert stats.n_steps == 2
    assert stats.n_steps_with_memory == 1  # only step 1 reads memory
    assert stats.prefix_words_p50 == 5.0
    assert stats.steps_per_sequence_p50 == 2.0
    assert stats.write_words_p50 == 5.0


def test_forward_reference_to_unwritten_memory_contributes_nothing() -> None:
    # Step 0 reads "future" before it's ever written → no prefix, as in a real run.
    seq = _seq(
        "s",
        [
            SequenceStep(step_id="0", user_request="q", expected_memory_reads=["future"]),
            SequenceStep(step_id="1", user_request="q", expected_memory_writes={"future": "x y"}),
        ],
    )
    stats = measure_sequences([seq])
    assert stats.n_steps_with_memory == 0  # step 0's read missed; step 1 only writes
    assert stats.prefix_words_p50 is None  # no read-bearing step → absent, not zero


def test_multi_read_prefix_joins_contents() -> None:
    seq = _seq(
        "s",
        [
            SequenceStep(step_id="0", user_request="q", expected_memory_writes={"a": "aa bb"}),
            SequenceStep(step_id="1", user_request="q", expected_memory_writes={"b": "cc dd ee"}),
            SequenceStep(step_id="2", user_request="q", expected_memory_reads=["a", "b"]),
        ],
    )
    stats = measure_sequences([seq])
    assert stats.prefix_words_p50 == 5.0  # "aa bb" + "cc dd ee" = 5 words


def test_calibrated_config_preserves_sharing_ratio_and_scales_to_target() -> None:
    stats = ShapeStats(
        n_sequences=10,
        n_steps=40,
        n_steps_with_memory=30,
        prefix_words_p50=120.0,
        prefix_words_p90=300.0,
        tail_words_p50=12.0,
        write_words_p50=20.0,
        steps_per_sequence_p50=4.0,
    )
    cfg = calibrated_config(stats, engine="vllm", concurrencies=(1, 4), target_requests=64)
    assert cfg.prompts_per_group == 4  # mirrors real steps-per-sequence (the sharing)
    assert cfg.groups == 16  # ceil(64 / 4) to hit the target
    assert cfg.total_requests >= 64
    assert cfg.prefix_words == 120  # real memory-prefix size
    assert cfg.max_tokens == 26  # round(20 * 1.3) from the write-word proxy
    assert cfg.engine == "vllm"


def test_calibrated_config_explicit_max_tokens_overrides_proxy() -> None:
    stats = ShapeStats(10, 40, 30, 120.0, 300.0, 12.0, 20.0, 4.0)
    cfg = calibrated_config(
        stats, engine="vllm", concurrencies=(1,), target_requests=8, max_tokens=256
    )
    assert cfg.max_tokens == 256


def test_calibrated_config_falls_back_when_no_memory_steps() -> None:
    # A corpus with no read-bearing steps still yields a usable config (prefix from tail).
    stats = ShapeStats(2, 6, 0, None, None, 9.0, None, 3.0)
    cfg = calibrated_config(stats, engine="sglang", concurrencies=(1,), target_requests=10)
    assert cfg.prefix_words == 9  # fell back to tail words
    assert cfg.prompts_per_group == 3
    assert cfg.max_tokens >= 1  # fell back to the default proxy


def test_calibrated_config_rejects_bad_target() -> None:
    stats = ShapeStats(1, 1, 0, None, None, 5.0, None, 1.0)
    with pytest.raises(ValueError):
        calibrated_config(stats, engine="vllm", concurrencies=(1,), target_requests=0)


def test_empty_corpus_measures_zeroes_not_crash() -> None:
    stats = measure_sequences([])
    assert stats.n_sequences == 0
    assert stats.prefix_words_p50 is None
    assert stats.steps_per_sequence_p50 is None
