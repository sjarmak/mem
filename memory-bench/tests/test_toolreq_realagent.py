"""mem-rk41.3 — the synthetic tool-requiring world -> real-agent task adapter + grid.

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
from pydantic import ValidationError

from membench.metrics.scorers import states_value
from membench.runner import toolreq_grid as grid
from membench.runner.headless_agent import Leg, render_cell_calls
from membench.runner.resume_cache import invocation_digest, load_cached
from membench.runner.toolreq_realagent import (
    ToolReqRealAgentTask,
    _reset_store,
    adapt_sequence,
    load_corpus_with_sequences,
    seed_ours_store_and_resolve_payloads,
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
    _sequences: object, _tasks: object, _store_path: object, _mem_bin: object
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
    """grid.run_corpus with the hermetic test wiring (repeats=2, no model, stubbed
    seed_fn) — only what a test actually varies rides in its call.

    mem_bin is handed to seed_fn and nowhere else, so the stubbed seeder never touches it;
    it is not part of the run identity (the resolved PAYLOAD is — see payload_fingerprint)."""
    return grid.run_corpus(
        tasks,
        sequences,
        out_dir=out,
        repeats=2,
        model="",
        dry_run=dry_run,
        store_path=store_path,
        mem_bin=str(MEM_BIN),
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


def _goal_step_dict_fields(task: ToolReqRealAgentTask) -> dict[str, dict[str, Any]]:
    """Every field of ``goal_step`` whose dumped value is a dict — i.e. every field whose
    keys are DATA, and whose order ``task_fingerprint`` therefore erases.

    Derived from the dump rather than named, for the reason ``task_fingerprint`` hashes
    ``goal_step`` whole: a hardcoded roster is one field short the day SequenceStep grows a
    fourth. TOP-LEVEL ONLY, and that depth is forced: sub-models dump to field-name-keyed
    dicts, so a recursive walk would flag every ``outcome_checks`` entry — whose key order
    is pydantic's declaration order, the safe case digest() sorts on purpose. The residual
    gap is a data dict added INSIDE OutcomeCheck/ExpectedAction: nested in a list, invisible
    here, and a live construction site (adapt_sequence populates outcome_checks). Both carry
    scalars and lists only today, so the exposure is currently zero."""
    return {
        name: value
        for name, value in task.goal_step.model_dump(mode="json").items()
        if isinstance(value, dict)
    }


def test_task_fingerprint_cannot_see_key_order_in_a_goal_step_dict_field() -> None:
    """The hazard the guard below exists for, shown on the concrete task type.

    ``digest`` is canonical JSON: object keys SORT, list order is KEPT
    (``test_resume_cache.test_the_digest_sorts_mapping_keys_but_never_reorders_a_list``).
    That contract is safe only while every input whose ORDER is measured reaches it as a
    LIST — which ``task_fingerprint`` honours for ``oracle_memory`` (hashed as ``.items()``)
    but cannot honour for a dict nested inside ``goal_step.model_dump()``. So two goal_steps
    differing ONLY in one dict field's key order fingerprint IDENTICALLY, and nothing else
    would catch it: those fields never reach the argv, so ``invocation_fingerprint`` is
    blind to them entirely. ``task_fingerprint`` is the only place they could register, and
    it is the place that sorts them. Content moves it; order does not.

    Asserted at the ``task_fingerprint`` level, not the digest level: that fact is already
    pinned in test_resume_cache, and it is the CONSEQUENCE here that matters. If this goes
    RED the fingerprint was taught to preserve these fields' order (the bead's second
    remedy) — at which point the guard below is dead and should go."""
    task = adapt_sequence(_toolreq_seq())
    dict_fields = _goal_step_dict_fields(task)
    # else an emptied roster makes every assertion below vacuous and this test green
    assert dict_fields, "no dict-valued field on goal_step — the hazard below is unprovable"
    for field in dict_fields:
        forward = dataclasses.replace(
            task, goal_step=task.goal_step.model_copy(update={field: {"k-a": "A", "k-b": "B"}})
        )
        reversed_order = dataclasses.replace(
            task, goal_step=task.goal_step.model_copy(update={field: {"k-b": "B", "k-a": "A"}})
        )
        # same content, different order — the fingerprint sees no difference
        assert getattr(forward.goal_step, field) == getattr(reversed_order.goal_step, field)
        assert grid.task_fingerprint(forward) == grid.task_fingerprint(reversed_order), (
            f"goal_step.{field} key order became visible to task_fingerprint — if that was "
            "deliberate, delete the guard test below; it is now dead"
        )

    # The contrast that makes the above a design and not an oversight: a LIST field's order
    # is preserved, because a list is how digest() is told an order is a measured input.
    reads_fwd = dataclasses.replace(
        task, goal_step=task.goal_step.model_copy(update={"expected_memory_reads": ["a", "b"]})
    )
    reads_rev = dataclasses.replace(
        task, goal_step=task.goal_step.model_copy(update={"expected_memory_reads": ["b", "a"]})
    )
    assert grid.task_fingerprint(reads_fwd) != grid.task_fingerprint(reads_rev)


def test_adapt_sequence_populates_no_goal_step_dict_field() -> None:
    """The guard, and the reason the collision above is survivable: ``adapt_sequence`` leaves
    every data dict on the goal_step EMPTY, so there is no key order to lose.

    Not a live defect — a landmine, pinned so it cannot become one silently. Nothing on this
    path reads these fields (``build_agent_prompt`` reads ``step.user_request``,
    ``score_goal_action`` reads ``step.outcome_checks``, ``realagent_probe`` mentions none of
    them), so two colliding records render byte-identical prompts and score identically: the
    cache hit is CORRECT. It arms the moment a change sources prompt text or scoring input
    from one of them — ``distractor_memories`` is the near one, since the runner SEEDS those
    into the store before a step's retrieve (the §10 Confusion axis, exactly what a future
    toolreq arm would want). From then the sorted-key digest silently collides two DIFFERENT
    measured inputs and a resumed PAID run serves one prompt's numbers as the other's: the
    identity-hashes-a-model-one-field-short family this grid exists to prevent, re-entered
    by the back door.

    Caught in CI rather than fixed in ``task_fingerprint`` because that fix is a no-op that
    is not free: all three fields are empty and ``canonicalize({})`` is ``"{}"``, so hashing
    them as item lists changes no behavior while moving every fingerprint — a full PAID
    re-spend for nothing. That call belongs to the moment a field with real semantics exists
    to make it for."""
    task = adapt_sequence(_toolreq_seq())
    dict_fields = _goal_step_dict_fields(task)
    # the enumeration must actually be finding the fields it claims to guard
    assert set(dict_fields) == {
        "environment_state",
        "expected_memory_writes",
        "distractor_memories",
    }, "SequenceStep's data-dict roster moved — confirm the new field is order-irrelevant"

    populated = {name: value for name, value in dict_fields.items() if value}
    assert not populated, (
        f"adapt_sequence now populates goal_step.{sorted(populated)} — task_fingerprint "
        "hashes goal_step via canonical JSON, which SORTS object keys, so two goal_steps "
        "differing only in this field's key order now fingerprint identically and a resumed "
        "PAID run can serve one prompt's numbers as the other's. Either keep the field out "
        "of the goal_step, or make task_fingerprint hash it as a LIST of items (which "
        "invalidates the persisted cache — free before the paid run, not after)."
    )


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
    outcomes = grid.evaluate_task(task, repeats=2, model="", dry_run=True).outcomes
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in grid.CHANNELS):
        assert by[("none", channel)].passes == 0
        assert by[("oracle", channel)].passes == by[("oracle", channel)].runs == 2
        # No ours_payload injected -> `ours` behaves like `none` (empty surfaced memory).
        assert by[("ours", channel)].passes == 0


def test_ours_arm_dry_run_passes_when_payload_states_current_value() -> None:
    task = adapt_sequence(_toolreq_seq())
    (current_opaque,) = task.current_opaque_values
    ours_payload = {"w-prior": f"the retention window is {current_opaque}"}
    outcomes = grid.evaluate_task(
        task, repeats=2, model="", dry_run=True, ours_payload=ours_payload
    ).outcomes
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in grid.CHANNELS):
        assert by[("ours", channel)].passes == by[("ours", channel)].runs == 2


def test_ours_arm_dry_run_honest_non_pass_when_payload_lacks_current_value() -> None:
    # The expected, honest substrate finding: a payload that never states the queried
    # task's own sequence-unique opaque value cannot make the simulated agent pass.
    task = adapt_sequence(_toolreq_seq())
    ours_payload = {"w-prior": "an unrelated retrieved fact about something else entirely"}
    outcomes = grid.evaluate_task(
        task, repeats=2, model="", dry_run=True, ours_payload=ours_payload
    ).outcomes
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in grid.CHANNELS):
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

    # Re-run: the persisted result is reused, nothing re-executed — no PAID work repeats.
    # The seed DOES run again (n == 2), deliberately. Seeding and resolving are free, and the
    # resolved `ours` payload is part of the cache identity, so it must be recomputed to know
    # whether a cached cell is still current. An earlier revision skipped the seed on a
    # fully-cached resume; that is precisely what made a stale `ours` payload (from a grown
    # corpus or a rebuilt bin/mem) invisible and reusable.
    second = _run_corpus(
        tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_counting_seed
    )
    assert second["executed"] == 0 and second["reused"] == 1
    assert seed_calls["n"] == 2


def _corpus_one(
    tmp_path: Path, work_id: str = "w-0"
) -> tuple[list[BenchmarkSequence], list[ToolReqRealAgentTask]]:
    corpus = tmp_path / "corpus"
    (corpus / "0").mkdir(parents=True)
    (corpus / "0" / "sequences.json").write_text(
        json.dumps([_toolreq_seq(work_id).model_dump()]), encoding="utf-8"
    )
    return load_corpus_with_sequences(corpus)


def _spy_run_arm(seen: list[str], *, oracle_passes: bool) -> Callable[..., Any]:
    """A ``run_arm`` double that records which arms were RUN instead of spawning ``claude -p``.

    It reports the invocations the PLAN declares, for the model it was HANDED — a double that
    reported anything else is refused at the cache's write boundary, which is that check doing its
    job. ``oracle_passes`` is the only thing the callers differ on."""

    def _run_arm(*, arm: str, channel, repeats: int, step, memory, model: str, **_kwargs: Any):
        seen.append(arm)  # a real paid cell would spawn `claude -p` here
        outcome = grid.ArmOutcome(
            arm=arm,
            channel=channel.value,
            passes=repeats if (oracle_passes and arm == "oracle") else 0,
            runs=repeats,
        )
        sent = render_cell_calls(
            arm=arm, channel=channel, legs=[Leg("goal", step, memory)], model=model
        )
        return outcome, sent

    return _run_arm


def test_dry_run_cache_never_satisfies_a_paid_run(tmp_path: Path, monkeypatch) -> None:
    # The highest-severity confound: a FREE dry-run's simulated result must NOT be reused by
    # a PAID run over the same --out. Prove the paid run re-executes (spied, so no real spend).
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)

    calls = {"n": 0}
    real_eval = grid.evaluate_task

    def _spy(task, **_kwargs):
        calls["n"] += 1
        return real_eval(task, repeats=2, model="", dry_run=True)  # simulate, never spend

    monkeypatch.setattr(grid, "evaluate_task", _spy)
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


_Rows = list[dict[str, Any]]


@pytest.mark.parametrize(
    ("mutate", "keeps_arity", "keeps_coverage", "why"),
    [
        pytest.param(
            lambda rows: rows[:1],
            False,
            False,
            "a TRUNCATED grid (schema drift, or a kill mid-write that still renamed) scores the "
            "task on a subset of its cells, and separates_all_channels reads a partial run as a "
            "full one — the silent-cap class the rig forbids",
            id="truncated-grid",
        ),
        pytest.param(
            lambda rows: [dict(rows[0]) for _ in rows],
            True,
            False,
            "counting rows is NOT enough: len(ARMS)*len(CHANNELS) copies of ONE cell has the "
            "right arity and covers nothing. task_verdict keys by (arm, channel), so the record "
            "yields an EMPTY verdict and the task vanishes from the separates/leaked accounting "
            "while still counting as reused",
            id="duplicate-cell-right-arity",
        ),
        pytest.param(
            lambda rows: [*rows, {**next(r for r in rows if r["arm"] == "oracle"), "passes": 0}],
            False,
            True,
            "the mirror case, which a set-only check misses: the correct rows PLUS one extra "
            "duplicate still cover the grid AS A SET, and task_verdict lets the LAST row for a "
            "key win — an appended duplicate silently overwrites the real measurement, rewriting "
            "a genuine SEPARATES into a fabricated KILL. Arity and coverage must BOTH be "
            "checked; neither subsumes the other",
            id="extra-duplicate-row-grid-covered",
        ),
        pytest.param(
            lambda rows: [{**rows[0], "arm": "some-renamed-arm"}, *rows[1:]],
            True,
            False,
            "a row naming an arm outside the grid (schema drift across a rename) keeps the arity "
            "and breaks only the coverage, so the set check is the one that has to catch it",
            id="unknown-arm",
        ),
        pytest.param(
            lambda rows: [{**row, "passes": 0, "runs": 0} for row in rows],
            True,
            True,
            "runs=0 keeps BOTH arity and coverage, but never reaches the runs == repeats grid "
            "check: the row schema itself (BaseCellOutcome, runs >= 1) refuses a zeroed row at "
            "the parse boundary. Kept because acceptance would read oracle 0/0 as a confident "
            "'KILL: no separation' for a task that was never evaluated; the runs == repeats "
            "conjunct is uniquely exercised by the dedicated forged-repeat-count test below",
            id="zeroed-runs",
        ),
        pytest.param(
            lambda rows: [{"arm": "none", "unexpected": 1} for _ in rows],
            True,
            False,
            "a row that is not a valid ArmOutcome kwargs mapping must MISS, not blow up inside "
            "ArmOutcome(**row) outside the guard",
            id="schema-drift",
        ),
    ],
)
def test_a_mutated_cache_record_is_a_miss_and_is_re_measured(
    tmp_path: Path,
    mutate: Callable[[_Rows], _Rows],
    keeps_arity: bool,
    keeps_coverage: bool,
    why: str,
) -> None:
    """Every way a persisted record can be corrupt, degenerate, or forged must be a MISS that
    re-executes — never an acceptance that publishes a number nobody measured.

    ``keeps_arity`` / ``keeps_coverage`` pin what each mutation deliberately PRESERVES, so a case
    cannot silently stop being adversarial in the way it claims: the rows that keep both are
    exactly the ones no structural check can catch, and they prove the value checks are the
    guard actually doing the work."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    first = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert "SEPARATES" in first["per_task"][0]["verdict"]

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    full = len(record["outcomes"])
    assert full == len(grid.ARMS) * len(grid.CHANNELS)

    mutated = mutate(record["outcomes"])
    assert (len(mutated) == full) is keeps_arity, why
    cells = {(row.get("arm"), row.get("channel")) for row in mutated}
    assert (cells == grid.expected_cells()) is keeps_coverage, why
    record["outcomes"] = mutated
    result_path.write_text(json.dumps(record), encoding="utf-8")

    summary = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert (summary["executed"], summary["reused"]) == (1, 0), why
    # and the re-executed task is fully re-measured, not scored on the mutated remains
    re_measured = summary["per_task"][0]
    assert len(re_measured["outcomes"]) == full
    assert {(o["arm"], o["channel"]) for o in re_measured["outcomes"]} == grid.expected_cells()
    assert "KILL" not in re_measured["verdict"] and "SEPARATES" in re_measured["verdict"]
    assert summary["separates_all_channels"] == 1


