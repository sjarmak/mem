"""Probe-gate mechanism (mem-75t.7.6): task construction, leak guards, candidate
harvest, pair scoring + gap arithmetic.

No Docker, no network: git operations run against a real temp repo (the
test_bundle_replay / test_assemble_batch no-monkeypatch idiom); execution is
exercised through the injectable `StreamExec` seam.
"""

import json
from pathlib import Path

import pytest
import toml

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.grading import OutcomeLeakError
from membench.grading.probe_direct import ProbeDirectScore, ProbeEfficiency
from membench.harbor import probe_gate as _pg
from membench.harbor.agent_memory import (
    AGENT_CONFIG_DIR,
    AGENT_MEMORY_ENV,
    AGENT_NATIVE_MEMORY_PATH,
    DELIVERED_MEMORY_PATHS,
    INSTRUCTION_MEMORY_PATH,
)
from membench.harbor.memory_inject import MEMORY_HEADER
from membench.harbor.probe_gate import (
    AGENT_ENV_FILENAME,
    CONDITIONS,
    NATIVE_MEMORY_PATHS,
    ORACLE_MEMORY_CONTAINER_PATH,
    EmptyRunError,
    MemoryNotConsumedError,
    PinMismatchError,
    ProbeConditionResult,
    _resolve_agent_env,
    assert_memory_consumed,
    assert_probe_task_clean,
    assert_run_pins,
    assert_strip_disjoint_from_gold,
    build_probe_task,
    detect_run_failure,
    harbor_stream_exec,
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
from membench.harbor.shuffled_condition import SHUFFLED, ShuffledSelection
from membench.harbor.task_env import REPLAY_ALLOWED_HOSTS
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


def test_probe_task_network_defaults_to_allowlist(clone: Path, tmp_path: Path) -> None:
    # Real probe runs must never default to public egress: the landed gold fix is
    # publicly fetchable from the rig's GitHub repo (mem-yeoz). Default = harbor's
    # allowlist mode (agent hosts + package registries); "public" stays an explicit
    # escape hatch.
    bundle = _bundle(clone)
    task_dir = build_probe_task(bundle, "none", tmp_path / "t", rig_repos={"demo": clone})
    env = toml.load(task_dir / "task.toml")["environment"]
    assert env["network_mode"] == "allowlist"
    assert env["allowed_hosts"] == list(REPLAY_ALLOWED_HOSTS)

    hatch = build_probe_task(
        bundle, "none", tmp_path / "t-pub", rig_repos={"demo": clone}, network="public"
    )
    hatch_env = toml.load(hatch / "task.toml")["environment"]
    assert hatch_env["network_mode"] == "public"
    assert "allowed_hosts" not in hatch_env


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


# --- memory delivery: native path + config-dir relocation (trace-explorer audit) --------


def test_injected_condition_bakes_native_path_and_config_dir_chmod(
    clone: Path, tmp_path: Path
) -> None:
    # The fix: deliver the injected memory to the agent's NATIVE read path (not just the
    # instruction path it ignored 0/50 times), and chmod the relocated config dir so the
    # adapter's runtime mkdir (as the agent user) works.
    bundle = _bundle(clone)
    task_dir = build_probe_task(bundle, "oracle", tmp_path / "t", rig_repos={"demo": clone})
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert f"COPY MEMORY.md {AGENT_NATIVE_MEMORY_PATH}" in dockerfile
    assert f"COPY MEMORY.md {INSTRUCTION_MEMORY_PATH}" in dockerfile  # instruction fallback kept
    assert f"RUN chmod -R 777 {AGENT_CONFIG_DIR}" in dockerfile


def test_injected_condition_writes_agent_env_sidecar(clone: Path, tmp_path: Path) -> None:
    # The CLAUDE_CONFIG_DIR relocation travels beside the task, applied by the exec layer.
    bundle = _bundle(clone)
    task_dir = build_probe_task(bundle, "oracle", tmp_path / "t", rig_repos={"demo": clone})
    sidecar = task_dir / AGENT_ENV_FILENAME
    assert json.loads(sidecar.read_text(encoding="utf-8")) == AGENT_MEMORY_ENV
    # It is harness metadata, NEVER in the Docker build context (environment/ only).
    assert not (task_dir / "environment" / AGENT_ENV_FILENAME).exists()


def test_none_condition_has_no_native_bake_or_sidecar(clone: Path, tmp_path: Path) -> None:
    # none/none-clean keep Harbor's default config dir untouched -- no injected file, so no
    # relocation and no native COPY (minimal blast radius on the established arms).
    bundle = _bundle(clone)
    task_dir = build_probe_task(bundle, "none", tmp_path / "t", rig_repos={"demo": clone})
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert AGENT_NATIVE_MEMORY_PATH not in dockerfile
    assert AGENT_CONFIG_DIR not in dockerfile
    assert not (task_dir / AGENT_ENV_FILENAME).exists()


def test_resolve_agent_env_reads_sidecar_or_none(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    injected = build_probe_task(bundle, "oracle", tmp_path / "inj", rig_repos={"demo": clone})
    plain = build_probe_task(bundle, "none", tmp_path / "plain", rig_repos={"demo": clone})
    assert _resolve_agent_env(injected) == AGENT_MEMORY_ENV
    assert _resolve_agent_env(plain) is None


def test_resolve_agent_env_rejects_wrong_shape_sidecar(tmp_path: Path) -> None:
    (tmp_path / AGENT_ENV_FILENAME).write_text('{"CLAUDE_CONFIG_DIR": 5}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"malformed.*str->str"):
        _resolve_agent_env(tmp_path)


def test_resolve_agent_env_rejects_invalid_json_sidecar(tmp_path: Path) -> None:
    # A truncated/corrupt sidecar raises the SAME "malformed" contract, not a raw
    # JSONDecodeError with a mismatched message.
    (tmp_path / AGENT_ENV_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match=r"malformed.*invalid JSON"):
        _resolve_agent_env(tmp_path)


def test_harbor_stream_exec_threads_sidecar_env_into_run_harbor_job(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The one line connecting delivery to execution: an injected task's sidecar env must
    # reach run_harbor_job; a plain task passes None.
    bundle = _bundle(clone)
    injected = build_probe_task(bundle, "oracle", tmp_path / "inj", rig_repos={"demo": clone})
    plain = build_probe_task(bundle, "none", tmp_path / "plain", rig_repos={"demo": clone})
    seen: list[object] = []

    def fake_run_harbor_job(task_dir: Path, **kwargs: object) -> Path:
        seen.append(kwargs.get("agent_env"))
        return task_dir

    monkeypatch.setattr(_pg, "run_harbor_job", fake_run_harbor_job)
    monkeypatch.setattr(_pg, "load_stream", lambda _job_dir: "")

    harbor_stream_exec(injected, jobs_dir=tmp_path / "j")
    harbor_stream_exec(plain, jobs_dir=tmp_path / "j")
    assert seen == [AGENT_MEMORY_ENV, None]


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


# --- memory consumption gate (trace-explorer audit: 0/50 injected files were read) -------


def _read_block(path: str) -> dict:
    return {"type": "tool_use", "name": "Read", "input": {"file_path": path}}


def _stream_with_result(*, tool_uses: tuple[dict, ...] = (), result_text: str | None = None) -> str:
    """A stream carrying tool_use blocks and an optional tool observation (the text a
    ``Read``/``cat`` returned) -- the two inputs the consumption gate reads."""
    events: list[dict] = [
        {
            "type": "assistant",
            "message": {
                "content": list(tool_uses),
                "usage": {"input_tokens": 100, "output_tokens": 40},
            },
        }
    ]
    if result_text is not None:
        events.append(
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "content": [{"type": "text", "text": result_text}]}
                    ]
                },
            }
        )
    return "\n".join(json.dumps(e) for e in events)


