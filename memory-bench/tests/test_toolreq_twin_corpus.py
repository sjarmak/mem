"""mem-9q8dg — the E1 twin corpus and its outcome-side necessity preflight."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from membench.grading.paired_ci import paired_delta_ci
from membench.metrics.scorers import states_value
from membench.runner import e1_necessity_preflight, toolreq_corpus
from membench.runner.e1_necessity_preflight import (
    EXIT_REFUSED,
    EXIT_UNVERIFIED,
    NECESSARY_PASS_CEILING,
    UNNECESSARY_PASS_FLOOR,
    necessity_preflight,
)
from membench.runner.toolreq_corpus import (
    CONTEXT_SEPARATOR,
    load_twin_corpus,
    twin_tasks,
    unnecessary_twin,
    variant_split,
)
from membench.runner.toolreq_realagent import (
    VARIANT_NECESSARY,
    VARIANT_UNNECESSARY,
    task_fingerprint,
)
from tests.toolreq_helpers import corpus


def _twin_corpus(tmp_path: Path, *work_ids: str):
    corpus_dir = tmp_path / "corpus"
    corpus(tmp_path, *work_ids)  # seeds tmp_path/corpus/0/sequences.json
    return load_twin_corpus(corpus_dir)


def test_twins_share_a_pair_key_and_split_evenly(tmp_path: Path) -> None:
    _, tasks = _twin_corpus(tmp_path, "w-0", "w-1")
    split = variant_split(tasks)
    assert len(split[VARIANT_NECESSARY]) == len(split[VARIANT_UNNECESSARY]) == 2
    assert {t.pair_key for t in split[VARIANT_NECESSARY]} == {
        t.pair_key for t in split[VARIANT_UNNECESSARY]
    }
    # ...but never a shared RESULT id: twins that collide on disk would overwrite each other.
    assert len({t.result_id for t in tasks}) == len(tasks)


def test_the_unnecessary_twin_states_the_current_value_and_never_a_stale_one(
    tmp_path: Path,
) -> None:
    _, tasks = _twin_corpus(tmp_path, "w-0")
    necessary, unnecessary = tasks
    forbidden = necessary.goal_step.outcome_checks[0].requires_action[0].forbidden_values
    assert forbidden  # the fixture really does carry a superseded value to guard against
    for value in necessary.current_opaque_values:
        # The whole contrast: the necessary half withholds it, the twin states it.
        assert not states_value(necessary.goal_step.user_request, value)
        assert states_value(unnecessary.goal_step.user_request, value)
    for value in forbidden:
        assert not states_value(unnecessary.goal_step.user_request, value)
    # Scoring is identical, so the halves differ only in what is in context.
    assert (
        unnecessary.goal_step.outcome_checks[0].requires_action
        == necessary.goal_step.outcome_checks[0].requires_action
    )
    assert unnecessary.oracle_memory == {}
    assert unnecessary.goal_step.expected_memory_reads == []
    assert necessary.goal_step.memory_necessary is True
    assert unnecessary.goal_step.memory_necessary is False


def test_the_non_value_text_of_a_twin_pair_is_identical(tmp_path: Path) -> None:
    # The confound this corpus must not carry (arXiv 2605.09252). E1's endpoint is P(agent
    # chooses to consult memory), so ANY wording present in one half only and absent from the
    # other is a prompt-only treatment on the measured behaviour. Off the values themselves the
    # twin's request must be the necessary request plus a fixed, behaviour-silent scaffold.
    _, tasks = _twin_corpus(tmp_path, "w-0")
    necessary, unnecessary = tasks

    # The heading is re-typed here, NOT imported: this test's job is to red when the wording
    # changes, and importing the constant would let "no recall required" walk back in silently.
    prefix, separator, block = unnecessary.goal_step.user_request.partition(
        CONTEXT_SEPARATOR + "Current state:\n"
    )
    assert separator, unnecessary.goal_step.user_request
    assert prefix == necessary.goal_step.user_request
    # ...and the block is the VALUES and nothing else — no provenance prose, no framing.
    assert block.splitlines() == [f"- {v}" for v in necessary.current_opaque_values]


def test_twins_are_distinct_worlds_to_the_cache(tmp_path: Path) -> None:
    # Same work_id, different measurement: a shared fingerprint would let a cached necessary
    # cell be served as the unnecessary half's result.
    #
    # Asserted on two tasks differing ONLY in ``variant``. Comparing the real twins instead is
    # vacuous — their goal_step and oracle_memory differ too, so the assertion holds even with
    # ``"variant": task.variant`` deleted from ``task_fingerprint``, i.e. it passes whether the
    # thing it names works or not.
    _, tasks = _twin_corpus(tmp_path, "w-0")
    necessary = tasks[0]
    relabelled = dataclasses.replace(necessary, variant=VARIANT_UNNECESSARY)
    assert relabelled.goal_step is necessary.goal_step  # nothing else moved
    assert task_fingerprint(relabelled) != task_fingerprint(necessary)
    assert task_fingerprint(tasks[0]) != task_fingerprint(tasks[1])


def test_a_twin_of_a_twin_is_refused(tmp_path: Path) -> None:
    _, tasks = _twin_corpus(tmp_path, "w-0")
    with pytest.raises(ValueError, match="can only twin"):
        unnecessary_twin(tasks[1])


def test_paired_estimator_imputes_nothing_over_the_twin_corpus(tmp_path: Path) -> None:
    # The reason twins share a key at all (bead correction 1). Pairing by ``pair_key`` matches
    # every task; ``n_imputed_zero == 0`` is the assertion that the estimator is reading real
    # deltas rather than fabricating them.
    _, tasks = _twin_corpus(tmp_path, "w-0", "w-1", "w-2")
    split = variant_split(tasks)
    unnecessary_rate = {t.pair_key: 1.0 for t in split[VARIANT_UNNECESSARY]}
    necessary_rate = {t.pair_key: 0.25 for t in split[VARIANT_NECESSARY]}
    ci = paired_delta_ci(unnecessary_rate, necessary_rate, population="itt", n_resamples=200)
    assert ci.n_imputed_zero == 0
    assert ci.n_pairs == 3
    assert ci.delta == pytest.approx(-0.75)


def test_keying_the_halves_apart_fabricates_a_confident_zero(tmp_path: Path) -> None:
    # The defect the shared key exists to prevent, pinned so a future "disambiguate the ids"
    # refactor cannot reintroduce it silently: key by ``result_id`` and the two halves are
    # disjoint, so every delta is imputed 0.0 and the estimator reports 0.0 regardless of the
    # truth — here, of a real -0.75.
    _, tasks = _twin_corpus(tmp_path, "w-0", "w-1", "w-2")
    split = variant_split(tasks)
    unnecessary_rate = {t.result_id: 1.0 for t in split[VARIANT_UNNECESSARY]}
    necessary_rate = {t.result_id: 0.25 for t in split[VARIANT_NECESSARY]}
    ci = paired_delta_ci(unnecessary_rate, necessary_rate, population="itt", n_resamples=200)
    assert ci.n_imputed_zero == 6
    assert ci.delta == 0.0 and ci.ci_low == 0.0 and ci.ci_high == 0.0


def test_cli_json_labels_both_classes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    corpus(tmp_path, "w-0", "w-1")
    assert toolreq_corpus.main(["--corpus-dir", str(tmp_path / "corpus"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    labels = [task["goal_step"]["memory_necessary"] for task in payload["tasks"]]
    assert labels.count(True) == labels.count(False) == 2
    assert payload["n_pairs"] == 2


def test_cli_refuses_an_empty_corpus(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert toolreq_corpus.main(["--corpus-dir", str(tmp_path / "empty"), "--json"]) == 1


def test_dry_run_preflight_separates_the_halves(tmp_path: Path) -> None:
    # Wiring only — the simulated runner copies values that are in the prompt, so this shows the
    # corpus GEOMETRY is right (unnecessary solvable with no memory, necessary not) and nothing
    # about a real agent. ``verified`` must stay false for exactly that reason.
    _, tasks = _twin_corpus(tmp_path, "w-0", "w-1")
    result = necessity_preflight(tasks, corpus_dir=tmp_path / "corpus", repeats=2)
    assert result.unnecessary_pass_rate == 1.0 > UNNECESSARY_PASS_FLOOR
    assert result.necessary_pass_rate == 0.0 < NECESSARY_PASS_CEILING
    assert result.accepted is True
    assert result.verified is False and result.mode == "dry_run"
    assert "NOT VERIFICATION" in result.note


def test_preflight_refuses_an_empty_half(tmp_path: Path) -> None:
    _, tasks = _twin_corpus(tmp_path, "w-0")
    necessary_only = [t for t in tasks if t.variant == VARIANT_NECESSARY]
    with pytest.raises(ValueError, match="no tasks in this half"):
        necessity_preflight(necessary_only, corpus_dir=tmp_path / "corpus")


def test_twin_tasks_is_order_stable(tmp_path: Path) -> None:
    _, tasks = _twin_corpus(tmp_path, "w-0", "w-1")
    assert [(t.work_id, t.variant) for t in tasks] == [
        ("w-0", VARIANT_NECESSARY),
        ("w-0", VARIANT_UNNECESSARY),
        ("w-1", VARIANT_NECESSARY),
        ("w-1", VARIANT_UNNECESSARY),
    ]
    _, again = load_twin_corpus(tmp_path / "corpus")
    assert [task_fingerprint(t) for t in tasks] == [task_fingerprint(t) for t in again]


def test_twin_tasks_of_an_already_twinned_corpus_is_refused(tmp_path: Path) -> None:
    _, tasks = _twin_corpus(tmp_path, "w-0")
    with pytest.raises(ValueError, match="can only twin"):
        twin_tasks(tasks)


def test_a_dry_run_cannot_exit_as_acceptance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The gate-facing half of "a label is not evidence". The dry run's rates are entailed by the
    # simulated runner and DO clear the thresholds, so ``accepted`` is true — and the process must
    # still not exit 0, because a CI gate reads the exit code, not the ``verified`` field.
    corpus(tmp_path, "w-0", "w-1")
    code = e1_necessity_preflight.main(["--corpus-dir", str(tmp_path / "corpus"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True and payload["verified"] is False
    assert code == EXIT_UNVERIFIED
    assert "UNRUN" in payload["note"]


def test_paid_without_a_model_refuses_before_it_loads_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ``--model`` defaults to "", and the paid path was never exercised, so an authorized run
    # would have discovered this AT SPEND TIME. The refusal also has to land before the corpus is
    # read: --corpus-dir names a directory that does not exist, and the run must still refuse for
    # the model rather than die on the corpus.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MEMBENCH_AGENT_MODEL", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stub-token-not-used")
    code = e1_necessity_preflight.main(
        ["--corpus-dir", str(tmp_path / "nonexistent"), "--paid", "--json"]
    )
    assert code == EXIT_REFUSED
    assert "REFUSING to spend" in capsys.readouterr().out


def test_paid_refuses_a_metered_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-stub")
    code = e1_necessity_preflight.main(
        ["--corpus-dir", str(tmp_path / "nonexistent"), "--paid", "--model", "stub-model"]
    )
    assert code == EXIT_REFUSED
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_a_dry_run_is_never_refused_for_paid_preconditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The refusals gate SPEND, not the free wiring check: a dry run spawns nothing, so an ambient
    # metered key or a missing token must not turn the default path into an exit 2 (mem-9bh93 —
    # a gate that reads an ambient env var reds the suite in the shell it targets).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-stub")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    corpus(tmp_path, "w-0")
    assert (
        e1_necessity_preflight.main(["--corpus-dir", str(tmp_path / "corpus")]) == EXIT_UNVERIFIED
    )
