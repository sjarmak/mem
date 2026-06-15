"""M3/M4 wired into the grid task-construction seam (build_probe_task).

No Docker: ``reconstruct_env`` tars a real temp repo (the test_probe_gate idiom).
These assert the two control conditions are first-class buildable conditions — the
payload is baked into the image, the SAME leak guard runs (a transcript / prior-work
dump quoting the gold diff fails loud), truncation is persisted, and the LOO boundary
is honoured for full-context.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.grading import OutcomeLeakError
from membench.harbor.probe_gate import ORACLE_MEMORY_CONTAINER_PATH, build_probe_task
from membench.schemas.bundle import BundleEnv, TaskBundle
from tests.helpers import git as _git

GOLD_DIFF = (
    "diff --git a/src/app.ts b/src/app.ts\n"
    "--- a/src/app.ts\n+++ b/src/app.ts\n@@ -1 +1 @@\n-const value = 1\n+const value = 2\n"
)


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
        file_diffs=(("src/app.ts", GOLD_DIFF),),
        replay_success_rate=1.0,
    )
    return TaskBundle(
        work_id="demo-1",
        rig="demo",
        issue_title="Fix the widget",
        trace_ref="/tmp/demo-trace.jsonl",
        output=output,
        env=BundleEnv(repo="demo", base_commit=commit, base_image="node:22-bookworm"),
        loo_excluded_work_ids=("demo-1", "sibling-2"),
    )


# --------------------------------------------------------------------------- #
# M3 raw-trajectory
# --------------------------------------------------------------------------- #
def test_raw_trajectory_bakes_transcript(clone: Path, tmp_path: Path):
    bundle = _bundle(clone)
    task_dir = build_probe_task(
        bundle,
        "raw-trajectory",
        tmp_path / "rt",
        rig_repos={"demo": clone},
        raw_transcript="the agent explored the repo and edited a file",
    )
    memory = (task_dir / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "explored the repo" in memory
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert f"COPY MEMORY.md {ORACLE_MEMORY_CONTAINER_PATH}" in dockerfile


def test_raw_trajectory_leak_guard_fails_loud_on_gold_diff(clone: Path, tmp_path: Path):
    bundle = _bundle(clone)
    with pytest.raises(OutcomeLeakError):
        build_probe_task(
            bundle,
            "raw-trajectory",
            tmp_path / "rt-leak",
            rig_repos={"demo": clone},
            raw_transcript=f"trace dump including the gold change:\n{GOLD_DIFF}",
        )


def test_raw_trajectory_truncation_persisted(clone: Path, tmp_path: Path):
    bundle = _bundle(clone)
    task_dir = build_probe_task(
        bundle,
        "raw-trajectory",
        tmp_path / "rt-trunc",
        rig_repos={"demo": clone},
        raw_transcript="x" * 5000,
        control_max_chars=1000,
    )
    trunc = task_dir / "truncation.json"
    assert trunc.is_file(), "truncation must be persisted, never silent"
    import json

    rec = json.loads(trunc.read_text())
    assert rec["truncated"] is True
    assert rec["kept_chars"] == 1000
    assert rec["original_chars"] == 5000


def test_raw_trajectory_requires_transcript(clone: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="raw_transcript"):
        build_probe_task(
            _bundle(clone), "raw-trajectory", tmp_path / "x", rig_repos={"demo": clone}
        )


# --------------------------------------------------------------------------- #
# M4 full-context
# --------------------------------------------------------------------------- #
def test_full_context_is_loo_bounded_in_baked_payload(clone: Path, tmp_path: Path):
    bundle = _bundle(clone)  # loo = demo-1, sibling-2
    task_dir = build_probe_task(
        bundle,
        "full-context",
        tmp_path / "fc",
        rig_repos={"demo": clone},
        in_scope_payloads={
            "demo-1": "OWN WORK should be withheld",
            "sibling-2": "SIBLING should be withheld",
            "prior-9": "legit prior work to inject",
        },
    )
    memory = (task_dir / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "prior-9" in memory and "legit prior work" in memory
    assert "should be withheld" not in memory  # LOO-excluded ids dropped by key


def test_full_context_requires_in_scope(clone: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="in_scope_payloads"):
        build_probe_task(_bundle(clone), "full-context", tmp_path / "x", rig_repos={"demo": clone})
