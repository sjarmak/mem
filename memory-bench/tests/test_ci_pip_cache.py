"""Guards that CI's python job caches pip, keyed on the file pip resolves deps from.

The interesting half is not `cache: pip` but WHICH file the key hashes.
`actions/setup-python@v5` computes it as `hashFiles(cache-dependency-path) ||
hashFiles("**/pyproject.toml")`, and the input itself defaults to
`**/requirements.txt`. This repo has no requirements.txt, so a BARE `cache: pip`
works today only by falling through to that backup glob — an order-dependent
accident: a requirements.txt added anywhere later captures the key, and pyproject
dep changes stop invalidating the cache. So the path is pinned, and these probes
pin the pin.

The expected path is DERIVED from the job's own `working-directory`, never
restated as a literal — a probe hardcoding `memory-bench/pyproject.toml` would
pass after the job moved, testing only that a string equals itself.
"""

from __future__ import annotations

import shlex
from typing import Any

import yaml

from tests.paths import REPO_ROOT

_CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _python_job() -> dict[str, Any]:
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    job: dict[str, Any] = workflow["jobs"]["python"]
    return job


def _setup_python_with() -> dict[str, Any]:
    """The `with:` block of the python job's setup-python step.

    Raises rather than skips when the step is absent: a probe that quietly found
    no step to inspect would report success while checking nothing.
    """
    for step in _python_job()["steps"]:
        if str(step.get("uses", "")).startswith("actions/setup-python@"):
            block: dict[str, Any] = step.get("with", {})
            return block
    raise AssertionError(
        f"no actions/setup-python step in the python job of {_CI_WORKFLOW.name} — "
        "the step was renamed or removed and these probes check nothing"
    )


def test_the_job_installs_deps_with_pip() -> None:
    """The premise the other two probes rest on, read from the real argv.

    `cache: pip` populates and restores pip's download cache and nothing else.
    Swap the install step to `uv sync` and both probes below keep passing while
    the cache config quietly becomes dead weight — still present, still looking
    configured, caching nothing. So the installer is read out of the real argv
    rather than assumed, the way the gate probes next door read theirs.
    """
    runs = [shlex.split(str(step.get("run", ""))) for step in _python_job()["steps"]]
    assert any(argv[:2] == ["pip", "install"] for argv in runs), (
        f"no `pip install` step in the python job of {_CI_WORKFLOW.name}, yet its "
        "setup-python step still declares `cache: pip` and pins a "
        "`cache-dependency-path`. If the installer changed, that config caches "
        "nothing — a different installer never reads pip's download cache"
    )


def test_python_job_caches_pip() -> None:
    """Without this the job re-resolves and rebuilds every dep on every PR."""
    assert _setup_python_with().get("cache") == "pip", (
        f"the python job's setup-python step in {_CI_WORKFLOW.name} declares no "
        "`cache: pip`, so every run reinstalls the whole dev dependency set from "
        "scratch (the typescript job has had `cache: npm` all along)"
    )


def test_cache_is_keyed_on_the_file_pip_resolves_deps_from() -> None:
    """Pin the explicit key path: existent, root-relative, and what pip reads."""
    declared = _setup_python_with().get("cache-dependency-path")

    # `pip install -e "."` runs under the job's working-directory, so the file pip
    # reads is <working-directory>/pyproject.toml — expressed from the repo root,
    # which is what setup-python resolves this input against.
    workdir = _python_job()["defaults"]["run"]["working-directory"]
    assert declared == f"{workdir}/pyproject.toml", (
        f"`cache-dependency-path: {declared}` is not the file this job's `pip "
        f"install` resolves deps from ({workdir}/pyproject.toml). Keying on "
        "anything else means real dependency changes reuse a stale cache. Note "
        "uv.lock is NOT the answer: the job installs with pip, which never reads "
        "it; and `None` means nothing is pinned at all, leaving the key to the "
        "`**/pyproject.toml` backup glob a stray requirements.txt would capture"
    )
    assert (REPO_ROOT / declared).is_file(), (
        f"`cache-dependency-path: {declared}` does not resolve to a file from the "
        f"repo root ({REPO_ROOT}) — setup-python resolves this input from the repo "
        "root, NOT from `defaults.run.working-directory` (which scopes to `run` "
        "steps only), and a path that matches nothing fails CI with 'No file matched'"
    )
