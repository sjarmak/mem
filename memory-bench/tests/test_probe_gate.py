"""Probe-gate mechanism (mem-75t.7.6): task construction, leak guards, candidate
harvest, pair scoring + gap arithmetic.

No Docker, no network: git operations run against a real temp repo (the
test_bundle_replay / test_assemble_batch no-monkeypatch idiom); execution is
exercised through the injectable `StreamExec` seam.
"""

import json
from pathlib import Path

import pytest

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.grading import OutcomeLeakError
from membench.grading.probe_direct import ProbeDirectScore, ProbeEfficiency
from membench.harbor.probe_gate import (
    CONDITIONS,
    NATIVE_MEMORY_PATHS,
    ORACLE_MEMORY_CONTAINER_PATH,
    EmptyRunError,
    PinMismatchError,
    ProbeConditionResult,
    assert_probe_task_clean,
    assert_run_pins,
    assert_strip_disjoint_from_gold,
    build_probe_task,
    detect_run_failure,
    harvest_candidate,
    oracle_context_payload,
    probe_instruction,
    probe_leak_labels,
    run_probe,
    score_condition,
    score_pair,
    stale_probe_worktrees,
    summarize_pairs,
)
from membench.schemas.bundle import BundleEnv, TaskBundle
from tests.helpers import git as _git