def test_a_record_forged_at_a_different_repeat_count_is_a_miss(tmp_path: Path) -> None:
    """The one mutation the runs == repeats grid check ALONE stands against: rows scaled to a
    different repeat count, with runs > 0.

    Every guard the zeroed-runs case leans on is deliberately satisfied here — each row alone is
    schema-valid (runs >= 1, passes <= runs), arity and coverage are kept, the stored verdict is
    recomputed to the one the FORGED rows imply (classify embeds passes/runs in the line, so an
    un-recomputed verdict would trip the derived-verdict check first), the identity is untouched,
    and uniform scaling keeps each ``ours`` row equal to its ``none`` row, so the
    empty-retrieval-parity validator stays satisfied under this corpus's stubbed seed_fn. Delete
    the ``cell.runs != identity.repeats`` conjunct in
    ``_rows_are_a_complete_grid_measured_at_this_identity`` and this record is ACCEPTED: a grid
    nobody measured at this run's repeats, served as reused at executed=0."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    first = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert "SEPARATES" in first["per_task"][0]["verdict"]

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    forged = [
        {**row, "passes": row["passes"] * 2, "runs": row["runs"] * 2} for row in record["outcomes"]
    ]
    # Pin what the forgery deliberately PRESERVES, so the case cannot silently stop being
    # adversarial in the way it claims: every row constructs, the grid is covered exactly, and
    # the verdict is the forged rows' own.
    cells = [grid.CellOutcome(**row) for row in forged]
    assert {(c.arm, c.channel) for c in cells} == grid.expected_cells()
    record["outcomes"] = forged
    record["verdict"] = grid.task_verdict(cells)
    result_path.write_text(json.dumps(record), encoding="utf-8")

    resumed = _run_corpus(tasks, sequences, out, dry_run=True, store_path=tmp_path / "store.db")
    assert (resumed["executed"], resumed["reused"]) == (
        1,
        0,
    ), "a grid measured at runs=4 was served for a repeats=2 identity"
    re_measured = resumed["per_task"][0]
    assert all(o["runs"] == 2 for o in re_measured["outcomes"])
    assert "SEPARATES" in re_measured["verdict"]


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
    assert grid.task_fingerprint(tasks_b[0]) != grid.task_fingerprint(tasks_a[0])

    second = _run_corpus(tasks_b, seqs_b, out, dry_run=True, store_path=store_path)
    assert (second["executed"], second["reused"]) == (1, 0), "reused another world's result"


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

    assert grid.task_fingerprint(drifted) != grid.task_fingerprint(task)


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
    # the RESOLVED model is recorded (in the nested identity)
    assert switched["per_task"][0]["identity"]["model"] == "model-b"


def test_a_changed_ours_payload_invalidates_the_cache(tmp_path: Path) -> None:
    """The `ours` payload is the arm's MEASURED INPUT and comes from CROSS-TASK retrieval, so it
    moves when a sibling is added to the corpus, when the corpus is regenerated, or when
    bin/mem's retrieval changes — none of which touch the queried task's own fields, so
    task_fingerprint cannot see any of them. Hashing the resolved payload covers all three at
    once: a different injected context is a different measurement and must not be reused."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    work_id = tasks[0].work_id

    def _payload_v1(*_a: object) -> dict[str, dict[str, str]]:
        return {work_id: {"w-prior": "lesson: the value is TOKEN-aaa"}}

    def _payload_v2(*_a: object) -> dict[str, dict[str, str]]:
        # same task, same corpus fields — retrieval now surfaces DIFFERENT text
        return {work_id: {"w-prior": "lesson: the value is TOKEN-bbb"}}

    first = _run_corpus(
        tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_payload_v1
    )
    assert (first["executed"], first["reused"]) == (1, 0)

    same = _run_corpus(
        tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_payload_v1
    )
    assert (same["executed"], same["reused"]) == (0, 1)  # unchanged payload still resumes

    changed = _run_corpus(
        tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_payload_v2
    )
    assert (changed["executed"], changed["reused"]) == (1, 0), "reused a stale ours measurement"


