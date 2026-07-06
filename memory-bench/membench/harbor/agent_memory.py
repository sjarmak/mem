"""Where injected prior-session memory must land so the agent actually READS it.

The trace-explorer audit (2026-07-05) found the memory arms inject a file the agent
never reads: every ``ours``/``oracle`` run checked
``/logs/agent/sessions/projects/-app/memory/MEMORY.md`` (Claude Code's OWN
native-memory path) and, finding it empty, proceeded with no memory. The injected
``/memory/MEMORY.md`` was ignored — the native-memory system-prompt block outranks the
task instruction. 0 of 50 memory-bearing traces consumed the injected file.

Root cause is deterministic, from Harbor's own source:

- ``harbor/agents/installed/claude_code.py`` sets
  ``CLAUDE_CONFIG_DIR = <agent_dir>/sessions`` = ``/logs/agent/sessions`` and the agent's
  native-memory feature reads ``$CLAUDE_CONFIG_DIR/projects/<cwd-slug>/memory/MEMORY.md``.
- ``/logs/agent`` is a RUNTIME bind mount (``EnvironmentPaths``: "Mounted from
  trial_dir/agent/"), so a build-time ``COPY`` into the native path is shadowed by the
  mount — baking there cannot work.

The fix: relocate ``CLAUDE_CONFIG_DIR`` to a build-bakeable directory OUTSIDE ``/app``
(so an agent write is classified ``OUTSIDE_WORK_DIR``, never polluting the candidate
diff) and OUTSIDE ``/logs`` (so a build-time ``COPY`` survives), then bake the injected
memory at the native sub-path under it. The override reaches the agent through the
installed agent's ``extra_env`` (``harbor/agents/factory.py`` ``extra_env=config.env``),
which is merged OVER the adapter's env at exec (``agents/installed/base.py``
``merged_env.update(self._extra_env)``), so it wins over the hard-coded default. Harvest
is unaffected — it reads ``/logs/agent/claude-code.txt`` (the adapter's ``tee`` target,
independent of ``CLAUDE_CONFIG_DIR``).

Side effect (inert for this pipeline, documented so it is not a surprise): relocating
``CLAUDE_CONFIG_DIR`` off ``/logs/agent`` moves Claude Code's session artifacts (JSONL
transcripts, ``.claude.json``, todos) out of the only tree Harbor mounts and collects.
So for injected legs Harbor's own ``trajectory.json`` / ``cost_usd`` / token bookkeeping
(``trial.py`` ``_get_session_dir`` looks under the fixed ``/logs/agent/sessions``) and its
post-run log download go unpopulated. The probe never reads those — it scores from
``claude-code.txt`` and parses efficiency from that stream — and the local spike never
injects, so nothing here regresses; but a future consumer of ``harvest_job_dir``'s
``trajectory.json`` on an injected leg would find it absent (it falls back to the stream).

Pure module: constants + string helpers only and no membench imports, so ``probe_gate``
can depend on it without dragging in ``probe_gate``'s heavy transitive deps (and a future
``harbor_exec`` default could too). ``harbor_exec`` currently stays decoupled via a generic
``Mapping`` param, so no import edge exists today.
"""

from __future__ import annotations

from collections.abc import Mapping

# The agent's container working directory (Harbor's WORKDIR; the probe instruction names
# it). The single source of truth — ``probe_gate`` imports it as ``CONTAINER_WORKDIR``.
AGENT_WORKDIR = "/app"

# Relocated Claude Code config dir. OUTSIDE /app and OUTSIDE /logs (see module docstring).
AGENT_CONFIG_DIR = "/agent-memory"

# The in-container path the fixed probe instruction names (kept as a belt-and-suspenders
# delivery target for an agent that DOES follow the instruction literally).
INSTRUCTION_MEMORY_PATH = "/memory/MEMORY.md"

# The env override handed to the installed agent (via the job config's ``agents[].env``)
# so its native-memory block resolves to `native_memory_path()`. NOT the OAuth token —
# that still flows from the harbor process env, untouched.
AGENT_MEMORY_ENV: Mapping[str, str] = {"CLAUDE_CONFIG_DIR": AGENT_CONFIG_DIR}


def config_slug(workdir: str) -> str:
    """Claude Code's per-project directory name: the absolute cwd with every ``/`` and
    ``.`` replaced by ``-`` (e.g. ``/app`` -> ``-app``, matching Harbor's own hard-coded
    ``projects/-app`` in the claude-code adapter)."""
    return workdir.replace("/", "-").replace(".", "-")


def native_memory_path(config_dir: str = AGENT_CONFIG_DIR, workdir: str = AGENT_WORKDIR) -> str:
    """Where Claude Code's native-memory feature reads ``MEMORY.md`` for ``workdir`` under
    ``config_dir`` — i.e. ``$CLAUDE_CONFIG_DIR/projects/<cwd-slug>/memory/MEMORY.md``."""
    return f"{config_dir}/projects/{config_slug(workdir)}/memory/MEMORY.md"


# The two in-container paths a build delivers the injected memory to: the agent's native
# read path (primary — its own instinct finds it) and the instruction path (fallback).
AGENT_NATIVE_MEMORY_PATH = native_memory_path()
DELIVERED_MEMORY_PATHS: tuple[str, ...] = (AGENT_NATIVE_MEMORY_PATH, INSTRUCTION_MEMORY_PATH)
