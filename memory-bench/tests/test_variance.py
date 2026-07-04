"""mem-eacq variance helpers: within-task repeat stats, pooled SD, the paired
MDE sampling-noise floor, and delta-of-means.

Pure arithmetic (ZFC) — hand-computed expectations, no fixtures needed. The
t-quantile expectations use standard table values (t_{.975,4} = 2.7764,
t_{.80,4} = 0.9410) so the test does not re-derive them from the same code
under test.
"""

import math

import pytest

from membench.grading.variance import (
    MetricStats,
    delta_of_means,
    mde_paired_floor,
    metric_stats_by_key,
    pooled_within_sd,
    within_task_stats,
)


class TestWithinTaskStats:
    def test_mean_sd_n(self) -> None:
        stats = within_task_stats([0.2, 0.4, 0.6])
        assert stats.mean == pytest.approx(0.4)
        assert stats.sd == pytest.approx(0.2)
        assert stats.n == 3
        assert stats.values == (0.2, 0.4, 0.6)

    def test_single_value_has_no_sd(self) -> None:
        stats = within_task_stats([0.7])
        assert stats.mean == pytest.approx(0.7)
        assert stats.sd is None
        assert stats.n == 1

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            within_task_stats([])


class TestMetricStatsByKey:
    def test_none_values_are_dropped_per_key(self) -> None:
        stats = metric_stats_by_key([{"a": 1.0, "b": None}, {"a": 3.0, "b": 0.5}])
        assert stats["a"].mean == pytest.approx(2.0)
        assert stats["a"].sd == pytest.approx(math.sqrt(2.0))
        assert stats["a"].n == 2
        assert stats["b"].n == 1
        assert stats["b"].sd is None

    def test_key_absent_everywhere_is_omitted(self) -> None:
        stats = metric_stats_by_key([{"c": None}, {"c": None}])
        assert "c" not in stats

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError):
            metric_stats_by_key([])


class TestPooledWithinSd:
    def test_df_weighted_pooling(self) -> None:
        a = within_task_stats([0.0, 0.1, 0.2, 0.1, 0.1])  # sd ~0.0707, n=5
        b = within_task_stats([0.0, 0.3, 0.6, 0.3, 0.3])  # 3x spread, n=5
        pooled = pooled_within_sd([a, b])
        assert pooled is not None
        # pooled variance = (4*var_a + 4*var_b) / 8 with equal df
        expected = math.sqrt(((a.sd or 0.0) ** 2 + (b.sd or 0.0) ** 2) / 2.0)
        assert pooled == pytest.approx(expected)

    def test_single_observation_tasks_carry_no_df(self) -> None:
        a = within_task_stats([0.5])
        b = within_task_stats([0.1, 0.3])
        pooled = pooled_within_sd([a, b])
        assert pooled == pytest.approx(b.sd)
        assert pooled_within_sd([a]) is None


class TestMdePairedFloor:
    def test_hand_computed_value(self) -> None:
        # (t_{.975,4} + t_{.80,4}) * sqrt(2/1) * 0.05 / sqrt(5)
        expected = (2.7764 + 0.9410) * math.sqrt(2.0) * 0.05 / math.sqrt(5)
        assert mde_paired_floor(0.05, n_tasks=5, k_repeats=1) == pytest.approx(expected, abs=1e-3)

    def test_repeats_and_pool_size_shrink_the_floor(self) -> None:
        base = mde_paired_floor(0.05, n_tasks=2, k_repeats=1)
        assert mde_paired_floor(0.05, n_tasks=2, k_repeats=5) < base
        assert mde_paired_floor(0.05, n_tasks=5, k_repeats=1) < base

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(ValueError):
            mde_paired_floor(0.05, n_tasks=1, k_repeats=3)
        with pytest.raises(ValueError):
            mde_paired_floor(0.05, n_tasks=5, k_repeats=0)
        with pytest.raises(ValueError):
            mde_paired_floor(-0.01, n_tasks=5, k_repeats=3)


class TestDeltaOfMeans:
    def test_delta_and_standard_error(self) -> None:
        base = MetricStats(mean=0.2, sd=0.1, n=5, values=(0.2,) * 5)
        treat = MetricStats(mean=0.5, sd=0.2, n=5, values=(0.5,) * 5)
        delta, se = delta_of_means(base, treat)
        assert delta == pytest.approx(0.3)
        assert se == pytest.approx(math.sqrt(0.01 / 5 + 0.04 / 5))

    def test_missing_sd_yields_no_se(self) -> None:
        base = MetricStats(mean=0.2, sd=None, n=1, values=(0.2,))
        treat = MetricStats(mean=0.5, sd=0.2, n=5, values=(0.5,) * 5)
        delta, se = delta_of_means(base, treat)
        assert delta == pytest.approx(0.3)
        assert se is None
