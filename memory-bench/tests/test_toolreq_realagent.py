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

import dataclasses
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from membench.metrics.scorers import states_value
from membench.runner.toolreq_realagent import (
    ToolReqRealAgentTask,
    adapt_sequence,
    load_corpus_with_sequences,
    sequence_lessons_opaque,
)
from membench.schemas.sequence import (
    BenchmarkSequence,
    ExpectedAction,
    OutcomeCheck,
    SequenceStep,
)
from tests.paths import DIST_MAIN, MEM_BIN, require_mem_cli

# The driver is a script; import it the way test_realagent_probe imports the probe CLI.
_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import grid_toolreq_realagent as driver  # noqa: E402

CURRENT = "30 days"
STALE = "90 days"


# The default no-op seed_fn: every hermetic test that exercises run_corpus() but doesn't
# care about the `ours` arm's actual payload injects this instead of the real seeder, so
# it never needs a built bin/mem (the injectable seed_fn convention the driver documents).
def _no_ours_payload(
    _sequences: object,
    _tasks: object,
    _store_path: object,
    _mem_bin: object,
    _resolve_ids: object = None,
) -> dict[str, dict[str, str]]:
    return {}


def _run_corpus(
    tasks: list[ToolReqRealAgentTask],
    sequences: list[BenchmarkSequence],
    out: Path,
    *,
    dry_run: bool,
    store_path: Path,
    seed_fn: Callable[..., dict[str, dict[str, str]]] = _no_ours_payload,
) -> dict[str, Any]:
    """driver.run_corpus with the hermetic test wiring (repeats=2, no model, stubbed
    seed_fn) — only what a test actually varies rides in its call."""
    return driver.run_corpus(
        tasks,
        sequences,
        out_dir=out,
        repeats=2,
        model="",
        dry_run=dry_run,
        store_path=store_path,
        seed_fn=seed_fn,
    )


def _toolreq_seq(
    seq_id: str = "w-t0", *, current: str = CURRENT, stale: str = STALE
) -> BenchmarkSequence:
    """A minimal tool-requiring sequence: a v1->v2 superseded retention chain plus a
    goal whose ``apply_config`` action requires the current value and forbids the stale
    one. Mirrors the frozen ``fixtures/worlds-tool`` shape without depending on the
    (untracked) generated corpus.

    ``current``/``stale`` are overridable so a test can build a REGENERATED corpus — same
    sequence ids, different authored values, hence a disjoint opaque-token space."""
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
                    "m-v1": f"the data retention window is {stale} — by A. Ree in #chat"
                },
            ),
            SequenceStep(
                step_id=f"{seq_id}-s1",
                user_request="Record the current value of the data retention window.",
                expected_memory_writes={
                    "m-v2": f"the data retention window is {current} — by B. Cee in #meeting"
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
                                arg_values=[current],
                                forbidden_values=[stale],
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


def test_load_corpus_with_sequences_pairs_in_order(tmp_path: Path) -> None:
    for seed, sid in enumerate(("w-0", "w-1")):
        seed_dir = tmp_path / str(seed)
        seed_dir.mkdir()
        (seed_dir / "sequences.json").write_text(
            json.dumps([_toolreq_seq(sid).model_dump()]), encoding="utf-8"
        )
    sequences, tasks = load_corpus_with_sequences(tmp_path)
    assert [s.sequence_id for s in sequences] == [t.work_id for t in tasks] == ["w-0", "w-1"]


# --- sequence_lessons_opaque ---------------------------------------------------------


def test_sequence_lessons_opaque_states_current_opaque_value_leak_safely() -> None:
    seq = _toolreq_seq()
    task = adapt_sequence(seq)
    lessons = sequence_lessons_opaque([seq], [task])
    assert len(lessons) == 1
    lesson = lessons[0]
    assert lesson["work_id"] == seq.sequence_id
    facts = lesson["payload"]["facts"]
    (current_opaque,) = task.current_opaque_values
    # reuses adapt_sequence's own oracle_memory dict verbatim -> the current opaque value
    # is stated, never the realistic current/stale values (leak-safe by construction).
    assert any(states_value(fact, current_opaque) for fact in facts)
    for fact in facts:
        assert not states_value(fact, CURRENT)
        assert not states_value(fact, STALE)


def test_sequence_lessons_opaque_rejects_length_mismatch() -> None:
    seq = _toolreq_seq()
    task = adapt_sequence(seq)
    with pytest.raises(ValueError, match="length mismatch"):
        sequence_lessons_opaque([seq, _toolreq_seq("w-extra")], [task])


def test_sequence_lessons_opaque_rejects_order_mismatch() -> None:
    seq_a, seq_b = _toolreq_seq("w-a"), _toolreq_seq("w-b")
    task_a, task_b = adapt_sequence(seq_a), adapt_sequence(seq_b)
    with pytest.raises(ValueError, match="order mismatch"):
        sequence_lessons_opaque([seq_a, seq_b], [task_b, task_a])


# --- driver (free dry-run) ----------------------------------------------------------


def test_dry_run_arms_separate_per_task() -> None:
    task = adapt_sequence(_toolreq_seq())
    outcomes = driver.evaluate_task(task, repeats=2, model="", dry_run=True)
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in driver.CHANNELS):
        assert by[("none", channel)].passes == 0
        assert by[("oracle", channel)].passes == by[("oracle", channel)].runs == 2
        # No ours_payload injected -> `ours` behaves like `none` (empty surfaced memory).
        assert by[("ours", channel)].passes == 0


def test_ours_arm_dry_run_passes_when_payload_states_current_value() -> None:
    task = adapt_sequence(_toolreq_seq())
    (current_opaque,) = task.current_opaque_values
    ours_payload = {"w-prior": f"the retention window is {current_opaque}"}
    outcomes = driver.evaluate_task(
        task, repeats=2, model="", dry_run=True, ours_payload=ours_payload
    )
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in driver.CHANNELS):
        assert by[("ours", channel)].passes == by[("ours", channel)].runs == 2


