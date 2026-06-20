"""Fail-to-pass curation (mem-bxhh.1): productionize the codeprobe ftp probe.

Pure logic is unit-tested here against real-shaped pytest junitxml; the container
orchestration is exercised with an injected runner that writes a canned junit
file (the test_repro_live idiom -- no Docker, real temp git repo for the worktree
plumbing).

The three bugs the productionization must NOT reintroduce are each pinned by a
test: (1) multi-file pytest paths must be separate argv elements, never a comma
or shell string; (2) the parent/landing diff is a set operation, not `comm`; (3)
a collection ERROR at parent counts as not-pass and classifies feature-presence,
distinct from a behavioral red->green.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from membench.cli import _resolve_landing_commits
from membench.harbor.ftp_curate import (
    FLAKY_TEST_SUBSTRINGS,
    CommitFtp,
    classify_ftp,
    container_command,
    curate_commit,
    curate_rig,
    drop_flaky,
    load_linked_commits,
    parse_junit_outcomes,
    pytest_argv,
    select_pytest_modules,
    single_parent,
)
from tests.helpers import git as _git

# --- junit parsing ----------------------------------------------------------------

PASS_FAIL_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3" failures="1" errors="0" skipped="1">
  <testcase classname="tests.test_roll" name="test_sum"
    file="tests/test_roll.py" line="3" time="0.01"/>
  <testcase classname="tests.test_roll" name="test_delta"
    file="tests/test_roll.py" line="7" time="0.01">
    <failure message="assert 1 == 2">E assert 1 == 2</failure>
  </testcase>
  <testcase classname="tests.test_roll" name="test_skip"
    file="tests/test_roll.py" line="9" time="0.0">
    <skipped message="needs net"/>
  </testcase>
</testsuite></testsuites>
"""

COLLECTION_ERROR_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1" failures="0" errors="1" skipped="0">
  <testcase classname="tests.test_new" name="tests/test_new.py" time="0.0">
    <error message="collection failure">ImportError: cannot import name 'feature'</error>
  </testcase>
