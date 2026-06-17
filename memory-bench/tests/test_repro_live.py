"""Live repro runner (mem-apg.3): apply candidate + gold-test diffs in a cached
worktree and interpret scoped test exits.

No Docker, no npm: git operations run against a real temp repo (the
test_probe_gate idiom); install/setup/test commands are harmless real argvs
(`true`, `python3 -c "sys.exit(n)"`) so exit-code interpretation is exercised
without any JS toolchain.
"""

import subprocess
from pathlib import Path

import pytest

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.harbor.repro_live import (
    MEM_TEST_CONFIG,
    RIG_TEST_CONFIGS,
    LiveReproRunner,
    RigTestConfig,
    WorkspaceTests,
)
from membench.schemas.bundle import BundleEnv, TaskBundle
from tests.helpers import git as _git

IMPL_DIFF = (
    "diff --git a/frontend/src/app.ts b/frontend/src/app.ts\n"
    "--- a/frontend/src/app.ts\n"
    "+++ b/frontend/src/app.ts\n"
    "@@ -1 +1 @@\n"
    "-export const value = 1\n"
    "+export const value = 2\n"
)

GOLD_TEST_DIFF = (
    "diff --git a/frontend/src/app.test.ts b/frontend/src/app.test.ts\n"
    "--- a/frontend/src/app.test.ts\n"
    "+++ b/frontend/src/app.test.ts\n"
    "@@ -1 +1 @@\n"
    "-// expects base\n"
    "+// expects gold\n"
)

# A candidate's own edit to the gold test file -- must be EXCLUDED from the apply
# (it conflicts with GOLD_TEST_DIFF; if it were applied, the gold diff would fail).
CANDIDATE_TEST_DIFF = (
    "diff --git a/frontend/src/app.test.ts b/frontend/src/app.test.ts\n"
    "--- a/frontend/src/app.test.ts\n"
    "+++ b/frontend/src/app.test.ts\n"
    "@@ -1 +1 @@\n"
    "-// expects base\n"
    "+// candidate hacked\n"
)

MANIFEST_DIFF = (
    "diff --git a/frontend/package.json b/frontend/package.json\n"
    "--- a/frontend/package.json\n"
    "+++ b/frontend/package.json\n"
    "@@ -1 +1 @@\n"
    "-{}\n"
    '+{ "x": 1 }\n'
)

BAD_DIFF = (
    "diff --git a/frontend/src/app.ts b/frontend/src/app.ts\n"
    "--- a/frontend/src/app.ts\n"
    "+++ b/frontend/src/app.ts\n"
    "@@ -1 +1 @@\n"
    "-this line never existed\n"
    "+so the apply must fail\n"
)