def test_ours_arm_dry_run_honest_non_pass_when_payload_lacks_current_value() -> None:
    # The expected, honest substrate finding: a payload that never states the queried
    # task's own sequence-unique opaque value cannot make the simulated agent pass.
    task = adapt_sequence(_toolreq_seq())
    ours_payload = {"w-prior": "an unrelated retrieved fact about something else entirely"}
    outcomes = driver.evaluate_task(
        task, repeats=2, model="", dry_run=True, ours_payload=ours_payload
    )
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in driver.CHANNELS):
        assert by[("ours", channel)].passes == 0


def test_run_corpus_persists_and_is_resumable(tmp_path: Path) -> None:
    seed_dir = tmp_path / "corpus" / "0"
    seed_dir.mkdir(parents=True)
    (seed_dir / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0").model_dump()]), encoding="utf-8"
    )
    sequences, tasks = load_corpus_with_sequences(tmp_path / "corpus")
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"

    seed_calls = {"n": 0}

    def _counting_seed(*_a: object) -> dict[str, dict[str, str]]:
        seed_calls["n"] += 1
        return {}

    first = _run_corpus(
        tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_counting_seed
    )
    assert first["n_tasks"] == 1
    assert first["executed"] == 1 and first["reused"] == 0
    assert seed_calls["n"] == 1
    assert (out / "w-0.json").is_file()
    assert "SEPARATES" in first["per_task"][0]["verdict"]

    # Re-run: the persisted result is reused, nothing re-executed — and the fully-cached
    # resume never re-seeds the ours store (the seed honors the same resumability contract).
    second = _run_corpus(
        tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_counting_seed
    )
    assert second["executed"] == 0 and second["reused"] == 1
    assert seed_calls["n"] == 1


def _corpus_one(
    tmp_path: Path, work_id: str = "w-0"
) -> tuple[list[BenchmarkSequence], list[ToolReqRealAgentTask]]:
    corpus = tmp_path / "corpus"
    (corpus / "0").mkdir(parents=True)
    (corpus / "0" / "sequences.json").write_text(
        json.dumps([_toolreq_seq(work_id).model_dump()]), encoding="utf-8"
    )
    return load_corpus_with_sequences(corpus)


