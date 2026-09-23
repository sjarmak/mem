"""Guards that ``public/`` is inside the three Python gates, at BOTH gate sites.

``public/`` is the published release: the data, the schema, and a standalone
validator a downloader is told to run. It is a SIBLING of ``memory-bench/``, and
both gate sites run from inside ``memory-bench/`` — CI through
``defaults.run.working-directory``, pre-commit through ``cd memory-bench``. So
"whole tree by default" (mem-sspvk, ``tests/test_python_gates_are_opt_out.py``)
stopped at the package boundary, and the validator shipped having never been
linted, formatted or type-checked. It failed all three the first time they were
pointed at it.

Three things have to hold at once, and each fails silently on its own:

1. The gate ARGV names ``../public`` (ruff, black) or ``files`` does (mypy).
2. pre-commit's ``files:`` pattern matches a path under ``public/``, or a
   commit that touches only ``public/`` never runs the hook at all.
3. The tools resolve THIS project's configuration for those files. ``public/``
   has no ``pyproject.toml`` above it before the git root, so a bare
   ``black --check ../public`` formats at black's default 88 columns while this
   repo's rule is 100 — a diff no in-repo configuration can satisfy. That one is
   the reason ``--config`` is on the gate command, and
   ``test_black_gate_uses_this_projects_line_length`` is what pins it.

The probes PLANT a real violation under ``public/`` and run the argv CI actually
runs, the same mechanism (and the same cleanup-on-failure discipline) as the
opt-out probes next door. Reading the argv out of the workflow is the point: a
probe that restated ``ruff check ../public`` would pass no matter what CI ran.
Unlike those probes, this file reads ``.pre-commit-config.yaml`` too — the local
site is half of what was broken here (mem-8o4li notes the general gap).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.paths import REPO, REPO_ROOT

_CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
_PYPROJECT = REPO / "pyproject.toml"
_PUBLIC = REPO_ROOT / "public"

# How the gate sites, which run from `memory-bench/`, spell the public tree.
_PUBLIC_TARGET = "../public"

# A real file in the published tree, used to test pre-commit's `files:` pattern
# the way pre-commit tests it: `re.search` against the repo-relative path.
_PUBLIC_FILE = "public/validator/membench_validate.py"

# Process-unique so two runs sharing a checkout cannot rmtree/mkdir the same path
# from under each other. Mundane and un-ignored on purpose: ruff and black skip
# .gitignore'd paths, so a probe named after a build artefact would be skipped for
# THAT reason and would then be testing nothing.
_PROBE_DIR = f"gate_probe_pkg_{os.getpid()}"

# One violation per gate, each visible to that gate ALONE, so a failure names
# which gate stopped reaching the published tree.
_VIOLATIONS = {
    # An unused import: pyflakes F401, and `F` is in `[tool.ruff.lint].select`.
    "ruff": ("unused_import.py", "import os\n"),
    # Lint-clean Python that only the FORMATTER objects to.
    "black": ("unformatted.py", "VALUES = { 'a' : 1 }\n"),
    # Declared `int`, returns `str`: rejected under `strict = true`.
    "mypy": ("bad_return.py", 'def answer() -> int:\n    return "not an int"\n'),
}

# Black's own default is 88 columns and this project's rule is 100, so a signature
# between the two reformats under the wrong configuration and is left alone under
# the right one. Built to width rather than written out, so the property cannot
# drift as the probe's name is edited.
_PROBE_COLUMNS = 95


def _long_but_legal(columns: int = _PROBE_COLUMNS) -> str:
    """Lint-clean source whose `def` line is exactly `columns` wide."""
    head, tail = "def gate_probe_", "(first: int, second: int) -> int:"
    padding = columns - len(head) - len(tail)
    assert padding > 0, f"cannot build a {columns}-column signature"
    return f"{head}{'w' * padding}{tail}\n    return first + second\n"


def _ci_gate_commands() -> dict[str, list[str]]:
    """The gate argv CI actually runs, keyed by tool, read from the workflow.

    Raises rather than skips when a gate is missing: a guard that quietly finds
    nothing to check reports success while checking nothing."""
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    commands: dict[str, list[str]] = {}
    for step in workflow["jobs"]["python"]["steps"]:
        argv = shlex.split(step.get("run", ""))
        if argv and argv[0] in _VIOLATIONS:
            commands[argv[0]] = argv
    missing = sorted(_VIOLATIONS.keys() - commands.keys())
    assert not missing, (
        f"no {missing} step found in the python job of {_CI_WORKFLOW.name} — the gate "
        "was renamed or removed, and these probes would silently check nothing"
    )
    return commands


def _pre_commit_hooks() -> dict[str, dict[str, Any]]:
    """The local pre-commit hooks, keyed by id."""
    config = yaml.safe_load(_PRE_COMMIT.read_text(encoding="utf-8"))
    hooks: dict[str, dict[str, Any]] = {}
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            hooks[hook["id"]] = hook
    return hooks


def _mypy_files() -> list[str]:
    config = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    files: list[str] = config["tool"]["mypy"]["files"]
    return files


def test_the_public_tree_exists_where_the_gates_point() -> None:
    """Every assertion below is vacuous if the path moved."""
    assert (REPO_ROOT / _PUBLIC_FILE).is_file(), f"{_PUBLIC_FILE} is not where the gates point"


@pytest.mark.parametrize("tool", ["ruff", "black"])
def test_ci_argv_names_the_public_tree(tool: str) -> None:
    """ruff and black take their targets on argv, so CI has to name public/."""
    argv = _ci_gate_commands()[tool]
    assert _PUBLIC_TARGET in argv, (
        f"CI's `{shlex.join(argv)}` does not name {_PUBLIC_TARGET} — the published "
        "release is outside this gate, and nothing about that failure is visible"
    )


def test_mypy_config_names_the_public_tree() -> None:
    """mypy takes its targets from config, so the crawl root list has to name it."""
    files = _mypy_files()
    assert _PUBLIC_TARGET in files, (
        f"[tool.mypy].files is {files} — public/ is a sibling of the crawl root, so "
        "'.' never reaches it"
    )


@pytest.mark.parametrize("hook_id", ["py-ruff", "py-black", "py-mypy"])
def test_pre_commit_runs_on_a_public_only_change(hook_id: str) -> None:
    """A commit touching only public/ must still run the hook. `files:` decides
    that, independently of what the hook's argv then looks at."""
    hook = _pre_commit_hooks()[hook_id]
    pattern = hook["files"]
    assert re.search(pattern, _PUBLIC_FILE) is not None, (
        f"{hook_id}'s files pattern {pattern!r} does not match {_PUBLIC_FILE} — a "
        "public/-only commit skips this gate entirely"
    )


