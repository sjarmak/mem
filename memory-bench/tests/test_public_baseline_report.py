"""The public baseline's report table and its driver (mem-r6yzk.5).

Three properties the published table stands on, each with a fixture built so the
wrong implementation passes nothing:

1. the delta is PAIRED PER RECORD, not a difference of pooled means (the fixture
   below makes the two disagree in SIGN, so an implementation that pooled would
   publish a loss as a win);
2. the injected-context volume column is present for EVERY arm (without it an arm
   that wins by injecting more is unfalsifiable);
3. a missing repeat RAISES rather than silently reporting a smaller n;
4. the candidate pool an arm is SEEDED with carries no role signal — its key order
   is a pure function of the record's own ids, and a first-k-of-what-I-was-handed
   policy does not beat chance at recovering gold on the released set.

Plus the driver's refusals: default is preflight, a fire without a named model or
without ``--out`` refuses, an arm whose infrastructure is absent refuses BEFORE any
credential check, and no test in this file spawns an agent or spends.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from membench.memory_systems import nemo_embed_system
from membench.report.public_baseline import (
    ARM_BASELINE,
    COST_FIELDS,
    BaselineCell,
    DuplicateCellError,
    MissingRepeatError,
    UnknownArmError,
    arm_costs,
    arms_of,
    build_report,
    judge_mean,
    rewards_by_record,
)
from membench.schemas.metrics import EfficiencyMetrics

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_driver() -> ModuleType:
    """Import the driver by path: ``scripts/`` is deliberately not a package, and a
    test that copied the driver's logic instead of importing it would be testing the
    copy."""
    spec = importlib.util.spec_from_file_location(
        "run_public_baseline", _SCRIPTS / "run_public_baseline.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


driver = _load_driver()


class _UncalledEmbedder:
    """Stands in for the pinned NeMo model at CONSTRUCTION only. Every method raises:
    a test that drifted into actually embedding would fail loudly here rather than
    quietly measure a stub."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("this test builds arms; it must not embed")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("this test builds arms; it must not embed")


# ---------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------


def cell(
    arm: str,
    record_id: str,
    repeat: int,
    reward: float,
    *,
    injected: int = 0,
    items: int = 0,
    stale: int = 0,
    status: str = "ok",
    tokens: int = 100,
    judge: float | None = None,
) -> BaselineCell:
    return BaselineCell(
        arm=arm,
        record_id=record_id,
        repeat=repeat,
        status=status,  # type: ignore[arg-type]
        paid=True,
        reward=reward,
        passed=reward >= 1.0,
        injected_items=items,
        injected_context_chars=injected,
        stale_injected=stale,
        efficiency=EfficiencyMetrics(
            total_tokens=tokens,
            input_tokens=tokens - 10,
            output_tokens=10,
            tool_calls_total=1,
            wall_clock_latency_ms=1234.0,
            cost_usd=0.0,
        ),
        judge_score=judge,
    )


# The disagreement fixture. Per-record rewards:
#     none:    r1 0.0   r2 0.0   r3 1.0     (pooled mean 1/3)
#     treated: r1 0.2   r2 0.2   r3 0.0     (pooled mean 2/15)
# Pooled difference of means: 2/15 - 1/3 = -0.2  → "the arm HURT".
# Paired per-record deltas: [+0.2, +0.2, -1.0], median +0.2 → "the arm helped on
# most tasks and collapsed on one". Opposite signs: only one of these can be the
# number published, and it is the paired one.
_PAIRED_REWARDS = {
    ARM_BASELINE: {"r1": 0.0, "r2": 0.0, "r3": 1.0},
    "treated": {"r1": 0.2, "r2": 0.2, "r3": 0.0},
}
POOLED_DIFF = -0.2
PAIRED_MEDIAN = pytest.approx(0.2)


def disagreement_cells(repeats: int = 3) -> list[BaselineCell]:
    return [
        cell(arm, record_id, repeat, reward, injected=10 if arm != ARM_BASELINE else 0)
        for arm, rewards in _PAIRED_REWARDS.items()
        for record_id, reward in rewards.items()
        for repeat in range(repeats)
    ]


# ---------------------------------------------------------------------------------
# 1. paired per-record, never pooled
# ---------------------------------------------------------------------------------


def test_delta_is_paired_per_record_not_a_pooled_mean() -> None:
    report = build_report(disagreement_cells(), repeats=3, n_resamples=200)
    treated = next(row for row in report.rows if row.arm == "treated")
    assert treated.delta is not None
    assert treated.delta.delta == PAIRED_MEDIAN
    assert treated.delta.n_pairs == 3
    # The pooled answer has the opposite sign; publishing it would invert the claim.
    assert treated.delta.delta != pytest.approx(POOLED_DIFF)
    assert treated.reward_mean == pytest.approx(2 / 15)


def test_baseline_row_carries_no_delta_against_itself() -> None:
    report = build_report(disagreement_cells(), repeats=3, n_resamples=200)
    assert report.rows[0].arm == ARM_BASELINE
    assert report.rows[0].delta is None
    assert report.to_dict()["arms"][0]["paired_delta_vs_baseline"] is None


