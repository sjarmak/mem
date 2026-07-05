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

import json
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
    """The default per-user base dir for the isolated judge config, under the temp
    root. User-scoped (``getuid``) so it never collides with another account's dir
    on a shared host; stable so repeated judge calls reuse one clean root instead
    of littering the temp dir."""
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    return Path(tempfile.gettempdir()) / f"membench-graded-judge-{uid}"


@dataclass(frozen=True)
class IsolatedJudgeConfig:
    """The isolation surface for one judge subprocess: the clean ``config_dir``
    (redirected ``CLAUDE_CONFIG_DIR``), a neutral ``cwd`` outside any project, the
    ``env`` the subprocess runs under, and the ``extra_argv`` flags it must carry.
    ``marker`` is the attributable record echoed into a run's pins."""

    config_dir: Path
    cwd: Path
    env: dict[str, str]
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


def ensure_isolated_config_dir(config_dir: Path) -> Path:
    """Materialize (idempotently) the minimal clean config dir and assert it carries
    NO host/project agent surface. Writes only ``settings.json``; if a forbidden
    entry (`FORBIDDEN_CONFIG_ENTRIES`) is somehow present, raises rather than run a
    judge that could load it — isolation fails LOUD, never open."""
    config_dir.mkdir(parents=True, exist_ok=True)
    present = [name for name in FORBIDDEN_CONFIG_ENTRIES if (config_dir / name).exists()]
    if present:
        raise RuntimeError(
            f"isolated judge config dir {config_dir} is not clean: {present} present -- "
            "refusing to run the judge under a config surface that could load a host agent"
        )
    (config_dir / "settings.json").write_text(_MINIMAL_SETTINGS, encoding="utf-8")
    return config_dir


def isolated_judge_env(
    config_dir: Path, base_env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """A copy of ``base_env`` (default ``os.environ``) with ``CLAUDE_CONFIG_DIR``
    redirected to ``config_dir``. Everything else is preserved verbatim -- notably
    ``CLAUDE_CODE_OAUTH_TOKEN`` (headless auth), ``PATH`` (locating ``claude``), and
    ``HOME`` -- so isolation changes the config surface WITHOUT breaking auth."""
    env = dict(os.environ if base_env is None else base_env)
    env[ENV_CLAUDE_CONFIG_DIR] = str(config_dir)
    return env


def prepare_isolated_judge(base: Path | None = None) -> IsolatedJudgeConfig:
    """Assemble the full isolation surface for a graded-judge subprocess. Creates a
    clean ``config`` dir (redirected ``CLAUDE_CONFIG_DIR``) and a distinct neutral
    ``cwd`` dir (both under ``base``, default `_default_isolation_base`); the two are
    separate so the judge never treats its own config dir as a project checkout.
    Returns the config dir, neutral cwd, isolated env, and the ``--strict-mcp-config``
    flag to append to argv."""
    root = base if base is not None else _default_isolation_base()
    config_dir = ensure_isolated_config_dir(root / "config")
    cwd = root / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    return IsolatedJudgeConfig(
        config_dir=config_dir,
        cwd=cwd,
        env=isolated_judge_env(config_dir),
        extra_argv=(STRICT_MCP_CONFIG_FLAG,),
    )


# Round-trip guard: settings we write must stay valid JSON (a malformed settings.json
# would make claude ignore the dir and could fall back to defaults).
assert json.loads(_MINIMAL_SETTINGS) == {}