@pytest.mark.parametrize("hook_id", ["py-ruff", "py-black"])
def test_pre_commit_argv_matches_ci(hook_id: str) -> None:
    """The local site checks what CI checks. Two sites that disagree are one site
    plus a surprise at push time."""
    tool = hook_id.removeprefix("py-")
    entry = _pre_commit_hooks()[hook_id]["entry"]
    assert _PUBLIC_TARGET in entry, f"{hook_id} entry does not name {_PUBLIC_TARGET}: {entry}"
    ci_argv = _ci_gate_commands()[tool]
    assert shlex.join(ci_argv) in entry, (
        f"{hook_id} runs {entry!r}, which is not CI's {shlex.join(ci_argv)!r} — the "
        "local gate and CI would disagree about the published tree"
    )


@pytest.fixture
def probe_root() -> Iterator[Path]:
    """An empty directory inside the published tree, removed afterwards.

    It has to live inside ``public/`` to be a faithful probe: the gates resolve
    ``../public`` relative to ``memory-bench/``, so a probe under ``tmp_path``
    would test nothing about the real targets. Cleanup runs on failure too — a
    leftover probe would fail every later gate run, and it would be sitting in the
    release directory."""
    root = _PUBLIC / _PROBE_DIR
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run_gate(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one gate as CI's python job runs it: its argv, its working directory."""
    executable = shutil.which(argv[0])
    if executable is None:
        pytest.skip(f"{argv[0]} not installed")
    return subprocess.run([executable, *argv[1:]], cwd=REPO, capture_output=True, text=True)


@pytest.mark.parametrize("tool", sorted(_VIOLATIONS))
def test_gate_reaches_the_public_tree(tool: str, probe_root: Path) -> None:
    argv = _ci_gate_commands()[tool]
    filename, source = _VIOLATIONS[tool]
    planted = probe_root / filename
    planted.write_text(source, encoding="utf-8")
    result = _run_gate(argv)
    printed = result.stdout + result.stderr  # black reports to stderr.
    assert result.returncode != 0, (
        f"`{shlex.join(argv)}` (from {_CI_WORKFLOW.name}) returned CLEAN on a planted "
        f"{tool} violation in public/{_PROBE_DIR}/ — this gate does not reach the "
        f"published tree:\n{printed}"
    )
    assert planted.name in printed, (
        f"`{shlex.join(argv)}` failed, but never named the planted probe "
        f"({planted.name}) — the failure does not show the gate reached it. Other "
        f"failures in the tree are fine; this one has to be among them:\n{printed}"
    )


def test_black_gate_uses_this_projects_line_length(probe_root: Path) -> None:
    """A clean result on a 95-column signature proves the gate read `[tool.black]`.

    Without `--config`, black finds no configuration above public/ before the git
    root and falls back to 88 columns, so it would split this line and report a
    diff the repo's own settings call correct. The gate would then be unsatisfiable
    for every file in the published tree."""
    source = _long_but_legal()
    line = source.splitlines()[0]
    assert 88 < len(line) <= 100, f"probe line is {len(line)} columns; it must be 89..100"
    planted = probe_root / "long_but_legal.py"
    planted.write_text(source, encoding="utf-8")
    argv = _ci_gate_commands()["black"]
    result = _run_gate(argv)
    printed = result.stdout + result.stderr
    assert planted.name not in printed, (
        f"`{shlex.join(argv)}` wants to reformat a {len(line)}-column line, so it is "
        f"not using this project's line-length of 100 for public/:\n{printed}"
    )
