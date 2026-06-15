"""Handoff efficiency — matched-pair effort deltas from trace_runs (mem-dsu).

The two efficiency metrics of "Handoff Debt" (arXiv 2606.02875), aligned to our
``trace_runs`` columns (mem-75t.2): agent events (n_turns + n_tool_calls, both
reported) and cumulative prompt tokens (input + cache_read + cache_creation,
output EXCLUDED). Deltas are computed on MATCHED runs (same interruption point +
successor + checkpoint, view varying) against the repo-only baseline, with paired
bootstrap 95% CIs (medians) and McNemar for the solve-rate companion — copying
the paper's methodology so the numbers are comparable. Pure ZFC mechanism:
arithmetic + resampling, no model, no semantic judgement.
"""

from __future__ import annotations

from membench.handoff_efficiency import (
    MatchedRun,
    RunEfficiency,
    agent_events,
    bootstrap_median_ci,
    mcnemar,
    prompt_tokens,
    summarize_handoff_efficiency,
)


def _eff(turns: int, tools: int, inp: int | None = None, **kw: object) -> RunEfficiency:
    return RunEfficiency(n_turns=turns, n_tool_calls=tools, input_tokens=inp, **kw)  # type: ignore[arg-type]


def test_agent_events_sums_turns_and_tool_calls() -> None:
    assert agent_events(_eff(10, 30)) == 40


def test_prompt_tokens_includes_cache_excludes_output() -> None:
    e = RunEfficiency(
        n_turns=1,
        n_tool_calls=0,
        input_tokens=1000,
        cache_read_tokens=500,
        cache_creation_tokens=200,
    )
    # output_tokens deliberately has no field here — the metric is prompt-side only.
    assert prompt_tokens(e) == 1700


def test_prompt_tokens_is_none_when_no_input_usage() -> None:
    # Strict typed absence: with no input_tokens there is no prompt-side measurement.
    assert prompt_tokens(_eff(1, 0, None)) is None
    # cache fields absent => treated as zero contribution, not as missing data.
    assert prompt_tokens(_eff(1, 0, 800)) == 800


def test_bootstrap_ci_is_deterministic_and_brackets_the_median() -> None:
    deltas = [0.4, 0.5, 0.45, 0.6, 0.55, 0.5, 0.48, 0.52]
    a = bootstrap_median_ci(deltas, n_resamples=2000, seed=7)
    b = bootstrap_median_ci(deltas, n_resamples=2000, seed=7)
    assert a == b  # seeded => reproducible
    median, lo, hi = a
    assert lo <= median <= hi
    assert 0.4 <= median <= 0.6


def test_mcnemar_counts_discordant_pairs() -> None:
    # baseline (repo-only) solved vs view solved, matched.
    base = [True, True, False, False, True]
    view = [True, False, True, True, True]
    res = mcnemar(base, view)
    # discordant: idx1 (base solved, view not) -> b; idx2,3 (view solved, base not) -> c
    assert res["b"] == 1
    assert res["c"] == 2
    assert res["n_discordant"] == 3
    assert 0.0 <= res["p_value"] <= 1.0


def test_mcnemar_no_discordance_is_p_one() -> None:
    res = mcnemar([True, False, True], [True, False, True])
    assert res["n_discordant"] == 0
    assert res["p_value"] == 1.0


def _matched(key: str, view: str, turns: int, tools: int, inp: int, solved: bool) -> MatchedRun:
    return MatchedRun(
        matched_key=key,
        view=view,
        efficiency=RunEfficiency(
            n_turns=turns,
            n_tool_calls=tools,
            input_tokens=inp,
            solved=solved,
        ),
    )


def test_summarize_computes_fractional_deltas_against_repo_only() -> None:
    # Two matched sets; in each, raw-trace halves events vs repo-only.
    runs = [
        _matched("k1", "repo-only", turns=50, tools=50, inp=2000, solved=False),
        _matched("k1", "raw-trace", turns=25, tools=25, inp=4000, solved=True),
        _matched("k2", "repo-only", turns=40, tools=40, inp=1600, solved=False),
        _matched("k2", "raw-trace", turns=20, tools=20, inp=3200, solved=True),
    ]
    summary = summarize_handoff_efficiency(runs, n_resamples=1000, seed=0)
    raw = summary["raw-trace"]
    # events delta = (repo - view)/repo = 0.5 in both matched sets.
    assert raw["events"]["median_delta"] == 0.5
    assert raw["events"]["n_pairs"] == 2
    # prompt tokens went UP for raw-trace (negative "debt repaid") — the 12x penalty.
    assert raw["prompt_tokens"]["median_delta"] < 0
    # turns and tool_calls reported separately (memo: report both, do not only sum).
    assert raw["turns"]["median_delta"] == 0.5
    assert raw["tool_calls"]["median_delta"] == 0.5
    # solve-rate companion: McNemar over the matched solved flags.
    assert raw["solve_rate"]["c"] == 2  # view solved both where baseline did not
    # repo-only is the baseline and gets no self-delta row.
    assert "repo-only" not in summary


def test_summarize_skips_groups_missing_the_baseline() -> None:
    # A matched set without a repo-only run yields no delta (never imputed).
    runs = [
        _matched("k1", "raw-trace", turns=20, tools=20, inp=3200, solved=True),
        _matched("k2", "repo-only", turns=40, tools=40, inp=1600, solved=False),
        _matched("k2", "raw-trace", turns=20, tools=20, inp=3200, solved=True),
    ]
    summary = summarize_handoff_efficiency(runs, n_resamples=500, seed=0)
    assert summary["raw-trace"]["events"]["n_pairs"] == 1


def test_first_prompt_tokens_delta_exposes_raw_trace_penalty() -> None:
    runs = [
        _matched_fp("k1", "repo-only", 7200, solved=False),
        _matched_fp("k1", "raw-trace", 87000, solved=True),
        _matched_fp("k1", "structured-notes", 10000, solved=True),
    ]
    summary = summarize_handoff_efficiency(runs, n_resamples=500, seed=0)
    # raw-trace blows up the first prompt (negative debt-repaid), notes stay small.
    assert summary["raw-trace"]["first_prompt_tokens"]["median_delta"] < 0
    assert summary["structured-notes"]["first_prompt_tokens"]["median_delta"] < 0
    assert (
        summary["structured-notes"]["first_prompt_tokens"]["median_delta"]
        > summary["raw-trace"]["first_prompt_tokens"]["median_delta"]
    )


def _matched_fp(key: str, view: str, first_inp: int, solved: bool) -> MatchedRun:
    return MatchedRun(
        matched_key=key,
        view=view,
        efficiency=RunEfficiency(
            n_turns=1,
            n_tool_calls=0,
            input_tokens=first_inp,
            first_input_tokens=first_inp,
            solved=solved,
        ),
    )