def test_assert_memory_consumed_passes_when_header_in_transcript() -> None:
    # A cat/Read observation (or a silent auto-load echoed in context) surfaces the header.
    stream = _stream_with_result(result_text=f"{MEMORY_HEADER}\n\n## oracle-files\n- src/x.py")
    assert_memory_consumed(stream, work_id="w", condition="oracle")


def test_assert_memory_consumed_passes_on_structured_read_of_delivered_path() -> None:
    # Read of a delivered path counts even if the observation was elided (no header text).
    for path in DELIVERED_MEMORY_PATHS:
        stream = _stream_with_result(tool_uses=(_read_block(path),))
        assert_memory_consumed(stream, work_id="w", condition="oracle")


def test_assert_memory_consumed_raises_on_wrong_native_path_check() -> None:
    # The exact failure mode: the agent cats its own EMPTY native dir and moves on. No
    # header, no delivered-path read -> the message names the wrong memory path it hit.
    wrong = "/logs/agent/sessions/projects/-app/memory/MEMORY.md"
    stream = _stream_with_result(
        tool_uses=(_read_block(wrong),),
        result_text="No memory file found",
    )
    with pytest.raises(MemoryNotConsumedError, match=r"never consumed.*logs/agent"):
        assert_memory_consumed(stream, work_id="w", condition="oracle")


