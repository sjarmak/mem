"""mem-rk41.3 — the synthetic tool-requiring world -> real-agent task adapter + driver.

Covers the two invariants the paid none/oracle ceiling-at-scale rests on:

* the adapter bridges the synthetic ``apply_config`` goal onto a real ``Write`` and
  OPAQUIFIES the reward-bearing values, so a ``none``-arm real agent cannot pass by
  guessing a realistic default (the mem-rk41.4 opaque-token discipline, generalized
  from one hardcoded probe to the frozen corpus); and
* the leak firewall never lets an authored value (realistic OR opaque) reach the
  agent-visible request, so ``none`` stays a genuine leak detector.

The driver's dry-run path (simulated memory-copying agent, no ``claude``) proves the
arms separate end to end for FREE, and its per-task result files make the paid sweep
resumable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from membench.metrics.scorers import states_value
from membench.runner.toolreq_realagent import (
    ToolReqRealAgentTask,
    adapt_sequence,
    load_corpus,
)
from membench.schemas.sequence import (
    BenchmarkSequence,
    ExpectedAction,
    OutcomeCheck,
    SequenceStep,
)

# The driver is a script; import it the way test_realagent_probe imports the probe CLI.
_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import grid_toolreq_realagent as driver  # noqa: E402

CURRENT = "30 days"
STALE = "90 days"


def _toolreq_seq(seq_id: str = "w-t0") -> BenchmarkSequence:
    """A minimal tool-requiring sequence: a v1->v2 superseded retention chain plus a
    goal whose ``apply_config`` action requires the current value and forbids the stale
    one. Mirrors the frozen ``fixtures/worlds-tool`` shape without depending on the
    (untracked) generated corpus."""
    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"{seq_id} initiative: reconcile retention",
        domain="data-infrastructure",
        goal="Deliver the current initiative.",
        steps=[
            SequenceStep(
                step_id=f"{seq_id}-s0",
                user_request="Record the current value of the data retention window.",
                expected_memory_writes={
                    "m-v1": f"the data retention window is {STALE} — by A. Ree in #chat"
                },
            ),
            SequenceStep(
                step_id=f"{seq_id}-s1",
                user_request="Record the current value of the data retention window.",
                expected_memory_writes={
                    "m-v2": f"the data retention window is {CURRENT} — by B. Cee in #meeting"
                },
                superseded_memory_ids=["m-v1"],
            ),
            SequenceStep(
                step_id=f"{seq_id}-goal",
                user_request=(
                    "Deliver the current initiative. Using the tool `apply_config`, apply "
                    "the current value of: the data retention window."
                ),
                available_tools=["apply_config"],
                expected_memory_reads=["m-v2"],
                outcome_checks=[
                    OutcomeCheck(
                        check_id=f"{seq_id}-goal-check",
                        description="goal requires apply_config carrying the current value",
                        requires_memory=["m-v2"],
                        requires_action=[
                            ExpectedAction(
                                tool="apply_config",
                                arg_values=[CURRENT],
                                forbidden_values=[STALE],
                            )
                        ],
                    )
                ],
            ),
        ],
    )


def _text_only_seq(seq_id: str = "w-text") -> BenchmarkSequence:
    """A text-answer sequence (no tool-requiring action) — the adapter must refuse it."""
    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"{seq_id} initiative",
        steps=[
            SequenceStep(
                step_id=f"{seq_id}-s0",
                user_request="Record.",
                expected_memory_writes={"m-a": f"the retention window is {CURRENT}"},
            ),
            SequenceStep(
                step_id=f"{seq_id}-goal",
                user_request="State the current value of: the retention window.",
                outcome_checks=[
                    OutcomeCheck(
                        check_id=f"{seq_id}-goal-check",
                        requires_memory=["m-a"],
                        forbidden_values=[STALE],
                    )
                ],
            ),
        ],
    )


# --- adapter ------------------------------------------------------------------------


def test_adapt_bridges_goal_onto_write_with_opaque_values() -> None:
    task = adapt_sequence(_toolreq_seq())
    assert isinstance(task, ToolReqRealAgentTask)
    step = task.goal_step
    assert step.available_tools == ["Write"]
    (action,) = step.outcome_checks[0].requires_action
    assert action.tool == "Write"
    # Reward values are opaquified — the realistic strings never survive into the action.
    assert CURRENT not in action.arg_values
    assert STALE not in action.forbidden_values
    assert action.arg_values and all(v.startswith("toolreq-") for v in action.arg_values)
    assert action.forbidden_values and all(
        v.startswith("toolreq-") for v in action.forbidden_values
    )
    # The simulator keys on exactly the opaque current value(s).
    assert task.current_opaque_values == tuple(action.arg_values)


def test_oracle_memory_surfaces_current_opaque_never_stale() -> None:
    task = adapt_sequence(_toolreq_seq())
    joined = " ".join(task.oracle_memory.values())
    (current_opaque,) = task.current_opaque_values
    assert states_value(joined, current_opaque)
    # neither the realistic values nor the (opaque) stale value may be surfaced
    assert not states_value(joined, CURRENT)
    assert not states_value(joined, STALE)
    (stale_opaque,) = task.goal_step.outcome_checks[0].requires_action[0].forbidden_values
    assert not states_value(joined, stale_opaque)


def test_bridged_request_leaks_no_value_and_names_the_real_tool() -> None:
    step = adapt_sequence(_toolreq_seq()).goal_step
    req = step.user_request
    # no authored value (realistic or opaque) may reach the agent-visible request
    for opaque in (
        *step.outcome_checks[0].requires_action[0].arg_values,
        *step.outcome_checks[0].requires_action[0].forbidden_values,
    ):
        assert not states_value(req, opaque)
    assert not states_value(req, CURRENT)
    assert not states_value(req, STALE)
    assert "apply_config" not in req
    assert "Write" in req and "config.json" in req


def test_adapt_rejects_text_only_sequence() -> None:
    with pytest.raises(ValueError, match="tool-requiring"):
        adapt_sequence(_text_only_seq())


def test_adapt_raises_on_required_id_with_no_backing_write() -> None:
    seq = _toolreq_seq()
    # point requires_memory at an id no step ever writes
    seq.steps[-1].outcome_checks[0].requires_memory.append("m-ghost")
    with pytest.raises(ValueError, match="m-ghost"):
        adapt_sequence(seq)


def test_opacity_deterministic_and_sequence_unique() -> None:
    a1 = adapt_sequence(_toolreq_seq("w-a"))
    a2 = adapt_sequence(_toolreq_seq("w-a"))
    b = adapt_sequence(_toolreq_seq("w-b"))
    assert a1.current_opaque_values == a2.current_opaque_values  # deterministic
    # same realistic value, different sequence id -> different opaque token (no collision)
    assert a1.current_opaque_values != b.current_opaque_values


# --- corpus loader ------------------------------------------------------------------


def test_load_corpus_reads_every_seed(tmp_path: Path) -> None:
    for seed, sid in enumerate(("w-0", "w-1")):
        seed_dir = tmp_path / str(seed)
        seed_dir.mkdir()
        (seed_dir / "sequences.json").write_text(
            json.dumps([_toolreq_seq(sid).model_dump()]), encoding="utf-8"
        )
    tasks = load_corpus(tmp_path)
    assert sorted(t.work_id for t in tasks) == ["w-0", "w-1"]


# --- driver (free dry-run) ----------------------------------------------------------


def test_dry_run_arms_separate_per_task() -> None:
    task = adapt_sequence(_toolreq_seq())
    outcomes = driver.evaluate_task(task, repeats=2, model="", dry_run=True)
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in driver.CHANNELS):
        assert by[("none", channel)].passes == 0
        assert by[("oracle", channel)].passes == by[("oracle", channel)].runs == 2


def test_run_corpus_persists_and_is_resumable(tmp_path: Path) -> None:
    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    tasks = load_corpus(tmp_path / "corpus")
    out = tmp_path / "out"

    first = driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert first["n_tasks"] == 1
    assert first["executed"] == 1 and first["reused"] == 0
    assert (out / "w-0.json").is_file()
    assert "SEPARATES" in first["per_task"][0]["verdict"]

    # Re-run: the persisted result is reused, nothing re-executed.
    second = driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert second["executed"] == 0 and second["reused"] == 1


def _corpus_one(tmp_path: Path, work_id: str = "w-0") -> list:
    corpus = tmp_path / "corpus"
    (corpus / "0").mkdir(parents=True)
    (corpus / "0" / "sequences.json").write_text(
        json.dumps([_toolreq_seq(work_id).model_dump()]), encoding="utf-8"
    )
    return load_corpus(corpus)


def test_dry_run_cache_never_satisfies_a_paid_run(tmp_path: Path, monkeypatch) -> None:
    # The highest-severity confound: a FREE dry-run's simulated result must NOT be reused by
    # a PAID run over the same --out. Prove the paid run re-executes (spied, so no real spend).
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)

    calls = {"n": 0}
    real_eval = driver.evaluate_task

    def _spy(task, **_kwargs):
        calls["n"] += 1
        return real_eval(task, repeats=2, model="", dry_run=True)  # simulate, never spend

    monkeypatch.setattr(driver, "evaluate_task", _spy)
    paid = driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=False)
    assert paid["executed"] == 1 and paid["reused"] == 0
    assert calls["n"] == 1


def test_corrupt_cache_file_is_re_executed_not_crashed(tmp_path: Path) -> None:
    tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{tasks[0].work_id}.json").write_text("{ half-written not json", encoding="utf-8")
    summary = driver.run_corpus(tasks, out_dir=out, repeats=2, model="", dry_run=True)
    assert summary["executed"] == 1 and summary["reused"] == 0  # cache-miss, no crash


def test_driver_refuses_to_spend_without_token(tmp_path: Path, monkeypatch) -> None:
    # The corpus-wide spend guard (financial-safety branch): no token + no --dry-run -> exit 2
    # and never spawn claude, same contract the probe's gate is tested for.
    _corpus_one(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    from membench.runner import realagent_probe as _rp

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("must NOT spawn claude when the spend gate fires")

    monkeypatch.setattr(_rp.subprocess, "run", _boom)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 2


def test_load_corpus_orders_seeds_numerically(tmp_path: Path) -> None:
    # 10 must follow 2, not precede it (lexical would misorder past nine seeds).
    for seed in (0, 2, 10):
        seed_dir = tmp_path / str(seed)
        seed_dir.mkdir()
        (seed_dir / "sequences.json").write_text(
            json.dumps([_toolreq_seq(f"w-{seed}").model_dump()]), encoding="utf-8"
        )
    order = [t.work_id for t in load_corpus(tmp_path)]
    assert order == ["w-0", "w-2", "w-10"]


# --- multi-subject (facts_per_task=3) fidelity to the real corpus shape --------------


def _multi_subject_seq(seq_id: str = "w-m") -> BenchmarkSequence:
    """The real corpus default: 3 subjects, only ONE of them a superseded chain (the
    apply_config action's reward). The other two are single-version currents in
    requires_memory — surfaced realistically to oracle, never scored."""
    flag_current = "canary-50%"
    timeout_current = "15s"
    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"{seq_id} initiative",
        steps=[
            SequenceStep(
                step_id=f"{seq_id}-s0",
                user_request="Record.",
                expected_memory_writes={"m-flag": f"the checkout flag is {flag_current}"},
            ),
            SequenceStep(
                step_id=f"{seq_id}-s1",
                user_request="Record.",
                expected_memory_writes={"m-r1": f"the retention window is {STALE}"},
            ),
            SequenceStep(
                step_id=f"{seq_id}-s2",
                user_request="Record.",
                expected_memory_writes={"m-r2": f"the retention window is {CURRENT}"},
                superseded_memory_ids=["m-r1"],
            ),
            SequenceStep(
                step_id=f"{seq_id}-s3",
                user_request="Record.",
                expected_memory_writes={"m-to": f"the deploy timeout is {timeout_current}"},
            ),
            SequenceStep(
                step_id=f"{seq_id}-goal",
                user_request=(
                    "Deliver. Using the tool `apply_config`, apply the current value of: "
                    "the checkout flag, the retention window, the deploy timeout."
                ),
                available_tools=["apply_config"],
                expected_memory_reads=["m-flag", "m-r2", "m-to"],
                outcome_checks=[
                    OutcomeCheck(
                        check_id=f"{seq_id}-goal-check",
                        requires_memory=["m-flag", "m-r2", "m-to"],
                        requires_action=[
                            ExpectedAction(
                                tool="apply_config",
                                arg_values=[CURRENT],
                                forbidden_values=[STALE],
                            )
                        ],
                    )
                ],
            ),
        ],
    )


def test_multi_subject_opaquifies_reward_and_surfaces_all_facts() -> None:
    task = adapt_sequence(_multi_subject_seq())
    # oracle surfaces all three required facts
    assert set(task.oracle_memory) == {"m-flag", "m-r2", "m-to"}
    joined = " ".join(task.oracle_memory.values())
    (current_opaque,) = task.current_opaque_values
    # the reward (chain) value is opaque and surfaced; the realistic chain value is gone
    assert states_value(joined, current_opaque)
    assert not states_value(joined, CURRENT)
    assert not states_value(joined, STALE)
    # non-reward currents stay realistic (never scored) — documented, intentional
    assert states_value(joined, "canary-50%")
    assert states_value(joined, "15s")
    # the bridged request still leaks nothing and reads cleanly (no doubled "the tool the")
    req = task.goal_step.user_request
    assert "the tool the" not in req
    assert not states_value(req, CURRENT) and not states_value(req, current_opaque)


def test_multi_subject_arms_separate_dry_run() -> None:
    task = adapt_sequence(_multi_subject_seq())
    outcomes = driver.evaluate_task(task, repeats=2, model="", dry_run=True)
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in driver.CHANNELS):
        assert by[("none", channel)].passes == 0
        assert by[("oracle", channel)].passes == 2