def test_report_refuses_a_grid_with_no_baseline_arm() -> None:
    cells = [c for c in disagreement_cells() if c.arm != ARM_BASELINE]
    with pytest.raises(UnknownArmError, match="baseline arm"):
        build_report(cells, repeats=3, n_resamples=200)


def test_per_record_reward_averages_the_repeats() -> None:
    cells = [
        cell(ARM_BASELINE, "r1", 0, 0.0),
        cell(ARM_BASELINE, "r1", 1, 1.0),
    ]
    assert rewards_by_record(cells, ARM_BASELINE, repeats=2) == {"r1": 0.5}


def test_arms_are_listed_with_the_baseline_first() -> None:
    assert arms_of(disagreement_cells())[0] == ARM_BASELINE


# ---------------------------------------------------------------------------------
# 2. the injected-context volume column
# ---------------------------------------------------------------------------------


def test_volume_column_present_for_every_arm() -> None:
    cells = [
        cell(ARM_BASELINE, "r1", 0, 0.0, injected=0, items=0),
        cell("lexical", "r1", 0, 1.0, injected=400, items=6),
        cell("oracle", "r1", 0, 1.0, injected=120, items=3),
    ]
    report = build_report(cells, repeats=1, n_resamples=200)
    rows = {row["arm"]: row["cost"] for row in report.to_dict()["arms"]}
    volumes = {arm: cost["injected_context_chars_mean"] for arm, cost in rows.items()}
    items = {arm: cost["injected_items_mean"] for arm, cost in rows.items()}
    assert volumes == {ARM_BASELINE: 0.0, "lexical": 400.0, "oracle": 120.0}
    assert items == {ARM_BASELINE: 0.0, "lexical": 6.0, "oracle": 3.0}
    # And in the rendered table, one cell per arm per unit — the guard is only real if
    # a reader of the published markdown sees it.
    lines = report.to_markdown().splitlines()
    header = next(line for line in lines if line.startswith("| arm |"))
    assert "injected items" in header
    assert "injected chars" in header
    for arm, volume in volumes.items():
        row = next(line for line in lines if line.startswith(f"| {arm} |"))
        assert f"| {items[arm]:.3f} | {volume:.1f} |" in row


def test_the_table_names_which_pairs_are_comparable_at_equal_volume() -> None:
    """Item volume is reported so a reader can tell a selection delta from a volume
    one, and the table says which pairs qualify rather than leaving the arithmetic to
    the reader. This is the vacuous-guard repair: the grouped arm shipped claiming the
    flat arms' width while injecting one item per group against their 6, and nothing in
    the published table contradicted the claim."""
    cells = [
        # lexical and grouped at the same width; oracle narrower; none empty.
        cell("lexical", "r1", 0, 1.0, injected=620, items=6),
        cell("grouped", "r1", 0, 1.0, injected=609, items=6),
        cell("oracle", "r1", 0, 1.0, injected=300, items=3),
        cell(ARM_BASELINE, "r1", 0, 0.0, injected=0, items=0),
    ]
    report = build_report(cells, repeats=1, n_resamples=200)
    assert report.equal_width_pairs == [("grouped", "lexical")]
    assert report.to_dict()["equal_width_pairs"] == [["grouped", "lexical"]]
    line = next(
        text for text in report.to_markdown().splitlines() if text.startswith("**Comparable")
    )
    assert "`grouped`↔`lexical`" in line
    assert "oracle" not in line
    # The sentence sits ABOVE the table: the confounded reading is the default one.
    lines = report.to_markdown().splitlines()
    assert lines.index(line) < lines.index(next(t for t in lines if t.startswith("| arm |")))


def test_equal_chars_do_not_make_two_arms_equal_width() -> None:
    """The reason items are carried as well as characters. Three long items and six
    short ones can sum to the same length; only the item count says the two arms handed
    the agent different amounts of retrieval."""
    cells = [
        cell("wide", "r1", 0, 1.0, injected=600, items=6),
        cell("deep", "r1", 0, 1.0, injected=600, items=3),
        cell(ARM_BASELINE, "r1", 0, 0.0, injected=0, items=0),
    ]
    report = build_report(cells, repeats=1, n_resamples=200)
    assert report.equal_width_pairs == []
    assert "no pair" in next(
        text for text in report.to_markdown().splitlines() if text.startswith("**Comparable")
    )


def test_volume_is_not_a_field_of_efficiency_metrics() -> None:
    """The reason the column is carried separately: an implementation that read the
    volume off EfficiencyMetrics would be reading a field that does not exist."""
    assert "injected_context_chars" not in EfficiencyMetrics.model_fields
    assert "injected_context_chars" not in COST_FIELDS


def test_costs_exclude_unmeasured_cells_so_a_broken_rig_is_not_cheap() -> None:
    cells = [
        cell("lexical", "r1", 0, 1.0, injected=400, tokens=900),
        cell("lexical", "r2", 0, 0.0, injected=0, tokens=0, status="timeout"),
    ]
    cost = arm_costs(cells, "lexical")
    assert cost.n_cells == 1
    assert cost.means["total_tokens"] == 900.0
    assert cost.injected_context_chars_mean == 400.0


# ---------------------------------------------------------------------------------
# 3. a missing repeat is an error
# ---------------------------------------------------------------------------------


