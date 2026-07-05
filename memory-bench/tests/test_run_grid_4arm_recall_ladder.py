"""4-rung recall-ladder wiring (mem-do8r): none / vector-rag / ours / oracle.

Adopts the Sakana (AIEWF 2026) recall ladder — fix the model, vary ONLY the recall
arm across four rungs. The single net-new wiring item over the existing 3-rung grid
(none/ours/oracle) is the ``vector-rag`` rung: a semantic-arm payload injector that
mirrors ``ours`` through the same leak-guard path (``inject_context`` +
``write_signature_overlap`` covariate, mem-tnyo H3 parity).

These tests exercise the wiring ONLY — the adapter emits four task dirs, the new
rung dispatches and writes the right file through the guard, and ``run_grid`` threads
``vector_payloads`` per rung — with a deterministic ``StubRunner`` (no Docker, no
network, no paid API) and, for the vector arm, the ``nemo-embed`` arm driven by a
deterministic fake embedder over a seeded in-memory corpus. They do NOT assert a lift
curve: the stub runner ignores injected memory by design, so a per-rung score is
fixture-authored, not earned. Rung-3 (``ours``) still needs a populated ``lessons``
store (ADR lock #2, a ``mem distill-lessons`` pass) before it is non-degenerate on the
real grid — that gate is out of scope here and deliberately not hidden by the stub.
"""

from __future__ import annotations

import json

import pytest

from membench.grading import RewardComponents, RewardRecord, RunTrace, TraceErrorRef
from membench.grading.curve import (
    InsufficientLadderError,
    build_curve,
    min_useful_combo,
    saturation_point,
)
from membench.harbor.grid import StubRunner, run_grid
from membench.harbor.memory_inject import (
    MEMORY_FILENAME,
    SIGNATURE_OVERLAP_FILENAME,
    inject_rung_memory,
    validate_rungs,
)

FOUR_RUNGS = ("none", "vector-rag", "ours", "oracle")


def _err(tool="tsc", file="src/a.ts", line=12, error_class="TS2345", signature=None):
    sig = signature if signature is not None else f"{tool}:{file}:{line}:{error_class}"
    return TraceErrorRef(tool=tool, file=file, line=line, error_class=error_class, signature=sig)


def _record(work_id="w1", rig="mem", title="Fix the broken parser"):
    return {
        "work_id": work_id,
        "rig": rig,
        "title": title,
        "lifecycle": {
            "created": "2026-01-01T00:00:00Z",
            "started": "2026-01-10T00:00:00Z",
            "closed": "2026-02-01T00:00:00Z",
            "status": "closed",
        },
    }


# --- validate_rungs: vector-rag is a known runnable rung ------------------------


def test_validate_rungs_accepts_vector_rag():
    # No raise, and vector-rag is not a deferred rung (empty deferred subset here).
    assert validate_rungs(FOUR_RUNGS) == ()


def test_validate_rungs_still_rejects_unknown():
    with pytest.raises(ValueError, match="unknown ablation rung"):
        validate_rungs(("none", "vektor-rag"))


# --- inject_rung_memory: the vector-rag dispatch branch -------------------------


def test_inject_vector_rag_writes_payloads_and_covariate(tmp_path):
    held = [_err()]
    payloads = {"prior-7": "lesson: validate the arg type before the call"}
    target = inject_rung_memory(
        tmp_path,
        "vector-rag",
        held_errors=held,
        vector_payloads=payloads,
    )
    assert target == tmp_path / MEMORY_FILENAME
    text = target.read_text(encoding="utf-8")
    assert "validate the arg type" in text
    assert "prior-7" in text
    # H3 parity with ours: the signature-overlap covariate is recorded (mem-tnyo).
    overlap = json.loads((tmp_path / SIGNATURE_OVERLAP_FILENAME).read_text(encoding="utf-8"))
    assert overlap["condition"] == "vector-rag"
    assert overlap["trigger"] == "query_text"  # vs ours' oracle trigger: the ONLY other delta
    assert "prior-7" in overlap["overlap"]


def test_inject_vector_rag_empty_payloads_is_a_tried_rung(tmp_path):
    # Empty retrieval still writes the memory file (the rung was tried), with the
    # explicit no-prior body — the same shape as an empty `ours`.
    target = inject_rung_memory(tmp_path, "vector-rag", held_errors=[_err()], vector_payloads={})
    assert "No relevant prior memory" in target.read_text(encoding="utf-8")


def test_inject_vector_rag_leak_guards_outcome_labels(tmp_path):
    # An outcome label smuggled into a retrieved payload must fail the write.
    with pytest.raises(AssertionError):
        inject_rung_memory(
            tmp_path,
            "vector-rag",
            held_errors=[_err()],
            vector_payloads={"leaky": "root cause was commit deadbeefcafe"},
            outcome_labels=["deadbeefcafe"],
        )
    # The guard runs BEFORE the write: the leaky payload never reaches disk.
    assert not (tmp_path / MEMORY_FILENAME).exists()


def test_inject_vector_rag_defaults_to_empty_when_payloads_none(tmp_path):
    target = inject_rung_memory(tmp_path, "vector-rag", held_errors=[_err()])
    assert "No relevant prior memory" in target.read_text(encoding="utf-8")


