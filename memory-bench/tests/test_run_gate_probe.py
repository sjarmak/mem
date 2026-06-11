"""Gate-probe CLI (mem-75t.7.6): resumability, dry-run, summary writing.

`scripts/run_gate_probe.py` is not a package module, so it is loaded from its file
path (the test_assemble_batch idiom). Execution goes through the injectable
`StreamExec` seam -- no Docker, no Harbor; git runs against a real temp repo.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.schemas.bundle import BundleEnv, TaskBundle

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_gate_probe.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("run_gate_probe", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_gate_probe"] = module
    spec.loader.exec_module(module)
    return module


probe_cli = _load_script()

GOLD_DIFF = (
    "diff --git a/src/app.ts b/src/app.ts\n"
    "--- a/src/app.ts\n"
    "+++ b/src/app.ts\n"
    "@@ -1 +1 @@\n"
    "-const value = 1\n"
    "+const value = 2\n"
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "app.ts").write_text("const value = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def _bundle(clone: Path, work_id: str) -> TaskBundle:
    commit = _git(clone, "rev-parse", "HEAD").strip()
    return TaskBundle(
        work_id=work_id,
        rig="demo",
        issue_title=f"Fix the widget ({work_id})",
        issue_body="It breaks.",
        trace_ref="/tmp/demo-trace.jsonl",
        output=ReplayResult(
            calls=(
                CallReplay(
                    index=0,
                    tool="Edit",
                    path="/orig/src/app.ts",
                    rebased_path="/orig/src/app.ts",
                    outcome=ReplayOutcome.APPLIED,
                ),
            ),
            file_diffs=(("src/app.ts", GOLD_DIFF),),
            replay_success_rate=1.0,
        ),
        env=BundleEnv(repo="demo", base_commit=commit, base_image="node:22-bookworm"),
        loo_excluded_work_ids=(work_id,),
    )


@pytest.fixture
def bundles_dir(clone: Path, tmp_path: Path) -> Path:
    out = tmp_path / "bundles"
    out.mkdir()
    for work_id in ("demo-a", "demo-b"):
        (out / f"{work_id}.json").write_text(
            _bundle(clone, work_id).model_dump_json(indent=2), encoding="utf-8"
        )
    return out


def _exec_stream_stub(calls_log: list[Path]):
    """An injectable exec that records which task dirs ran and returns a fixed
    perfect-candidate transcript."""
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
    stream = json.dumps(event)

    def exec_stream(task_dir: Path) -> str:
        calls_log.append(task_dir)
        return stream

    return exec_stream


def test_load_bundles_sorted_and_limited(bundles_dir: Path) -> None:
    bundles = probe_cli.load_bundles(bundles_dir)
    assert [b.work_id for b in bundles] == ["demo-a", "demo-b"]
    assert [b.work_id for b in probe_cli.load_bundles(bundles_dir, limit=1)] == ["demo-a"]
    with pytest.raises(FileNotFoundError):
        probe_cli.load_bundles(bundles_dir / "missing")


def test_batch_runs_persists_and_summarizes(bundles_dir: Path, clone: Path, tmp_path: Path) -> None:
    bundles = probe_cli.load_bundles(bundles_dir)
    probe_dir = tmp_path / "probe"
    executed: list[Path] = []
    tally = probe_cli.run_probe_batch(
        bundles,
        ("none", "oracle"),
        probe_dir=probe_dir,
        tasks_dir=probe_dir / "tasks",
        rig_repos={"demo": clone},
        exec_stream=_exec_stream_stub(executed),
        worktree_root=tmp_path / "wt",
    )
    assert tally == {"executed": 4, "skipped": 0, "planned": 0}
    assert len(executed) == 4
    for work_id in ("demo-a", "demo-b"):
        for condition in ("none", "oracle"):
            result_file = probe_dir / f"{work_id}.{condition}.json"
            assert result_file.is_file()
            payload = json.loads(result_file.read_text(encoding="utf-8"))
            assert payload["work_id"] == work_id
            assert payload["condition"] == condition
            assert payload["score"]["combined"] == 1.0

    out = probe_cli.write_summary(probe_dir, bundles)
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["n_pairs"] == 2
    # Stub candidate is identical under both conditions -> zero gap, no majority.
    assert summary["gaps"]["combined"]["deltas"] == [0.0, 0.0]
    assert summary["gap_positive_majority"] is False
    # No checkout left behind.
    assert probe_cli.sweep_probe_worktrees(clone) is None


def test_existing_result_files_are_skipped_on_rerun(
    bundles_dir: Path, clone: Path, tmp_path: Path
) -> None:
    bundles = probe_cli.load_bundles(bundles_dir)
    probe_dir = tmp_path / "probe"
    kwargs = {
        "probe_dir": probe_dir,
        "tasks_dir": probe_dir / "tasks",
        "rig_repos": {"demo": clone},
        "worktree_root": tmp_path / "wt",
    }
    first: list[Path] = []
    probe_cli.run_probe_batch(
        bundles, ("none", "oracle"), exec_stream=_exec_stream_stub(first), **kwargs
    )
    assert len(first) == 4

    # Drop ONE result; the rerun must execute exactly that pair and skip the rest.
    (probe_dir / "demo-b.oracle.json").unlink()
    second: list[Path] = []
    tally = probe_cli.run_probe_batch(
        bundles, ("none", "oracle"), exec_stream=_exec_stream_stub(second), **kwargs
    )
    assert tally == {"executed": 1, "skipped": 3, "planned": 0}
    assert [p.name for p in second] == ["demo-b.oracle"]
    assert (probe_dir / "demo-b.oracle.json").is_file()


def test_dry_run_constructs_validates_and_executes_nothing(
    bundles_dir: Path, clone: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundles = probe_cli.load_bundles(bundles_dir)
    probe_dir = tmp_path / "probe"
    executed: list[Path] = []
    tally = probe_cli.run_probe_batch(
        bundles,
        ("none", "oracle"),
        probe_dir=probe_dir,
        tasks_dir=probe_dir / "tasks",
        rig_repos={"demo": clone},
        exec_stream=_exec_stream_stub(executed),
        dry_run=True,
    )
    assert tally == {"executed": 0, "skipped": 0, "planned": 4}
    assert executed == []  # nothing ran
    assert list(probe_dir.glob("*.json")) == []  # no results, no summary
    # The tasks themselves WERE constructed + validated.
    assert (probe_dir / "tasks" / "demo-a.none" / "instruction.md").is_file()
    assert (probe_dir / "tasks" / "demo-a.oracle" / "memory" / "MEMORY.md").is_file()
    plan = capsys.readouterr().out
    assert plan.count("PLAN") == 4
    assert "demo-a" in plan and "oracle" in plan


def test_write_summary_none_when_unpaired(bundles_dir: Path, clone: Path, tmp_path: Path) -> None:
    bundles = probe_cli.load_bundles(bundles_dir)
    probe_dir = tmp_path / "probe"
    probe_cli.run_probe_batch(
        bundles,
        ("none",),  # single-condition run -- nothing to pair
        probe_dir=probe_dir,
        tasks_dir=probe_dir / "tasks",
        rig_repos={"demo": clone},
        exec_stream=_exec_stream_stub([]),
        worktree_root=tmp_path / "wt",
    )
    assert probe_cli.write_summary(probe_dir, bundles) is None
    assert not (probe_dir / "summary.json").exists()
