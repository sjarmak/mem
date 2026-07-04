"""FtpReproRunner (mem-bxhh.3.3): score a candidate against a bundle's curated ftp
node-id subset, container pytest, fail-to-pass shape.

No Docker: git runs against a real temp repo (the test_ftp_curate / test_repro_live
idiom); the container exec is an injected runner that captures the worktree state
pytest would see and writes a canned junit. The junit -> ReproOutcome mapping and the
candidate-exclude / landing-overlay git plumbing are unit-tested here; the real
pytest behaviour is proven by the live validity-gate run over the codeprobe bundles.
"""

import subprocess
from pathlib import Path

from membench.bundle.anchor import anchor_bundle, landing_gold_diff
from membench.harbor.ftp_repro import FtpReproRunner
from membench.schemas.bundle import FtpOracle, TaskBundle
from tests.helpers import git as _git

NODE_A = "tests/test_c.py::test_a"
NODE_B = "tests/test_c.py::test_b"

PARENT_FEATURE = "def f():\n    return 1\n"
LANDING_FEATURE = "def f():\n    return 2\n"
PARENT_TEST = "from feature import f\n\ndef test_a():\n    assert f()\n"
LANDING_TEST = (
    "from feature import f\n\n"
    "def test_a():\n    assert f()\n\n"
    "def test_b():\n    assert f() == 2\n"
)
# A candidate that ALSO edits the gold test module -- must be excluded (the curated
# tests are the spec); the landing overlay is what the run actually scores.
SABOTAGED_TEST = "from feature import f\n\ndef test_b():\n    assert False\n"


def _testcase(name: str, child: str = "") -> str:
    inner = f"<{child}/>" if child else ""
    return (
        f'<testcase classname="tests.test_c" name="{name}" '
        f'file="tests/test_c.py" line="1" time="0.0">{inner}</testcase>'
    )


def _suite(*cases: str) -> str:
    return f'<testsuites><testsuite name="pytest">{"".join(cases)}</testsuite></testsuites>'


def _clone(tmp_path: Path) -> tuple[Path, str, str]:
    """A codeprobe-shaped repo with a parent commit and a behavioral landing commit;
    returns (clone, parent_sha, landing_sha)."""
    repo = tmp_path / "clone"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "feature.py").write_text(PARENT_FEATURE, encoding="utf-8")
    (repo / "tests" / "test_c.py").write_text(PARENT_TEST, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "parent")
    parent = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "feature.py").write_text(LANDING_FEATURE, encoding="utf-8")
    (repo / "tests" / "test_c.py").write_text(LANDING_TEST, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "behavioral landing")
    landing = _git(repo, "rev-parse", "HEAD").strip()
    return repo, parent, landing


def _bundle(clone: Path, parent: str, landing: str, *, ftp_tests: tuple[str, ...]) -> TaskBundle:
    ftp = FtpOracle(
        commit=landing,
        parent=parent,
        ftp_tests=ftp_tests,
        behavioral=ftp_tests,
        feature_presence=(),
        type="behavioral",
    )
    gold = landing_gold_diff(clone, parent, landing)
    return anchor_bundle(
        rig="codeprobe",
        repo="codeprobe",
        base_image="codeprobe-base:py3.11",
        ftp=ftp,
        gold_file_diffs=gold,
        issue_title="behavioral landing",
    )


class FakeContainer:
    """Injected `Runner`: real git passes through; the docker exec captures the
    worktree state pytest would see (proving apply + overlay ran) and writes the
    canned junit for this scenario."""

    def __init__(self, junit: str) -> None:
        self._junit = junit
        self.argvs: list[tuple[str, ...]] = []
        self.seen_feature: str | None = None
        self.seen_test: str | None = None

    def __call__(self, argv, **kwargs):  # type: ignore[no-untyped-def]
        self.argvs.append(tuple(argv))
        if argv and argv[0] == "git":
            # Forward kwargs so a piped patch (git apply -, input=...) reaches git.
            return subprocess.run(argv, **kwargs)
        mount = next(a for a in argv if ":/app" in a)
        host_dir = Path(mount.split(":/app")[0])
        self.seen_feature = (host_dir / "feature.py").read_text(encoding="utf-8")
        self.seen_test = (host_dir / "tests" / "test_c.py").read_text(encoding="utf-8")
        (host_dir / ".ftp-junit.xml").write_text(self._junit, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def worktree_adds(self) -> int:
        return sum(1 for a in self.argvs if "worktree" in a and "add" in a)


def _runner(clone: Path, tmp_path: Path, fake: FakeContainer) -> FtpReproRunner:
    (tmp_path / "wt").mkdir(exist_ok=True)
    return FtpReproRunner(
        rig_repos={"codeprobe": clone}, worktree_root=tmp_path / "wt", runner=fake
    )


def test_gold_candidate_turns_ftp_green(tmp_path: Path) -> None:
    clone, parent, landing = _clone(tmp_path)
    bundle = _bundle(clone, parent, landing, ftp_tests=(NODE_B,))
    fake = FakeContainer(_suite(_testcase("test_a"), _testcase("test_b")))
    with _runner(clone, tmp_path, fake) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff=bundle.output.diff_by_file())
    assert outcome.passed and outcome.error is None
    assert outcome.tests_passed == 1 and outcome.tests_total == 1
    assert outcome.test_ratio == 1.0
    # The candidate's impl reached the worktree; the gold tests came from the landing
    # overlay (parent had only test_a).
    assert fake.seen_feature == LANDING_FEATURE
    assert "def test_b" in (fake.seen_test or "")


