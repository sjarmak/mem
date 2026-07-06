"""Config isolation for the non-graded ``claude -p`` callsites (mem-hv9l).

mem-9ld4 isolated the graded rubric judge after a host ``code-reviewer`` agent
hijacked its replies (mem-eacq); the same vector was open at the three sibling
callsites — the bbon comparative judge, the oracle curator, and the task-type
classifier. These tests pin the shared invariants for all of them, mirroring the
`test_graded.py` invocation-env assertions: every ``claude -p`` spawn runs under a
clean EMPTY ``CLAUDE_CONFIG_DIR`` + ``--strict-mcp-config`` + a neutral cwd, auth
is preserved, the lazy default isolation materializes once per instance with the
callsite's own audit label, and the marker is echoed. All offline — injected
runners, no real claude, no network.
"""

import json
import subprocess

import pytest

from membench.bbon.comparative_judge import ClaudeComparativeJudge
from membench.judge_config import (
    ENV_CLAUDE_CONFIG_DIR,
    FORBIDDEN_CONFIG_ENTRIES,
    STRICT_MCP_CONFIG_FLAG,
    prepare_isolated_judge,
)
from membench.oracle.curator import ClaudeOracleCurator
from membench.task_types import claude_model_runner

# (class, monkeypatch target for its lazy prepare, expected audit label, model env var)
CASES = [
    pytest.param(
        ClaudeComparativeJudge,
        "membench.bbon.comparative_judge.prepare_isolated_judge",
        "comparative",
        "MEMBENCH_COMPARATIVE_JUDGE_MODEL",
        id="comparative-judge",
    ),
    pytest.param(
        ClaudeOracleCurator,
        "membench.oracle.curator.prepare_isolated_judge",
        "curator",
        "MEMBENCH_ORACLE_CURATOR_MODEL",
        id="oracle-curator",
    ),
]


def _capturing_runner(captured: dict):  # type: ignore[no-untyped-def]
    def runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(argv, 0, stdout="reply", stderr="")

    return runner


@pytest.mark.parametrize("cls, patch_target, label, env_model", CASES)
def test_complete_invokes_under_isolated_config(  # type: ignore[no-untyped-def]
    cls, patch_target, label, env_model, tmp_path, monkeypatch
) -> None:
    # Host env carries a contaminating account config + an OAuth token.
    monkeypatch.setenv(ENV_CLAUDE_CONFIG_DIR, "/home/ds/.claude-homes/account3/.claude")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-token")
    monkeypatch.delenv(env_model, raising=False)
    captured: dict = {}
    isolation = prepare_isolated_judge(base=tmp_path)

    cls(runner=_capturing_runner(captured), isolation=isolation).complete("p")

    argv, env, cwd = captured["argv"], captured["env"], captured["cwd"]
    # MCP disabled (boot-hang + agent-load guard).
    assert STRICT_MCP_CONFIG_FLAG in argv
    # Config surface redirected AWAY from the host account, to the clean dir.
    assert env[ENV_CLAUDE_CONFIG_DIR] == str(isolation.config_dir)
    assert "account3" not in env[ENV_CLAUDE_CONFIG_DIR]
    # Auth preserved -- isolation changes config, not credentials.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "secret-token"
    # Neutral cwd, outside the repo and distinct from the config dir.
    assert cwd == isolation.cwd
    assert cwd != isolation.config_dir
    # The clean config dir carries no host/project agent surface.
    for forbidden in FORBIDDEN_CONFIG_ENTRIES:
        assert not (isolation.config_dir / forbidden).exists()


@pytest.mark.parametrize("cls, patch_target, label, env_model", CASES)
def test_lazy_isolation_materializes_once_with_callsite_label(  # type: ignore[no-untyped-def]
    cls, patch_target, label, env_model, tmp_path, monkeypatch
) -> None:
    # The lazy default must materialize on first complete() and be reused (a
    # re-creation per call would splinter the audit trail), and it must carry the
    # CALLSITE's label -- a 'graded'-labelled dir would misattribute the retained
    # evidence the mem-eacq contamination was diagnosed from.
    calls: dict = {"n": 0, "label": None}

    def counting_prepare(base=None, *, label="graded"):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        calls["label"] = label
        return prepare_isolated_judge(base=tmp_path / str(calls["n"]))

    monkeypatch.setattr(patch_target, counting_prepare)
    captured: dict = {}
    instance = cls(runner=_capturing_runner(captured))

    instance.complete("p")
    first_config_dir = captured["env"][ENV_CLAUDE_CONFIG_DIR]
    instance.complete("p")

    assert calls["n"] == 1
    assert calls["label"] == label
    assert captured["env"][ENV_CLAUDE_CONFIG_DIR] == first_config_dir