def test_reordered_ours_payload_invalidates_the_cache(tmp_path: Path) -> None:
    """The `ours` payload's ORDER is a measured input, not incidental. resolve_payloads builds
    the dict from `mem retrieve`'s RANKED items and build_agent_prompt renders the memory block
    by iterating available_memory.items(), so insertion order IS the order the agent reads the
    lessons in. Retrieval can return the same SET in a different ORDER (a reseed moving an FTS
    tiebreak, a ranking change in bin/mem) — a different prompt, so a different measurement. A
    sorted fingerprint would call the two runs identical and serve the stale one."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    work_id = tasks[0].work_id

    def _rank_x(*_a: object) -> dict[str, dict[str, str]]:
        return {work_id: {"w-a": "fact A", "w-b": "fact B"}}

    def _rank_y(*_a: object) -> dict[str, dict[str, str]]:
        # identical CONTENT, retrieval ranked them the other way round
        return {work_id: {"w-b": "fact B", "w-a": "fact A"}}

    assert grid.payload_fingerprint({"w-a": "A", "w-b": "B"}) != grid.payload_fingerprint(
        {"w-b": "B", "w-a": "A"}
    )

    first = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_rank_x)
    assert (first["executed"], first["reused"]) == (1, 0)

    same = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_rank_x)
    assert (same["executed"], same["reused"]) == (0, 1)  # unchanged rank order still resumes

    flipped = _run_corpus(
        tasks, sequences, out, dry_run=True, store_path=store_path, seed_fn=_rank_y
    )
    assert (flipped["executed"], flipped["reused"]) == (
        1,
        0,
    ), "reused a measurement taken against a differently-ordered prompt"


def test_reordered_oracle_memory_invalidates_the_cache(tmp_path: Path) -> None:
    """Same hole, one field over, and on the arm that defines the CEILING the whole verdict is
    read against. arm_memories hands `oracle` a dict(task.oracle_memory), which build_agent_prompt
    renders in insertion order — so reordering oracle_memory without changing its content (an
    ordinary edit to how adapt_sequence builds the dict) changes the oracle prompt. Sorting it
    inside task_fingerprint made that change invisible and served the pre-change ceiling."""
    _, tasks = _corpus_one(tmp_path)
    task = tasks[0]
    forward = dict(task.oracle_memory)
    if len(forward) < 2:  # the fixture must actually be able to express an order
        forward = {"m-1": "the current value is X", "m-2": "the retention window applies"}
    reversed_order = dict(reversed(list(forward.items())))

    assert list(forward) != list(reversed_order)  # same content, different order
    assert forward == reversed_order
    assert grid.task_fingerprint(
        dataclasses.replace(task, oracle_memory=forward)
    ) != grid.task_fingerprint(
        dataclasses.replace(task, oracle_memory=reversed_order)
    ), "the ceiling arm's prompt changed but the fingerprint did not"


def test_a_short_grid_under_the_empty_flag_is_a_miss_not_an_escaping_keyerror(
    tmp_path: Path,
) -> None:
    """A TRUNCATED record that also carries `ours_retrieval_empty` must be a clean MISS — and, the
    part worth a test of its own, it must fail as a ValidationError and nothing else.

    `_empty_retrieval_flag_agrees...` reads the very (arm, channel) rows the file is missing. Were
    it to index them blindly it would raise KeyError, which is NOT a ValidationError: it would sail
    past `load_cached`'s handler and kill a PAID resume on one bad file. Every other check here can
    only make a record MISS; this is the one that could make the whole sweep DIE, so the validator
    is total over its input rather than trusting the completeness check to have run first."""
    identity = grid.RunIdentity(
        repeats=2,
        dry_run=True,
        model="",
        arms=list(grid.ARMS),
        protocol=grid.EXECUTION_PROTOCOL,
        task_fingerprint="fp-task",
        invocation_fingerprint="fp-invocation",
        ours_payload_fingerprint="fp-payload",
        ours_retrieval_empty=True,  # the flag the subclass validator keys on...
    )
    # ...over a grid missing the very `ours` rows it would index.
    rows = [grid.CellOutcome(arm="none", channel="recalled", passes=0, runs=2)]
    result_path = tmp_path / "w-0.json"
    result_path.write_text(
        json.dumps(
            {
                "work_id": "w-0",
                "identity": json.loads(identity.model_dump_json()),
                "outcomes": [json.loads(row.model_dump_json()) for row in rows],
                "verdict": "anything",
            }
        ),
        encoding="utf-8",
    )
    assert load_cached(result_path, identity, grid.CachedResult) is None


def test_empty_flag_contradicting_the_ours_rows_is_a_miss(tmp_path: Path) -> None:
    """`ours_retrieval_empty` claims the ours cell was NEVER RUN — it was relabeled from `none`,
    so it is none-equal by construction. A file that sets the flag while carrying an ours row
    that DIFFERS from its channel's none row is self-contradictory: the flag is the denominator
    that makes a flat (ours 0/N) attributable, the rows are the measurement, and here they
    disagree. Ordinary runs cannot produce it (both derive from the same payload object), so this
    is a corrupted/hand-edited file — the exact input class every other check here rejects."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text())
    # w-0 is lifecycle-earliest: LOO gives no priors
    assert record["identity"]["ours_retrieval_empty"] is True

    # forge a spent-looking ours measurement while keeping the never-ran flag
    for row in record["outcomes"]:
        if row["arm"] == "ours":
            row["passes"] = row["runs"]
    result_path.write_text(json.dumps(record))

    # the file's OWN identity, unchanged — so the miss is the rows/flag contradiction
    # (CachedResult's cross-row validator), not an identity mismatch
    identity = grid.RunIdentity(**record["identity"])
    assert (
        load_cached(result_path, identity, grid.CachedResult) is None
    ), "accepted a fabricated ours cell"


