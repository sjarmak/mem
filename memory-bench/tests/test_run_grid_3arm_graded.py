"""Graded 3-arm runner repeats wiring (mem-eacq): the >=3 headline floor, the
legacy-rep-1 dir layout, and the repeats-collapsed summary block. No Docker, no
agent runs — rows are assembled from in-memory `GridConditionResult`s.

`scripts/run_grid_3arm_graded.py` is not a package module, so it is loaded from
its file path (the test_run_grid_3arm idiom, preloading sibling-script imports).
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from membench.grading.probe_direct import ProbeEfficiency
from membench.harbor.bundle_grid import GridConditionResult, three_arm_row

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


for _sibling in ("run_gate_probe", "run_grid", "run_grid_3arm"):
    if _sibling not in sys.modules:
        _load_script(_sibling)
graded_cli = _load_script("run_grid_3arm_graded")


def _result(
    work_id: str, condition: str, *, diff_sim: float, turns: int = 10
) -> GridConditionResult:
    return GridConditionResult(
        work_id=work_id,
        condition=condition,
        score_direct=0.0,
        score_artifact=0.5,
        direct_mode="test_repro",
        repro_passed=False,
        repro_error=None,
        diff_sim=diff_sim,
        efficiency=ProbeEfficiency(turns=turns, tool_calls=5, input_tokens=10, output_tokens=100),
        candidate_files=("src/app.ts",),
    )


def _row(work_id: str, *, none_sim: float, ours_sim: float):
    return three_arm_row(
        _result(work_id, "none-clean", diff_sim=none_sim),
        _result(work_id, "ours", diff_sim=ours_sim),
        _result(work_id, "builtin", diff_sim=none_sim),
        ours_retrieval_empty=False,
    )


def test_repeats_floor_rejects_below_three() -> None:
    with pytest.raises(SystemExit):
        graded_cli.main(["--repeats", "2"])


def test_legacy_rep_dir_keeps_rep1_at_base() -> None:
    base = Path("/x/grid-ce")
    assert graded_cli.legacy_rep_dir(base, 1) == base
    assert graded_cli.legacy_rep_dir(base, 3) == base / "rep3"


def test_repeats_block_collapses_within_task() -> None:
    rows_by_rep = [
        [_row("demo-a", none_sim=0.4, ours_sim=0.6)],
        [_row("demo-a", none_sim=0.5, ours_sim=0.7)],
        [_row("demo-a", none_sim=0.6, ours_sim=0.8)],
    ]
    block = graded_cli.repeats_block(rows_by_rep)

    assert block["k"] == 3
    (bundle,) = block["per_bundle"]
    assert bundle["work_id"] == "demo-a"
    none_sim = bundle["arms"]["none-clean"]["diff_sim"]
    assert none_sim["mean"] == pytest.approx(0.5)
    assert none_sim["sd"] == pytest.approx(0.1)
    assert none_sim["n"] == 3
    # Delta of means, never per-rep pairing: 0.7 - 0.5.
    delta = bundle["deltas"]["ours_vs_none_clean"]["diff_sim"]
    assert delta["delta"] == pytest.approx(0.2)
    assert delta["se"] == pytest.approx((0.01 / 3 + 0.01 / 3) ** 0.5)


def test_repeats_block_requires_consistent_bundles() -> None:
    rows_by_rep = [
        [_row("demo-a", none_sim=0.4, ours_sim=0.6)],
        [_row("demo-b", none_sim=0.5, ours_sim=0.7)],
    ]
    with pytest.raises(ValueError):
        graded_cli.repeats_block(rows_by_rep)
