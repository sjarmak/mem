"""Guards that the three Python gates cover a NEW directory by DEFAULT.

The gates used to hand-enumerate their targets on the CI command line (`ruff check
membench tests`, `black --check membench tests`, `mypy --strict membench`). That
makes coverage opt-IN: a directory nobody remembers to add is silently unchecked,
which is exactly how `conftest.py` and `examples/` escaped all three gates
(mem-sspvk) and how `scripts/` escaped them before mem-cv06b. The failure is
silent by construction — an uncovered directory reports no error, so nothing
distinguishes "clean" from "never looked at".

The gates are now opt-OUT: `ruff check .`, `black --check .`, and a bare `mypy`
whose `files`/`exclude` live in `[tool.mypy]`. These probes pin that inversion by
PLANTING a new package directory containing a real violation, then running each
gate against it and asserting the gate fails and names the planted file.

The commands are READ OUT OF `.github/workflows/ci.yml`, never restated here.
That indirection is the entire point: a probe that hardcoded `ruff check .` would
pass no matter what CI ran, testing only that ruff works. Reading the real argv
means reverting the workflow to enumerated targets makes the planted directory
invisible to the gate, and these tests fail — which is what a guard is for.

What they do NOT assert: which directories are excluded. `[tool.mypy].exclude` is
expected to change (mem-cv06b deletes `^scripts/` when its sweep lands); pinning
its membership here would make a legitimate coverage INCREASE fail a test. The
mechanism is the invariant, not the exclusion list.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import yaml

# The memory-bench root: holds `pyproject.toml` (so the gates read the real
# config) and is the `working-directory:` of CI's python job.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI_WORKFLOW = _REPO_ROOT.parent / ".github" / "workflows" / "ci.yml"

# Planted at the crawl root, where only an opt-out gate reaches it.
#
# The name is deliberately mundane, and must stay un-ignored: `ruff` and `black`
# skip .gitignore'd paths, so listing this probe in .gitignore — or naming it
# after a build artefact (`dist`, `build`, `reports`, `tasks`) — would make the
# gate skip it for THAT reason and the probe would then be testing nothing.
#
# Process-unique so concurrent runners (pytest-xdist, or two developers sharing
# a checkout) can't rmtree/mkdir the same path from under each other. Cross-
# visibility between concurrent probes is harmless by construction: each
# violation below is visible to exactly one tool, so another probe's planted
# file reads as clean to this one's gate.
_PROBE_DIR = f"gate_probe_pkg_{os.getpid()}"

# One violation per gate, each chosen to be visible to that gate ALONE — so a
# failure names which gate stopped reaching new code, and no probe free-rides on
# another tool's objection.
_VIOLATIONS = {
    # An unused import: pyflakes F401, and `F` is in `[tool.ruff.lint].select`.
    "ruff": ("unused_import.py", "import os\n"),
    # Valid, lint-clean Python that only the FORMATTER objects to (spacing,
    # quote style).
    "black": ("unformatted.py", "VALUES = { 'a' : 1 }\n"),
    # Declared `int`, returns `str`: rejected under `strict = true`.
    "mypy": ("bad_return.py", 'def answer() -> int:\n    return "not an int"\n'),
}

# Plants (filename, source) into the probe package and returns the written path.
Plant = Callable[[str, str], Path]


def _gate_commands() -> dict[str, list[str]]:
    """The gate argv CI actually runs, keyed by tool, read from the workflow.

    Raises rather than skips when the workflow can't be parsed or a gate is
    missing: a guard that quietly finds nothing to check would report success
    while checking nothing — the same silent-noncoverage bug it exists to catch.
    """
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["python"]["steps"]
    commands: dict[str, list[str]] = {}
    for step in steps:
        argv = shlex.split(step.get("run", ""))
        if argv and argv[0] in _VIOLATIONS:
            commands[argv[0]] = argv
    missing = sorted(_VIOLATIONS.keys() - commands.keys())
    assert not missing, (
        f"no {missing} step found in the python job of {_CI_WORKFLOW.name} — the gate "
        "was renamed or removed, and these probes would silently check nothing"
    )
    return commands


@pytest.fixture
def plant() -> Iterator[Plant]:
    """Plant a package directory at the crawl root and remove it afterwards.

    It must live INSIDE `_REPO_ROOT` to be a faithful probe: every gate resolves
    its targets relative to this directory (`files = ["."]`, and `ruff`/`black`
    are handed `.`), so a probe under `tmp_path` would test nothing about the
    real crawl. Cleanup runs on failure too — a leftover probe would fail every
    later gate run, including the developer's next commit.
    """
    root = _REPO_ROOT / _PROBE_DIR
    shutil.rmtree(root, ignore_errors=True)

    def _plant(filename: str, source: str) -> Path:
        root.mkdir()
        planted = root / filename
        planted.write_text(source, encoding="utf-8")
        return planted

    try:
        yield _plant
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run_gate(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one gate as CI's python job runs it: its argv, its working directory."""
    executable = shutil.which(argv[0])
    if executable is None:
        pytest.skip(f"{argv[0]} not installed")
    return subprocess.run(
        [executable, *argv[1:]],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("tool", sorted(_VIOLATIONS))
def test_gate_reaches_a_new_directory(tool: str, plant: Plant) -> None:
    argv = _gate_commands()[tool]
    planted = plant(*_VIOLATIONS[tool])
    result = _run_gate(argv)
    printed = result.stdout + result.stderr  # black reports to stderr.
    assert result.returncode != 0, (
        f"`{shlex.join(argv)}` (from {_CI_WORKFLOW.name}) returned CLEAN on a planted "
        f"{tool} violation in {_PROBE_DIR}/ — this gate does not reach new "
        f"directories, so newly added code is unchecked:\n{printed}"
    )
    assert planted.name in printed, (
        f"`{shlex.join(argv)}` failed, but on something other than the planted probe "
        f"({planted.name} unnamed) — the failure does not show the gate reached "
        f"it:\n{printed}"
    )