def test_a_cached_identity_carrying_an_unknown_field_is_a_miss(tmp_path: Path) -> None:
    """THE subset hole, and the reason the identity is a model instead of a dict.

    The predecessor accepted a file whose identity matched as a SUBSET:
    ``any(loaded.get(k) != v for k, v in identity.items())`` walks only the keys THIS reader
    thought to enumerate. A record written by a NEWER binary — one that measures under a
    dimension this reader has never heard of (say a decoding temperature, or a second channel)
    — agrees on every field they share and is served as ``reused``. The reader publishes a number
    measured under conditions it cannot even name.

    ``extra="forbid"`` + whole-object ``==`` turns that into a miss, which is the fail-safe
    direction: a miss re-measures (costing a run), an acceptance publishes someone else's."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    first = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (first["executed"], first["reused"]) == (1, 0)

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    # every field this reader KNOWS about is untouched and still agrees — the file differs only
    # by a dimension it has never heard of, which is exactly what a subset walk cannot see
    record["identity"]["temperature"] = 0.7
    result_path.write_text(json.dumps(record), encoding="utf-8")

    resumed = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (resumed["executed"], resumed["reused"]) == (
        1,
        0,
    ), "served a result measured under an identity dimension the reader cannot name"


def test_a_cached_record_in_the_old_spread_identity_shape_is_a_miss(tmp_path: Path) -> None:
    """The migration case: files already on disk from the predecessor spread the identity across
    the record's top level instead of nesting it. Nesting is what makes acceptance a whole-object
    ``==`` rather than a field-by-field walk, so the old shape must MISS and be re-measured — and
    it must do so as a cache miss, not as an unhandled ValidationError escaping mid-resume and
    killing a paid sweep."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    # rewrite it the way the predecessor wrote it: identity spread, no nested key
    legacy = {k: v for k, v in record.items() if k != "identity"} | record["identity"]
    assert "identity" not in legacy and legacy["model"] == ""  # genuinely the old shape
    result_path.write_text(json.dumps(legacy), encoding="utf-8")

    resumed = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (resumed["executed"], resumed["reused"]) == (1, 0), "accepted a legacy-shaped record"
    # and the re-measured file is written back in the current shape
    rewritten = json.loads(result_path.read_text(encoding="utf-8"))
    assert "identity" in rewritten and "model" not in rewritten