def test_missing_repeat_raises_rather_than_shrinking_n() -> None:
    cells = disagreement_cells()
    short = [c for c in cells if not (c.arm == "treated" and c.record_id == "r2" and c.repeat == 2)]
    with pytest.raises(MissingRepeatError, match="2 of 3"):
        build_report(short, repeats=3, n_resamples=200)


def test_unmeasured_repeat_is_missing_not_a_scored_zero() -> None:
    cells = [
        cell(ARM_BASELINE, "r1", 0, 0.0),
        cell(ARM_BASELINE, "r1", 1, 0.0),
        cell("treated", "r1", 0, 1.0),
        cell("treated", "r1", 1, 0.0, status="error"),
    ]
    with pytest.raises(MissingRepeatError, match="1 of 2"):
        build_report(cells, repeats=2, n_resamples=200)


def test_a_record_outside_the_reported_set_raises() -> None:
    cells = [cell("treated", "r1", 0, 1.0)]
    with pytest.raises(MissingRepeatError, match="outside the reported record set"):
        rewards_by_record(cells, "treated", repeats=1, records=["r2"])


def test_duplicate_cell_raises() -> None:
    cells = [cell("treated", "r1", 0, 1.0), cell("treated", "r1", 0, 0.0)]
    with pytest.raises(DuplicateCellError):
        build_report(cells, repeats=1, n_resamples=200)


def test_judge_is_report_only_and_absent_by_default() -> None:
    cells = [
        cell(ARM_BASELINE, "r1", 0, 0.0),
        cell("treated", "r1", 0, 1.0, judge=0.75),
    ]
    report = build_report(cells, repeats=1, n_resamples=200)
    assert judge_mean(cells, ARM_BASELINE) is None
    treated = next(row for row in report.rows if row.arm == "treated")
    assert treated.judge_score_mean == pytest.approx(0.75)
    # The judge never moved the reward or the delta.
    assert treated.reward_mean == 1.0
    assert "report-only" in report.to_markdown()


def test_cell_row_round_trips() -> None:
    original = cell("treated", "r1", 2, 0.5, injected=42, items=4, judge=0.25)
    assert BaselineCell.from_row(original.to_row()) == original


@pytest.mark.parametrize("field", ["injected_context_chars", "injected_items", "stale_injected"])
def test_cell_row_missing_a_field_raises_rather_than_defaulting(field: str) -> None:
    row = cell("treated", "r1", 0, 1.0).to_row()
    del row[field]
    with pytest.raises(KeyError):
        BaselineCell.from_row(row)


# ---------------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------------


def _record_payload(record_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "record_id": record_id,
        "tier": "sound",
        "origin": "synthetic",
        "question": {
            "text": f"What is the current owner of {record_id}?",
            "asked_at": "2026-01-02T03:04:05Z",
            "scope_id": "seq-1",
        },
        "answer": {
            "check_kind": "value-set",
            "expected_values": ["dana"],
            "forbidden_values": ["rey"],
        },
        # The three buckets and the pool are one set, which is what a consumer
        # reassembles the arm's world from. `m3` is the stale entry: its text states the
        # forbidden value, so an answer policy that pastes the pool fails.
        "candidate_pool": {"ids": ["m1", "m2", "m3"]},
        "evidence": {
            "gold": {"m1": "the owner is dana"},
            "distractors": {"m2": "unrelated chatter"},
            "superseded": {"m3": "the owner is rey"},
            "gold_ids": ["m1"],
            "superseded_ids": ["m3"],
        },
        "loo": {
            "boundary": "2026-01-02T03:04:05Z",
            "candidates": ["m1", "m2", "m3"],
            "excluded_ids": [],
            "exclusion_axes": [],
            "query": {"id": f"w-{record_id}", "convoy_key": None},
        },
        "provenance": {
            "world_id": "world-1",
            "seed": 7,
            "sequence_id": "seq-1",
            "generator_version": "v1",
            "exporter_version": "public-export.v2",
            "cross_session_gold_ids": [],
            "source_sha256": f"sha-{record_id}",
        },
        "necessity": {},
    }
    payload.update(overrides)
    return payload


