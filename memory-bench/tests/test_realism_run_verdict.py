"""Tests for the mem-ovi.2 realism verdict-matrix run wire (``realism.run_verdict``).

Everything runs offline: the real-corpus query is a fake ``CorpusRunner``, transcripts
come from an in-memory reader, and the semantic axis uses ``StubComparativeJudge``. No
live store, no daemon. The suite asserts the run RESOLVES no fork (it sweeps the whole
A x B x C matrix) and surfaces NO pass/fail gate — the two invariants the bead pins.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from membench.bbon.comparative_judge import StubComparativeJudge
from membench.realism.features import features_from_sequence
from membench.realism.run_verdict import (
    FORK_A_SUBSETS,
    FORK_B_SEGMENTERS,
    FORK_C_FILTERS,
    VerdictMatrix,
    _is_kickoff,
    construct_axis,
    load_synthetic_corpus,
    run,
    semantic_axis,
    structural_matrix,
)
from membench.schemas.sequence import BenchmarkSequence, SequenceStep


def _seq(seq_id: str, requests: list[str], *, tools: list[str] | None = None) -> BenchmarkSequence:
    return BenchmarkSequence(
        sequence_id=seq_id,
        title=f"title {seq_id}",
        goal="reconcile decisions",
        steps=[
            SequenceStep(
                step_id=f"{seq_id}-s{i}",
                user_request=req,
                available_tools=list(tools or []),
            )
            for i, req in enumerate(requests)
        ],
    )


def _synthetic() -> list[BenchmarkSequence]:
    return [
        _seq("t0", ["do the first thing", "then the second"], tools=["Read", "Edit"]),
        _seq("t1", ["a longer authored request here", "wrap it up"], tools=["Bash"]),
    ]


def _jsonl(*entries: dict[str, object]) -> str:
    return "\n".join(json.dumps(e) for e in entries)


def _user(text: str, *, meta: bool = False) -> dict[str, object]:
    entry: dict[str, object] = {"type": "user", "message": {"role": "user", "content": text}}
    if meta:
        entry["isMeta"] = True
    return entry


def _assistant(text: str, *, tool: str | None = None) -> dict[str, object]:
    blocks: list[dict[str, object]] = [{"type": "text", "text": text}]
    if tool is not None:
        blocks.append({"type": "tool_use", "name": tool})
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


def _kickoff(rig: str) -> str:
    """A real worker-pool kickoff turn: a session HEADER line then the injected
    instruction, so the marker is NOT at position 0 (the strip-kickoff filter must
    match it as a substring, the regression this shape guards)."""
    header = f"[ds-research] /home/ds/projects/mem/{rig} - 2026-01-01"
    return f"{header}\n\nRun `gc prime` to initialize your context."


# Two real sessions, each opening with a kickoff turn (fork C).
_SESSION_A = _jsonl(
    _user(_kickoff("mem-worker")),
    _user("investigate the failing loader test"),
    _assistant("looking now", tool="Bash"),
    _user("great, ship the fix"),
)
_SESSION_B = _jsonl(
    _user(_kickoff("mem-pl")),
    _user("add a regression test for the parser"),
    _assistant("done", tool="Read"),
)


def _fake_runner_records() -> dict[str, list[dict[str, object]]]:
    """rig -> the records that a --rig-filtered ``mem query`` returns."""
    return {
        "mem": [
            {"work_id": "w0", "rig": "mem", "trace": {"jsonl_path": "/sess/a.jsonl"}},
        ],
        "*": [
            {"work_id": "w0", "rig": "mem", "trace": {"jsonl_path": "/sess/a.jsonl"}},
            {"work_id": "w1", "rig": "gascity", "trace": {"jsonl_path": "/sess/b.jsonl"}},
        ],
    }


def _fake_runner(argv: list[str]) -> dict[str, object]:
    records = _fake_runner_records()
    rig = None
    if "--rig" in argv:
        rig = argv[argv.index("--rig") + 1]
    return {"records": records.get(rig or "*", [])}


def _fake_reader(path: str) -> str:
    return {"/sess/a.jsonl": _SESSION_A, "/sess/b.jsonl": _SESSION_B}[path]


def _matrix_kwargs() -> dict[str, Any]:
    return {"runner": _fake_runner, "transcript_reader": _fake_reader}


@pytest.fixture
def cells() -> list:
    """The full swept structural matrix over the two fake sessions -- shared by the
    structural-axis tests, which each assert a different property of the same sweep."""
    syn = [features_from_sequence(s) for s in _synthetic()]
    return structural_matrix(syn, "unused.db", **_matrix_kwargs())


# --- fork registries -------------------------------------------------------------


def test_fork_registries_expose_default_and_alternative() -> None:
    # Each fork must offer at least two candidates or the "matrix" collapses to a
    # single resolved definition — exactly what the bead forbids.
    assert set(FORK_A_SUBSETS) >= {"all-closed", "mem-closed"}
    assert set(FORK_B_SEGMENTERS) >= {"userturn", "allmsg"}
    assert set(FORK_C_FILTERS) >= {"default", "strip-kickoff"}


def test_is_kickoff_matches_canonical_header_prefixed_line() -> None:
    # Pins the real kickoff shape so a harness-wording drift breaks THIS test instead
    # of silently degenerating strip-kickoff to default (Fork C would go dead).
    assert _is_kickoff(_user(_kickoff("mem-worker")))
    # A genuine authored turn that merely mentions priming is not the kickoff.
    assert not _is_kickoff(_user("please document how to run gc prime for new workers"))
    assert not _is_kickoff(_user("investigate the failing loader test"))


# --- synthetic corpus loader -----------------------------------------------------


def test_load_synthetic_corpus_reads_frozen_sequences(tmp_path: Path) -> None:
    world = tmp_path / "w"
    world.mkdir()
    (world / "sequences.json").write_text(
        json.dumps([s.model_dump() for s in _synthetic()]), encoding="utf-8"
    )
    loaded = load_synthetic_corpus(world)
    assert [s.sequence_id for s in loaded] == ["t0", "t1"]


def test_load_synthetic_corpus_rejects_non_list(tmp_path: Path) -> None:
    world = tmp_path / "w"
    world.mkdir()
    (world / "sequences.json").write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list"):
        load_synthetic_corpus(world)


# --- structural matrix -----------------------------------------------------------


def test_structural_matrix_sweeps_every_fork_combination(cells: list) -> None:
    # |A| x |B| x |C| cells, each uniquely keyed.
    assert len(cells) == len(FORK_A_SUBSETS) * len(FORK_B_SEGMENTERS) * len(FORK_C_FILTERS)
    keys = {(c.fork_a, c.fork_b, c.fork_c) for c in cells}
    assert len(keys) == len(cells)


def test_structural_cell_reports_signed_off_per_feature_vector(cells: list) -> None:
    cell = cells[0]
    assert set(cell.reference.matchable) == {
        "n_steps",
        "n_tool_calls",
        "tool_diversity",
        "task_text_length",
    }
    assert set(cell.reference.memory_op) == {"n_memory_writes", "n_memory_reads"}
    assert cell.n_synthetic == 2
    # No aggregate / gate leaks into the serialized cell.
    assert "aggregate" not in cell.to_dict()
    assert "passes" not in cell.to_dict()


def test_fork_a_changes_real_corpus_size(cells: list) -> None:
    by_a = {c.fork_a: c.n_real for c in cells}
    # mem-closed sees one session, all-closed sees both.
    assert by_a["mem-closed"] == 1
    assert by_a["all-closed"] == 2


def test_fork_b_segmentation_changes_step_count(cells: list) -> None:
    # allmsg (every message a step) yields >= user-turn count on the same corpus.
    userturn = {(c.fork_a, c.fork_c): c for c in cells if c.fork_b == "userturn"}
    allmsg = {(c.fork_a, c.fork_c): c for c in cells if c.fork_b == "allmsg"}
    # The KS vectors differ between the two segmentations (they are not the same run).
    assert any(userturn[k].reference.matchable != allmsg[k].reference.matchable for k in userturn)


def test_fork_c_strip_kickoff_drops_the_kickoff_turn() -> None:
    # Compare n_steps of the real corpus directly via the segmenter path: strip-kickoff
    # must remove the "Run `gc prime`" opener, so user-turn count drops by one.
    from membench.realism.mem_corpus import load_real_corpus
    from membench.realism.real_loader import features_from_trace

    default_traces = load_real_corpus(
        "unused.db",
        runner=_fake_runner,
        transcript_reader=_fake_reader,
        filters={"rig": "mem"},
        message_filter=FORK_C_FILTERS["default"],
    )
    stripped_traces = load_real_corpus(
        "unused.db",
        runner=_fake_runner,
        transcript_reader=_fake_reader,
        filters={"rig": "mem"},
        message_filter=FORK_C_FILTERS["strip-kickoff"],
    )
    default_steps = features_from_trace(default_traces[0]).n_steps
    stripped_steps = features_from_trace(stripped_traces[0]).n_steps
    assert stripped_steps == default_steps - 1


def test_structural_matrix_skips_empty_subset() -> None:
    syn = [features_from_sequence(s) for s in _synthetic()]

    def empty_runner(argv: list[str]) -> dict[str, object]:
        return {"records": []}

    cells = structural_matrix(syn, "unused.db", runner=empty_runner, transcript_reader=_fake_reader)
    assert cells == []


# --- semantic axis ---------------------------------------------------------------


def _stub_judge(realism: float, reads_real: bool) -> StubComparativeJudge:
    def fn(_prompt: str) -> str:
        return json.dumps({"realism": realism, "reads_real": reads_real, "rationale": "ok"})

    return StubComparativeJudge(fn=fn)


def test_semantic_axis_aggregates_per_task_verdicts() -> None:
    axis = semantic_axis(_synthetic(), _stub_judge(0.8, True))
    assert axis.aggregate.n == 2
    assert axis.aggregate.mean_realism == pytest.approx(0.8)
    assert axis.aggregate.real_fraction == pytest.approx(1.0)
    assert [tid for tid, _ in axis.per_task] == ["t0", "t1"]
    # Raw numbers only — no gate in the serialized axis.
    assert "passes" not in axis.to_dict()


def test_semantic_axis_requires_a_corpus() -> None:
    with pytest.raises(ValueError):
        semantic_axis([], _stub_judge(0.5, False))


# --- construct axis + full run ---------------------------------------------------


def test_construct_axis_is_reported_na_not_fabricated() -> None:
    verdict = construct_axis()
    assert verdict["status"] == "N/A"
    assert "bxhh.5" in str(verdict["reason"])


def _write_world(root: Path, sequences: list[BenchmarkSequence]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sequences.json").write_text(
        json.dumps([s.model_dump() for s in sequences]), encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps({"sequences_sha256": "abc123"}), encoding="utf-8"
    )
    return root


def test_run_end_to_end_without_judge_is_structural_only(tmp_path: Path) -> None:
    world = _write_world(tmp_path / "w", _synthetic())
    matrix = run(world, "unused.db", judge=None, **_matrix_kwargs())
    assert isinstance(matrix, VerdictMatrix)
    assert matrix.semantic is None
    assert matrix.construct["status"] == "N/A"
    assert matrix.provenance["held"] is True
    assert matrix.provenance["sequences_sha256"] == "abc123"
    assert len(matrix.structural) == len(FORK_A_SUBSETS) * len(FORK_B_SEGMENTERS) * len(
        FORK_C_FILTERS
    )


def test_run_serialization_has_three_axes_and_no_defensible_verdict(tmp_path: Path) -> None:
    world = _write_world(tmp_path / "w", _synthetic())
    matrix = run(world, "unused.db", judge=_stub_judge(0.7, True), **_matrix_kwargs())
    blob = matrix.to_dict()
    assert set(blob) == {"provenance", "structural", "semantic", "construct"}
    semantic = blob["semantic"]
    assert isinstance(semantic, dict)
    assert semantic["mean_realism"] == pytest.approx(0.7)
    # The gated composite must never appear — the run reports axes, not a verdict.
    assert "defensible" not in json.dumps(blob)
