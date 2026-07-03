"""mem-eacq variance-pilot CLI: rep-dir layout, rep-metric collection, report
arithmetic. No Docker, no agent runs — the execution loop is `run_probe_batch`
/ `score_runs`, already covered by their own suites; these tests pin the
pilot-specific aggregation on fixture result JSONs.

`scripts/run_variance_pilot.py` is not a package module, so it is loaded from
its file path (the test_run_grid_3arm idiom, preloading sibling-script imports).
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from membench.grading.probe_direct import ProbeEfficiency
from membench.harbor.bundle_grid import GridConditionResult

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
pilot = _load_script("run_variance_pilot")


def _result(
    work_id: str,
    condition: str,
    *,
    diff_sim: float,
    judge_score: float | None,
    repro_passed: bool | None = False,
) -> GridConditionResult:
    return GridConditionResult(
        work_id=work_id,
        condition=condition,
        score_direct=0.0,
        score_artifact=0.5,
        direct_mode="test_repro",
        repro_passed=repro_passed,
        repro_error=None,
        diff_sim=diff_sim,
        judge_score=judge_score,
        efficiency=ProbeEfficiency(turns=10, tool_calls=5, input_tokens=10, output_tokens=100),
        candidate_files=("src/app.ts",),
    )


def _write(grid_dir: Path, result: GridConditionResult) -> None:
    grid_dir.mkdir(parents=True, exist_ok=True)
    path = grid_dir / f"{result.work_id}.{result.condition}.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def test_rep_dir_layout() -> None:
    assert pilot.rep_dir(Path("/x/probe-eacq"), 1) == Path("/x/probe-eacq/rep1")
    assert pilot.rep_dir(Path("/x/probe-eacq"), 5) == Path("/x/probe-eacq/rep5")


def test_collect_rep_metrics_reads_every_rep_and_raises_on_gaps(tmp_path: Path) -> None:
    for rep, sim in ((1, 0.4), (2, 0.5)):
        _write(
            pilot.rep_dir(tmp_path, rep),
            _result("demo-a", "none-clean", diff_sim=sim, judge_score=0.5),
        )

    metrics = pilot.collect_rep_metrics(tmp_path, ["demo-a"], repeats=2, condition="none-clean")
    assert [m["diff_sim"] for m in metrics["demo-a"]] == [0.4, 0.5]

    with pytest.raises(FileNotFoundError):
        pilot.collect_rep_metrics(tmp_path, ["demo-a"], repeats=3, condition="none-clean")


def test_pilot_report_stats_pooling_and_mde(tmp_path: Path) -> None:
    metrics_by_bundle = {
        "demo-a": [
            {"diff_sim": 0.40, "judge_score": 0.5, "repro_passed": 0.0},
            {"diff_sim": 0.50, "judge_score": 0.5, "repro_passed": 0.0},
            {"diff_sim": 0.60, "judge_score": 1.0, "repro_passed": 1.0},
        ],
        "demo-b": [
            {"diff_sim": 0.20, "judge_score": None, "repro_passed": 0.0},
            {"diff_sim": 0.20, "judge_score": None, "repro_passed": 0.0},
            {"diff_sim": 0.20, "judge_score": None, "repro_passed": 0.0},
        ],
    }
    report = pilot.pilot_report(metrics_by_bundle, condition="none-clean")

    assert report["condition"] == "none-clean"
    assert report["n_bundles"] == 2
    assert report["repeats"] == {"demo-a": 3, "demo-b": 3}
    a_sim = report["per_bundle"]["demo-a"]["diff_sim"]
    assert a_sim["mean"] == pytest.approx(0.5)
    assert a_sim["sd"] == pytest.approx(0.1)
    # judge_score absent on every demo-b rep -> only demo-a contributes.
    assert "judge_score" not in report["per_bundle"]["demo-b"]
    # pooled: demo-b's diff_sim sd is 0 -> pooled sd = sqrt(0.01/2)
    assert report["pooled_within_sd"]["diff_sim"] == pytest.approx(0.1 / 2**0.5)
    # MDE floors exist for every pooled metric, keyed n{N}_k{k}, and shrink with k.
    mde = report["mde_floor"]["diff_sim"]
    assert mde["n2_k1"] > mde["n2_k5"]
    assert mde["n2_k1"] > mde["n5_k1"]
    # The bead's noise read: pooled SD compared against the 0.05 threshold.
    noise = report["noise_read"]
    assert noise["threshold"] == pytest.approx(0.05)
    assert noise["exceeds"]["diff_sim"] is True


def test_pilot_report_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        pilot.pilot_report({}, condition="none-clean")


def test_main_rejects_repeats_below_two() -> None:
    with pytest.raises(SystemExit):
        pilot.main(["--repeats", "1"])
