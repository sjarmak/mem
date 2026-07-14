"""Agent-memory delivery paths (trace-explorer audit 2026-07-05): pure constants +
string helpers that decide WHERE injected memory lands so the agent reads it."""

from pathlib import Path

from membench.harbor.agent_memory import (
    AGENT_CONFIG_DIR,
    AGENT_MEMORY_ENV,
    AGENT_NATIVE_MEMORY_DIR,
    AGENT_NATIVE_MEMORY_PATH,
    AGENT_WORKDIR,
    DELIVERED_MEMORY_PATHS,
    INSTRUCTION_MEMORY_PATH,
    NATIVE_MEMORY_GLOB,
    config_slug,
    native_memory_path,
    path_covers_native_memory,
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


def test_native_memory_glob_matches_the_path_it_globs_for(tmp_path: Path) -> None:
    # The glob is the slug-agnostic form of native_memory_path, for callers whose sandbox
    # cwd slug is not worth reconstructing (toolreq_builtin's engagement check). If the two
    # ever drift, that caller silently finds nothing — which reads as "native memory never
    # engaged", i.e. a false mechanism-disabled verdict on the PAID path.
    written = Path(native_memory_path(config_dir=str(tmp_path), workdir="/some/sandbox"))
    written.parent.mkdir(parents=True)
    written.write_text("x", encoding="utf-8")
    assert list(tmp_path.glob(NATIVE_MEMORY_GLOB)) == [written]


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


def test_native_memory_dir_is_parent_of_native_path() -> None:
    assert f"{AGENT_CONFIG_DIR}/projects/-app/memory" == AGENT_NATIVE_MEMORY_DIR
    assert f"{AGENT_NATIVE_MEMORY_DIR}/MEMORY.md" == AGENT_NATIVE_MEMORY_PATH


def test_path_covers_native_memory_matches_cc_autoload_dir() -> None:
    # Claude Code's init event reports the auto-load DIRECTORY with a trailing slash (the
    # real shape confirmed on a live Harbor leg): {"auto": ".../-app/memory/"}. It must
    # register as covering the delivered native file.
    assert path_covers_native_memory(AGENT_NATIVE_MEMORY_DIR + "/")
    assert path_covers_native_memory(AGENT_NATIVE_MEMORY_DIR)  # no trailing slash
    assert path_covers_native_memory(AGENT_NATIVE_MEMORY_PATH)  # some versions report the file


def test_path_covers_native_memory_rejects_other_paths() -> None:
    # The pre-fix bug path (CC's empty native dir under /logs) and the instruction fallback
    # are NOT the relocated native dir -> not a native auto-load signal.
    assert not path_covers_native_memory("/logs/agent/sessions/projects/-app/memory/")
    assert not path_covers_native_memory(INSTRUCTION_MEMORY_PATH)
    assert not path_covers_native_memory("/app/CLAUDE.md")
