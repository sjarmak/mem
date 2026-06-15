"""The M3/M4 control-condition build-or-exclude loop (build_probe_task injectable).

No git / reconstruct_env: the builder seam is stubbed, so these assert the loop's
own logic — leak-guard rejection → recorded exclusion, missing transcript → recorded
no_transcript, truncation surfaced, and the coverage report shape.
"""

from __future__ import annotations

from membench.bundle.replay import ReplayResult
from membench.grading.leak_guard import OutcomeLeakError
from membench.harbor.control_grid import (
    build_one,
    coverage_summary,
    run_control_build,
)
from membench.schemas.bundle import BundleEnv, TaskBundle


def _bundle(work_id="w1", trace="/nonexistent/trace.jsonl"):
    return TaskBundle(
        work_id=work_id,
        rig="demo",
        issue_title="t",
        trace_ref=trace,
        output=ReplayResult(calls=(), file_diffs=(), replay_success_rate=0.0),
        env=BundleEnv(repo="r", base_commit="abc", base_image="img"),
        loo_excluded_work_ids=(work_id,),
    )


def _ok_builder(returns_truncation=False):
    def build(bundle, condition, task_dir, **kw):
        task_dir.mkdir(parents=True, exist_ok=True)
        if returns_truncation:
            (task_dir / "truncation.json").write_text("{}", encoding="utf-8")
        return task_dir

    return build


def _leaking_builder(bundle, condition, task_dir, **kw):
    raise OutcomeLeakError([("<text>", "GOLD")])


def test_leak_rejection_becomes_recorded_exclusion(tmp_path):
    out = build_one(
        _bundle(),
        "raw-trajectory",
        tmp_path / "t",
        raw_transcript="leaky transcript",
        builder=_leaking_builder,
    )
    assert out.status == "leak_excluded"
    assert "GOLD" in out.reason
    assert out.task_dir is None


def test_missing_transcript_recorded_not_crashed(tmp_path):
    # resolve_raw_transcript returns None for an absent trace_ref ⇒ no_transcript.
    outs = run_control_build([_bundle(trace="/no/such/file.jsonl")], "raw-trajectory", tmp_path)
    assert outs[0].status == "no_transcript"
    assert "absent" in outs[0].reason


def test_built_task_surfaces_truncation(tmp_path):
    out = build_one(
        _bundle(),
        "raw-trajectory",
        tmp_path / "t",
        raw_transcript="ok",
        builder=_ok_builder(returns_truncation=True),
    )
    assert out.status == "built"
    assert out.truncated is True
    assert out.task_dir is not None


def test_coverage_summary_labels_every_outcome(tmp_path):
    outs = [
        build_one(
            _bundle("ok1"),
            "raw-trajectory",
            tmp_path / "a",
            raw_transcript="x",
            builder=_ok_builder(),
        ),
        build_one(
            _bundle("leak1"),
            "raw-trajectory",
            tmp_path / "b",
            raw_transcript="x",
            builder=_leaking_builder,
        ),
        build_one(_bundle("notrace", trace="/no/file"), "raw-trajectory", tmp_path / "c"),
    ]
    summ = coverage_summary(outs)
    assert summ["n"] == 3
    assert summ["counts"] == {"built": 1, "leak_excluded": 1, "no_transcript": 1}
    assert summ["built"] == ["ok1"]
    assert {e["work_id"] for e in summ["excluded"]} == {"leak1", "notrace"}