def test_assert_memory_consumed_does_not_false_pass_on_oracle_file_path() -> None:
    # MEDIUM-4 regression: oracle payloads are bare file paths the agent edits anyway.
    # A run that edits the gold file but never reads memory must still fail the gate.
    stream = _stream(_edit_block("/app/src/x.py", "a", "b"))
    with pytest.raises(MemoryNotConsumedError):
        assert_memory_consumed(stream, work_id="w", condition="oracle")


def test_run_probe_passes_gate_when_injected_memory_is_read(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    task_dir = build_probe_task(bundle, "oracle", tmp_path / "t", rig_repos={"demo": clone})
    stream = _stream(
        _read_block(AGENT_NATIVE_MEMORY_PATH),
        _edit_block("/app/src/app.ts", "const value = 1", "const value = 2"),
    )
    result = run_probe(bundle, "oracle", task_dir, clone=clone, exec_stream=lambda _td: stream)
    assert result.condition == "oracle"
    assert result.score.file_f1 == 1.0


def test_run_probe_raises_memory_not_consumed_for_injected_condition(
    clone: Path, tmp_path: Path
) -> None:
    bundle = _bundle(clone)
    task_dir = build_probe_task(bundle, "oracle", tmp_path / "t", rig_repos={"demo": clone})
    # The agent works (edits app.ts) but never reads the injected memory -> the arm silently
    # degenerated to none; the gate must refuse to score it.
    stream = _stream(_edit_block("/app/src/app.ts", "const value = 1", "const value = 2"))
    with pytest.raises(MemoryNotConsumedError, match=r"demo-1 \[oracle\]"):
        run_probe(bundle, "oracle", task_dir, clone=clone, exec_stream=lambda _td: stream)
    # The gate fires BEFORE the candidate harvest -> no probe worktree was created.
    assert stale_probe_worktrees(clone) == ()


def test_run_probe_skips_gate_for_non_injected_condition(clone: Path, tmp_path: Path) -> None:
    # none has no injected memory, so the gate never runs even with no memory read.
    bundle = _bundle(clone)
    task_dir = build_probe_task(bundle, "none", tmp_path / "t", rig_repos={"demo": clone})
    stream = _stream(_edit_block("/app/src/app.ts", "const value = 1", "const value = 2"))
    result = run_probe(bundle, "none", task_dir, clone=clone, exec_stream=lambda _td: stream)
    assert result.score.file_f1 == 1.0


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


def _donor_selection(work_id: str = "demo-1") -> "ShuffledSelection":
    return ShuffledSelection(
        work_id=work_id,
        donor_work_id="other-9",
        recipient_repo="demo",
        donor_repo="other-repo",
        recipient_chars=len(OURS_PAYLOAD),
        donor_chars=len(DONOR_PAYLOAD),
    )


DONOR_PAYLOAD = json.dumps(
    {
        "citation": {"work_id": "other-prior-1", "rig": "other"},
        "lessons": [{"subtitle": "pin the CLI version", "facts": ["drift confounds arms"]}],
    },
    sort_keys=True,
)


def test_build_probe_task_shuffled_mirrors_ours_with_donor_payload(
    clone: Path, tmp_path: Path
) -> None:
    """mem-hhto: the placebo runs on the SAME clean-room base + injection path as
    ``ours``; only the payload content (a different bundle's retrieval) differs,
    and the donor selection is persisted as provenance."""
    bundle = _bundle_via_json_roundtrip(clone, tmp_path)
    rig_repos = {"demo": clone}
    none_dir = build_probe_task(bundle, "none", tmp_path / "t-none", rig_repos=rig_repos)
    shuffled_dir = build_probe_task(
        bundle,
        SHUFFLED,
        tmp_path / "t-shuffled",
        rig_repos=rig_repos,
        shuffled_payloads={"other-prior-1": DONOR_PAYLOAD},
        shuffled_donor=_donor_selection(),
    )

    # Clean room + the donor payload baked into the image at the oracle's path.
    dockerfile = (shuffled_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "rm -rf" in dockerfile
    assert f"COPY MEMORY.md {ORACLE_MEMORY_CONTAINER_PATH}" in dockerfile
    memory = (shuffled_dir / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "other-prior-1" in memory
    assert (shuffled_dir / "instruction.md").read_bytes() == (
        none_dir / "instruction.md"
    ).read_bytes()

    # The donor bundle id lands in the run conditions (task.toml metadata) AND the
    # full selection record persists as the task's provenance sidecar.
    assert 'shuffled_donor_work_id = "other-9"' in (shuffled_dir / "task.toml").read_text(
        encoding="utf-8"
    )
    sidecar = ShuffledSelection.model_validate_json(
        (shuffled_dir / "shuffled-donor.json").read_text(encoding="utf-8")
    )
    assert sidecar == _donor_selection()


def test_build_probe_task_shuffled_requires_payload_and_selection(
    clone: Path, tmp_path: Path
) -> None:
    bundle = _bundle(clone)
    rig_repos = {"demo": clone}
    for empty in (None, {}):
        with pytest.raises(ValueError, match=r"shuffled.*payload"):
            build_probe_task(
                bundle,
                SHUFFLED,
                tmp_path / "t",
                rig_repos=rig_repos,
                shuffled_payloads=empty,
                shuffled_donor=_donor_selection(),
            )
    with pytest.raises(ValueError, match="shuffled_donor"):
        build_probe_task(
            bundle,
            SHUFFLED,
            tmp_path / "t",
            rig_repos=rig_repos,
            shuffled_payloads={"other-prior-1": DONOR_PAYLOAD},
        )
    # A selection recorded for a different recipient is a caller bug.
    with pytest.raises(ValueError, match="recipient"):
        build_probe_task(
            bundle,
            SHUFFLED,
            tmp_path / "t",
            rig_repos=rig_repos,
            shuffled_payloads={"other-prior-1": DONOR_PAYLOAD},
            shuffled_donor=_donor_selection(work_id="someone-else"),
        )
    # Non-shuffled conditions reject the shuffled arguments.
    with pytest.raises(ValueError, match="shuffled"):
        build_probe_task(
            bundle,
            "none-clean",
            tmp_path / "t",
            rig_repos=rig_repos,
            shuffled_payloads={"other-prior-1": DONOR_PAYLOAD},
            shuffled_donor=_donor_selection(),
        )


def test_leak_guard_fires_on_planted_gold_in_shuffled_payload(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    with pytest.raises(OutcomeLeakError):
        build_probe_task(
            bundle,
            SHUFFLED,
            tmp_path / "t",
            rig_repos={"demo": clone},
            shuffled_payloads={"other-prior-1": f"donor diff: {GOLD_DIFF}"},
            shuffled_donor=_donor_selection(),
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


def _live_pinned_stream(model: str = "claude-sonnet-4-6", version: str = "2.1.173") -> str:
    """A billed (non-dead) stream carrying the init pins — passes
    detect_run_failure so the pin assertion is actually reached."""
    init = {"type": "system", "subtype": "init", "model": model, "claude_code_version": version}
    billed = {
        "type": "assistant",
        "message": {"content": [], "usage": {"input_tokens": 100, "output_tokens": 40}},
    }
    return "\n".join(json.dumps(event) for event in (init, billed)) + "\n"


def test_pinned_stream_exec_composes_dead_run_check_before_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The shared driver seam's ordering is load-bearing: a dead run's init has
    no model fields, so asserting pins first would raise a misleading
    PinMismatchError instead of the batch-handled EmptyRunError."""
    import membench.harbor.probe_gate as probe_gate_module
    from membench.harbor.probe_gate import pinned_stream_exec

    exec_stream = pinned_stream_exec(
        jobs_dir=tmp_path, model="claude-sonnet-4-6", cli_version="2.1.173"
    )

    monkeypatch.setattr(probe_gate_module, "harbor_stream_exec", lambda *a, **k: _DEAD_RUN_401)
    with pytest.raises(EmptyRunError, match=r"demo\.none-clean"):
        exec_stream(tmp_path / "demo.none-clean")

    monkeypatch.setattr(
        probe_gate_module,
        "harbor_stream_exec",
        lambda *a, **k: _live_pinned_stream(model="claude-haiku-4-5"),
    )
    with pytest.raises(PinMismatchError, match="model"):
        exec_stream(tmp_path / "demo.none-clean")

    live = _live_pinned_stream()
    monkeypatch.setattr(probe_gate_module, "harbor_stream_exec", lambda *a, **k: live)
    assert exec_stream(tmp_path / "demo.none-clean") == live


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


# --- mem-tnyo: trigger labeling + the ours-issue-trigger control ---------------------


def test_ours_task_toml_records_the_oracle_trigger(clone: Path, tmp_path: Path) -> None:
    """The `ours` condition's retrieval query is formed from the held record's
    own stored trace errors -- an oracle trigger, labeled explicitly in the run
    conditions (additive: the condition key is unchanged)."""
    bundle = _bundle(clone)
    task = build_probe_task(
        bundle,
        "ours",
        tmp_path / "t-ours",
        rig_repos={"demo": clone},
        ours_payloads={"gc-prior-1": OURS_PAYLOAD},
    )
    metadata = toml.loads((task / "task.toml").read_text(encoding="utf-8"))["metadata"]
    assert metadata["condition"] == "ours"
    assert metadata["trigger"] == "oracle"


def test_non_ours_conditions_carry_no_trigger(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    task = build_probe_task(bundle, "none-clean", tmp_path / "t-nc", rig_repos={"demo": clone})
    metadata = toml.loads((task / "task.toml").read_text(encoding="utf-8"))["metadata"]
    assert "trigger" not in metadata


def test_build_probe_task_issue_trigger_mirrors_ours(clone: Path, tmp_path: Path) -> None:
    """`ours-issue-trigger` runs the SAME clean-room strip, injection path, and
    leak guards as `ours`; only the payload provenance (the retrieval query's
    trigger) differs, and task.toml records it."""
    from membench.harbor.probe_gate import OURS_ISSUE_TRIGGER

    bundle = _bundle_via_json_roundtrip(clone, tmp_path)
    rig_repos = {"demo": clone}
    none_dir = build_probe_task(bundle, "none", tmp_path / "t-none", rig_repos=rig_repos)
    task = build_probe_task(
        bundle,
        OURS_ISSUE_TRIGGER,
        tmp_path / "t-it",
        rig_repos=rig_repos,
        ours_payloads={"gc-prior-1": OURS_PAYLOAD},
    )

    dockerfile = (task / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "rm -rf" in dockerfile  # clean-room strip
    assert f"COPY MEMORY.md {ORACLE_MEMORY_CONTAINER_PATH}" in dockerfile
    assert "gc-prior-1" in (task / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    # Byte-identical prompt: the trigger is a payload-provenance variable only.
    assert (task / "instruction.md").read_bytes() == (none_dir / "instruction.md").read_bytes()
    metadata = toml.loads((task / "task.toml").read_text(encoding="utf-8"))["metadata"]
    assert metadata["condition"] == OURS_ISSUE_TRIGGER
    assert metadata["trigger"] == "issue-text"


def test_issue_trigger_requires_payloads(clone: Path, tmp_path: Path) -> None:
    from membench.harbor.probe_gate import OURS_ISSUE_TRIGGER

    bundle = _bundle(clone)
    with pytest.raises(ValueError, match="payload"):
        build_probe_task(bundle, OURS_ISSUE_TRIGGER, tmp_path / "t", rig_repos={"demo": clone})


def test_leak_guard_fires_on_planted_gold_in_issue_trigger_payload(
    clone: Path, tmp_path: Path
) -> None:
    from membench.harbor.probe_gate import OURS_ISSUE_TRIGGER

    bundle = _bundle(clone)
    with pytest.raises(OutcomeLeakError):
        build_probe_task(
            bundle,
            OURS_ISSUE_TRIGGER,
            tmp_path / "t",
            rig_repos={"demo": clone},
            ours_payloads={"gc-prior-1": f"lesson quoting the fix:\n{GOLD_DIFF}"},
        )


def test_ours_family_persists_signature_overlap_covariate(clone: Path, tmp_path: Path) -> None:
    """H3 parity (mem-tnyo): with held signatures supplied, the build persists
    the per-payload overlap covariate at the task-dir root (run provenance,
    outside the Docker build context) -- and does NOT reject the payload."""
    from membench.harbor.memory_inject import SIGNATURE_OVERLAP_FILENAME
    from membench.harbor.probe_gate import OURS_ISSUE_TRIGGER

    bundle = _bundle(clone)
    held = ("tsc:src/app.ts:1:TS2345", "tsc:app.ts:TS2345")
    task = build_probe_task(
        bundle,
        OURS_ISSUE_TRIGGER,
        tmp_path / "t-it",
        rig_repos={"demo": clone},
        ours_payloads={"gc-prior-1": "prior work hit tsc:app.ts:TS2345 and fixed it"},
        held_signatures=held,
    )
    record = json.loads((task / SIGNATURE_OVERLAP_FILENAME).read_text(encoding="utf-8"))
    assert record["trigger"] == "issue-text"
    assert record["overlap"]["gc-prior-1"] == ["tsc:app.ts:TS2345"]
    # Outside the image build context: never agent-readable.
    assert not (task / "environment" / SIGNATURE_OVERLAP_FILENAME).exists()


def test_non_ours_conditions_reject_held_signatures(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone)
    with pytest.raises(ValueError, match="held_signatures"):
        build_probe_task(
            bundle,
            "none-clean",
            tmp_path / "t",
            rig_repos={"demo": clone},
            held_signatures=("tsc:app.ts:TS2345",),
        )
