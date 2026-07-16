"""Compile-time (`mypy --strict`) probes pinning WHY the `mem retrieve` envelope
is a `TypedDict`, not `dict[str, Any]`.

The `ours` arm reads the `mem retrieve --json` payload through a `RetrieveRunner`
(`OursQuery -> RetrieveEnvelope`). Before this bead the runner returned
`dict[str, Any]`, so every downstream access (`result["items"]`,
`item["work_id"]`) typed as `Any` and `mypy --strict` did NO structural check on
the envelope: a typo'd or CLI-renamed key compiled clean and only failed at
RUNTIME — inside a paid measurement. These probes drive the real `mypy` over tiny
modules that import the REAL `RetrieveEnvelope`/`RetrieveRunner`, so the guarantee
regresses loudly if the types are ever widened back to `Any`:

* correct key -> clean;
* typo'd key -> a `typeddict-item` error naming the key;
* the pre-bead `Callable[[OursQuery], dict[str, Any]]` runner shape -> STILL clean
  on the identical bogus access (the contrast that pins why the gap existed).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# The memory-bench root (holds `membench/` + `pyproject.toml`); put it on MYPYPATH
# so the probe modules resolve the real package, and run mypy from here so it reads
# the same `[tool.mypy]` config the CI gate uses.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# A runner whose result reads the CORRECT key: the `work_id` a `RetrievedItem`
# actually declares. Clean under `--strict`.
_CORRECT = """\
from membench.memory_systems.ours_system import OursQuery, RetrieveRunner


def consume(run: RetrieveRunner) -> str:
    result = run(OursQuery(work_id="B", scope="cross_rig", store_path="/tmp/s.db"))
    return result["items"][0]["work_id"]
"""

# The same access with a typo'd/renamed key (`work-id`). Under the TypedDict this
# is a `typeddict-item` error that NAMES the key — the runtime `KeyError` moved to
# compile time.
_TYPO = """\
from membench.memory_systems.ours_system import OursQuery, RetrieveRunner


def consume(run: RetrieveRunner) -> str:
    result = run(OursQuery(work_id="B", scope="cross_rig", store_path="/tmp/s.db"))
    return result["items"][0]["work-id"]
"""

# The runner shape BEFORE this bead: `OursQuery -> dict[str, Any]`. The identical
# bogus-key access types as `Any` all the way down, so `--strict` reports NOTHING.
# This is the contrast that pins why the gap existed — reconstruct the old shape and
# watch the typo sail through.
_OLD_DICT_ANY = """\
from collections.abc import Callable
from typing import Any

from membench.memory_systems.ours_system import OursQuery

OldRunner = Callable[[OursQuery], dict[str, Any]]


def consume(run: OldRunner) -> Any:
    result = run(OursQuery(work_id="B", scope="cross_rig", store_path="/tmp/s.db"))
    return result["items"][0]["work-id"]
"""


@pytest.fixture(scope="module")
def mypy_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One mypy cache shared across the three probes. The costly work is analysing
    the real `membench` import graph the probes pull in; with a per-test cache each
    probe re-does it cold (~3s each). Sharing one cache lets the first probe warm it
    and the rest reuse it (~0.4s each). Isolation is unaffected — each probe module
    still lives under its own per-test `tmp_path`, so only the analysis cache (keyed
    on the byte-identical `membench` sources) is shared, never a probe."""
    return tmp_path_factory.mktemp("mypy_cache")


def _run_mypy(tmp_path: Path, cache_dir: Path, source: str) -> subprocess.CompletedProcess[str]:
    """Type-check one probe module with the project's mypy under `--strict`.

    `--follow-imports=silent` resolves the real `membench` types (so the probe is
    honest) while suppressing errors inside the followed package — the only error
    surfaced is the probe's own. The probe lives under the per-test `tmp_path`;
    `cache_dir` is the module-shared mypy cache (see `mypy_cache`)."""
    mypy = shutil.which("mypy")
    if mypy is None:
        pytest.skip("mypy not installed")
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")
    env = {**os.environ, "MYPYPATH": str(_REPO_ROOT)}
    return subprocess.run(
        [
            mypy,
            "--strict",
            "--follow-imports=silent",
            "--no-error-summary",
            "--cache-dir",
            str(cache_dir),
            str(probe),
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_correct_key_type_checks_clean(tmp_path: Path, mypy_cache: Path) -> None:
    result = _run_mypy(tmp_path, mypy_cache, _CORRECT)
    assert result.returncode == 0, f"expected clean, got:\n{result.stdout}{result.stderr}"


def test_typoed_key_is_a_typeddict_error_naming_the_key(tmp_path: Path, mypy_cache: Path) -> None:
    result = _run_mypy(tmp_path, mypy_cache, _TYPO)
    assert result.returncode != 0, "typo'd key should fail --strict but passed"
    out = result.stdout + result.stderr
    # The error must name the key and the TypedDict, not just fail vaguely.
    assert "work-id" in out, f"error should name the typo'd key:\n{out}"
    assert "has no key" in out and "RetrievedItem" in out, out
    assert "typeddict-item" in out, out


def test_old_dict_any_runner_stays_green_on_the_same_bogus_access(
    tmp_path: Path, mypy_cache: Path
) -> None:
    # The regression pin: the pre-bead `dict[str, Any]` runner accepts the exact
    # typo the TypedDict rejects. If this ever starts failing, the envelope is no
    # longer `Any` at the seam by accident — but that is the TypedDict's job now.
    result = _run_mypy(tmp_path, mypy_cache, _OLD_DICT_ANY)
    assert result.returncode == 0, (
        "dict[str, Any] should type the bogus access as Any and stay green, "
        f"proving why --strict missed it before the TypedDict:\n{result.stdout}{result.stderr}"
    )
