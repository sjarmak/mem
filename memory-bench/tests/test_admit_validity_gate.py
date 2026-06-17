"""Oracle-soundness pre-admission gate (mem-1eph): the validity gate runs BEFORE
the grid-ready manifest is written, so a scope-admitted bundle with a broken oracle
is rejected before it can consume a grid N (the mem-apg.9 ordering bug).

Offline: the scope decisions are constructed `FanoutDecision`s and the repro runner
is a fake keyed by work_id, so the two-stage admission is exercised with no claude
call and no checkout. Loaded from its file path (the run_gate_probe test idiom).
"""

import importlib.util
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from membench.bundle.assemble import FanoutDecision, Rejection, RejectionReason
from membench.bundle.replay import CallReplay, ReplayOutcome, ReplayResult
from membench.grading.dual_verifier import ReproOutcome
from membench.harbor.bundle_grid import load_grid_ready_work_ids
from membench.schemas.bundle import BundleEnv, TaskBundle

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "admit_batch_guarded.py"

IMPL = "diff --git a/src/app.ts b/src/app.ts\n@@\n-1\n+2\n"
TEST = "diff --git a/src/app.test.ts b/src/app.test.ts\n@@\n-// base\n+// gold\n"


def _load_script():
    spec = importlib.util.spec_from_file_location("admit_batch_guarded", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["admit_batch_guarded"] = module
    spec.loader.exec_module(module)
    return module


abg = _load_script()


def _bundle(work_id: str) -> TaskBundle:
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
        work_id=work_id,
        rig="demo",
        issue_title="t",
        trace_ref="/tmp/t.jsonl",
        output=output,
        env=BundleEnv(repo="demo", base_commit="c1", base_image="img"),
        loo_excluded_work_ids=(work_id,),
    )


def _scope_admit(work_id: str) -> "abg.GuardRow":
    return abg.GuardRow(work_id, "epic", FanoutDecision(None, 2, True, "scope matches"))


def _scope_reject(work_id: str) -> "abg.GuardRow":
    rej = Rejection(
        work_id=work_id, reason=RejectionReason.ISSUE_FANOUT_SCOPE_MISMATCH, detail="spans many"
    )
    return abg.GuardRow(work_id, "epic", FanoutDecision(rej, 31, True, "issue over-describes"))


@dataclass
class _FakeRunner:
    """Per-work_id (gold, empty) outcomes; records the work_ids it was asked to run
    so a test can assert the gate never touched a scope-rejected bundle."""

    outcomes: Mapping[str, tuple[ReproOutcome, ReproOutcome]]
    seen: list[str]

    def run(self, *, bundle: TaskBundle, candidate_diff: Mapping[str, str]) -> ReproOutcome:
        self.seen.append(bundle.work_id)
        gold, empty = self.outcomes[bundle.work_id]
        return gold if candidate_diff else empty


_SOUND = (
    ReproOutcome(passed=True, tests_passed=1, tests_total=1),
    ReproOutcome(passed=False, tests_passed=0, tests_total=1),
)
_BROKEN_GOLD = (
    ReproOutcome(passed=False, tests_passed=0, tests_total=1),
    ReproOutcome(passed=False, tests_passed=0, tests_total=1),
)
_BROKEN_EMPTY = (
    ReproOutcome(passed=True, tests_passed=1, tests_total=1),
    ReproOutcome(passed=True, tests_passed=1, tests_total=1),
)


# --- apply_validity_gate -----------------------------------------------------------


def test_gate_runs_only_on_scope_admitted_bundles() -> None:
    rows = [_scope_admit("a"), _scope_reject("b")]
    bundles = {"a": _bundle("a"), "b": _bundle("b")}
    runner = _FakeRunner({"a": _SOUND}, seen=[])
    out = abg.apply_validity_gate(rows, bundles, runner)
    # The scope-rejected bundle's oracle is never run (expensive; moot); the
    # scope-admitted one is gated twice (gold + empty candidate).
    assert runner.seen.count("a") == 2
    assert "b" not in runner.seen
    by_id = {r.work_id: r for r in out}
    assert by_id["a"].validity is not None
    assert by_id["b"].validity is None