def test_empty_candidate_leaves_ftp_red(tmp_path: Path) -> None:
    clone, parent, landing = _clone(tmp_path)
    bundle = _bundle(clone, parent, landing, ftp_tests=(NODE_B,))
    fake = FakeContainer(_suite(_testcase("test_a"), _testcase("test_b", 'failure message="x"')))
    with _runner(clone, tmp_path, fake) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={})
    assert not outcome.passed and outcome.error is None
    assert outcome.tests_passed == 0 and outcome.tests_total == 1
    assert outcome.test_ratio == 0.0
    # No candidate applied -> parent source; landing tests still overlaid.
    assert fake.seen_feature == PARENT_FEATURE
    assert "def test_b" in (fake.seen_test or "")


def test_partial_credit_under_binary_anchor(tmp_path: Path) -> None:
    clone, parent, landing = _clone(tmp_path)
    bundle = _bundle(clone, parent, landing, ftp_tests=(NODE_A, NODE_B))
    fake = FakeContainer(_suite(_testcase("test_a"), _testcase("test_b", 'failure message="x"')))
    with _runner(clone, tmp_path, fake) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff={})
    assert not outcome.passed  # binary anchor: NOT all ftp green
    assert outcome.tests_passed == 1 and outcome.tests_total == 2
    assert outcome.test_ratio == 0.5


def test_candidate_test_edits_are_excluded_overlay_wins(tmp_path: Path) -> None:
    clone, parent, landing = _clone(tmp_path)
    bundle = _bundle(clone, parent, landing, ftp_tests=(NODE_B,))
    # Candidate sabotages the spec test module AND ships the impl; the module edit
    # must be dropped and replaced by the landing overlay.
    candidate = dict(bundle.output.diff_by_file())
    sabotage = (
        "diff --git a/tests/test_c.py b/tests/test_c.py\n"
        "--- a/tests/test_c.py\n"
        "+++ b/tests/test_c.py\n"
        "@@ -1,5 +1,3 @@\n"
        "-from feature import f\n-\n-def test_a():\n-    assert f()\n-\n"
        "+from feature import f\n+def test_b():\n+    assert False\n"
    )
    candidate["tests/test_c.py"] = sabotage
    fake = FakeContainer(_suite(_testcase("test_b")))
    with _runner(clone, tmp_path, fake) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff=candidate)
    assert outcome.error is None
    # The sabotaged test never reached the worktree -- the landing module did.
    assert fake.seen_test == LANDING_TEST
    assert "assert False" not in (fake.seen_test or "")


def test_missing_ftp_oracle_is_a_runner_error(tmp_path: Path) -> None:
    clone, parent, landing = _clone(tmp_path)
    bundle = _bundle(clone, parent, landing, ftp_tests=(NODE_B,))
    no_oracle = bundle.model_copy(
        update={"verification": bundle.verification.model_copy(update={"ftp_oracle": None})}
    )
    fake = FakeContainer(_suite())
    with _runner(clone, tmp_path, fake) as runner:
        outcome = runner.run(bundle=no_oracle, candidate_diff={})
    assert not outcome.passed
    assert outcome.error is not None and "ftp_oracle" in outcome.error
    assert fake.argvs == []  # no checkout, no container -- bailed before any IO


def test_unapplyable_candidate_falls_back_with_reason(tmp_path: Path) -> None:
    clone, parent, landing = _clone(tmp_path)
    bundle = _bundle(clone, parent, landing, ftp_tests=(NODE_B,))
    bad = {
        "feature.py": (
            "diff --git a/feature.py b/feature.py\n"
            "--- a/feature.py\n"
            "+++ b/feature.py\n"
            "@@ -1 +1 @@\n"
            "-this line never existed\n"
            "+so the apply must fail\n"
        )
    }
    fake = FakeContainer(_suite())
    with _runner(clone, tmp_path, fake) as runner:
        outcome = runner.run(bundle=bundle, candidate_diff=bad)
    assert not outcome.passed
    assert outcome.error is not None and "apply" in outcome.error
    # The container never ran: an unscoreable candidate degrades the leg, not a fake 0.
    assert not any(":/app" in a for argv in fake.argvs for a in argv)


def test_worktree_is_cached_and_reset_across_runs(tmp_path: Path) -> None:
    clone, parent, landing = _clone(tmp_path)
    bundle = _bundle(clone, parent, landing, ftp_tests=(NODE_B,))
    fake = FakeContainer(_suite(_testcase("test_b")))
    with _runner(clone, tmp_path, fake) as runner:
        first = runner.run(bundle=bundle, candidate_diff=bundle.output.diff_by_file())
        second = runner.run(bundle=bundle, candidate_diff={})
    assert first.passed and second.passed
    assert fake.worktree_adds() == 1  # one checkout reused across both runs
    # The second run reset the first run's candidate: parent source restored.
    assert fake.seen_feature == PARENT_FEATURE