PASS_ARGV = ("python3", "-c", "import sys; sys.exit(0)")
FAIL_ARGV = ("python3", "-c", "import sys; sys.exit(1)")


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    (repo / "frontend" / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "frontend" / "src" / "app.ts").write_text("export const value = 1\n", encoding="utf-8")
    (repo / "frontend" / "src" / "app.test.ts").write_text("// expects base\n", encoding="utf-8")
    (repo / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def _bundle(clone: Path, *, file_diffs: tuple[tuple[str, str], ...]) -> TaskBundle:
    commit = _git(clone, "rev-parse", "HEAD").strip()
    output = ReplayResult(
        calls=(
            CallReplay(
                index=0,
                tool="Edit",
                path="/orig/frontend/src/app.ts",
                rebased_path="/orig/frontend/src/app.ts",
                outcome=ReplayOutcome.APPLIED,
            ),
        ),
        file_diffs=file_diffs,
        replay_success_rate=1.0,
    )
    return TaskBundle(
        work_id="demo-1",
        rig="demo",
        issue_title="Fix the widget",
        issue_body="",
        trace_ref="/tmp/demo-trace.jsonl",
        output=output,
        env=BundleEnv(repo="demo", base_commit=commit, base_image="node:22-bookworm"),
        loo_excluded_work_ids=("demo-1",),
    )


GOLD = (("frontend/src/app.ts", IMPL_DIFF), ("frontend/src/app.test.ts", GOLD_TEST_DIFF))


def _config(
    test_argv: tuple[str, ...] = PASS_ARGV,
    *,
    install: tuple[tuple[str, ...], ...] = (("true",),),
    setup: tuple[tuple[str, ...], ...] = (),
) -> RigTestConfig:
    return RigTestConfig(
        install=install,
        setup=setup,
        workspaces=(WorkspaceTests(prefix="frontend/", cwd="frontend", argv_prefix=test_argv),),
    )


class RecordingRunner:
    """Delegates to the real subprocess.run, recording every argv."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(tuple(argv))
        return subprocess.run(argv, **kwargs)

    def count(self, argv0: str) -> int:
        return sum(1 for call in self.calls if call and call[0] == argv0)


def _runner(clone: Path, config: RigTestConfig, tmp_path: Path, recording: RecordingRunner):
    return LiveReproRunner(
        rig_repos={"demo": clone},
        configs={"demo": config},
        worktree_root=tmp_path / "worktrees",
        runner=recording,
    )


def test_pass_excludes_candidate_test_edits_and_caches_worktree(
    clone: Path, tmp_path: Path
) -> None:
    (tmp_path / "worktrees").mkdir()
    recording = RecordingRunner()
    bundle = _bundle(clone, file_diffs=GOLD)
    # The candidate edited the gold test file too -- conflicting with the gold test
    # diff. Exclusion is what lets the gold tests apply (and the run pass).
    candidate = {
        "frontend/src/app.ts": IMPL_DIFF,
        "frontend/src/app.test.ts": CANDIDATE_TEST_DIFF,
    }
    with _runner(clone, _config(), tmp_path, recording) as runner:
        first = runner.run(bundle=bundle, candidate_diff=candidate)
        second = runner.run(bundle=bundle, candidate_diff=candidate)

    assert first.passed and first.error is None
    assert second.passed
    # One install across both runs: the worktree is cached, the second run resets it.
    assert recording.count("true") == 1
    # Test argv received the gold test file RELATIVE to the workspace cwd.
    test_calls = [c for c in recording.calls if c[:1] == ("python3",)]
    assert all(call[-1] == "src/app.test.ts" for call in test_calls)
    assert len(test_calls) == 2


def test_failing_tests_are_a_fail_not_an_error(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, file_diffs=GOLD)
    with _runner(clone, _config(FAIL_ARGV), tmp_path, RecordingRunner()) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={"frontend/src/app.ts": IMPL_DIFF})
    assert not outcome.passed
    assert outcome.error is None


def test_setup_failure_is_a_fail_not_an_error(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, file_diffs=GOLD)
    config = _config(setup=(FAIL_ARGV,))
    with _runner(clone, config, tmp_path, RecordingRunner()) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={"frontend/src/app.ts": IMPL_DIFF})
    assert not outcome.passed
    assert outcome.error is None


def test_unapplyable_candidate_diff_is_an_error(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, file_diffs=GOLD)
    with _runner(clone, _config(), tmp_path, RecordingRunner()) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={"frontend/src/app.ts": BAD_DIFF})
    assert not outcome.passed
    assert outcome.error is not None and "candidate diff" in outcome.error


def test_gold_diff_without_tests_is_an_error(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, file_diffs=(("frontend/src/app.ts", IMPL_DIFF),))
    with _runner(clone, _config(), tmp_path, RecordingRunner()) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={})
    assert outcome.error is not None and "no test files" in outcome.error


def test_gold_test_outside_known_workspaces_is_an_error(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(
        clone,
        file_diffs=(("scripts/check.test.ts", GOLD_TEST_DIFF),),
    )
    with _runner(clone, _config(), tmp_path, RecordingRunner()) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={})
    assert outcome.error is not None and "outside known workspaces" in outcome.error


def test_unknown_rig_and_missing_clone_are_errors(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, file_diffs=GOLD)
    no_config = LiveReproRunner(rig_repos={"demo": clone}, configs={})
    assert "no test config" in (no_config.run(bundle=bundle, candidate_diff={}).error or "")
    no_clone = LiveReproRunner(rig_repos={}, configs={"demo": _config()})
    assert "no local clone" in (no_clone.run(bundle=bundle, candidate_diff={}).error or "")


def test_install_failure_is_an_error_and_never_caches(clone: Path, tmp_path: Path) -> None:
    recording = RecordingRunner()
    bundle = _bundle(clone, file_diffs=GOLD)
    config = _config(install=(("false",),))
    with _runner(clone, config, tmp_path, recording) as runner:
        first = runner.run(bundle=bundle, candidate_diff={})
        runner.run(bundle=bundle, candidate_diff={})
    assert "install" in (first.error or "")
    # The failed worktree was removed, so the second run re-attempts the install.
    assert recording.count("false") == 2
    # No worktree survived on the clone.
    assert "repro-" not in _git(clone, "worktree", "list")


def test_manifest_change_reinstalls_within_one_run(clone: Path, tmp_path: Path) -> None:
    recording = RecordingRunner()
    bundle = _bundle(clone, file_diffs=GOLD)
    candidate = {
        "frontend/src/app.ts": IMPL_DIFF,
        "frontend/package.json": MANIFEST_DIFF,
    }
    with _runner(clone, _config(), tmp_path, recording) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff=candidate)
    assert outcome.passed
    # Once at worktree creation + once after the manifest-touching apply.
    assert recording.count("true") == 2


def test_blank_manifest_diff_never_triggers_reinstall(clone: Path, tmp_path: Path) -> None:
    """The reinstall trigger covers exactly the APPLIED paths: a blank-diff
    manifest entry is never applied, so it must not cost a cold install."""
    recording = RecordingRunner()
    bundle = _bundle(clone, file_diffs=GOLD)
    candidate = {
        "frontend/src/app.ts": IMPL_DIFF,
        "frontend/package.json": "   ",
    }
    with _runner(clone, _config(), tmp_path, recording) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff=candidate)
    assert outcome.passed
    assert recording.count("true") == 1


def test_per_file_partial_credit_no_short_circuit(clone: Path, tmp_path: Path) -> None:
    """S1 (mem-g6a): two gold-test files, one passing and one failing -> the binary
    anchor FAILS but the per-file counts record 1/2, and BOTH files ran (no
    short-circuit on the first failure)."""
    # A second gold test file in the same workspace; the impl diff is shared.
    second_test = (
        "diff --git a/frontend/src/two.test.ts b/frontend/src/two.test.ts\n"
        "--- a/frontend/src/two.test.ts\n"
        "+++ b/frontend/src/two.test.ts\n"
        "@@ -1 +1 @@\n-// base\n+// gold\n"
    )
    (clone / "frontend" / "src" / "two.test.ts").write_text("// base\n", encoding="utf-8")
    _git(clone, "add", ".")
    _git(clone, "commit", "-qm", "two")
    bundle = _bundle(
        clone,
        file_diffs=(*GOLD, ("frontend/src/two.test.ts", second_test)),
    )
    # First test file exits 0, second exits 1: a per-file runner keyed on the path's
    # last segment so exactly one of the two files fails.
    recording = RecordingRunner()
    config = RigTestConfig(
        install=(("true",),),
        setup=(),
        workspaces=(
            WorkspaceTests(
                prefix="frontend/",
                cwd="frontend",
                argv_prefix=(
                    "python3",
                    "-c",
                    "import sys; sys.exit(0 if sys.argv[1].endswith('app.test.ts') else 1)",
                ),
            ),
        ),
    )
    with _runner(clone, config, tmp_path, recording) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={"frontend/src/app.ts": IMPL_DIFF})
    assert not outcome.passed  # binary anchor still all-or-nothing
    assert outcome.error is None
    assert outcome.tests_passed == 1 and outcome.tests_total == 2
    assert outcome.test_ratio == 0.5
    # No short-circuit: both gold-test files were executed.
    test_calls = [c for c in recording.calls if c[:1] == ("python3",)]
    assert len(test_calls) == 2


def test_files_across_two_workspaces_each_run_once(clone: Path, tmp_path: Path) -> None:
    """A gold diff spanning two workspaces: each file is mapped to exactly one
    workspace and run exactly once, so tests_total counts both without double-counting
    across prefixes."""
    backend_test = (
        "diff --git a/backend/api.test.ts b/backend/api.test.ts\n"
        "--- a/backend/api.test.ts\n"
        "+++ b/backend/api.test.ts\n"
        "@@ -1 +1 @@\n-// base\n+// gold\n"
    )
    (clone / "backend").mkdir()
    (clone / "backend" / "api.test.ts").write_text("// base\n", encoding="utf-8")
    _git(clone, "add", ".")
    _git(clone, "commit", "-qm", "backend")
    bundle = _bundle(clone, file_diffs=(*GOLD, ("backend/api.test.ts", backend_test)))
    recording = RecordingRunner()
    config = RigTestConfig(
        install=(("true",),),
        setup=(),
        workspaces=(
            WorkspaceTests(prefix="frontend/", cwd="frontend", argv_prefix=PASS_ARGV),
            WorkspaceTests(prefix="backend/", cwd="backend", argv_prefix=PASS_ARGV),
        ),
    )
    with _runner(clone, config, tmp_path, recording) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={"frontend/src/app.ts": IMPL_DIFF})
    assert outcome.passed
    assert outcome.tests_passed == 2 and outcome.tests_total == 2
    # Each gold test file ran exactly once (two files -> two test invocations).
    assert recording.count("python3") == 2


def test_all_files_pass_anchor_and_ratio_agree(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, file_diffs=GOLD)
    with _runner(clone, _config(), tmp_path, RecordingRunner()) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={"frontend/src/app.ts": IMPL_DIFF})
    assert outcome.passed and outcome.test_ratio == 1.0
    assert outcome.tests_passed == 1 and outcome.tests_total == 1


def test_setup_failure_credits_zero_files(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, file_diffs=GOLD)
    with _runner(clone, _config(setup=(FAIL_ARGV,)), tmp_path, RecordingRunner()) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={"frontend/src/app.ts": IMPL_DIFF})
    assert not outcome.passed and outcome.error is None
    assert outcome.tests_passed == 0 and outcome.tests_total == 1 and outcome.test_ratio == 0.0


def test_mem_rig_is_registered() -> None:
    """The `mem` rig is wired into the default config map, so the runner no longer
    hard-errors `no test config for rig 'mem'` -- the mem-us6j oracle-soundness
    blocker (mem-qarg wave-2 triage: "infra gap, not a broken oracle")."""
    assert RIG_TEST_CONFIGS["mem"] is MEM_TEST_CONFIG


def test_mem_rig_config_routes_ts_and_python_suites(tmp_path: Path) -> None:
    """The real MEM_TEST_CONFIG routes a root-TS gold test to `npx vitest run` from the
    worktree root and a memory-bench gold test to `python -m pytest <path>` from
    memory-bench/ -- the two surfaces the mem rig actually carries. npm/pip/test argvs
    are stubbed to exit 0 (no JS/Python toolchain needed); git runs for real so the
    worktree + gold-diff apply are exercised end-to-end."""
    repo = tmp_path / "clone"
    (repo / "tests").mkdir(parents=True)
    (repo / "memory-bench" / "tests").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "tests" / "cli.test.ts").write_text("// base\n", encoding="utf-8")
    (repo / "memory-bench" / "tests" / "test_x.py").write_text("# base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    commit = _git(repo, "rev-parse", "HEAD").strip()

    ts_gold = (
        "diff --git a/tests/cli.test.ts b/tests/cli.test.ts\n"
        "--- a/tests/cli.test.ts\n+++ b/tests/cli.test.ts\n"
        "@@ -1 +1 @@\n-// base\n+// gold\n"
    )
    py_gold = (
        "diff --git a/memory-bench/tests/test_x.py b/memory-bench/tests/test_x.py\n"
        "--- a/memory-bench/tests/test_x.py\n+++ b/memory-bench/tests/test_x.py\n"
        "@@ -1 +1 @@\n-# base\n+# gold\n"
    )
    bundle = TaskBundle(
        work_id="mem-us6j",
        rig="mem",
        issue_title="Fix the thing",
        issue_body="",
        trace_ref="/tmp/mem-trace.jsonl",
        output=ReplayResult(
            calls=(),
            file_diffs=(
                ("tests/cli.test.ts", ts_gold),
                ("memory-bench/tests/test_x.py", py_gold),
            ),
            replay_success_rate=1.0,
        ),
        env=BundleEnv(repo="mem", base_commit=commit, base_image="node:22-bookworm"),
        loo_excluded_work_ids=("mem-us6j",),
    )

    recorded: list[tuple[Path, tuple[str, ...]]] = []

    def runner(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv and argv[0] == "git":
            return subprocess.run(argv, **kwargs)
        recorded.append((Path(kwargs["cwd"]), tuple(argv)))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    with LiveReproRunner(
        rig_repos={"mem": repo},
        configs={"mem": MEM_TEST_CONFIG},
        worktree_root=tmp_path / "worktrees",
        runner=runner,
    ) as r:
        outcome = r.run(bundle=bundle, candidate_diff={})

    assert outcome.passed
    assert outcome.tests_passed == 2 and outcome.tests_total == 2

    vitest = [(cwd, argv) for cwd, argv in recorded if "vitest" in argv]
    pytest_runs = [(cwd, argv) for cwd, argv in recorded if argv[:3] == ("python3", "-m", "pytest")]
    assert len(vitest) == 1
    cwd, argv = vitest[0]
    # Root TS suite: stripped bare filename, run from the worktree root (repro-*).
    assert argv == ("npx", "vitest", "run", "cli.test.ts")
    assert cwd.name.startswith("repro-")
    assert len(pytest_runs) == 1
    cwd, argv = pytest_runs[0]
    # Python suite: path relative to memory-bench/, run from memory-bench/.
    assert argv == ("python3", "-m", "pytest", "tests/test_x.py")
    assert cwd.name == "memory-bench"
    # Install seeded both toolchains once.
    argvs = [a for _, a in recorded]
    assert ("npm", "ci", "--no-audit", "--no-fund") in argvs
    assert ("python3", "-m", "pip", "install", "-e", "memory-bench[dev]") in argvs


def test_close_removes_cached_worktrees(clone: Path, tmp_path: Path) -> None:
    bundle = _bundle(clone, file_diffs=GOLD)
    runner = _runner(clone, _config(), tmp_path, RecordingRunner())
    assert runner.run(bundle=bundle, candidate_diff={}).passed
    assert "repro-" in _git(clone, "worktree", "list")
    runner.close()
    assert "repro-" not in _git(clone, "worktree", "list")