def test_a_forged_verdict_is_a_miss(tmp_path: Path) -> None:
    """The verdict is DERIVED from the rows, so a persisted one may only be the one they imply.

    A file whose rows say KILL and whose verdict string says SEPARATES agrees with this run on every
    other field — identity intact, grid complete, flag consistent — so nothing else in the record
    can refuse it. The summary is the second lock: it counts KINDS re-derived from the rows
    (`CachedResult.kinds`), never substrings of the stored verdict, so a forgery that got past the
    validator still could not reach the headline."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text(encoding="utf-8"))
    assert "SEPARATES" in record["verdict"]  # the rows genuinely separate

    record["verdict"] = "[recalled] LEAK: none 3/3 — value reached the prompt"
    result_path.write_text(json.dumps(record), encoding="utf-8")

    identity = grid.RunIdentity(**record["identity"])
    assert (
        load_cached(result_path, identity, grid.CachedResult) is None
    ), "accepted a verdict its rows deny"

    resumed = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (resumed["executed"], resumed["reused"]) == (1, 0)
    assert resumed["leaked"] == [], "a forged verdict reached the summary"


def test_a_cache_hit_does_not_rewrite_the_result_file(tmp_path: Path) -> None:
    """A reused record is republished by nobody: it already passed every validator, so rewriting it
    could only reproduce it. That keeps a fully cache-served resume at ZERO writes, which is what
    lets the result files' mtimes say which tasks a run actually measured — and it is only sound
    because the verdict is now checked rather than silently recomputed on the way back out."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)

    result_path = out / f"{tasks[0].work_id}.json"
    before = result_path.stat().st_mtime_ns

    resumed = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (resumed["executed"], resumed["reused"]) == (0, 1)
    assert result_path.stat().st_mtime_ns == before, "rewrote a result it had just reused"