def _release(tmp_path: Path, *record_ids: str) -> Path:
    release = tmp_path / "data"
    release.mkdir(parents=True)
    lines = [json.dumps(_record_payload(rid)) for rid in record_ids]
    (release / "public.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return release


def test_load_release_reads_every_record(tmp_path: Path) -> None:
    records = driver.load_release(_release(tmp_path, "b", "a"))
    assert [record.record_id for record in records] == ["a", "b"]
    assert records[0].gold_ids == ("m1",)
    assert records[0].gold_payloads == {"m1": "the owner is dana"}
    assert records[0].candidate_chars > records[0].gold_chars


def test_load_release_refuses_a_malformed_record(tmp_path: Path) -> None:
    release = tmp_path / "data"
    release.mkdir()
    payload = _record_payload("a")
    del payload["answer"]["expected_values"]
    (release / "public.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(driver.PublicBaselineError, match="malformed release"):
        driver.load_release(release)


def test_load_release_refuses_a_gold_id_with_no_content(tmp_path: Path) -> None:
    release = tmp_path / "data"
    release.mkdir()
    payload = _record_payload("a")
    payload["evidence"]["gold_ids"] = ["m9"]
    (release / "public.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(driver.PublicBaselineError, match="no content"):
        driver.load_release(release)


def test_load_release_refuses_an_empty_directory(tmp_path: Path) -> None:
    empty = tmp_path / "data"
    empty.mkdir()
    with pytest.raises(driver.PublicBaselineError, match=r"no \.jsonl"):
        driver.load_release(empty)


def test_grade_zeroes_an_answer_that_states_a_superseded_value(tmp_path: Path) -> None:
    record = driver.load_release(_release(tmp_path, "a"))[0]
    assert driver.grade("the owner is dana", record) == (1.0, True)
    assert driver.grade("it was rey, now dana", record) == (0.0, False)
    assert driver.grade("no idea", record) == (0.0, False)


def test_grid_keys_are_the_drivers_own_repeat_loop(tmp_path: Path) -> None:
    records = driver.load_release(_release(tmp_path, "a", "b"))
    keys = driver.grid_keys(records, ["none", "oracle"], 3)
    assert len(keys) == 2 * 2 * 3
    assert len(set(keys)) == len(keys)
    assert ("oracle", "b", 2) in keys


def test_price_counts_every_cell_and_prices_the_ceiling(tmp_path: Path) -> None:
    records = driver.load_release(_release(tmp_path, "a", "b"))
    arms = ["none", "lexical", "oracle"]
    probes = {arm: driver.ProbeResult(arm, True, "fixture") for arm in arms}
    plan = driver.price(records, arms=arms, repeats=3, probes=probes, release_dir=tmp_path)
    assert plan.total_cells == 3 * 2 * 3
    by_arm = {arm_plan.arm: arm_plan for arm_plan in plan.arms}
    # the floor injects nothing, the ceiling injects gold only, a ranker's ceiling
    # is the whole candidate pool
    assert by_arm["none"].est_prompt_chars < by_arm["oracle"].est_prompt_chars
    assert by_arm["oracle"].est_prompt_chars < by_arm["lexical"].est_prompt_chars
    assert plan.unavailable == []
    assert plan.to_dict()["n_records"] == 2


def test_price_reports_an_unavailable_arm_rather_than_dropping_it(tmp_path: Path) -> None:
    records = driver.load_release(_release(tmp_path, "a"))
    probes = {
        "none": driver.ProbeResult("none", True, "fixture"),
        "ours": driver.ProbeResult("ours", False, "--store was not given"),
    }
    plan = driver.price(records, arms=["none", "ours"], repeats=1, probes=probes)
    assert plan.unavailable == ["ours"]
    assert plan.total_cells == 2


def test_ours_probe_fails_without_a_store(tmp_path: Path) -> None:
    from membench.memory_systems.local_stack import LocalModelStack

    probe = driver.probe_arm(
        "ours", stack=LocalModelStack.from_env(), store_path=None, mem_bin=None
    )
    assert probe.available is False
    assert "--store" in probe.detail and "--mem-bin" in probe.detail


def test_unknown_arm_is_refused_not_guessed() -> None:
    from membench.memory_systems.local_stack import LocalModelStack

    with pytest.raises(driver.PublicBaselineError, match="unknown arm"):
        driver.probe_arm(
            "telepathy", stack=LocalModelStack.from_env(), store_path=None, mem_bin=None
        )


def test_run_cells_grades_a_fake_agent_end_to_end(tmp_path: Path) -> None:
    records = driver.load_release(_release(tmp_path, "a", "b"))
    seen: list[str] = []

    def answer(prompt: str, *, record: Any, arm: str) -> driver.AgentAnswer:
        seen.append(arm)
        # The oracle's prompt carries the gold text; the floor's carries none.
        text = "dana" if "the owner is dana" in prompt else "unknown"
        return driver.AgentAnswer(
            text=text,
            input_tokens=len(prompt) // 4,
            output_tokens=3,
            tool_calls=0,
            wall_clock_latency_ms=12.0,
        )

    keys = driver.grid_keys(records, ["none", "oracle"], 2)
    cells = driver.run_cells(
        records,
        keys,
        arm_factory=driver.default_arm_factory(),
        answer=answer,
        paid=False,
    )
    assert len(cells) == len(keys)
    report = build_report(cells, repeats=2, n_resamples=200)
    oracle = next(row for row in report.rows if row.arm == "oracle")
    assert oracle.reward_mean == 1.0
    assert oracle.delta is not None and oracle.delta.delta == pytest.approx(1.0)
    assert oracle.cost.injected_context_chars_mean > 0
    # The item column is MEASURED from the payloads, not declared: the oracle injects
    # this fixture's one gold entry per cell, the floor injects nothing, and the two
    # arms are therefore NOT comparable at equal volume. A wiring that dropped the
    # count would report every arm at zero items and name every pair comparable.
    assert oracle.cost.injected_items_mean == 1.0
    baseline = report.rows[0]
    assert baseline.reward_mean == 0.0
    assert baseline.cost.injected_context_chars_mean == 0.0
    assert baseline.cost.injected_items_mean == 0.0
    assert report.equal_width_pairs == []
    assert set(seen) == {"none", "oracle"}


def test_run_cells_skips_the_cells_a_partial_already_bought(tmp_path: Path) -> None:
    records = driver.load_release(_release(tmp_path, "a"))
    calls: list[str] = []

    def answer(prompt: str, *, record: Any, arm: str) -> driver.AgentAnswer:
        calls.append(record.record_id)
        return driver.AgentAnswer(
            text="dana", input_tokens=1, output_tokens=1, tool_calls=0, wall_clock_latency_ms=1.0
        )

    keys = driver.grid_keys(records, ["none"], 2)
    landed = [cell("none", "a", 0, 1.0)]
    cells = driver.run_cells(
        records,
        keys,
        arm_factory=driver.default_arm_factory(),
        answer=answer,
        paid=True,
        landed=landed,
    )
    assert len(calls) == 1  # repeat 0 was resumed, only repeat 1 was bought
    assert len(cells) == 2


def test_admissible_cells_refuses_a_partial_from_another_run(tmp_path: Path) -> None:
    records = driver.load_release(_release(tmp_path, "a"))
    grid = driver.grid_keys(records, ["none"], 1)
    identity = driver.resume_identity(
        model="m",
        cli_version="1.2.3",
        release=driver.release_fingerprint(records),
        arms=["none"],
        repeats=1,
        scope=driver.DEFAULT_SCOPE,
    )
    summary = driver.artifact([cell("none", "a", 0, 1.0)], identity=identity, grid_size=1)
    assert len(driver.admissible_cells(summary, identity=identity, grid=grid)) == 1

    other = dict(identity) | {"release_fingerprint": "a-different-corpus"}
    with pytest.raises(driver.ResumeMismatchError, match="different run"):
        driver.admissible_cells(summary, identity=other, grid=grid)


def test_admissible_cells_drops_unmeasured_and_refuses_off_grid(tmp_path: Path) -> None:
    records = driver.load_release(_release(tmp_path, "a"))
    grid = driver.grid_keys(records, ["none"], 1)
    identity = driver.resume_identity(
        model="m",
        cli_version="1.2.3",
        release=driver.release_fingerprint(records),
        arms=["none"],
        repeats=1,
        scope=driver.DEFAULT_SCOPE,
    )
    timed_out = driver.artifact(
        [cell("none", "a", 0, 0.0, status="timeout")], identity=identity, grid_size=1
    )
    assert driver.admissible_cells(timed_out, identity=identity, grid=grid) == []

    stray = driver.artifact([cell("none", "zz", 0, 1.0)], identity=identity, grid_size=1)
    with pytest.raises(driver.ResumeMismatchError, match="not a cell of this grid"):
        driver.admissible_cells(stray, identity=identity, grid=grid)


def test_release_fingerprint_moves_when_the_corpus_does(tmp_path: Path) -> None:
    one = driver.load_release(_release(tmp_path / "a", "a"))
    two = driver.load_release(_release(tmp_path / "b", "a", "b"))
    assert driver.release_fingerprint(one) != driver.release_fingerprint(two)


def test_default_run_is_preflight_and_spends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = _release(tmp_path, "a", "b")
    code = driver.main(["--release-dir", str(release), "--arms", "none,oracle", "--repeats", "3"])
    assert code == 0
    captured = capsys.readouterr()
    plan = json.loads(captured.out)
    assert plan["total_cells"] == 2 * 2 * 3
    assert plan["n_records"] == 2
    assert {arm["arm"] for arm in plan["arms"]} == {"none", "oracle"}
    assert "PREFLIGHT" in captured.err


def test_arms_must_include_the_baseline(tmp_path: Path) -> None:
    release = _release(tmp_path, "a")
    assert driver.main(["--release-dir", str(release), "--arms", "oracle"]) == 2


def test_missing_release_is_its_own_exit_code(tmp_path: Path) -> None:
    assert driver.main(["--release-dir", str(tmp_path / "nope")]) == 4


# The credential ladder runs AFTER the arm-availability probe, so these three name
# an arm set this machine can actually run (`none,oracle`, both pure Python). With
# the default six-arm set they would refuse one rung earlier, on `ours` having no
# --store and `nemo-embed` having no embedder, and would no longer be testing the
# rung they are named for. The earlier rung has its own tests further down.


def test_fire_without_a_model_refuses_before_spending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MEMBENCH_AGENT_MODEL", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    release = _release(tmp_path, "a")
    code = driver.main(
        [
            "--release-dir",
            str(release),
            "--arms",
            "none,oracle",
            "--fire",
            "--out",
            str(tmp_path / "o.json"),
        ]
    )
    assert code == 2
    assert "no model named" in capsys.readouterr().err


def test_fire_without_an_out_artifact_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setenv("MEMBENCH_AGENT_MODEL", "some-model")
    release = _release(tmp_path, "a")
    code = driver.main(["--release-dir", str(release), "--arms", "none,oracle", "--fire"])
    assert code == 2
    assert "--out is required" in capsys.readouterr().err


def test_fire_with_a_metered_api_key_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    release = _release(tmp_path, "a")
    code = driver.main(
        [
            "--release-dir",
            str(release),
            "--arms",
            "none,oracle",
            "--fire",
            "--model",
            "m",
            "--out",
            str(tmp_path / "o.json"),
        ]
    )
    assert code == 2
    assert "ANTHROPIC_API_KEY is set" in capsys.readouterr().err


def test_preflight_and_fire_cannot_be_the_same_keystroke(tmp_path: Path) -> None:
    release = _release(tmp_path, "a")
    with pytest.raises(SystemExit):
        driver.main(["--release-dir", str(release), "--preflight", "--fire"])


def test_every_local_arm_is_driven_in_the_request_shape_it_serves(tmp_path: Path) -> None:
    """The floor injects nothing, the ceiling injects exactly gold, and the two
    ranking arms inject from the candidate pool. Driving an arm through the wrong
    request family is silent: it returns nothing and the ceiling reads as a floor."""
    from membench.runtime import IdClock, StepContext

    record = driver.load_release(_release(tmp_path, "a"))[0]
    factory = driver.default_arm_factory()
    volumes: dict[str, int] = {}
    for arm in ("none", "lexical", "grouped", "oracle"):
        ctx = StepContext(trial_id=f"t-{arm}", session_id="a", step_id="goal", clock=IdClock())
        payloads = driver.retrieve_for(factory(arm), arm, record, ctx)
        volumes[arm] = sum(len(text) for text in payloads.values())
    assert volumes["none"] == 0
    assert volumes["oracle"] == len("the owner is dana")
    assert volumes["lexical"] > 0
    assert volumes["grouped"] > 0


def test_ours_is_refused_without_its_store_rather_than_built_broken() -> None:
    with pytest.raises(driver.PublicBaselineError, match="needs --store"):
        driver.default_arm_factory()("ours")


# ---------------------------------------------------------------------------------
# mem-r6yzk B4/B6 — the release shape the driver refuses, and the width it runs at
# ---------------------------------------------------------------------------------


def test_load_release_refuses_a_release_with_no_superseded_bucket(tmp_path: Path) -> None:
    """The defect this strictness exists for. The driver used to read the bucket with a
    `.get(...) or {}` default, so a release that published no stale text built a pool
    with no trap in it - and the copy-paste-everything policy scored a clean 1.000."""
    release = tmp_path / "data"
    release.mkdir()
    payload = _record_payload("a")
    del payload["evidence"]["superseded"]
    (release / "public.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(driver.PublicBaselineError, match="malformed release"):
        driver.load_release(release)


def test_load_release_refuses_a_pool_that_disagrees_with_the_evidence(tmp_path: Path) -> None:
    """The pool a consumer can BUILD and the pool the record PUBLISHES are one set, or
    the arms rank over a different set than the leave-one-out cut admitted and every
    number is measured against the wrong denominator."""
    release = tmp_path / "data"
    release.mkdir()
    payload = _record_payload("a")
    payload["candidate_pool"]["ids"] = ["m1", "m2"]
    (release / "public.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(driver.PublicBaselineError, match="not the record's published"):
        driver.load_release(release)


def test_stale_injected_counts_the_stale_entries_an_arm_surfaced(tmp_path: Path) -> None:
    """Reward alone cannot tell a record failed by surfacing staleness from one failed
    by retrieving nothing. This column is the difference."""
    record = driver.load_release(_release(tmp_path, "a"))[0]
    assert record.superseded_ids == ("m3",)
    assert record.stale_injected({}) == 0
    assert record.stale_injected({"m1": "the owner is dana"}) == 0
    assert record.stale_injected(dict(record.candidates)) == 1


def test_every_ranking_arm_is_built_at_the_public_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The width ruling, at the seam that applies it. The registry default is 10, which
    is wider than this corpus' pool: an arm left there returns every candidate that
    overlaps the query at all, which is not retrieval, and the table reads the ranking
    arms as tied at the ceiling while none of them chose anything.

    `nemo-embed` builds its real client from `default_nemo_embedder`, which imports
    sentence-transformers and loads the pinned weights. Only that model load is
    stubbed: the factory branch, the registry call and the width argument are the
    real ones, and the stub is never asked to embed anything."""
    monkeypatch.setattr(
        nemo_embed_system, "default_nemo_embedder", lambda *a, **k: _UncalledEmbedder()
    )
    assert driver.PUBLIC_TOP_K == 6
    factory = driver.default_arm_factory()
    for arm in ("lexical", "nemo-embed"):
        assert factory(arm)._top_k == driver.PUBLIC_TOP_K, arm
    grouped = factory("grouped")
    # Same item width, different shape: PUBLIC_PER_GROUP_K items from each of
    # PUBLIC_GROUPS_K groups, and the product is the flat arms' width. The factory is
    # wired from those two constants rather than from a literal, so a split that stops
    # multiplying out to PUBLIC_TOP_K fails at driver import, not here.
    assert grouped.per_group_k == driver.PUBLIC_PER_GROUP_K
    assert grouped.groups_k == driver.PUBLIC_GROUPS_K
    assert grouped.effective_k == driver.PUBLIC_TOP_K
    assert grouped.base._top_k == driver.PUBLIC_TOP_K


def test_the_cost_table_reports_stale_volume_per_arm() -> None:
    """An arm that wins by injecting less staleness and one that wins by retrieving
    better are different findings; without this column they read the same."""
    cells = [
        cell("lexical", "r1", 1, 1.0, injected=100, stale=0),
        cell("lexical", "r2", 1, 0.0, injected=100, stale=2),
    ]
    cost = arm_costs(cells, "lexical")
    assert cost.stale_injected_total == 2
    assert cost.stale_injected_mean == pytest.approx(1.0)
    assert cost.to_dict()["stale_injected_mean"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------------
# mem-r6yzk N1/N2 — the pool the arm is handed carries no role signal
#
# The defect these cover: the driver merged `gold`, `distractors` and `superseded`
# into one dict IN BUCKET ORDER and seeded the ranking arms with it. Python dicts
# keep insertion order, so gold sat in the low slots of all 160 released records and
# "take the first k entries I was handed" scored gold recall 560/560, the exact gold
# set on every record, and a mean graded reward of 1.000 without retrieving anything.
# The published files never had that ordering - the exporter sorts by alias - so the
# driver was reconstructing a signal the corpus had already removed, and doing the
# one thing public/README.md tells a runner not to do.
# ---------------------------------------------------------------------------------


def _multi_role_payload(record_id: str, roles: dict[str, list[str]]) -> dict[str, Any]:
    """A record whose four candidates can be re-filed under any buckets.

    The aliases are chosen so BUCKET order and ALIAS order disagree: filed as
    gold-then-distractor-then-superseded the pool reads d, c, a, b, while by alias it
    reads a, b, c, d. An implementation that keeps insertion order hands the gold out
    first; one that sorts does not."""
    texts = {
        "k-aaa": "the retention window is 7 years",
        "k-bbb": "the retention window is 1 year",
        "k-ccc": "the retention window is 30 days",
        "k-ddd": "the deploy timeout is 15s",
    }
    gold, distractors, superseded = roles["gold"], roles["distractors"], roles["superseded"]
    return _record_payload(
        record_id,
        candidate_pool={"ids": sorted(texts)},
        evidence={
            "gold": {mid: texts[mid] for mid in gold},
            "distractors": {mid: texts[mid] for mid in distractors},
            "superseded": {mid: texts[mid] for mid in superseded},
            "gold_ids": list(gold),
            "superseded_ids": list(superseded),
        },
    )


def _load_one(tmp_path: Path, payload: dict[str, Any]) -> Any:
    release = tmp_path / "data"
    release.mkdir(parents=True)
    (release / "public.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return driver.load_release(release)[0]


def test_the_seeded_pool_is_keyed_by_alias_not_by_bucket(tmp_path: Path) -> None:
    """The order of the mapping the arm is seeded with is a pure function of the
    record's own ids. Change nothing but which bucket each candidate is filed under
    and the key order must not move."""
    roles_a = {"gold": ["k-ddd", "k-ccc"], "distractors": ["k-aaa"], "superseded": ["k-bbb"]}
    roles_b = {"gold": ["k-bbb", "k-aaa"], "distractors": ["k-ddd"], "superseded": ["k-ccc"]}
    first = _load_one(tmp_path / "a", _multi_role_payload("r", roles_a))
    second = _load_one(tmp_path / "b", _multi_role_payload("r", roles_b))

    expected = ["k-aaa", "k-bbb", "k-ccc", "k-ddd"]
    assert list(first.candidates) == expected
    assert list(second.candidates) == expected, (
        "the pool's key order moved when only the bucket partition changed, so the "
        "order is labelling candidates by role"
    )
    # And the defect's own signature: under bucket order the first |gold| entries ARE
    # the gold set, which is what made a no-retrieval policy score the ceiling.
    bucket_order = ["k-ddd", "k-ccc", "k-aaa", "k-bbb"]
    assert set(bucket_order[:2]) == set(first.gold_ids)
    assert set(list(first.candidates)[:2]) != set(first.gold_ids)


def test_the_seeded_pool_order_is_stable_across_loads(tmp_path: Path) -> None:
    """Deterministic across runs and processes: it is `sorted` over id strings, so it
    cannot ride PYTHONHASHSEED or the order the exporter happened to write."""
    roles = {"gold": ["k-ddd", "k-ccc"], "distractors": ["k-aaa"], "superseded": ["k-bbb"]}
    payload = _multi_role_payload("r", roles)
    once = _load_one(tmp_path / "1", payload)
    twice = _load_one(tmp_path / "2", payload)
    assert list(once.candidates) == list(twice.candidates)
    assert list(once.candidates) == sorted(once.candidates)


def test_a_candidate_filed_under_two_buckets_is_refused(tmp_path: Path) -> None:
    """Merging the buckets means a duplicated id would silently take the last bucket's
    text, and the record would carry one candidate with two roles."""
    release = tmp_path / "data"
    release.mkdir()
    payload = _record_payload("a")
    payload["evidence"]["distractors"]["m1"] = "the owner is dana"
    (release / "public.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(driver.PublicBaselineError, match="two evidence buckets"):
        driver.load_release(release)


RELEASE_DIR = Path(__file__).resolve().parents[2] / "public" / "data"


def _first_k_policy(records: list[Any], k: int) -> dict[str, float]:
    """The policy the driver must not reward: take the first k entries of the mapping
    you were handed, inject nothing else, and paste them as the answer.

    Reported against the closed-form chance rate for a uniform k-subset of the same
    pool: mean recall k/n per record, and the 3-sigma band of that mean from the
    hypergeometric variance. A pool ordering that carries role information shows up
    as a mean far above the band; an ordering that carries none sits inside it."""
    gold_hits = gold_total = exact = 0
    reward = chance = variance = 0.0
    for record in records:
        first = list(record.candidates)[:k]
        gold = set(record.gold_ids)
        gold_hits += len(gold & set(first))
        gold_total += len(gold)
        exact += int(set(first) == gold)
        reward += driver.grade("\n".join(record.candidates[mid] for mid in first), record)[0]
        n, g = len(record.candidates), len(gold)
        chance += k / n
        variance += (k * (g / n) * (1 - g / n) * (n - k) / (n - 1)) / (g * g)
    n_records = len(records)
    return {
        "recall": gold_hits / gold_total,
        "exact": exact,
        "reward": reward / n_records,
        "chance_recall": chance / n_records,
        "three_sigma": 3.0 * math.sqrt(variance) / n_records,
    }


def test_first_k_of_the_seeded_pool_does_not_beat_chance_on_the_released_set() -> None:
    """The measurement, on the real released set. Before the fix: recall 560/560 =
    1.0000, exact gold set 80/160 at k=4 (160/160 at k=|gold|) on the v1 geometry, mean
    graded reward
    1.0000. A missing corpus FAILS rather than skips - a silent skip is how this went
    unmeasured in the first place."""
    assert RELEASE_DIR.is_dir(), f"released set missing at {RELEASE_DIR}"
    records = driver.load_release(RELEASE_DIR)
    assert len(records) >= 100, f"only {len(records)} released records; too few to measure"

    measured = _first_k_policy(records, driver.PUBLIC_TOP_K)
    detail = (
        f"first-{driver.PUBLIC_TOP_K} recall {measured['recall']:.4f} vs chance "
        f"{measured['chance_recall']:.4f} +/- {measured['three_sigma']:.4f}; exact gold set "
        f"{measured['exact']:.0f}/{len(records)}; mean reward {measured['reward']:.4f}"
    )
    assert measured["recall"] <= measured["chance_recall"] + measured["three_sigma"], detail
    assert measured["exact"] == 0, detail
    # The gold-only control still passes every record, so the low reward above is the
    # policy failing and not the grader being unable to score anything.
    gold_only = sum(
        1 for record in records if driver.grade("\n".join(record.gold_payloads.values()), record)[1]
    )
    assert gold_only == len(records)
    assert measured["reward"] < 0.5, detail


def test_first_k_at_the_gold_width_does_not_recover_the_gold_set() -> None:
    """The same probe at the width that made the defect maximal: k = |gold| per
    record, where bucket ordering handed back the gold set exactly, 160/160."""
    assert RELEASE_DIR.is_dir(), f"released set missing at {RELEASE_DIR}"
    records = driver.load_release(RELEASE_DIR)
    recovered = sum(
        1
        for record in records
        if set(list(record.candidates)[: len(record.gold_ids)]) == set(record.gold_ids)
    )
    assert recovered == 0, f"first-|gold| recovers the gold set on {recovered}/{len(records)}"


# ---------------------------------------------------------------------------------
# mem-r6yzk N2 — an arm that is not installed here refuses before the credentials
# ---------------------------------------------------------------------------------


def test_fire_refuses_an_unavailable_arm_before_the_credential_ladder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A set ANTHROPIC_API_KEY normally refuses first. `ours` without a store cannot
    be bought under any credentials, so the operator must be told that instead of
    being sent after the token."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    release = _release(tmp_path, "a")
    code = driver.main(
        [
            "--release-dir",
            str(release),
            "--arms",
            "none,ours",
            "--fire",
            "--model",
            "m",
            "--out",
            str(tmp_path / "o.json"),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "--store" in err and "ours" in err
    assert "ANTHROPIC_API_KEY" not in err


def test_fire_refuses_an_unavailable_arm_with_no_model_and_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MEMBENCH_AGENT_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    release = _release(tmp_path, "a")
    code = driver.main(["--release-dir", str(release), "--arms", "none,ours", "--fire"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--store" in err
    assert "no model named" not in err and "--out is required" not in err


def test_fire_refuses_nemo_embed_when_the_embedder_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The arm the verifier could not run on this machine. sentence-transformers is in
    neither `pyproject.toml` nor `uv.lock`, so a machine built from the lockfile has
    no embedder; naming the arm must refuse at the probe, not at a credential."""
    monkeypatch.setattr(
        driver,
        "find_spec",
        lambda name: None if name == "sentence_transformers" else object(),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    release = _release(tmp_path, "a")
    code = driver.main(
        [
            "--release-dir",
            str(release),
            "--arms",
            "none,nemo-embed",
            "--fire",
            "--model",
            "m",
            "--out",
            str(tmp_path / "o.json"),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "sentence_transformers" in err
    assert "ANTHROPIC_API_KEY" not in err


def test_fire_refuses_an_unknown_arm_before_the_credential_ladder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    release = _release(tmp_path, "a")
    code = driver.main(
        ["--release-dir", str(release), "--arms", "none,telepathy", "--fire", "--model", "m"]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "unknown arm" in err
    assert "ANTHROPIC_API_KEY" not in err
