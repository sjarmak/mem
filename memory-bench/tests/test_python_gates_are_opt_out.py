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

Scope of that pin: `ci.yml` only. `.pre-commit-config.yaml` restates the same
argv and is NOT read here, so narrowing the local gate alone leaves these probes
green. That gap is real and tracked in mem-8o4li; the durable fix is one gate
site rather than a second probe.

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
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from tests.paths import REPO, REPO_ROOT

# `REPO` is the memory-bench root: it holds `pyproject.toml` (so the gates read
# the real config) and is the `working-directory:` of CI's python job. `REPO_ROOT`
# is the outer git root, one level up, where the workflow lives.
_CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Planted at the crawl root, where only an opt-out gate reaches it.
#
# The name is deliberately mundane, and must stay un-ignored: `ruff` and `black`
# skip .gitignore'd paths, so listing this probe in .gitignore — or naming it
# after a build artefact (`dist`, `build`, `reports`, `tasks`) — would make the
# gate skip it for THAT reason and the probe would then be testing nothing.
#
# Process-unique so two runs sharing a checkout can't rmtree/mkdir the same path
# from under each other.
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
def probe_root() -> Iterator[Path]:
    """An empty package directory at the crawl root, removed afterwards.

    It must live INSIDE `REPO` to be a faithful probe: every gate resolves its
    targets relative to that directory (`files = ["."]`, and `ruff`/`black` are
    handed `.`), so a probe under `tmp_path` would test nothing about the real
    crawl. Cleanup runs on failure too — a leftover probe would fail every later
    gate run, including the developer's next commit.
    """
    root = REPO / _PROBE_DIR
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
    return subprocess.run(
        [executable, *argv[1:]],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("tool", sorted(_VIOLATIONS))
def test_gate_reaches_a_new_directory(tool: str, probe_root: Path) -> None:
    argv = _gate_commands()[tool]
    filename, source = _VIOLATIONS[tool]
    planted = probe_root / filename
    planted.write_text(source, encoding="utf-8")
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
