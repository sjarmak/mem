"""Bundle-grid dual scoring (mem-apg.3): cached-run rescoring, pairing arithmetic,
ours-rung evidence, and the grid summary.

No Docker, no network, no live test runs: the repro leg goes through
`StubReproRunner`; retrieval through an injected ``run_json``.
"""

import json
from pathlib import Path

import pytest

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.grading.dual_verifier import ReproOutcome, StubReproRunner
from membench.grading.probe_direct import ProbeEfficiency
from membench.harbor.bundle_grid import (
    GridConditionResult,
    OursRungEvidence,
    load_grid_ready_work_ids,
    ours_rung_evidence,
    pair_grid,
    score_grid_condition,
    summarize_grid,
)
from membench.memory_systems.ours_system import OursQuery
from membench.schemas.bundle import BundleEnv, CuratedOracle, TaskBundle
from tests.helpers import git as _git

IMPL_DIFF = (
    "diff --git a/src/app.ts b/src/app.ts\n"
    "--- a/src/app.ts\n"
    "+++ b/src/app.ts\n"
    "@@ -1 +1 @@\n"
    "-const value = 1\n"
    "+const value = 2\n"
)

TEST_DIFF = (
    "diff --git a/src/app.test.ts b/src/app.test.ts\n"
    "--- a/src/app.test.ts\n"
    "+++ b/src/app.test.ts\n"
    "@@ -1 +1 @@\n"
    "-// base\n"
    "+// gold\n"
)


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "app.ts").write_text("const value = 1\n", encoding="utf-8")
    (repo / "src" / "app.test.ts").write_text("// base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def _bundle(clone: Path) -> TaskBundle:
    commit = _git(clone, "rev-parse", "HEAD").strip()
    output = ReplayResult(
        calls=(
            CallReplay(
                index=0,
                tool="Edit",
                path="/orig/src/app.ts",
                rebased_path="/orig/src/app.ts",
                outcome=ReplayOutcome.APPLIED,
            ),
        ),
        file_diffs=(("src/app.test.ts", TEST_DIFF), ("src/app.ts", IMPL_DIFF)),
        replay_success_rate=1.0,
    )
    return TaskBundle(
        work_id="demo-1",
        rig="demo",
        issue_title="Fix the widget",
        issue_body="",
        trace_ref="/tmp/demo-trace.jsonl",
        output=output,
        env=BundleEnv(repo="demo", base_commit=commit, base_image="node:22-bookworm"),
        oracle_context=CuratedOracle(oracle_answer=("src/app.test.ts", "src/app.ts")),
        loo_excluded_work_ids=("demo-1",),
    )


def _job_dir(tmp_path: Path, stream: str) -> Path:
    job_dir = tmp_path / "job"
    agent = job_dir / "trial-1" / "agent"
    agent.mkdir(parents=True)
    (agent / "claude-code.txt").write_text(stream, encoding="utf-8")
    return job_dir


def _stream() -> str:
    """One assistant event editing src/app.ts inside the container workspace."""
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {
                        "file_path": "/app/src/app.ts",
                        "old_string": "const value = 1",
                        "new_string": "const value = 2",
                    },
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 40},
        },
    }
    return json.dumps(event)