</testsuite></testsuites>
"""


def test_parse_junit_outcomes_distinguishes_pass_fail_error_skip() -> None:
    outcomes = parse_junit_outcomes(PASS_FAIL_JUNIT)
    assert outcomes["tests/test_roll.py::test_sum"] == "passed"
    assert outcomes["tests/test_roll.py::test_delta"] == "failed"
    assert outcomes["tests/test_roll.py::test_skip"] == "skipped"


def test_parse_junit_outcomes_marks_collection_error() -> None:
    outcomes = parse_junit_outcomes(COLLECTION_ERROR_JUNIT)
    # The file-level collection error is recorded as `error`; no per-test nodeid
    # for the absent feature exists -- which is exactly what makes its landing
    # counterpart classify feature-presence.
    assert set(outcomes.values()) == {"error"}


def test_parse_junit_outcomes_rejects_dtd() -> None:
    # The junit is written by untrusted in-container code; a DTD/entity decl is
    # refused rather than parsed (entity-injection / billion-laughs guard).
    hostile = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e "x">]>'
        '<testsuites><testsuite><testcase name="&e;"/></testsuite></testsuites>'
    )
    with pytest.raises(RuntimeError, match="DTD/entity"):
        parse_junit_outcomes(hostile)


def test_load_linked_commits_dedupes_and_filters_linkage() -> None:
    payload = {
        "commits": [
            {"work_id": "w1", "commit_sha": "sha_a", "linkage": "canonical"},
            {"work_id": "w2", "commit_sha": "sha_a", "linkage": "canonical"},  # dup sha
            {"work_id": "w3", "commit_sha": "sha_b", "linkage": "unique"},  # filtered out
            "not-a-mapping",  # skipped by the isinstance guard
            {"work_id": "w4", "linkage": "canonical"},  # no commit_sha -> skipped
        ]
    }
    # Default keeps canonical only, de-duped, order-preserving.
    assert load_linked_commits(payload) == ["sha_a"]
    # Widening the linkage set picks up the unique link too.
    assert load_linked_commits(payload, linkages=frozenset({"canonical", "unique"})) == [
        "sha_a",
        "sha_b",
    ]


# --- fail-to-pass classification (bug 2 + bug 3) ----------------------------------


def test_classify_ftp_behavioral_when_test_ran_and_failed_at_parent() -> None:
    parent = {"tests/test_roll.py::test_delta": "failed"}
    landing = {"tests/test_roll.py::test_delta": "passed"}
    result = classify_ftp(parent, landing)
    assert result.ftp_tests == ("tests/test_roll.py::test_delta",)
    assert result.behavioral == ("tests/test_roll.py::test_delta",)
    assert result.feature_presence == ()
    assert result.type == "behavioral"


def test_classify_ftp_feature_presence_when_absent_at_parent() -> None:
    # Parent only has the file-level collection error; the individual test nodeid
    # is absent -> feature-presence, not behavioral.
    parent = {"tests/test_new.py::tests/test_new.py": "error"}
    landing = {"tests/test_new.py::test_added": "passed"}
    result = classify_ftp(parent, landing)
    assert result.ftp_tests == ("tests/test_new.py::test_added",)
    assert result.feature_presence == ("tests/test_new.py::test_added",)
    assert result.behavioral == ()
    assert result.type == "feature-presence"


def test_classify_ftp_excludes_tests_already_passing_at_parent() -> None:
    parent = {"a::t": "passed"}
    landing = {"a::t": "passed"}
    result = classify_ftp(parent, landing)
    assert result.ftp_tests == ()
    # No ftp surfaced even though tests ran -> type is 'none', not a misleading
    # 'feature-presence'.
    assert result.type == "none"


def test_classify_ftp_prefers_behavioral_type_when_mixed() -> None:
    parent = {"a::ran": "failed"}
    landing = {"a::ran": "passed", "b::new": "passed"}
    result = classify_ftp(parent, landing)
    assert sorted(result.ftp_tests) == ["a::ran", "b::new"]
    assert result.behavioral == ("a::ran",)
    assert result.feature_presence == ("b::new",)
    assert result.type == "behavioral"  # prefer behavioral when any is present


# --- pytest invocation (bug 1) ----------------------------------------------------


def test_pytest_argv_passes_files_as_separate_elements_not_comma() -> None:
    argv = pytest_argv(["tests/a.py", "tests/b.py"], "/app/.ftp-junit.xml")
    assert "tests/a.py" in argv
    assert "tests/b.py" in argv
    # The original bug: comma-joined multi-file paths. Assert no element fuses them.
    assert not any("," in part for part in argv)
    assert "--junitxml=/app/.ftp-junit.xml" in argv


def test_container_command_installs_pytest_and_keeps_files_separate() -> None:
    cmd = container_command(["tests/a.py", "tests/b.py"], "/app/.ftp-junit.xml")
    # pytest is a test-only dep -> the install must name it, else `pytest: command
    # not found` (the bug that produced no junit on the first live run).
    assert "pip install -e . pytest -q" in cmd
    assert " && pytest tests/a.py tests/b.py " in cmd
    # No comma fuses the two files even after the shell-join.
    assert "tests/a.py,tests/b.py" not in cmd


def test_container_command_chowns_after_pytest_so_host_can_clean() -> None:
    cmd = container_command(["tests/a.py"], "/app/.ftp-junit.xml", chown_to="1000:1000")
    # chown is the FINAL step, joined with `;` not `&&`, so a failing pytest run
    # (nonzero exit -- the normal case) still restores host ownership of the
    # root-written egg-info, keeping the worktree removable. --no-dereference
    # stops a planted symlink redirecting the chown onto a host file.
    assert cmd.rstrip().endswith("; chown -R --no-dereference 1000:1000 /app")
    assert " ; chown" in cmd and "&& chown" not in cmd


# --- guards -----------------------------------------------------------------------


def test_single_parent_accepts_one_parent() -> None:
    assert single_parent(["deadbeef"]) == "deadbeef"


def test_single_parent_rejects_merge_commit() -> None:
    with pytest.raises(ValueError, match="2 parents"):
        single_parent(["p1", "p2"])


def test_single_parent_rejects_root_commit() -> None:
    with pytest.raises(ValueError, match="0 parents"):
        single_parent([])


def test_select_pytest_modules_excludes_fixtures_conftest_and_non_modules() -> None:
    # The selector must be stricter than a "under tests/" match: passing
    # tests/conftest.py or a *.json fixture to pytest is a collection error that
    # derails the whole run (the 6f0c65 false 0-ftp bug). Only test_*.py /
    # *_test.py modules are runnable; conftest/fixtures are auto-discovered.
    paths = [
        "tests/test_executor.py",
        "tests/conftest.py",
        "tests/fixtures/claude_normal.json",
        "pkg/foo_test.py",
        "src/executor.py",
        "docs/adapters.md",
    ]
    assert select_pytest_modules(paths) == ["tests/test_executor.py", "pkg/foo_test.py"]


def test_drop_flaky_removes_known_env_flaky_test() -> None:
    outcomes = {
        "tests/test_x.py::test_validate_ready": "failed",
        "tests/test_x.py::test_real": "passed",
    }
    filtered = drop_flaky(outcomes, FLAKY_TEST_SUBSTRINGS)
    assert "tests/test_x.py::test_validate_ready" not in filtered
    assert "tests/test_x.py::test_real" in filtered


# --- orchestration (injected runner; real temp repo for git/worktree plumbing) ----


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _testcase(name: str, child: str = "") -> str:
    inner = f"<{child}/>" if child else ""
    return (
        f'<testcase classname="tests.test_c" name="{name}" '
        f'file="tests/test_c.py" line="1" time="0.0">{inner}</testcase>'
    )


def _suite(*cases: str) -> str:
    return f'<testsuites><testsuite name="pytest">{"".join(cases)}</testsuite></testsuites>'


def _leg_runner(parent_junit: str, landing_junit: str, overlay_seen: dict[str, bool]):  # type: ignore[no-untyped-def]
    """A fake `Runner`: real git passes through; the pytest/docker exec writes the
    per-leg canned junit (keyed by the -parent/-landing worktree suffix). It also
    records that the parent leg, when it runs, already sees the overlaid gold test
    file -- proving `_overlay_paths` ran before the parent pytest."""

    def run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[0] == "git":
            return subprocess.run(argv, capture_output=True, text=True, check=False)
        mount = next(a for a in argv if ":/app" in a and a != "-v")
        host_dir = Path(mount.split(":/app")[0])
        if host_dir.name.endswith("-parent"):
            overlay_seen["parent_has_gold_test"] = (host_dir / "tests" / "test_c.py").exists()
            (host_dir / ".ftp-junit.xml").write_text(parent_junit)
        else:
            (host_dir / ".ftp-junit.xml").write_text(landing_junit)
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def test_curate_commit_behavioral_end_to_end(tmp_path: Path) -> None:
    """A landing commit that MODIFIES a test + source: the gold test runs against
    the parent source and fails (red->green) -> behavioral. The parent leg must
    see the overlaid landing test file, not the parent's own old one."""
    repo = _init_repo(tmp_path)
    (repo / "feature.py").write_text("def f():\n    return 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_c.py").write_text(
        "from feature import f\n\ndef test_a():\n    assert f()\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "parent (codeprobe-aaaa)")

    (repo / "feature.py").write_text("def f():\n    return 2\n")
    (repo / "tests" / "test_c.py").write_text(
        "from feature import f\n\n"
        "def test_a():\n    assert f()\n\n"
        "def test_b():\n    assert f() == 2\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "behavioral change (codeprobe-bbbb)")
    landing_sha = _git(repo, "rev-parse", "HEAD").strip()

    # Gold test on parent source: test_a passes, the new test_b FAILS (f()==1).
    parent_junit = _suite(_testcase("test_a"), _testcase("test_b", 'failure message="x"'))
    landing_junit = _suite(_testcase("test_a"), _testcase("test_b"))
    overlay: dict[str, bool] = {}

    result = curate_commit(
        rig="codeprobe",
        landing_sha=landing_sha,
        clone=repo,
        runner=_leg_runner(parent_junit, landing_junit, overlay),
        worktree_root=tmp_path,
    )
    assert isinstance(result, CommitFtp)
    assert result.ftp_tests == ("tests/test_c.py::test_b",)
    assert result.behavioral == ("tests/test_c.py::test_b",)
    assert result.type == "behavioral"
    # The overlay (gold test diff) reached the parent worktree before its pytest.
    assert overlay["parent_has_gold_test"] is True


def test_curate_commit_feature_presence_end_to_end(tmp_path: Path) -> None:
    """A landing commit whose gold test imports an absent feature collection-errors
    at the parent (no per-test nodeid) -> feature-presence."""
    repo = _init_repo(tmp_path)
    (repo / "feature.py").write_text("def f():\n    return 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_c.py").write_text("def test_a():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "parent (codeprobe-aaaa)")

    (repo / "feature.py").write_text("def f():\n    return 1\n\ndef g():\n    return 2\n")
    (repo / "tests" / "test_c.py").write_text(
        "from feature import g\n\ndef test_g():\n    assert g() == 2\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "new feature+test (codeprobe-bbbb)")
    landing_sha = _git(repo, "rev-parse", "HEAD").strip()

    # Parent source lacks g -> the overlaid gold test collection-errors (file-level).
    parent_junit = _suite(
        '<testcase classname="tests.test_c" name="tests/test_c.py" time="0.0">'
        '<error message="ImportError"/></testcase>'
    )
    landing_junit = _suite(_testcase("test_g"))
    overlay: dict[str, bool] = {}

    result = curate_commit(
        rig="codeprobe",
        landing_sha=landing_sha,
        clone=repo,
        runner=_leg_runner(parent_junit, landing_junit, overlay),
        worktree_root=tmp_path,
    )
    assert isinstance(result, CommitFtp)
    assert result.feature_presence == ("tests/test_c.py::test_g",)
    assert result.behavioral == ()
    assert result.type == "feature-presence"


def test_curate_commit_skips_when_no_test_files_touched(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "a.py").write_text("# v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "parent")
    (repo / "a.py").write_text("# v2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "impl only (codeprobe-cccc)")
    landing_sha = _git(repo, "rev-parse", "HEAD").strip()

    def fake_runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[0] == "git":
            return subprocess.run(argv, capture_output=True, text=True, check=False)
        raise AssertionError("must not run pytest when no test files are touched")

    result = curate_commit(
        rig="codeprobe",
        landing_sha=landing_sha,
        clone=repo,
        base_image="python:3.11-bookworm",
        runner=fake_runner,
        worktree_root=tmp_path,
    )
    assert result is None


def test_curate_rig_isolates_per_commit_failure_and_keeps_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A single corpus-invalid commit (gold-test path absent at landing, a
    # non-installable parent) raises RuntimeError inside curate_commit; the rig
    # run must isolate it and still return the other commits' curated oracles --
    # the JSON is written only after the loop, so an abort would discard them all.
    good = CommitFtp(
        commit="b" * 40,
        parent="a" * 40,
        ftp_tests=("tests/test_x.py::test_a",),
        behavioral=("tests/test_x.py::test_a",),
        feature_presence=(),
        type="behavioral",
    )

    def fake_curate_commit(rig, landing_sha, clone, **kwargs):  # type: ignore[no-untyped-def]
        if landing_sha == "bad":
            raise RuntimeError("pathspec 'tests/test_gone.py' did not match any file(s)")
        return good

    monkeypatch.setattr("membench.harbor.ftp_curate.curate_commit", fake_curate_commit)

    logs: list[str] = []
    results = curate_rig(
        "scix_experiments",
        ["bad", "b" * 40],
        Path("/unused"),
        log=logs.append,
    )

    assert results == [good]
    assert any("uncurate-able" in line for line in logs)
    assert "1 errored" in logs[-1]


# --- CLI commit resolution (precedence + envelope unwrap) --------------------------


def _ftp_args(**overrides: object) -> SimpleNamespace:
    base = {
        "rig": "codeprobe",
        "commits": None,
        "linked_json": None,
        "store": None,
        "linkages": "canonical",
        "mem_bin": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_landing_commits_commits_override_wins() -> None:
    # --commits is the debug override: split on comma, no derivation/shell-out.
    args = _ftp_args(commits="sha1, sha2 ,sha3")
    assert _resolve_landing_commits(args) == ["sha1", "sha2", "sha3"]


def test_resolve_landing_commits_from_linked_json_envelope(tmp_path: Path) -> None:
    # A `mem link-outcomes --json` envelope file: the {ok,data,errors} wrapper is
    # unwrapped to `.data`, then canonical SHAs are taken.
    envelope = {
        "ok": True,
        "data": {
            "rig": "codeprobe",
            "commits": [
                {"work_id": "w1", "commit_sha": "abc", "linkage": "canonical"},
                {"work_id": "w2", "commit_sha": "def", "linkage": "unique"},
            ],
        },
    }
    path = tmp_path / "linked.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    args = _ftp_args(linked_json=str(path))
    assert _resolve_landing_commits(args) == ["abc"]