def test_cell_outcome_mirrors_every_arm_outcome_field() -> None:
    """`CachedResult.of` builds each row as `CellOutcome(**asdict(arm_outcome))` and CellOutcome is
    `extra="forbid"`, so a field added to ArmOutcome raises ValidationError — at PERSIST time,
    after the paid cells are already spent, mid-sweep. Catch the drift in CI instead."""
    assert {f.name for f in dataclasses.fields(grid.ArmOutcome)} == set(
        grid.CellOutcome.model_fields
    )


def test_repeats_zero_is_refused_and_never_caches_a_0_of_0_verdict(tmp_path: Path) -> None:
    """--repeats 0 runs zero agent turns and persists six 0/0 rows, which task_verdict reads as a
    confident 'KILL: oracle ceiling 0/0 -- no separation' for a task that was NEVER EVALUATED.
    The predecessor's `runs == repeats` guard was VACUOUSLY TRUE at 0, so the file cached clean and
    every resume served it as `reused`. This is the same 0/0 hole an earlier fix believed it had
    closed -- it only closed it for repeats >= 1."""
    # the argument gate refuses it outright
    with pytest.raises(SystemExit):
        driver.main(["--repeats", "0", "--dry-run", "--corpus-dir", str(tmp_path)])

    # and the parse boundary refuses a 0/0 row even if one reaches disk -- the survivor of the
    # deleted `_valid_cell` ladder is CellOutcome.runs (ge=1), which rejects the same input
    # structurally instead of by hand-written guard
    with pytest.raises(ValidationError):
        grid.CellOutcome(arm="oracle", channel="recalled", passes=0, runs=0)
    # ... and the identity cannot even claim repeats=0 (RunIdentity.repeats, ge=1)
    with pytest.raises(ValidationError):
        grid.RunIdentity(
            repeats=0,
            dry_run=True,
            model="",
            arms=list(grid.ARMS),
            protocol=grid.EXECUTION_PROTOCOL,
            task_fingerprint="x",
            ours_payload_fingerprint="x",
            invocation_fingerprint="x",
            ours_retrieval_empty=False,
        )

    # a sweep of nothing must not be able to publish a verdict
    zero = [
        grid.ArmOutcome(arm=arm, channel=channel.value, passes=0, runs=0)
        for channel in grid.CHANNELS
        for arm in grid.ARMS
    ]
    assert "KILL" in grid.task_verdict(zero)  # this is exactly what must never be cacheable


def test_duplicate_work_ids_are_refused(tmp_path: Path) -> None:
    """work_id keys BOTH the identity map and the <work_id>.json path, so a duplicate aliases two
    DIFFERENT tasks onto one cache file: the second overwrites the first and, on resume, that one
    record is served for both -- the second task's verdict reported as the first task's. Corpus
    work ids are sequence-derived, so a regenerated or hand-assembled corpus can repeat one."""
    seq_a = _toolreq_seq("dup", current="AAA-CUR", stale="AAA-STALE")
    seq_b = _toolreq_seq("dup", current="BBB-CUR", stale="BBB-STALE")
    tasks = [adapt_sequence(seq_a), adapt_sequence(seq_b)]
    assert tasks[0] != tasks[1] and tasks[0].work_id == tasks[1].work_id  # distinct, same id

    with pytest.raises(ValueError, match="duplicate work_id"):
        _run_corpus(
            tasks, [seq_a, seq_b], tmp_path / "out", dry_run=True, store_path=tmp_path / "s.db"
        )


def invocation_fp(task: ToolReqRealAgentTask, payload: dict[str, str]) -> str:
    return grid.invocation_fingerprint(task, payload, model="")