def test_score_grid_condition_test_repro_primary(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    result = score_grid_condition(
        bundle,
        "none",
        _job_dir(tmp_path, _stream()),
        clone=clone,
        test_runner=StubReproRunner(ReproOutcome(passed=True)),
        worktree_root=tmp_path,
    )
    assert result.direct_mode == "test_repro"
    assert result.repro_passed is True
    assert result.score_direct == 1.0
    # Candidate touched 1 of the 2 oracle files: precision 1.0, recall 0.5.
    assert result.score_artifact == pytest.approx(2 / 3)
    assert result.candidate_files == ("src/app.ts",)
    assert result.efficiency.output_tokens == 40
    assert result.metrics()["repro_passed"] == 1.0


def test_score_grid_condition_records_fallback_reason(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    result = score_grid_condition(
        bundle,
        "oracle",
        _job_dir(tmp_path, _stream()),
        clone=clone,
        test_runner=StubReproRunner(ReproOutcome(passed=False, error="npm exploded")),
        worktree_root=tmp_path,
    )
    assert result.direct_mode == "diff_sim"
    assert result.repro_passed is None
    assert result.repro_error == "npm exploded"
    assert result.diff_sim_combined is not None


def _condition(
    condition: str,
    *,
    score_direct: float = 1.0,
    repro_passed: bool | None = True,
    output_tokens: int | None = 100,
    turns: int = 10,
) -> GridConditionResult:
    return GridConditionResult(
        work_id="demo-1",
        condition=condition,
        score_direct=score_direct,
        score_artifact=0.5,
        direct_mode="test_repro" if repro_passed is not None else "diff_sim",
        repro_passed=repro_passed,
        repro_error=None,
        diff_sim_combined=None,
        efficiency=ProbeEfficiency(
            turns=turns, tool_calls=5, input_tokens=10, output_tokens=output_tokens
        ),
        candidate_files=("src/app.ts",),
    )


def test_pair_grid_deltas_oracle_minus_none() -> None:
    pair = pair_grid(
        _condition("none", score_direct=0.0, repro_passed=False, turns=20),
        _condition("oracle", score_direct=1.0, repro_passed=True, turns=12),
    )
    deltas = dict(pair.deltas)
    assert deltas["score_direct"] == 1.0
    assert deltas["repro_passed"] == 1.0
    assert deltas["turns"] == -8.0


def test_pair_grid_omits_metrics_missing_on_either_side() -> None:
    pair = pair_grid(
        _condition("none", output_tokens=None),
        _condition("oracle"),
    )
    assert "output_tokens" not in dict(pair.deltas)


def test_pair_grid_rejects_mismatches() -> None:
    with pytest.raises(ValueError, match="needs \\(none, oracle\\)"):
        pair_grid(_condition("oracle"), _condition("oracle"))


def test_ours_rung_evidence_counts_lesson_bearing_items() -> None:
    def fake_runner(query: OursQuery) -> dict:
        assert query.work_id == "demo-1" and query.scope == "same_rig_temporal"
        return {
            "items": [{"lessons": []}, {"lessons": [{"payload": "x"}]}],
            "total_matched": 7,
        }

    evidence = ours_rung_evidence(
        _bundle_for_evidence(),
        mem_bin="mem",
        store_path=Path("/tmp/store.db"),
        runner=fake_runner,
    )
    assert evidence == OursRungEvidence(
        work_id="demo-1", items=2, items_with_lessons=1, total_matched=7
    )


def _bundle_for_evidence() -> TaskBundle:
    output = ReplayResult(
        calls=(), file_diffs=(("src/app.ts", IMPL_DIFF),), replay_success_rate=0.0
    )
    return TaskBundle(
        work_id="demo-1",
        rig="demo",
        issue_title="t",
        issue_body="",
        trace_ref="/tmp/t.jsonl",
        output=output,
        env=BundleEnv(repo="demo", base_commit="0" * 40, base_image="node:22-bookworm"),
        loo_excluded_work_ids=("demo-1",),
    )


def test_summarize_grid_paired_deltas_and_rung_availability() -> None:
    pairs = [
        pair_grid(
            _condition("none", score_direct=0.0, repro_passed=False, turns=20),
            _condition("oracle", score_direct=1.0, repro_passed=True, turns=12),
        )
    ]
    evidence = [OursRungEvidence(work_id="demo-1", items=0, items_with_lessons=0, total_matched=0)]
    summary = summarize_grid(pairs, evidence)

    assert summary["n_pairs"] == 1
    assert summary["per_bundle"][0]["deltas"]["turns"] == -8.0
    assert summary["gaps"]["score_direct"]["mean_delta"] == 1.0
    assert summary["quality_guard"]["repro_scored_pairs"] == 1
    assert summary["quality_guard"]["repro_passed"] == {"none": 0, "oracle": 1}
    ours = summary["rung_availability"]["ours"]
    assert ours["status"] == "not_executable"
    assert "0 lesson-bearing item(s)" in ours["reason"]
    assert ours["evidence"][0]["items_with_lessons"] == 0
    assert "mem-whi" in summary["rung_availability"]["builtin"]


def test_summarize_grid_ours_status_follows_evidence() -> None:
    """Once a distiller populates lessons, the summary must say so on its own --
    the status is derived from the gathered evidence, never a constant."""
    pairs = [pair_grid(_condition("none"), _condition("oracle"))]
    evidence = [OursRungEvidence(work_id="demo-1", items=3, items_with_lessons=2, total_matched=9)]
    ours = summarize_grid(pairs, evidence)["rung_availability"]["ours"]
    assert ours["status"] == "payload_available"
    assert "2 lesson-bearing item(s)" in ours["reason"]


def test_summarize_grid_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        summarize_grid([], [])


def test_load_grid_ready_work_ids(tmp_path: Path) -> None:
    manifest = tmp_path / "pool.json"
    manifest.write_text(json.dumps({"admitted": ["a", "b"]}), encoding="utf-8")
    assert load_grid_ready_work_ids(manifest) == ("a", "b")
    manifest.write_text(json.dumps({"admitted": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="no admitted work_ids"):
        load_grid_ready_work_ids(manifest)