GOLD_DIFF = (
    "diff --git a/src/app.ts b/src/app.ts\n"
    "--- a/src/app.ts\n"
    "+++ b/src/app.ts\n"
    "@@ -1 +1 @@\n"
    "-const value = 1\n"
    "+const value = 2\n"
)


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A real temp git repo standing in for the rig clone."""
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


def _bundle(clone: Path, *, issue_body: str = "The widget breaks on load.") -> TaskBundle:
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
        issue_body=issue_body,
        trace_ref="/tmp/demo-trace.jsonl",
        output=output,
        env=BundleEnv(repo="demo", base_commit=commit, base_image="node:22-bookworm"),
        loo_excluded_work_ids=("demo-1",),
    )


def _bundle_via_json_roundtrip(clone: Path, tmp_path: Path) -> TaskBundle:
    """The bundle as it would be loaded from a real ``.mem/bundles/*.json`` file."""
    path = tmp_path / "demo-1.json"
    path.write_text(_bundle(clone).model_dump_json(indent=2), encoding="utf-8")
    return TaskBundle.model_validate_json(path.read_text(encoding="utf-8"))


# --- task construction (both conditions, from a bundle JSON fixture) ----------------


def test_build_probe_task_both_conditions(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle_via_json_roundtrip(clone, tmp_path)
    rig_repos = {"demo": clone}
    dirs = {
        condition: build_probe_task(
            bundle, condition, tmp_path / f"task-{condition}", rig_repos=rig_repos
        )
        for condition in CONDITIONS
    }

    for condition, task_dir in dirs.items():
        assert (task_dir / "task.toml").is_file()
        assert (task_dir / "environment" / "Dockerfile").is_file()
        assert (task_dir / "environment" / "repo.tar").is_file()
        dockerfile = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
        assert "FROM node:22-bookworm" in dockerfile
        instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
        assert bundle.issue_title in instruction
        assert bundle.issue_body in instruction
        assert "/app" in instruction
        assert f"[{condition}]" in (task_dir / "task.toml").read_text(encoding="utf-8")

    # The prompt is BYTE-IDENTICAL across conditions.
    assert (dirs["none"] / "instruction.md").read_bytes() == (
        dirs["oracle"] / "instruction.md"
    ).read_bytes()

    # Only the oracle condition carries the injected context, baked into the image.
    assert not (dirs["none"] / "memory").exists()
    assert not (dirs["none"] / "environment" / "MEMORY.md").exists()
    memory = (dirs["oracle"] / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "src/app.ts" in memory
    assert "Files likely relevant" in memory
    assert (dirs["oracle"] / "environment" / "MEMORY.md").is_file()
    oracle_dockerfile = (dirs["oracle"] / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert f"COPY MEMORY.md {ORACLE_MEMORY_CONTAINER_PATH}" in oracle_dockerfile


def test_build_probe_task_rejects_unknown_condition_and_rig(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    with pytest.raises(ValueError, match="unknown probe condition"):
        build_probe_task(bundle, "builtin", tmp_path / "t", rig_repos={"demo": clone})
    with pytest.raises(RuntimeError, match="no local clone"):
        build_probe_task(bundle, "none", tmp_path / "t", rig_repos={})


def test_oracle_payload_is_paths_only(clone: Path) -> None:
    bundle = _bundle(clone)
    payload = oracle_context_payload(bundle)
    assert "- src/app.ts" in payload
    assert "const value" not in payload  # never diff content
    assert bundle.env.base_commit not in payload


# --- leak guard ------------------------------------------------------------------------


def test_leak_guard_fires_on_planted_gold_diff_fragment(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, issue_body=f"see this diff:\n{GOLD_DIFF}")
    with pytest.raises(OutcomeLeakError):
        build_probe_task(bundle, "none", tmp_path / "t", rig_repos={"demo": clone})
    assert not (tmp_path / "t").exists()  # leak aborts before anything reaches disk


def test_leak_guard_fires_on_planted_base_commit(clone: Path, tmp_path: Path) -> None:
    commit = _git(clone, "rev-parse", "HEAD").strip()
    bundle = _bundle(clone, issue_body=f"reproduce at {commit}")
    with pytest.raises(OutcomeLeakError):
        build_probe_task(bundle, "oracle", tmp_path / "t", rig_repos={"demo": clone})


def test_leak_guard_fires_on_verification_field_marker(clone: Path) -> None:
    bundle = _bundle(clone)
    with pytest.raises(OutcomeLeakError):
        assert_probe_task_clean(
            {"instruction.md": 'bundle dump: {"replay_success_rate": 1.0}'}, bundle
        )
    with pytest.raises(OutcomeLeakError):
        assert_probe_task_clean({"instruction.md": "score_direct=0.5"}, bundle)


def test_probe_leak_labels_cover_commit_diffs_and_markers(clone: Path) -> None:
    bundle = _bundle(clone)
    labels = probe_leak_labels(bundle)
    assert bundle.env.base_commit in labels
    assert GOLD_DIFF in labels
    assert "replay_success_rate" in labels
    assert "score_artifact" in labels


def test_prompt_identical_across_conditions_by_construction(clone: Path) -> None:
    bundle = _bundle(clone)
    # probe_instruction takes no condition argument -- one prompt for the pair.
    assert probe_instruction(bundle) == probe_instruction(bundle)
    assert "memory" in probe_instruction(bundle)  # the fixed if-exists pointer


# --- candidate harvest (rebase from container path onto a fresh checkout) ---------------


def _stream(*blocks: dict) -> str:
    """A minimal Claude Code stream-json transcript carrying the given tool_use
    blocks plus a usage-bearing assistant event."""
    events = [
        {
            "type": "assistant",
            "message": {
                "content": list(blocks),
                "usage": {"input_tokens": 100, "output_tokens": 40},
            },
        }
    ]
    return "\n".join(json.dumps(e) for e in events)


def _edit_block(path: str, old: str, new: str) -> dict:
    return {
        "type": "tool_use",
        "name": "Edit",
        "input": {"file_path": path, "old_string": old, "new_string": new},
    }


def test_harvest_candidate_rebases_container_paths(clone: Path) -> None:
    bundle = _bundle(clone)
    stream = _stream(
        _edit_block("/app/src/app.ts", "const value = 1", "const value = 2"),
        _edit_block("/etc/passwd", "root", "toor"),  # outside /app -> classified skip
    )
    result = harvest_candidate(stream, bundle, clone=clone)
    diffs = result.diff_by_file()
    assert set(diffs) == {"src/app.ts"}
    assert "+const value = 2" in diffs["src/app.ts"]
    outcomes = [c.outcome for c in result.calls]
    assert outcomes == [ReplayOutcome.APPLIED, ReplayOutcome.OUTSIDE_WORK_DIR]
    # The per-harvest checkout is gone and the clone lists no probe worktrees.
    assert stale_probe_worktrees(clone) == ()


def test_harvest_candidate_cleans_checkout_on_replay_failure(clone: Path) -> None:
    bundle = _bundle(clone)
    with pytest.raises(ValueError, match="malformed"):
        harvest_candidate(
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit",'
            '"input":{"file_path":"/app/src/app.ts"}}]}}',
            bundle,
            clone=clone,
        )
    assert stale_probe_worktrees(clone) == ()


# --- run_probe through the injectable exec seam ------------------------------------------


def test_run_probe_scores_candidate_against_gold(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    stream = _stream(_edit_block("/app/src/app.ts", "const value = 1", "const value = 2"))
    seen: list[Path] = []

    def fake_exec(task_dir: Path) -> str:
        seen.append(task_dir)
        return stream

    task_dir = build_probe_task(bundle, "none", tmp_path / "t", rig_repos={"demo": clone})
    result = run_probe(bundle, "none", task_dir, clone=clone, exec_stream=fake_exec)
    assert seen == [task_dir]
    assert result.work_id == "demo-1"
    assert result.condition == "none"
    # Candidate reproduces the gold edit exactly -> perfect direct score.
    assert result.score.file_f1 == 1.0
    assert result.score.combined == 1.0
    assert result.efficiency.turns == 1
    assert result.efficiency.input_tokens == 100
    assert result.replay_applied == 1
    assert result.replay_total == 1


# --- empty-run detection (mem-75t.7.6 run incident) --------------------------------------

# The actual 401 transcript shape from the 2026-06-11 incident: a synthetic assistant
# event with all-zero usage, then an is_error result event carrying api_error_status.
_DEAD_RUN_401 = "\n".join(
    json.dumps(e)
    for e in (
        {"type": "system", "subtype": "init", "session_id": "x"},
        {"type": "system", "subtype": "api_retry", "attempt": 1, "error_status": 401},
        {
            "type": "assistant",
            "message": {
                "model": "<synthetic>",
                "content": [{"type": "text", "text": "Failed to authenticate. API Error: 401"}],
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 401,
            "num_turns": 1,
            "result": "Failed to authenticate. API Error: 401 Invalid authentication credentials",
        },
    )
)


def test_detect_run_failure_flags_error_result_event() -> None:
    reason = detect_run_failure(_DEAD_RUN_401)
    assert reason is not None
    assert "api_error_status=401" in reason


def test_detect_run_failure_flags_zero_output_tokens() -> None:
    # No result event at all, but the agent billed zero output -> nothing ran.
    stream = json.dumps(
        {"type": "assistant", "message": {"content": [], "usage": {"input_tokens": 5}}}
    )
    reason = detect_run_failure(stream)
    assert reason is not None
    assert "zero output tokens" in reason


def test_detect_run_failure_passes_a_billed_single_turn_run() -> None:
    # A real one-turn run (the gold-reproducing stub) bills output -> NOT a dead run.
    live = _stream(_edit_block("/app/src/app.ts", "const value = 1", "const value = 2"))
    assert detect_run_failure(live) is None


def test_run_probe_raises_empty_run_error_on_dead_transcript(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    task_dir = build_probe_task(bundle, "none", tmp_path / "t", rig_repos={"demo": clone})
    with pytest.raises(EmptyRunError, match=r"demo-1 \[none\].*api_error_status=401"):
        run_probe(bundle, "none", task_dir, clone=clone, exec_stream=lambda _td: _DEAD_RUN_401)
    # The guard fires BEFORE the candidate harvest -> no probe worktree was created.
    assert stale_probe_worktrees(clone) == ()


# --- pair scoring + summary/gap arithmetic ------------------------------------------------


def _condition_result(
    condition: str,
    *,
    work_id: str = "demo-1",
    combined: float,
    file_f1: float = 0.5,
    hunk_overlap: float = 0.5,
    turns: int = 10,
    tool_calls: int = 5,
    input_tokens: int | None = 1000,
    output_tokens: int | None = 200,
) -> ProbeConditionResult:
    return ProbeConditionResult(
        work_id=work_id,
        condition=condition,
        score=ProbeDirectScore(
            file_precision=file_f1,
            file_recall=file_f1,
            file_f1=file_f1,
            per_file_overlap=(),
            hunk_overlap=hunk_overlap,
            combined=combined,
        ),
        efficiency=ProbeEfficiency(
            turns=turns,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        candidate_files=(),
        replay_applied=0,
        replay_total=0,
        replay_outside_work_dir=0,
    )


def test_score_pair_deltas_are_oracle_minus_none() -> None:
    none = _condition_result("none", combined=0.2, turns=20, input_tokens=2000)
    oracle = _condition_result("oracle", combined=0.6, turns=12, input_tokens=1500)
    pair = score_pair(none, oracle)
    deltas = dict(pair.deltas)
    assert deltas["combined"] == pytest.approx(0.4)
    assert deltas["turns"] == pytest.approx(-8.0)
    assert deltas["input_tokens"] == pytest.approx(-500.0)


def test_score_pair_omits_metrics_with_missing_tokens() -> None:
    none = _condition_result("none", combined=0.2, input_tokens=None)
    oracle = _condition_result("oracle", combined=0.6, input_tokens=1500)
    deltas = dict(score_pair(none, oracle).deltas)
    assert "input_tokens" not in deltas  # absence is typed, never imputed 0
    assert "output_tokens" in deltas


def test_score_pair_rejects_mismatches() -> None:
    with pytest.raises(ValueError, match="work_id mismatch"):
        score_pair(
            _condition_result("none", combined=0.1),
            _condition_result("oracle", combined=0.2, work_id="other"),
        )
    with pytest.raises(ValueError, match=r"needs \(none, oracle\)"):
        score_pair(
            _condition_result("oracle", combined=0.1),
            _condition_result("none", combined=0.2),
        )


def test_summarize_pairs_gap_arithmetic() -> None:
    pairs = [
        score_pair(
            _condition_result("none", work_id=f"b{i}", combined=none_c, turns=20),
            _condition_result("oracle", work_id=f"b{i}", combined=oracle_c, turns=10),
        )
        for i, (none_c, oracle_c) in enumerate([(0.1, 0.5), (0.2, 0.4), (0.3, 0.2)])
    ]
    summary = summarize_pairs(pairs)
    assert summary["n_pairs"] == 3
    gap = summary["gaps"]["combined"]
    assert gap["deltas"] == pytest.approx([0.4, 0.2, -0.1])
    assert gap["mean_delta"] == pytest.approx((0.4 + 0.2 - 0.1) / 3)
    assert gap["median_delta"] == pytest.approx(0.2)
    assert gap["n_oracle_gt_none"] == 2
    assert summary["gap_positive_majority"] is True  # 2/3 strict majority
    assert summary["gaps"]["turns"]["mean_delta"] == pytest.approx(-10.0)
    assert [b["work_id"] for b in summary["per_bundle"]] == ["b0", "b1", "b2"]


def test_summarize_pairs_no_majority_on_even_split() -> None:
    pairs = [
        score_pair(
            _condition_result("none", work_id=f"b{i}", combined=n),
            _condition_result("oracle", work_id=f"b{i}", combined=o),
        )
        for i, (n, o) in enumerate([(0.1, 0.5), (0.5, 0.1)])
    ]
    assert summarize_pairs(pairs)["gap_positive_majority"] is False


def test_summarize_pairs_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        summarize_pairs([])


def test_score_condition_empty_candidate_scores_zero(clone: Path) -> None:
    bundle = _bundle(clone)
    empty = ReplayResult(calls=(), file_diffs=(), replay_success_rate=0.0)
    result = score_condition(bundle, "none", empty, _stream())
    assert result.score.combined == 0.0
    assert result.candidate_files == ()


# --- clean-room conditions (mem-p3w: none-clean / ours) ----------------------------------


OURS_PAYLOAD = json.dumps(
    {
        "citation": {"work_id": "gc-prior-1", "rig": "demo"},
        "lessons": [{"subtitle": "warm bd before gc hook", "facts": ["bd cold-start is slow"]}],
    },
    sort_keys=True,
)


def test_build_probe_task_none_clean_strips_native_memory(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle_via_json_roundtrip(clone, tmp_path)
    rig_repos = {"demo": clone}
    none_dir = build_probe_task(bundle, "none", tmp_path / "t-none", rig_repos=rig_repos)
    clean_dir = build_probe_task(
        bundle, "none-clean", tmp_path / "t-none-clean", rig_repos=rig_repos
    )

    dockerfile = (clean_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    for native in NATIVE_MEMORY_PATHS:
        assert f"/app/{native}" in dockerfile
    assert "rm -rf" in dockerfile
    # The strip runs AFTER the repo snapshot lands at /app.
    assert dockerfile.index("tar -xf") < dockerfile.index("rm -rf")

    # The legacy (native-memory-present) condition is untouched by construction.
    assert "rm -rf" not in (none_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")

    # Same prompt, no injected memory: the ONLY variable vs `none` is the strip.
    assert (clean_dir / "instruction.md").read_bytes() == (none_dir / "instruction.md").read_bytes()
    assert not (clean_dir / "memory").exists()
    assert not (clean_dir / "environment" / "MEMORY.md").exists()


def test_build_probe_task_ours_injects_lessons_and_strips(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle_via_json_roundtrip(clone, tmp_path)
    rig_repos = {"demo": clone}
    none_dir = build_probe_task(bundle, "none", tmp_path / "t-none", rig_repos=rig_repos)
    ours_dir = build_probe_task(
        bundle,
        "ours",
        tmp_path / "t-ours",
        rig_repos=rig_repos,
        ours_payloads={"gc-prior-1": OURS_PAYLOAD},
    )

    # Clean room + the retrieved payload baked into the image at the oracle's path.
    dockerfile = (ours_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "rm -rf" in dockerfile
    assert f"COPY MEMORY.md {ORACLE_MEMORY_CONTAINER_PATH}" in dockerfile
    memory = (ours_dir / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "gc-prior-1" in memory
    assert "warm bd before gc hook" in memory
    assert (ours_dir / "environment" / "MEMORY.md").is_file()

    # The prompt stays byte-identical -- only the injected file differs.
    assert (ours_dir / "instruction.md").read_bytes() == (none_dir / "instruction.md").read_bytes()


def test_build_probe_task_ours_requires_payloads(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    for empty in (None, {}):
        with pytest.raises(ValueError, match=r"ours.*payload"):
            build_probe_task(
                bundle, "ours", tmp_path / "t", rig_repos={"demo": clone}, ours_payloads=empty
            )


def test_non_ours_conditions_reject_payloads(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    with pytest.raises(ValueError, match="payload"):
        build_probe_task(
            bundle,
            "none-clean",
            tmp_path / "t",
            rig_repos={"demo": clone},
            ours_payloads={"gc-prior-1": OURS_PAYLOAD},
        )


def test_leak_guard_fires_on_planted_gold_in_ours_payload(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    with pytest.raises(OutcomeLeakError):
        build_probe_task(
            bundle,
            "ours",
            tmp_path / "t",
            rig_repos={"demo": clone},
            ours_payloads={"gc-prior-1": f"prior diff: {GOLD_DIFF}"},
        )
    assert not (tmp_path / "t").exists()


def test_strip_disjoint_guard_rejects_gold_touching_memory_surface(clone: Path) -> None:
    """A bundle whose gold diff touches a stripped path cannot run clean-room: the
    stripped image and the (unstripped) scoring checkout would diverge."""
    bundle = _bundle(clone)
    assert_strip_disjoint_from_gold(bundle)  # src/app.ts only: fine

    bad_output = ReplayResult(
        calls=(),
        file_diffs=(("CLAUDE.md", "diff --git a/CLAUDE.md b/CLAUDE.md\n+x\n"),),
        replay_success_rate=1.0,
    )
    bad = bundle.model_copy(update={"output": bad_output})
    with pytest.raises(ValueError, match="clean-room strip"):
        assert_strip_disjoint_from_gold(bad)


# --- run-pin assertion (mem-p3w: instrument parity with the cached builtin arm) ---------


def _pinned_stream(model: str = "claude-sonnet-4-6", version: str = "2.1.173") -> str:
    # A non-init system event precedes the init (the thinking_tokens shape) -- the
    # assertion must skip it rather than read absent model fields as drift.
    decoy = {"type": "system", "subtype": "thinking_tokens"}
    init = {
        "type": "system",
        "subtype": "init",
        "model": model,
        "claude_code_version": version,
    }
    return (
        "\n".join(
            json.dumps(event) for event in (decoy, init, {"type": "result", "is_error": False})
        )
        + "\n"
    )


def test_assert_run_pins_accepts_matching_stream() -> None:
    assert_run_pins(_pinned_stream(), model="claude-sonnet-4-6", cli_version="2.1.173")


def test_assert_run_pins_rejects_model_and_version_drift() -> None:
    with pytest.raises(PinMismatchError, match="model"):
        assert_run_pins(
            _pinned_stream(model="claude-haiku-4-5"),
            model="claude-sonnet-4-6",
            cli_version="2.1.173",
        )
    with pytest.raises(PinMismatchError, match="version"):
        assert_run_pins(
            _pinned_stream(version="2.2.0"), model="claude-sonnet-4-6", cli_version="2.1.173"
        )


def test_assert_run_pins_rejects_stream_without_init_event() -> None:
    for stream in (
        json.dumps({"type": "result"}) + "\n",
        # A system event that is NOT the init must not satisfy the check.
        json.dumps({"type": "system", "subtype": "thinking_tokens"}) + "\n",
    ):
        with pytest.raises(PinMismatchError, match="no system init"):
            assert_run_pins(stream, model="claude-sonnet-4-6", cli_version="2.1.173")


def test_touches_native_memory_is_root_anchored(clone: Path) -> None:
    """The strip removes only /app/<name>, so nested copies neither conflict with
    the strip nor count as native-memory surface."""
    from membench.harbor.probe_gate import touches_native_memory

    assert touches_native_memory("CLAUDE.md")
    assert touches_native_memory(".claude/skills/.gitkeep")
    assert touches_native_memory(".agents/migration/originals/CLAUDE.md")
    assert not touches_native_memory("src/CLAUDE.md")
    assert not touches_native_memory("docs/AGENTS.md")

    nested_output = ReplayResult(
        calls=(),
        file_diffs=(("src/CLAUDE.md", "diff --git a/src/CLAUDE.md b/src/CLAUDE.md\n+x\n"),),
        replay_success_rate=1.0,
    )
    nested = _bundle(clone).model_copy(update={"output": nested_output})
    assert_strip_disjoint_from_gold(nested)  # nested copy: no conflict