def test_invocation_fingerprint_catches_what_a_field_model_misses(tmp_path: Path) -> None:
    """The point of invocation_fingerprint: it hashes the COMMAND LINE, not a model of it.

    Every defect in this file's history was the same shape -- the identity hashed a field-by-field
    MODEL of the executed input and the model was missing a field (memory ORDER, retrieval RANK,
    goal_step, ...). Each fix added the missing field; the next review found the next one. Hashing
    the rendered argv cannot be incomplete ABOUT THE INVOCATION, because it is the invocation.

    Asserted here on the two axes that already shipped as bugs (memory order on both the ours and
    the oracle arm) plus the trust channel's framing, all of which reach the prompt text."""
    _, tasks = _corpus_one(tmp_path)
    task = tasks[0]

    # ours payload rank order -- shipped as a bug
    assert invocation_fp(task, {"a": "A", "b": "B"}) != invocation_fp(task, {"b": "B", "a": "A"})

    # oracle memory order -- shipped as a bug, on the CEILING arm
    om = {"m-1": "one", "m-2": "two"}
    fwd, rev = dataclasses.replace(task, oracle_memory=om), dataclasses.replace(
        task, oracle_memory=dict(reversed(list(om.items())))
    )
    assert invocation_fp(fwd, {}) != invocation_fp(rev, {})

    # identical inputs are stable (an UNSTABLE fingerprint is the expensive direction -- it
    # would re-spend real money on every resume)
    assert invocation_fp(task, {"a": "A"}) == invocation_fp(task, {"a": "A"})

    # and the memory CONTENT still moves it, obviously
    assert invocation_fp(task, {"a": "A"}) != invocation_fp(task, {"a": "DIFFERENT"})


def test_the_fingerprint_is_the_invocations_the_arms_actually_send(tmp_path: Path) -> None:
    """THE standing invariant, and it costs ZERO tokens: what the identity hashes must be what the
    arms put on the wire.

    The invocations come back RECORDED off the CLI seam (`RecordingRunner`), so this compares the
    fingerprint against the real command lines rather than against a second rendering of them. And
    `dry_run` swaps only the CLI RUNNER — never the prompt builder, never the argv — so the free
    path's invocations ARE the paid path's. Re-introduce a hand-written mirror of the arms' cells
    beside the fingerprint and let it drift by one field, and this goes RED before any money moves.

    Run with a NON-EMPTY ours payload on purpose: that is when all three arms are planned and all
    three spend, so the cell the empty-retrieval convention would otherwise skip is covered here."""
    _, tasks = _corpus_one(tmp_path)
    task = tasks[0]
    payload = {"w-prior": "the retention window is TOKEN-abc"}
    sent = grid.evaluate_task(task, repeats=2, model="", dry_run=True, ours_payload=payload).calls
    assert invocation_digest(sent) == grid.invocation_fingerprint(task, payload, model="")


def test_the_never_run_ours_cell_contributes_a_row_but_no_invocation(tmp_path: Path) -> None:
    """The empty-retrieval convention, stated at the seam that now enforces it. `ours` is relabeled
    from `none` and never spent on, so it must contribute a ROW (the grid is complete) and NO
    invocation (it sent none). The fingerprint is over the plan, which omits it — and the identity
    still separates the two worlds, because `ours_retrieval_empty` and `ours_payload_fingerprint`
    are themselves identity fields."""
    _, tasks = _corpus_one(tmp_path)
    task = tasks[0]
    evaluation = grid.evaluate_task(task, repeats=2, model="", dry_run=True, ours_payload={})

    assert {(c.arm, c.channel) for c in evaluation.outcomes} == grid.expected_cells()
    assert not any(c.arm == "ours" for c in evaluation.calls)
    assert invocation_digest(evaluation.calls) == grid.invocation_fingerprint(task, {}, model="")


def test_forged_not_empty_flag_with_a_fabricated_ours_cell_is_a_miss(tmp_path: Path) -> None:
    """The OTHER direction of the empty-flag hole, and the one that actually pays.

    An earlier fix cross-validated the flag only when it was True (ours rows had to equal none's)
    and called the hole closed. It was half closed. A file claiming ours_retrieval_empty=False --
    i.e. 'ours really was measured' -- while carrying a fabricated passing ours cell walked past
    every check, because the flag itself was never compared against what THIS run resolves. The
    result: a task whose retrieval returned NOTHING is served a fully-passing (ours 2/2), with
    zero spend, no crash, and a green suite. The flag now rides IN the identity, so a file whose
    flag disagrees with the live-resolved payload is a miss in both directions."""
    sequences, tasks = _corpus_one(tmp_path)
    out = tmp_path / "out"
    store_path = tmp_path / "store.db"
    # the real run: retrieval is empty (lifecycle-earliest task, LOO admits no priors)
    first = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert first["ours_empty_retrieval"] == [tasks[0].work_id]

    result_path = out / f"{tasks[0].work_id}.json"
    record = json.loads(result_path.read_text())
    assert record["identity"]["ours_retrieval_empty"] is True

    # forge: claim ours WAS measured, and that it passed everything
    record["identity"]["ours_retrieval_empty"] = False
    for row in record["outcomes"]:
        if row["arm"] == "ours":
            row["passes"] = row["runs"]
    result_path.write_text(json.dumps(record))

    resumed = _run_corpus(tasks, sequences, out, dry_run=True, store_path=store_path)
    assert (resumed["executed"], resumed["reused"]) == (
        1,
        0,
    ), "served a fabricated ours measurement for a task whose retrieval was empty"
    assert "(ours 2/2)" not in resumed["per_task"][0]["verdict"]
    assert resumed["ours_empty_retrieval"] == [tasks[0].work_id]  # the truth is restored


