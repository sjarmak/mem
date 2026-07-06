"""Agent-memory delivery paths (trace-explorer audit 2026-07-05): pure constants +
string helpers that decide WHERE injected memory lands so the agent reads it."""

from membench.harbor.agent_memory import (
    AGENT_CONFIG_DIR,
    AGENT_MEMORY_ENV,
    AGENT_NATIVE_MEMORY_PATH,
    AGENT_WORKDIR,
    DELIVERED_MEMORY_PATHS,
    INSTRUCTION_MEMORY_PATH,
    config_slug,
    native_memory_path,
)
from membench.harbor.probe_gate import CONTAINER_WORKDIR


def test_probe_gate_reuses_agent_workdir_no_duplicate_literal() -> None:
    # probe_gate imports AGENT_WORKDIR as CONTAINER_WORKDIR (single source of truth), so
    # the native-memory slug and the reconstruct_env WORKDIR can never drift apart.
    assert CONTAINER_WORKDIR is AGENT_WORKDIR


def test_config_slug_matches_harbor_hardcoded_app_slug() -> None:
    # Harbor's claude adapter hard-codes ``projects/-app`` for the /app workdir; our slug
    # must produce the same string or the native path diverges from what the agent reads.
    assert config_slug("/app") == "-app"
    assert config_slug("/home/ds/projects/mem") == "-home-ds-projects-mem"
    assert config_slug("/a.b/c") == "-a-b-c"  # dots slugged too, like Claude Code


def test_native_memory_path_is_config_dir_projects_slug_memory() -> None:
    assert f"{AGENT_CONFIG_DIR}/projects/-app/memory/MEMORY.md" == AGENT_NATIVE_MEMORY_PATH
    assert native_memory_path("/x", "/app") == "/x/projects/-app/memory/MEMORY.md"


def test_config_dir_is_outside_app_and_logs() -> None:
    # OUTSIDE /app: an agent write is OUTSIDE_WORK_DIR, never polluting the diff.
    # OUTSIDE /logs: /logs/agent is a runtime bind mount that would shadow the build COPY.
    assert not AGENT_CONFIG_DIR.startswith("/app")
    assert not AGENT_CONFIG_DIR.startswith("/logs")
    assert AGENT_NATIVE_MEMORY_PATH.startswith(AGENT_CONFIG_DIR)


def test_agent_memory_env_relocates_config_dir_only_no_secret() -> None:
    assert AGENT_MEMORY_ENV == {"CLAUDE_CONFIG_DIR": AGENT_CONFIG_DIR}
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in AGENT_MEMORY_ENV


def test_delivered_paths_are_native_then_instruction() -> None:
    assert DELIVERED_MEMORY_PATHS == (AGENT_NATIVE_MEMORY_PATH, INSTRUCTION_MEMORY_PATH)
    assert INSTRUCTION_MEMORY_PATH == "/memory/MEMORY.md"