@pytest.mark.parametrize("cls, patch_target, label, env_model", CASES)
def test_isolation_marker_reports_isolated(  # type: ignore[no-untyped-def]
    cls, patch_target, label, env_model, tmp_path
) -> None:
    # tmp_path-scoped isolation (the test_graded.py convention): falling through to
    # the real default base would be non-hermetic I/O against shared temp state.
    marker = cls(isolation=prepare_isolated_judge(base=tmp_path)).isolation_marker
    assert marker["isolated_config"] is True
    assert marker["strict_mcp_config"] is True
    assert "account3" not in str(marker["config_dir"])


@pytest.mark.parametrize("cls, patch_target, label, env_model", CASES)
def test_pinned_model_flag_rides_with_isolation_argv(  # type: ignore[no-untyped-def]
    cls, patch_target, label, env_model, tmp_path, monkeypatch
) -> None:
    # Isolation must not disturb the conditional --model contract (unlike graded's
    # unconditional pin): pinned -> --model present alongside the isolation flags;
    # unpinned -> no --model, isolation flags still present.
    monkeypatch.delenv(env_model, raising=False)
    isolation = prepare_isolated_judge(base=tmp_path)
    captured: dict = {}

    cls(model="haiku", runner=_capturing_runner(captured), isolation=isolation).complete("p")
    assert "--model" in captured["argv"] and "haiku" in captured["argv"]
    assert STRICT_MCP_CONFIG_FLAG in captured["argv"]

    cls(runner=_capturing_runner(captured), isolation=isolation).complete("p")
    assert "--model" not in captured["argv"]
    assert STRICT_MCP_CONFIG_FLAG in captured["argv"]


# --- task-type classifier runner (membench.task_types.claude_model_runner) --------
# The factory takes isolation as a REQUIRED positional -- there is no un-isolated
# way to build the production runner (fail-closed by construction), so its tests
# cover the invocation surface rather than an opt-in.


def test_claude_model_runner_invokes_under_isolated_config(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(ENV_CLAUDE_CONFIG_DIR, "/home/ds/.claude-homes/account3/.claude")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-token")
    captured: dict = {}
    isolation = prepare_isolated_judge(base=tmp_path)

    run = claude_model_runner("haiku", isolation, runner=_capturing_runner(captured))
    reply = run("classify these")

    assert reply == "reply"  # raw stdout, no unwrapping (parse_classification's job)
    argv, env, cwd = captured["argv"], captured["env"], captured["cwd"]
    assert argv[:3] == ["claude", "-p", "classify these"]
    assert argv[argv.index("--model") + 1] == "haiku"
    assert STRICT_MCP_CONFIG_FLAG in argv
    assert env[ENV_CLAUDE_CONFIG_DIR] == str(isolation.config_dir)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "secret-token"
    assert cwd == isolation.cwd


def test_claude_model_runner_env_assembled_fresh_per_call(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A credential refreshed between batches must reach the next spawn -- the env
    # is snapshotted per call, never cached at factory-build time.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token-1")
    captured: dict = {}
    run = claude_model_runner(
        "haiku", prepare_isolated_judge(base=tmp_path), runner=_capturing_runner(captured)
    )

    run("batch 1")
    assert captured["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "token-1"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token-2")
    run("batch 2")
    assert captured["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "token-2"


def test_claude_model_runner_nonzero_exit_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def failing(argv, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="quota exceeded")

    run = claude_model_runner("haiku", prepare_isolated_judge(base=tmp_path), runner=failing)
    with pytest.raises(RuntimeError, match="claude -p failed"):
        run("p")


# --- ClaudeComparativeJudge end-to-end under isolation ------------------------------
# One non-parametrized smoke: the wrapped-CLI-JSON unwrap still works with the
# isolation argv/env in place (the reply path is unchanged by mem-hv9l).


def test_comparative_judge_reply_path_unchanged_under_isolation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    inner = '{"winner": "B", "confidence": 0.9, "rationale": "warm"}'

    def runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"result": inner, "usage": {}}), stderr=""
        )

    judge = ClaudeComparativeJudge(runner=runner, isolation=prepare_isolated_judge(base=tmp_path))
    assert json.loads(judge.complete("p"))["winner"] == "B"