def test_sound_oracle_is_admitted() -> None:
    rows = [_scope_admit("a")]
    runner = _FakeRunner({"a": _SOUND}, seen=[])
    out = abg.apply_validity_gate(rows, {"a": _bundle("a")}, runner)
    assert out[0].admitted and out[0].validity.valid


def test_broken_gold_oracle_rejected_before_consuming_n() -> None:
    rows = [_scope_admit("a")]
    runner = _FakeRunner({"a": _BROKEN_GOLD}, seen=[])
    out = abg.apply_validity_gate(rows, {"a": _bundle("a")}, runner)
    assert not out[0].admitted
    assert out[0].scope_admitted  # passed stage 1, failed stage 2
    assert "did not reproduce" in out[0].validity.reason


def test_empty_passing_oracle_rejected() -> None:
    rows = [_scope_admit("a")]
    runner = _FakeRunner({"a": _BROKEN_EMPTY}, seen=[])
    out = abg.apply_validity_gate(rows, {"a": _bundle("a")}, runner)
    assert not out[0].admitted
    assert "empty diff reproduced" in out[0].validity.reason


def test_scope_rejected_never_admitted_regardless_of_oracle() -> None:
    rows = [_scope_reject("b")]
    out = abg.apply_validity_gate(rows, {"b": _bundle("b")}, _FakeRunner({}, seen=[]))
    assert not out[0].admitted and out[0].validity is None


# --- build_manifest + reader round-trip --------------------------------------------


def test_manifest_admitted_is_both_stages(tmp_path: Path) -> None:
    rows = [_scope_admit("a"), _scope_admit("c"), _scope_reject("b")]
    bundles = {"a": _bundle("a"), "c": _bundle("c"), "b": _bundle("b")}
    runner = _FakeRunner({"a": _SOUND, "c": _BROKEN_GOLD}, seen=[])
    rows = abg.apply_validity_gate(rows, bundles, runner)
    manifest = abg.build_manifest(rows)

    assert manifest["schema"] == abg.MANIFEST_SCHEMA
    # 'a' clears both; 'c' fails the oracle; 'b' fails scope.
    assert manifest["admitted"] == ["a"]

    path = tmp_path / "grid-ready-pool.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_grid_ready_work_ids(path) == ("a",)


def test_manifest_provenance_distinguishes_scope_and_oracle_rejects() -> None:
    rows = [_scope_admit("c"), _scope_reject("b")]
    bundles = {"c": _bundle("c"), "b": _bundle("b")}
    rows = abg.apply_validity_gate(rows, bundles, _FakeRunner({"c": _BROKEN_GOLD}, seen=[]))
    prov = {p["work_id"]: p for p in abg.build_manifest(rows)["provenance"]}

    # Broken-oracle reject: passed scope, oracle_sound=False, reason names the breach.
    assert prov["c"]["scope_admitted"] is True
    assert prov["c"]["oracle_sound"] is False
    assert "did not reproduce" in prov["c"]["oracle_reason"]
    assert prov["c"]["admitted"] is False

    # Scope reject: oracle gate never ran -> nulls, not a misleading False.
    assert prov["b"]["scope_admitted"] is False
    assert prov["b"]["oracle_sound"] is None
    assert prov["b"]["oracle_reason"] is None
    assert prov["b"]["admitted"] is False


# --- _DryReproRunner ---------------------------------------------------------------


def test_dry_repro_runner_declares_every_oracle_sound() -> None:
    rows = abg.apply_validity_gate([_scope_admit("a")], {"a": _bundle("a")}, abg._DryReproRunner())
    assert rows[0].admitted and rows[0].validity.valid
