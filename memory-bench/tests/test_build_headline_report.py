"""Tests for the mem-apg.4 headline report builder.

`scripts/build_headline_report.py` is not a package module, so it is loaded from its
file path (the test_run_grid_3arm idiom). The builder is pure deterministic
aggregation over a grid summary; these tests pin the contract the bead cares about:

- per-rung reward + leg means come straight off the scored cells (no re-weighting);
- the saturation / minimum-useful readouts REFUSE on the short live ladder and become
  computable once the ladder reaches four rungs — i.e. the refusal is ladder-driven,
  not a hardcoded "not yet";
- efficiency aggregates are surfaced verbatim from the grid's paired deltas;
- coverage groups by rig and a `None` reward leg drops from the sample as reduced `n`,
  never a silent zero;
- the rendered markdown reads off the machine artifact, so doc and JSON cannot drift.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_script("build_headline_report")


def _cell(repro, artifact, *, out=1000.0, inp=100.0, turns=50.0, tools=30.0):
    return {
        "repro_passed": repro,
        "score_artifact": artifact,
        "output_tokens": out,
        "input_tokens": inp,
        "turns": turns,
        "tool_calls": tools,
    }


def _two_rung_summary():
    """A minimal none/oracle grid: known per-rung means and one unscored repro row."""
    return {
        "conditions": ["none", "oracle"],
        "per_bundle": [
            {
                "work_id": "demo-rig-aaaaa",
                "none": _cell(0.0, 0.4, out=1200.0),
                "oracle": _cell(0.0, 0.6, out=900.0),
            },
            {
                "work_id": "demo-rig-bbbbb",
                "none": _cell(1.0, 0.8, out=800.0),
                # oracle repro unscored (diff_sim fallback) -> drops from repro leg
                "oracle": _cell(None, 0.4, out=1100.0),
            },
        ],
        "gaps": {
            "output_tokens": {
                "median_delta": -250.0,
                "mean_delta": -200.0,
                "n_oracle_gt_none": 1,
                "n_pairs": 2,
            },
            "turns": {
                "median_delta": 0.0,
                "mean_delta": 0.0,
                "n_oracle_gt_none": 0,
                "n_pairs": 2,
            },
        },
        "quality_guard": {
            "repro_scored_pairs": 3,
            "repro_passed": {"none": 1, "oracle": 0},
        },
        "rung_availability": {
            "none": "executed",
            "oracle": "executed",
            "ours": {"status": "not_executable", "reason": "0 lessons"},
        },
    }


def test_held_out_n_and_executable_rungs():
    art = builder.assemble(_two_rung_summary())
    assert art["held_out_n"] == 2
    assert art["executable_rungs"] == ["none", "oracle"]


def test_per_rung_reward_means_use_combined_reward():
    # combined_reward = 0.5*repro + 0.5*artifact, averaged over tasks.
    # none:   (0.5*0 + 0.5*0.4)=0.2 ; (0.5*1 + 0.5*0.8)=0.9  -> mean 0.55
    # oracle: (0.5*0 + 0.5*0.6)=0.3 ; (0.5*0 + 0.5*0.4)=0.2  -> mean 0.25
    art = builder.assemble(_two_rung_summary())
    rewards = {r["rung"]: r["mean_reward"] for r in art["reward_curve"]}
    assert rewards["none"] == pytest.approx(0.55)
    assert rewards["oracle"] == pytest.approx(0.25)
    # none/oracle ladder: floor_lift & ceiling_gap need `ours` -> None; the span
    # (top - bottom in ladder order = oracle - none) is the readout that resolves.
    assert art["floor_lift"] is None
    assert art["ceiling_gap"] is None
    assert art["reward_span"]["from_rung"] == "none"
    assert art["reward_span"]["to_rung"] == "oracle"
    assert art["reward_span"]["delta"] == pytest.approx(0.25 - 0.55)


def test_artifact_leg_mean_and_repro_leg_drops_unscored_row():
    art = builder.assemble(_two_rung_summary())
    repro = {s["rung"]: s for s in art["reward_legs"]["repro_passed"]}
    artifact = {s["rung"]: s for s in art["reward_legs"]["score_artifact"]}
    # oracle repro had one None -> only 1 task in the sample, not 2.
    assert repro["oracle"]["n"] == 1
    assert repro["none"]["n"] == 2
    # artifact-F1 means: none (0.4,0.8)->0.6 ; oracle (0.6,0.4)->0.5
    assert artifact["none"]["mean"] == pytest.approx(0.6)
    assert artifact["oracle"]["mean"] == pytest.approx(0.5)


def test_saturation_refused_on_short_ladder():
    art = builder.assemble(_two_rung_summary())
    assert art["saturation_point"] is None
    assert art["min_useful_combo"] is None
    assert art["ladder_refusal"] is not None
    assert "4" in art["ladder_refusal"]  # names the ≥4-rung requirement


def test_saturation_computable_once_ladder_reaches_four_rungs():
    # Same machinery, a full four-rung ladder -> the readouts resolve (proving the
    # refusal above is ladder-driven, not a hardcoded gate).
    summary = {
        "conditions": ["none", "ours", "builtin", "oracle"],
        "per_bundle": [
            {
                "work_id": "demo-rig-aaaaa",
                "none": _cell(0.0, 0.2),
                "ours": _cell(1.0, 1.0),
                "builtin": _cell(1.0, 1.0),
                "oracle": _cell(1.0, 1.0),
            }
        ],
        "gaps": {},
        "quality_guard": {},
        "rung_availability": {},
    }
    art = builder.assemble(summary)
    # ours already reaches the ceiling -> saturation and min-useful both at ours.
    assert art["saturation_point"] == "ours"
    assert art["min_useful_combo"] == "ours"
    assert art["ladder_refusal"] is None


def test_per_task_curve_has_one_row_per_bundle_and_aggregate_is_its_mean():
    art = builder.assemble(_two_rung_summary())
    rows = art["per_task_curve"]
    assert [r["work_id"] for r in rows] == ["demo-rig-aaaaa", "demo-rig-bbbbb"]
    # the aggregate none reward is the mean of the per-task none rewards
    none_rewards = [r["rewards"]["none"] for r in rows]
    agg_none = next(r["mean_reward"] for r in art["reward_curve"] if r["rung"] == "none")
    assert agg_none == pytest.approx(sum(none_rewards) / len(none_rewards))


def test_efficiency_rollup_surfaces_grid_deltas_verbatim():
    art = builder.assemble(_two_rung_summary())
    assert art["efficiency"]["output_tokens"]["median_delta"] == -250.0
    assert art["efficiency"]["turns"]["n_pairs"] == 2


def test_source_coverage_groups_by_rig_prefix():
    art = builder.assemble(_two_rung_summary())
    assert art["source_coverage"] == {"demo-rig": 2}


def test_markdown_renders_refusal_and_sections_without_drift():
    art = builder.assemble(_two_rung_summary())
    md = builder.render_markdown(art)
    assert "# mem-apg.4" in md
    assert "REFUSED" in md
    assert "Merged-diff outcome-lift" in md
    assert "structurally uncomputable" in md
    # the reward-span number printed is the one in the artifact (no recompute)
    assert f"{art['reward_span']['delta']:.3f}" in md


def test_footnote_uses_per_rung_repro_denominator_not_held_out_n():
    # oracle was repro-scored on only 1 of 2 bundles (one None) -> denominator 1, not 2.
    art = builder.assemble(_two_rung_summary())
    md = builder.render_markdown(art)
    assert "none 1/2" in md  # none scored both
    assert "oracle 0/1" in md  # oracle scored one, zero passes


def test_curated_and_other_non_default_rungs_are_not_dropped_from_coverage():
    summary = _two_rung_summary()
    summary["rung_availability"]["curated"] = "degenerate: collapses to oracle"
    md = builder.render_markdown(builder.assemble(summary))
    assert "curated" in md
    assert "degenerate: collapses to oracle" in md


def test_absent_efficiency_metric_surfaces_as_not_reported():
    # the two-rung fixture supplies only output_tokens + turns; the other two canonical
    # metrics must appear as explicit "(not reported)" rows, never silently vanish.
    md = builder.render_markdown(builder.assemble(_two_rung_summary()))
    assert "input_tokens | (not reported)" in md
    assert "tool_calls | (not reported)" in md