@pytest.mark.parametrize(
    "bad_id",
    [
        "summary-toolreq-realagent",  # claims the summary's filename
        "../escape",  # writes the result outside --out
        "nested/id",  # path separator
        "",  # empty
    ],
)
def test_unsafe_work_ids_are_refused(tmp_path: Path, bad_id: str) -> None:
    """work_id is CORPUS DATA used to build a filesystem path (<work_id>.json), so it must be a
    safe, unclaimed filename before it is trusted as one. A task named after the summary gets its
    result OVERWRITTEN by the summary main() writes into the same dir afterwards -- so it misses
    forever and every resume re-pays for it. A separator/traversal writes outside --out entirely.
    Neither fabricates a number, but both are the file's root shape: untrusted input consumed
    without a check at the boundary it crosses."""
    seq = _toolreq_seq("w-safe")
    task = dataclasses.replace(adapt_sequence(seq), work_id=bad_id)
    with pytest.raises(ValueError, match="unsafe work_id"):
        _run_corpus([task], [seq], tmp_path / "out", dry_run=True, store_path=tmp_path / "s.db")


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

    monkeypatch.setattr(grid, "run_arm", _spy_run_arm(seen, oracle_passes=True))
    summary = grid.run_corpus(
        tasks,
        sequences,
        out_dir=tmp_path / "out",
        repeats=2,
        model="sonnet",
        dry_run=False,  # PAID path — the one that must not burn a turn on an empty payload
        store_path=tmp_path / "store.db",
        mem_bin=str(MEM_BIN),
        seed_fn=_no_ours_payload,  # resolves to {} -> empty payload for every task
    )

    assert "ours" not in seen, "spent a paid run on an empty `ours` payload"
    record = summary["per_task"][0]
    assert record["identity"]["ours_retrieval_empty"] is True
    by = {(o["arm"], o["channel"]): o for o in record["outcomes"]}
    assert len(by) == len(grid.ARMS) * len(grid.CHANNELS)  # ours still reported, not dropped
    for channel in grid.CHANNELS:
        ours, none = by[("ours", channel.value)], by[("none", channel.value)]
        assert (ours["passes"], ours["runs"]) == (none["passes"], none["runs"])


def test_non_empty_ours_payload_still_spends(tmp_path: Path, monkeypatch) -> None:
    """The guard above must be narrow: a task WITH a resolved payload still runs its own
    `ours` cell (otherwise the arm could never score above `none`)."""
    sequences, tasks = _corpus_one(tmp_path)
    seen: list[str] = []

    def _payload(*_args: object) -> dict[str, dict[str, str]]:
        return {tasks[0].work_id: {"lesson-1": "the retention window is TOKEN-abc"}}

    monkeypatch.setattr(grid, "run_arm", _spy_run_arm(seen, oracle_passes=False))
    summary = grid.run_corpus(
        tasks,
        sequences,
        out_dir=tmp_path / "out",
        repeats=2,
        model="sonnet",
        dry_run=False,
        store_path=tmp_path / "store.db",
        mem_bin=str(MEM_BIN),
        seed_fn=_payload,
    )
    assert seen.count("ours") == len(grid.CHANNELS)
    assert summary["per_task"][0]["identity"]["ours_retrieval_empty"] is False


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
    outcomes = grid.evaluate_task(task, repeats=2, model="", dry_run=True).outcomes
    by = {(o.arm, o.channel): o for o in outcomes}
    for channel in (c.value for c in grid.CHANNELS):
        assert by[("none", channel)].passes == 0
        assert by[("oracle", channel)].passes == 2


# --- ours seeding


def test_reset_store_removes_the_db_and_its_sqlite_sidecars(tmp_path: Path) -> None:
    """The seed's freshness rests on this six-line delete. SQLite keeps committed data the
    main file does not yet hold in the -wal sidecar, so leaving -wal/-shm behind can restore
    the previous corpus's rows on the next open — the store would not be fresh. All three go."""
    store_path = tmp_path / "store.db"
    sidecars = [store_path.with_name(store_path.name + s) for s in ("", "-wal", "-shm")]
    for path in sidecars:
        path.write_text("previous corpus", encoding="utf-8")

    _reset_store(store_path)

    assert not [p for p in sidecars if p.exists()]


def test_reset_store_is_a_no_op_when_no_store_exists(tmp_path: Path) -> None:
    """The FIRST seed of a run resets a store path that does not exist yet — that is the
    normal path, not an error (`missing_ok`)."""
    _reset_store(tmp_path / "store.db")  # must not raise


# --- ours seeding, end to end (real mem CLI; skips like test_pipeline_e2e when TS is unbuilt)


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
    payloads = seed_ours_store_and_resolve_payloads(
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

    seed_ours_store_and_resolve_payloads(old, old_tasks, store_path, str(MEM_BIN))
    payloads = seed_ours_store_and_resolve_payloads(new, new_tasks, store_path, str(MEM_BIN))

    # w-1 holds out its own lesson, so what it sees cross-task is w-0's — which corpus's?
    surfaced = " ".join(payloads["w-1"].values())
    assert surfaced, "cross-task retrieval never fired"
    dead = set(old_tasks[0].current_opaque_values)
    live = set(new_tasks[0].current_opaque_values)
    assert dead and live and not (dead & live)  # the two corpora share no token
    assert not [tok for tok in dead if tok in surfaced], "stale corpus leaked into the payload"
    assert [tok for tok in live if tok in surfaced], "current corpus never surfaced"
