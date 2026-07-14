"""Config isolation for the graded ``claude -p`` rubric judge (mem-9ld4).

Pure-plumbing mechanism tests: the clean config dir is materialized empty, a
contaminating agent surface makes it fail LOUD, the env redirects
``CLAUDE_CONFIG_DIR`` while preserving auth, and the marker is attributable. No
model call anywhere.
"""

import errno
import shutil

import pytest

from membench.judge_config import (
    ENV_CLAUDE_CONFIG_DIR,
    FORBIDDEN_CONFIG_ENTRIES,
    STRICT_MCP_CONFIG_FLAG,
    IsolatedJudgeConfig,
    ensure_isolated_config_dir,
    isolated_judge_env,
    prepare_isolated_judge,
    run_isolated_claude,
)


def _isolation(tmp_path) -> IsolatedJudgeConfig:  # type: ignore[no-untyped-def]
    return IsolatedJudgeConfig(
        config_dir=tmp_path / "config",
        cwd=tmp_path / "cwd",
        extra_argv=(STRICT_MCP_CONFIG_FLAG,),
    )


def test_prepare_materializes_clean_empty_config(tmp_path) -> None:  # type: ignore[no-untyped-def]
    iso = prepare_isolated_judge(base=tmp_path)
    # Exactly a settings.json -- no agents/skills/rules/commands/CLAUDE.md.
    assert (iso.config_dir / "settings.json").exists()
    for forbidden in FORBIDDEN_CONFIG_ENTRIES:
        assert not (iso.config_dir / forbidden).exists()
    # Neutral cwd, distinct from the config dir (so the upward CLAUDE.md walk is empty).
    assert iso.cwd.is_dir()
    assert iso.cwd != iso.config_dir
    assert STRICT_MCP_CONFIG_FLAG in iso.extra_argv


def test_ensure_is_idempotent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    d = tmp_path / "config"
    first = ensure_isolated_config_dir(d)
    second = ensure_isolated_config_dir(d)
    assert first == second == d
    assert (d / "settings.json").read_text() == "{}\n"


@pytest.mark.parametrize("forbidden", FORBIDDEN_CONFIG_ENTRIES)
def test_ensure_fails_loud_on_contaminating_surface(tmp_path, forbidden: str) -> None:  # type: ignore[no-untyped-def]
    # A host/project agent surface present in the "clean" dir must abort, never run.
    d = tmp_path / "config"
    d.mkdir()
    (d / forbidden).mkdir() if forbidden != "CLAUDE.md" else (d / forbidden).write_text("x")
    with pytest.raises(RuntimeError, match="not clean"):
        ensure_isolated_config_dir(d)


@pytest.mark.parametrize("forbidden", FORBIDDEN_CONFIG_ENTRIES)
def test_prepare_fails_loud_on_contaminating_cwd(tmp_path, forbidden: str) -> None:  # type: ignore[no-untyped-def]
    # The forbidden-entries check applies to cwd too, not just config_dir -- a
    # contaminating surface left in the neutral cwd must abort the same way.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / forbidden).mkdir() if forbidden != "CLAUDE.md" else (cwd / forbidden).write_text("x")
    with pytest.raises(RuntimeError, match="not clean"):
        prepare_isolated_judge(base=tmp_path)


def test_env_redirects_config_dir_and_preserves_auth(tmp_path) -> None:  # type: ignore[no-untyped-def]
    base = {
        ENV_CLAUDE_CONFIG_DIR: "/home/ds/.claude-homes/account3/.claude",
        "CLAUDE_CODE_OAUTH_TOKEN": "secret-token",
        "PATH": "/usr/bin",
        "HOME": "/home/ds",
    }
    env = isolated_judge_env(tmp_path / "config", base_env=base)
    assert env[ENV_CLAUDE_CONFIG_DIR] == str(tmp_path / "config")
    assert "account3" not in env[ENV_CLAUDE_CONFIG_DIR]
    # Auth / PATH / HOME preserved verbatim -- isolation changes config, not credentials.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "secret-token"
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/ds"


def test_marker_is_attributable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    marker = prepare_isolated_judge(base=tmp_path).marker
    assert marker["isolated_config"] is True
    assert marker["strict_mcp_config"] is True
    assert marker["config_dir"] == str(tmp_path / "config")


def test_default_base_is_fresh_per_prepare() -> None:
    # Regression (3ca1989): the default base must be a NEW dir on every call. A
    # stable per-uid path let concurrent judge invocations share (and race on) one
    # base. Production retains these dirs deliberately (they hold the judge's audit
    # trail); the test owns the two it creates, so it removes them.
    a = prepare_isolated_judge(label="freshness-check")
    b = prepare_isolated_judge(label="freshness-check")
    try:
        assert a.config_dir != b.config_dir
        assert a.cwd != b.cwd
        assert a.config_dir.parent != b.config_dir.parent
        # The label rides in the retained dir's prefix -- the audit trail names
        # its callsite.
        assert "freshness-check" in a.config_dir.parent.name
    finally:
        shutil.rmtree(a.config_dir.parent, ignore_errors=True)
        shutil.rmtree(b.config_dir.parent, ignore_errors=True)


def test_default_base_requires_label() -> None:
    # A retained default-base dir without a callsite label would be unattributable
    # (or worse, silently mislabelled) audit evidence: fail loud instead.
    with pytest.raises(ValueError, match="label is required"):
        prepare_isolated_judge()


def test_run_isolated_claude_raises_on_permission_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Regression (mem-k1a2i): the choke point's docstring promises every spawn
    # failure raises via `error` -- PermissionError must not escape raw.
    def runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        raise PermissionError(errno.EACCES, "Permission denied", "claude")

    with pytest.raises(RuntimeError, match="could not spawn"):
        run_isolated_claude(
            "prompt",
            isolation=_isolation(tmp_path),
            runner=runner,
            timeout_s=30.0,
            model=None,
            callsite="test callsite",
        )


def test_run_isolated_claude_raises_on_enoexec(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError(errno.ENOEXEC, "Exec format error", "claude")

    with pytest.raises(RuntimeError, match="could not spawn"):
        run_isolated_claude(
            "prompt",
            isolation=_isolation(tmp_path),
            runner=runner,
            timeout_s=30.0,
            model=None,
            callsite="test callsite",
        )
