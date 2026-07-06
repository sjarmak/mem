"""Config isolation for the graded ``claude -p`` rubric judge (mem-9ld4).

The graded S3 judge shells a headless ``claude -p``. Left to inherit the host
``CLAUDE_CONFIG_DIR`` (an operator account carrying skills / rules / *agents* —
notably a ``code-reviewer`` subagent), a reviewable candidate diff intermittently
hijacks the session into CODE-REVIEW mode: the reply comes back as a
``{"findings": [...], "level": ...}`` object instead of the rubric
``{"criteria": [...]}``. That contaminated every graded number to date
(mem-eacq variance pilot; Stephanie ruled ISOLATE + RE-SCORE, mem-9ld4).

The fix is config/mechanism only — the judge MODEL and round count are unchanged
(``claude-sonnet-4-6`` x3). This module materializes a minimal, EMPTY
``CLAUDE_CONFIG_DIR`` (a lone ``settings.json`` — no agents, skills, rules,
commands, or ``CLAUDE.md``) and a neutral working directory outside any project,
then hands back the env + argv + cwd the judge subprocess must run under so it can
NEVER load a host or project agent. Both config surfaces are closed:

- **Host config** — ``CLAUDE_CONFIG_DIR`` is redirected to the clean dir, so the
  operator account's ``agents/skills/rules/CLAUDE.md`` are all bypassed.
- **Project config** — the judge runs with ``cwd`` set to a neutral dir under the
  temp root (not the repo), so ``claude``'s upward walk for ``CLAUDE.md`` /
  ``.claude/`` finds nothing.
- **MCP** — ``--strict-mcp-config`` (with no ``--mcp-config``) disables every MCP
  server, matching the headless-agent boot-hang guard.

Auth is unaffected: headless ``claude -p`` authenticates from the
``CLAUDE_CODE_OAUTH_TOKEN`` env var (the documented headless path), which
`isolated_judge_env` preserves along with ``PATH`` / ``HOME`` — it overrides only
``CLAUDE_CONFIG_DIR``.

ZFC: pure plumbing — directory materialization, env assembly, and a fail-loud
cleanliness assertion. No semantic judgment; the judge model still does all the
grading.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# The env var that redirects the entire ``claude`` config surface (skills, rules,
# agents, CLAUDE.md, settings) to a chosen directory.
ENV_CLAUDE_CONFIG_DIR = "CLAUDE_CONFIG_DIR"

# Config-dir entries that would let a host/project agent or instruction back into
# the judge session. If any of these is present in the "clean" dir, isolation has
# failed and we refuse to run rather than silently grade under contamination.
FORBIDDEN_CONFIG_ENTRIES = ("agents", "skills", "rules", "commands", "CLAUDE.md")

# A minimal, valid settings file so the dir is an intentional (not accidentally
# empty) config root. It enables nothing — no hooks, no MCP, no plugins.
_MINIMAL_SETTINGS = "{}\n"

# Disables every MCP server (matches the headless-agent boot-hang guard).
STRICT_MCP_CONFIG_FLAG = "--strict-mcp-config"


def _default_isolation_base() -> Path:
    """A fresh, unique base dir for one isolated judge config. ``mkdtemp`` guarantees
    the path is exclusive to this call -- concurrent judge invocations (grid runs,
    variance pilots, parallel bundles) never share a base dir, so the
    mkdir -> forbidden-entries-check -> settings.json write below needs no lock.

    Deliberately never cleaned up: the judge's ``claude -p`` writes its session
    transcripts under ``config/projects/``, and a run's pins reference this dir by
    path (`IsolatedJudgeConfig.marker`) -- together they are the post-hoc audit
    trail that proves what the judge actually did (the mem-eacq contamination was
    diagnosed from exactly this evidence). Retention is bounded by the system
    temp-dir lifecycle."""
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    return Path(tempfile.mkdtemp(prefix=f"membench-graded-judge-{uid}-"))


@dataclass(frozen=True)
class IsolatedJudgeConfig:
    """The isolation surface for one judge subprocess: the clean ``config_dir``
    (redirected ``CLAUDE_CONFIG_DIR``), a neutral ``cwd`` outside any project, and the
    ``extra_argv`` flags it must carry. ``marker`` is the attributable record echoed
    into a run's pins. There is no cached ``env`` here -- the subprocess env is
    assembled fresh per call (`isolated_judge_env`) so a mid-run credential refresh
    (e.g. ``CLAUDE_CODE_OAUTH_TOKEN``) is never shipped stale."""

    config_dir: Path
    cwd: Path
    extra_argv: tuple[str, ...]

    @property
    def marker(self) -> dict[str, object]:
        """The pins record: isolation is on, and exactly which clean config dir the
        judge ran under. Makes an isolated score attributable and distinguishable
        from a pre-isolation (contaminated) one."""
        return {
            "isolated_config": True,
            "config_dir": str(self.config_dir),
            "strict_mcp_config": STRICT_MCP_CONFIG_FLAG in self.extra_argv,
        }


def _assert_no_forbidden_entries(directory: Path) -> None:
    """Fail-loud cleanliness check shared by every isolation-surface dir (config AND
    cwd): if a forbidden entry (`FORBIDDEN_CONFIG_ENTRIES`) is somehow present, raise
    rather than run a judge that could load it -- isolation fails LOUD, never open."""
    present = [name for name in FORBIDDEN_CONFIG_ENTRIES if (directory / name).exists()]
    if present:
        raise RuntimeError(
            f"isolated judge dir {directory} is not clean: {present} present -- "
            "refusing to run the judge under a config surface that could load a host agent"
        )


def ensure_isolated_config_dir(config_dir: Path) -> Path:
    """Materialize (idempotently) the minimal clean config dir and assert it carries
    NO host/project agent surface. Writes only ``settings.json``."""
    config_dir.mkdir(parents=True, exist_ok=True)
    _assert_no_forbidden_entries(config_dir)
    (config_dir / "settings.json").write_text(_MINIMAL_SETTINGS, encoding="utf-8")
    return config_dir


def isolated_judge_env(
    config_dir: Path, base_env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """A copy of ``base_env`` (default ``os.environ``, read FRESH on every call) with
    ``CLAUDE_CONFIG_DIR`` redirected to ``config_dir``. Everything else is preserved
    verbatim -- notably ``CLAUDE_CODE_OAUTH_TOKEN`` (headless auth), ``PATH``
    (locating ``claude``), and ``HOME`` -- so isolation changes the config surface
    WITHOUT breaking auth. Call this at subprocess-launch time, not once at judge
    construction -- a cached snapshot would ship a stale token if credentials are
    refreshed mid-run."""
    env = dict(os.environ if base_env is None else base_env)
    env[ENV_CLAUDE_CONFIG_DIR] = str(config_dir)
    return env


def prepare_isolated_judge(base: Path | None = None) -> IsolatedJudgeConfig:
    """Assemble the full isolation surface for a graded-judge subprocess. Creates a
    clean ``config`` dir (redirected ``CLAUDE_CONFIG_DIR``) and a distinct neutral
    ``cwd`` dir (both under ``base``, default `_default_isolation_base` -- a fresh
    unique dir per call, so concurrent invocations never race); the two are separate
    so the judge never treats its own config dir as a project checkout. Both dirs get
    the same fail-loud cleanliness check. Returns the config dir, neutral cwd, and the
    ``--strict-mcp-config`` flag to append to argv."""
    root = base if base is not None else _default_isolation_base()
    config_dir = ensure_isolated_config_dir(root / "config")
    cwd = root / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    _assert_no_forbidden_entries(cwd)
    return IsolatedJudgeConfig(
        config_dir=config_dir,
        cwd=cwd,
        extra_argv=(STRICT_MCP_CONFIG_FLAG,),
    )