def test_dry_run_cache_never_satisfies_a_paid_run(tmp_path: Path, monkeypatch) -> None:
    # The highest-severity confound: a FREE dry-run's simulated result must NOT be reused by
    # a PAID run over the same --out. Prove the paid run re-executes (spied, so no real spend).
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)

    calls = {"n": 0}
    real_eval = driver.evaluate_task

    def _spy(task, **_kwargs):
        calls["n"] += 1
        return real_eval(task, repeats=2, model="", dry_run=True)  # simulate, never spend

    monkeypatch.setattr(driver, "evaluate_task", _spy)
    paid = _run_corpus(tasks, sequences, out, dry_run=False, store_path=store_path)
    assert paid["executed"] == 1 and paid["reused"] == 0
    assert calls["n"] == 1


def test_corrupt_cache_file_is_re_executed_not_crashed(tmp_path: Path) -> None:
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{tasks[0].work_id}.json").write_text("{ half-written not json", encoding="utf-8")
    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0  # cache-miss, no crash


@pytest.mark.parametrize(
    "cache_text",
    [
        pytest.param("null", id="json-null"),
        pytest.param("3", id="json-number"),
        pytest.param("[]", id="json-list"),
        pytest.param('"cached"', id="json-string"),
    ],
)
def test_non_object_cache_is_a_miss_not_an_attributeerror(tmp_path: Path, cache_text: str) -> None:
    """A cache file holding VALID JSON of the wrong shape survives ``json.loads`` and then
    dies on ``.get`` — an AttributeError that aborts the whole sweep, contradicting the
    docstring's miss-never-crash guarantee. Every non-object payload must be a plain miss."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{tasks[0].work_id}.json").write_text(cache_text, encoding="utf-8")
    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0


def test_partial_cache_arity_is_a_miss_never_a_silent_truncation(tmp_path: Path) -> None:
    """A cache whose identity matches but whose ``outcomes`` list is TRUNCATED (a channel or
    arm dropped — schema drift, or a kill mid-write that still renamed) must re-execute. If
    it is accepted, the task is scored on a subset of its cells and the headline
    ``separates_all_channels`` silently reads a partial run as a full one — the silent-cap
    class the rig forbids."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    full = len(record["outcomes"])
    assert full == len(driver.ARMS) * len(driver.CHANNELS)
    record["outcomes"] = record["outcomes"][:1]  # drop every cell but one
    result_path.write_text(json.dumps(record), encoding="utf-8")

    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0
    assert len(summary["per_task"][0]["outcomes"]) == full


def test_duplicate_cell_cache_is_a_miss_even_though_the_arity_is_right(tmp_path: Path) -> None:
    """Counting rows is NOT enough. A cache holding len(ARMS)*len(CHANNELS) copies of the SAME
    cell has the right arity but does not cover the grid. task_verdict keys by (arm, channel),
    so such a record yields an EMPTY verdict — the task would then be counted as ``reused``
    while silently vanishing from the separates/leaked accounting. The cells must cover every
    (arm, channel) exactly once or the record is a miss."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    one_cell = record["outcomes"][0]
    record["outcomes"] = [dict(one_cell) for _ in record["outcomes"]]  # right count, one cell
    result_path.write_text(json.dumps(record), encoding="utf-8")

    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0
    # and the re-executed task is scored, not silently dropped
    assert summary["separates_all_channels"] == 1
    assert {(o["arm"], o["channel"]) for o in summary["per_task"][0]["outcomes"]} == (
        driver.expected_cells()
    )


def test_regenerated_corpus_never_reuses_the_previous_worlds_results(tmp_path: Path) -> None:
    """Work ids are positional (``w-0``...), so a regenerated corpus REUSES them. The run
    identity (repeats, dry_run, model, arms) does not change when the WORLD does, so without a
    per-task world fingerprint a re-run over the same ``--out`` reports the previous world's
    numbers with zero cells evaluated — on the PAID path. Reproduced before the fingerprint
    existed: executed=0, reused=N, old verdicts printed as the new world's."""
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"

    corpus_a = tmp_path / "a"
    (corpus_a / "0").mkdir(parents=True)
    (corpus_a / "0" / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0", current="11 days", stale="12 days").model_dump()]),
        encoding="utf-8",
    )
    seqs_a, tasks_a = load_corpus_with_sequences(corpus_a)
    first = _run_corpus(tasks_a, seqs_a, out, dry_run=True, store_path=store_path)
    assert (first["executed"], first["reused"]) == (1, 0)

    # Same work_id, a genuinely different world (new authored values -> new opaque tokens).
    corpus_b = tmp_path / "b"
    (corpus_b / "0").mkdir(parents=True)
    (corpus_b / "0" / "sequences.json").write_text(
        json.dumps([_toolreq_seq("w-0", current="99 days", stale="98 days").model_dump()]),
        encoding="utf-8",
    )
    seqs_b, tasks_b = load_corpus_with_sequences(corpus_b)
    assert tasks_b[0].work_id == tasks_a[0].work_id  # the id collides, as in the real corpus
    assert driver.task_fingerprint(tasks_b[0]) != driver.task_fingerprint(tasks_a[0])

    second = _run_corpus(tasks_b, seqs_b, out, dry_run=True, store_path=store_path)
    assert (second["executed"], second["reused"]) == (1, 0), "reused another world's result"


def test_extra_duplicate_row_is_a_miss_even_though_the_grid_is_covered(tmp_path: Path) -> None:
    """The mirror of the duplicate-cell test, and the case a set-only check misses. The six
    correct rows PLUS one extra duplicate row still cover the grid AS A SET. task_verdict keys
    by (arm, channel), so the LAST row for a key wins — an appended duplicate silently
    overwrites the real measurement, rewriting a genuine SEPARATES into a fabricated KILL while
    the task is counted as `reused` with zero spend and no crash. Coverage and arity must BOTH
    be checked; neither subsumes the other."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    first = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert "SEPARATES" in first["per_task"][0]["verdict"]

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    oracle_cell = next(o for o in record["outcomes"] if o["arm"] == "oracle")
    record["outcomes"].append({**oracle_cell, "passes": 0})  # duplicate, ceiling zeroed
    assert {(o["arm"], o["channel"]) for o in record["outcomes"]} == driver.expected_cells()
    result_path.write_text(json.dumps(record), encoding="utf-8")

    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0
    verdict = summary["per_task"][0]["verdict"]
    assert "KILL" not in verdict and "SEPARATES" in verdict  # re-measured, not overwritten


def test_fingerprint_tracks_the_executed_prompt_not_just_the_authored_values(
    tmp_path: Path,
) -> None:
    """The fingerprint must cover what is EXECUTED and SCORED, not only the reward values.
    ``goal_step`` is the prompt actually sent to the agent and carries the outcome_checks it
    is graded against. An ADAPTER change (new bridged wording, a different tool, an altered
    ExpectedAction) that leaves the authored values untouched must still invalidate the cache
    — otherwise a resumed --out serves pre-change answers as if they measured the new prompt.
    This needs no corrupted file, only ordinary iteration on adapt_sequence."""
    task = adapt_sequence(_toolreq_seq("w-0"))
    drifted = dataclasses.replace(
        task,
        goal_step=task.goal_step.model_copy(
            update={"user_request": "A DIFFERENT GOAL: delete the retention config instead."}
        ),
    )
    # The reward-bearing content is byte-identical; only the executed prompt moved.
    assert drifted.oracle_memory == task.oracle_memory
    assert drifted.current_opaque_values == task.current_opaque_values
    assert drifted.goal_step.user_request != task.goal_step.user_request

    assert driver.task_fingerprint(drifted) != driver.task_fingerprint(task)


def test_switching_the_resolved_model_invalidates_the_cache(tmp_path: Path, monkeypatch) -> None:
    """``--model`` defaults to "" and the agent then resolves it from MEMBENCH_AGENT_MODEL, so
    caching the RAW flag makes the driver's primary independent variable invisible: run the
    sweep under one model, repoint the env var, resume, and every cached task is served as
    ``reused`` with the FIRST model's numbers relabelled as the second's. The identity must
    carry the RESOLVED model."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"

    monkeypatch.setenv("MEMBENCH_AGENT_MODEL", "model-a")
    first = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (first["executed"], first["reused"]) == (1, 0)

    same = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (same["executed"], same["reused"]) == (0, 1)  # unchanged model still resumes

    # `--model` is still "" — only the env var the agent actually resolves through moved.
    monkeypatch.setenv("MEMBENCH_AGENT_MODEL", "model-b")
    switched = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (switched["executed"], switched["reused"]) == (
        1,
        0,
    ), "served model-a's numbers as model-b"
    assert switched["per_task"][0]["model"] == "model-b"  # the RESOLVED model is recorded


def test_unknown_arm_or_channel_cache_is_a_miss(tmp_path: Path) -> None:
    """A row naming an arm/channel outside the grid (schema drift across a rename) is a miss."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["outcomes"][0]["arm"] = "some-renamed-arm"
    result_path.write_text(json.dumps(record), encoding="utf-8")

    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("passes", "0", id="passes-str"),
        pytest.param("runs", "2", id="runs-str"),
        pytest.param("passes", None, id="passes-null"),
        pytest.param("passes", 1.5, id="passes-float"),
        pytest.param("passes", True, id="passes-bool"),  # bool is an int subclass — must miss
        pytest.param("passes", -1, id="passes-negative"),
        pytest.param("passes", 99, id="passes-exceeds-runs"),  # would inflate the ceiling
        pytest.param("arm", 3, id="arm-not-str"),
    ],
)
def test_value_drifted_cache_row_is_a_miss_not_a_crash(
    tmp_path: Path, field: str, value: object
) -> None:
    """The miss-ladder must validate VALUES, not just structure. JSON carries no int/str
    distinction and ``ArmOutcome`` is a plain frozen dataclass that type-checks nothing, so
    ``{"passes": "0"}`` constructs fine and only detonates later inside ``task_verdict``
    (``passes > 0`` -> TypeError) — an unhandled exception escaping mid-resume and killing a
    paid sweep. A ``passes`` above ``runs`` is the quieter version: no crash, just a
    fabricated ceiling."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["outcomes"][0][field] = value
    result_path.write_text(json.dumps(record), encoding="utf-8")

    # must re-execute, and must not raise
    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0


@pytest.mark.parametrize(
    "cache_text",
    [
        pytest.param("[" * 20_000 + "]" * 20_000, id="deeply-nested-recursionerror"),
        pytest.param('{"repeats": ' + "1" * 6000 + "}", id="huge-int-literal-valueerror"),
    ],
)
def test_hostile_json_never_escapes_the_parse_boundary(tmp_path: Path, cache_text: str) -> None:
    """``json.loads`` raises more than JSONDecodeError. Deeply nested JSON raises
    RecursionError, and an integer literal over CPython's 4300-digit limit raises a bare
    ValueError. Neither was caught, so one corrupted cache file killed a resumed PAID sweep —
    the same "unhandled exception ends the sweep" failure this ladder exists to prevent."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{tasks[0].work_id}.json").write_text(cache_text, encoding="utf-8")
    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0


def test_cells_measured_at_another_repeat_count_are_a_miss(tmp_path: Path) -> None:
    """A cell's ``runs`` must equal the run's ``repeats``. Nothing else can produce it
    (``run_arm`` loops ``range(repeats)``), so a disagreeing record is corrupt. The degenerate
    ``runs=0`` case is the dangerous one: it satisfies ``0 <= passes <= runs``, keeps full grid
    coverage, and makes task_verdict read ``oracle 0/0`` and emit a confident
    "KILL: no separation" for a task that was never evaluated — counted as ``reused``."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    first = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert "SEPARATES" in first["per_task"][0]["verdict"]

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    for row in record["outcomes"]:  # zero every cell, leave `repeats` in the identity alone
        row["passes"], row["runs"] = 0, 0
    result_path.write_text(json.dumps(record), encoding="utf-8")

    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0
    verdict = summary["per_task"][0]["verdict"]
    assert "KILL" not in verdict and "SEPARATES" in verdict  # re-measured, not fabricated


def test_schema_drifted_cache_row_is_a_miss_not_a_typeerror(tmp_path: Path) -> None:
    """An ``outcomes`` row that is not a valid ``ArmOutcome`` kwargs mapping must miss, not
    blow up in ``ArmOutcome(**row)`` outside the guard."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    record["outcomes"] = [{"arm": "none", "unexpected": 1} for _ in record["outcomes"]]
    result_path.write_text(json.dumps(record), encoding="utf-8")

    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert summary["executed"] == 1 and summary["reused"] == 0


def test_empty_ours_payload_is_none_equivalent_and_never_spends(
    tmp_path: Path, monkeypatch
) -> None:
    """Temporal LOO admits no priors for the lifecycle-EARLIEST task, so its ``ours`` payload
    is empty by construction. Spending paid ``claude -p`` on it buys a guaranteed non-pass and
    makes ``(ours 0/N)`` unattributable — retrieval miss, or memory did not help? Follow the
    ``run_grid_3arm`` convention: reuse the ``none`` cell (delta exactly 0), flag
    ``ours_retrieval_empty``, and spend nothing."""
    sequences, tasks = _corpus_one(tmp_path)
    seen: list[str] = []

    def _spy_run_arm(*, arm: str, channel, repeats: int, **_kwargs: Any) -> driver.ArmOutcome:
        seen.append(arm)  # a real paid cell would spawn `claude -p` here
        return driver.ArmOutcome(
            arm=arm, channel=channel.value, passes=repeats if arm == "oracle" else 0, runs=repeats
        )

    monkeypatch.setattr(driver, "run_arm", _spy_run_arm)
    summary = driver.run_corpus(
        tasks,
        sequences,
        out_dir=tmp_path / "out",
        repeats=2,
        model="sonnet",
        dry_run=False,  # PAID path — the one that must not burn a turn on an empty payload
        store_path=tmp_path / "store.db",
        seed_fn=_no_ours_payload,  # resolves to {} -> empty payload for every task
    )

    assert "ours" not in seen, "spent a paid run on an empty `ours` payload"
    record = summary["per_task"][0]
    assert record["ours_retrieval_empty"] is True
    by = {(o["arm"], o["channel"]): o for o in record["outcomes"]}
    assert len(by) == len(driver.ARMS) * len(driver.CHANNELS)  # ours still reported, not dropped
    for channel in driver.CHANNELS:
        ours, none = by[("ours", channel.value)], by[("none", channel.value)]
        assert (ours["passes"], ours["runs"]) == (none["passes"], none["runs"])


def test_non_empty_ours_payload_still_spends(tmp_path: Path, monkeypatch) -> None:
    """The guard above must be narrow: a task WITH a resolved payload still runs its own
    `ours` cell (otherwise the arm could never score above `none`)."""
    sequences, tasks = _corpus_one(tmp_path)
    seen: list[str] = []

    def _spy_run_arm(*, arm: str, channel, repeats: int, **_kwargs: Any) -> driver.ArmOutcome:
        seen.append(arm)
        return driver.ArmOutcome(arm=arm, channel=channel.value, passes=0, runs=repeats)

    def _payload(*_args: object) -> dict[str, dict[str, str]]:
        return {tasks[0].work_id: {"lesson-1": "the retention window is TOKEN-abc"}}

    monkeypatch.setattr(driver, "run_arm", _spy_run_arm)
    summary = driver.run_corpus(
        tasks,
        sequences,
        out_dir=tmp_path / "out",
        repeats=2,
        model="sonnet",
        dry_run=False,
        store_path=tmp_path / "store.db",
        seed_fn=_payload,
    )
    assert seen.count("ours") == len(driver.CHANNELS)
    assert summary["per_task"][0]["ours_retrieval_empty"] is False


def test_driver_refuses_to_spend_without_token(tmp_path: Path, monkeypatch) -> None:
    # The corpus-wide spend guard (financial-safety branch): no token + no --dry-run -> exit 2
    # and never spawn claude, same contract the probe's gate is tested for. The spend gate
    # fires in main() BEFORE run_corpus/seed_fn ever runs, so this needs no built bin/mem.
    _corpus_one(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    from membench.runner import realagent_probe as _rp

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("must NOT spawn claude when the spend gate fires")

    monkeypatch.setattr(_rp.subprocess, "run", _boom)
    code = driver.main(["--corpus-dir", str(tmp_path / "corpus"), "--out", str(tmp_path / "out")])
    assert code == 2


def test_corpus_walk_orders_seeds_numerically(tmp_path: Path) -> None:
    # 10 must follow 2, not precede it (lexical would misorder past nine seeds).
    for seed in (0, 2, 10):
        seed_dir = tmp_path / str(seed)
        seed_dir.mkdir()
        (seed_dir / "sequences.json").write_text(
            json.dumps([_toolreq_seq(f"w-{seed}").model_dump()]), encoding="utf-8"
        )
    _, tasks = load_corpus_with_sequences(tmp_path)
    order = [t.work_id for t in tasks]
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


# --- ours seeding (real mem CLI, skips like test_pipeline_e2e when the TS build is absent)


def test_ours_seeding_retrieves_cross_task_never_leaks_own_id_or_realistic_value(
    tmp_path: Path,
) -> None:
    """The bead's acceptance, against a real built store: seeding two sequences and
    resolving the `ours` payload for the later one fires a genuine cross-task retrieval
    (surfaces the earlier sequence's lesson), never returns the queried task's own id
    (same_rig_temporal excludes self by construction), and never states a realistic
    (non-opaque) value — only sequence_lessons_opaque's opaque content ever rides in."""
    require_mem_cli(DIST_MAIN)

    seqs = [_toolreq_seq("w-0"), _toolreq_seq("w-1")]
    tasks = [adapt_sequence(seq) for seq in seqs]
    payloads = driver.seed_ours_store_and_resolve_payloads(
        seqs, tasks, tmp_path / "store.db", str(MEM_BIN)
    )

    held = "w-1"
    assert held in payloads
    injected = payloads[held]
    assert injected, "retrieval never fired cross-task — the store seeded nothing usable"
    assert held not in injected  # never its own id
    joined = " ".join(injected.values())
    assert not states_value(joined, CURRENT)
    assert not states_value(joined, STALE)


def test_reseeding_a_store_never_surfaces_the_previous_corpus(tmp_path: Path) -> None:
    """Seeding must be genuinely FRESH, as the docstring claims. ``lessons`` is append-only,
    so importing into a store left behind by an EARLIER corpus keeps that corpus's opaque
    tokens alive; ``resolve_payloads`` then renders those dead tokens into the cross-task
    payload of every task, silently contaminating the paid ``ours`` measurement with values
    that are no longer in the world. Regenerate the corpus, re-seed the same ``--out``, and
    only the current corpus may surface."""
    require_mem_cli(DIST_MAIN)
    store_path = tmp_path / "store.db"

    old = [
        _toolreq_seq("w-0", current="11 days", stale="12 days"),
        _toolreq_seq("w-1", current="13 days", stale="14 days"),
    ]
    new = [
        _toolreq_seq("w-0", current="21 days", stale="22 days"),
        _toolreq_seq("w-1", current="23 days", stale="24 days"),
    ]
    old_tasks = [adapt_sequence(seq) for seq in old]
    new_tasks = [adapt_sequence(seq) for seq in new]

    driver.seed_ours_store_and_resolve_payloads(old, old_tasks, store_path, str(MEM_BIN))
    payloads = driver.seed_ours_store_and_resolve_payloads(new, new_tasks, store_path, str(MEM_BIN))

    # w-1 holds out its own lesson, so what it sees cross-task is w-0's — which corpus's?
    surfaced = " ".join(payloads["w-1"].values())
    assert surfaced, "cross-task retrieval never fired"
    dead = set(old_tasks[0].current_opaque_values)
    live = set(new_tasks[0].current_opaque_values)
    assert dead and live and not (dead & live)  # the two corpora share no token
    assert not [tok for tok in dead if tok in surfaced], "stale corpus leaked into the payload"
    assert [tok for tok in live if tok in surfaced], "current corpus never surfaced"
