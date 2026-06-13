"""CSB oracle validity gate (mem-g6a / mem-r5y): gold reproduces, empty fails.

Offline: a tiny runner returns distinct outcomes for the gold candidate (non-empty)
and the empty candidate, so the invariant is exercised without any checkout.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.grading.dual_verifier import ReproOutcome
from membench.grading.validity_gate import validity_gate
from membench.schemas.bundle import BundleEnv, TaskBundle

IMPL = "diff --git a/src/app.ts b/src/app.ts\n@@\n-1\n+2\n"
TEST = "diff --git a/src/app.test.ts b/src/app.test.ts\n@@\n-// base\n+// gold\n"


def _bundle() -> TaskBundle:
    output = ReplayResult(
        calls=(
            CallReplay(
                index=0,
                tool="Edit",
                path="/o/x",
                rebased_path="/o/x",
                outcome=ReplayOutcome.APPLIED,
            ),
        ),
        file_diffs=(("src/app.test.ts", TEST), ("src/app.ts", IMPL)),
        replay_success_rate=1.0,
    )
    return TaskBundle(
        work_id="demo-1",
        rig="demo",
        issue_title="t",
        trace_ref="/tmp/t.jsonl",
        output=output,
        env=BundleEnv(repo="demo", base_commit="c1", base_image="img"),
        loo_excluded_work_ids=("demo-1",),
    )


@dataclass
class _Runner:
    """Returns ``gold`` for a non-empty candidate, ``empty`` for the empty one."""

    gold: ReproOutcome
    empty: ReproOutcome

    def run(self, *, bundle: TaskBundle, candidate_diff: Mapping[str, str]) -> ReproOutcome:
        return self.gold if candidate_diff else self.empty


def test_valid_when_gold_passes_and_empty_fails() -> None:
    runner = _Runner(
        gold=ReproOutcome(passed=True, tests_passed=1, tests_total=1),
        empty=ReproOutcome(passed=False, tests_passed=0, tests_total=1),
    )
    result = validity_gate(_bundle(), test_runner=runner)
    assert result.valid
    assert result.gold_repro_passed and not result.empty_repro_passed
    assert result.gold_test_ratio == 1.0 and result.empty_test_ratio == 0.0


def test_invalid_when_gold_does_not_reproduce() -> None:
    runner = _Runner(
        gold=ReproOutcome(passed=False, tests_passed=0, tests_total=1),
        empty=ReproOutcome(passed=False, tests_passed=0, tests_total=1),
    )
    result = validity_gate(_bundle(), test_runner=runner)
    assert not result.valid and "did not reproduce" in result.reason


def test_invalid_when_empty_reproduces() -> None:
    runner = _Runner(
        gold=ReproOutcome(passed=True, tests_passed=1, tests_total=1),
        empty=ReproOutcome(passed=True, tests_passed=1, tests_total=1),
    )
    result = validity_gate(_bundle(), test_runner=runner)
    assert not result.valid and "empty diff reproduced" in result.reason


def test_invalid_when_empty_partially_passes() -> None:
    # A gold test that passes without the fix -> the tests are not fail-to-pass.
    runner = _Runner(
        gold=ReproOutcome(passed=True, tests_passed=2, tests_total=2),
        empty=ReproOutcome(passed=False, tests_passed=1, tests_total=2),
    )
    result = validity_gate(_bundle(), test_runner=runner)
    assert not result.valid and "without the fix" in result.reason


def test_invalid_when_gold_runner_errors() -> None:
    runner = _Runner(
        gold=ReproOutcome(passed=False, error="git apply failed"),
        empty=ReproOutcome(passed=False, tests_passed=0, tests_total=1),
    )
    result = validity_gate(_bundle(), test_runner=runner)
    assert not result.valid and "runner error" in result.reason