# --- run_grid: full four-rung ladder threads vector_payloads --------------------


def test_run_grid_four_rung_ladder_scores_every_rung(tmp_path):
    held = [_err(file="src/a.ts", line=12, error_class="TS2345")]
    touched = frozenset({"src/a.ts"})
    # Deterministic per-rung traces: none never reaches the file (floor), the
    # memory rungs reach it and resolve, oracle resolves (ceiling). These are
    # fixture-authored to exercise the SCORING wiring per rung, NOT a real curve.
    runner = StubRunner(
        {
            "none": RunTrace(errors=tuple(held), files_touched=frozenset()),
            "vector-rag": RunTrace(errors=(), files_touched=touched),
            "ours": RunTrace(errors=(), files_touched=touched),
            "oracle": RunTrace(errors=(), files_touched=touched),
        }
    )
    records = run_grid(
        _record(),
        tmp_path,
        held_errors=held,
        runner=runner,
        rungs=FOUR_RUNGS,
        vector_payloads={"v-prior": "vector-retrieved lesson"},
        ours_payloads={"w-prior": "ranked-ledger lesson"},
        oracle_payload="curated oracle prior",
    )
    by_rung = {r.rung: r for r in records}
    assert set(by_rung) == set(FOUR_RUNGS)
    # Every rung emits ONE comparable RewardRecord keyed by (work_id, rung, repeat) —
    # the length pin catches a double-emitted rung the dict collapse would hide.
    assert len(records) == len(FOUR_RUNGS)
    for rec in records:
        assert rec.work_id == "w1"
        assert rec.repeat_idx == 0
    # The scoring pipeline differentiates rungs: floor unreached, memory rungs resolve.
    assert by_rung["none"].components.path_reached is False
    assert by_rung["vector-rag"].components.path_reached is True
    assert by_rung["vector-rag"].components.trace_error_resolved is True


def test_run_grid_vector_rag_injects_the_vector_payload(tmp_path):
    held = [_err()]
    touched = frozenset({"src/a.ts"})
    runner = StubRunner({rung: RunTrace(errors=(), files_touched=touched) for rung in FOUR_RUNGS})
    run_grid(
        _record(),
        tmp_path,
        held_errors=held,
        runner=runner,
        rungs=FOUR_RUNGS,
        vector_payloads={"v-prior": "cosine-nearest prior lesson"},
        ours_payloads={"w-prior": "ranked-ledger lesson"},
        oracle_payload="curated oracle prior",
    )
    # none: no memory file; vector-rag/ours/oracle: their own distinct payload.
    assert not (tmp_path / "w1-none" / MEMORY_FILENAME).exists()
    vec = (tmp_path / "w1-vector-rag" / MEMORY_FILENAME).read_text(encoding="utf-8")
    assert "cosine-nearest prior lesson" in vec
    assert "ranked-ledger lesson" not in vec  # rungs don't cross-contaminate
    # ...in either direction: the vector payload must not bleed into ours/oracle.
    for rung in ("ours", "oracle"):
        other = (tmp_path / f"w1-{rung}" / MEMORY_FILENAME).read_text(encoding="utf-8")
        assert "cosine-nearest prior lesson" not in other


# --- build_curve auto-detect places vector-rag between none and ours ------------


def _reward(rung):
    # A rung's mean reward is read off path_reached+resolved; pin it directly here so
    # the order assertion doesn't depend on the scorer (that is exercised above).
    return RewardRecord(
        work_id="w1",
        rung=rung,
        repeat_idx=0,
        components=RewardComponents(path_reached=True, trace_error_resolved=True),
    )


def test_build_curve_orders_vector_rag_between_none_and_ours():
    # Regression for the ladder-order landmine: vector-rag must sit at index 1 (canonical
    # none < vector-rag < ours <= oracle), NOT get appended after oracle as a rung the
    # auto-detect doesn't recognize. Passing rungs=None exercises _ladder_order's
    # DEFAULT_RUNGS-driven auto-detect, the path a caller who omits `rungs=` takes.
    # Scrambled input order, so a pass proves the ladder is REORDERED to canonical, not
    # merely that input order was preserved.
    scrambled = ("oracle", "none", "ours", "vector-rag")
    curve = build_curve([_reward(rung) for rung in scrambled])
    assert [r.rung for r in curve.rungs] == ["none", "vector-rag", "ours", "oracle"]


def test_recall_only_ladder_refuses_combination_readouts():
    # Regression for _require_full_ladder's AXIS gate (curve.py): the recall ladder
    # reaches four rungs but contains no combination rung (builtin / ours+builtin),
    # so the combination readouts must refuse -- their semantics over a recall-only
    # ladder are a category error (pending mem-do8r.2). The 4-rung must-not-raise
    # pins in test_curve.py all include `builtin`, so they pass under the old
    # count-only gate too; this recall-only ladder is what tells the gates apart.
    curve = build_curve([_reward(rung) for rung in FOUR_RUNGS])
    with pytest.raises(InsufficientLadderError):
        saturation_point(curve)
    with pytest.raises(InsufficientLadderError):
        min_useful_combo(curve)
