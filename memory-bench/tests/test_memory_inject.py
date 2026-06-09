"""Tests for per-rung memory CONTENT injection (mem-apg.3d).

mem-apg.2's adapter emits the per-rung info SPEC (which memory a rung MAY access);
this module writes the actual memory CONTENT into each rung's task dir. The
load-bearing validity property (architect H3): the `oracle` rung must NOT leak the
held-out bead's OWN trace_error signature into the agent-readable memory, because
that signature is exactly the answer the deterministic scorer checks. A self-leak
is a validity bug and must fail the run loudly, never be silently stripped.
"""

import pytest

from membench.grading import TraceErrorRef
from membench.harbor.memory_inject import (
    MEMORY_FILENAME,
    DeferredRungError,
    OracleSelfLeakError,
    inject_rung_memory,
)


def _err(tool="tsc", file="src/a.ts", line=12, error_class="TS2345", signature=None):
    sig = signature if signature is not None else f"{tool}:{file}:{line}:{error_class}"
    return TraceErrorRef(tool=tool, file=file, line=line, error_class=error_class, signature=sig)


def _memory_text(task_dir):
    return (task_dir / MEMORY_FILENAME).read_text(encoding="utf-8")


# --- none: no memory written --------------------------------------------------


def test_none_rung_writes_no_memory_file(tmp_path):
    inject_rung_memory(tmp_path, "none", held_errors=[_err()])
    assert not (tmp_path / MEMORY_FILENAME).exists()


def test_none_rung_returns_none_path(tmp_path):
    assert inject_rung_memory(tmp_path, "none", held_errors=[_err()]) is None


# --- ours: distilled payloads written, outcome-label leak guarded -------------


def test_ours_rung_writes_payloads(tmp_path):
    written = inject_rung_memory(
        tmp_path,
        "ours",
        held_errors=[_err()],
        ours_payloads={"w-prior": "lesson: avoid the off-by-one in the cursor"},
    )
    assert written == tmp_path / MEMORY_FILENAME
    text = _memory_text(tmp_path)
    assert "off-by-one" in text
    assert "w-prior" in text


def test_ours_rung_empty_payloads_writes_empty_memory(tmp_path):
    # An empty retrieval is a real outcome (no relevant prior memory), not an
    # error: the rung still gets a memory file so the agent sees "ours was tried".
    written = inject_rung_memory(tmp_path, "ours", held_errors=[_err()], ours_payloads={})
    assert written == tmp_path / MEMORY_FILENAME
    assert (tmp_path / MEMORY_FILENAME).exists()


def test_ours_rung_raises_on_outcome_label_leak(tmp_path):
    from membench.grading import OutcomeLeakError

    with pytest.raises(OutcomeLeakError):
        inject_rung_memory(
            tmp_path,
            "ours",
            held_errors=[_err()],
            ours_payloads={"w-prior": "see commit deadbeefcafe for the fix"},
            outcome_labels=("deadbeefcafe",),
        )


def test_ours_rung_leak_aborts_before_write(tmp_path):
    from membench.grading import OutcomeLeakError

    with pytest.raises(OutcomeLeakError):
        inject_rung_memory(
            tmp_path,
            "ours",
            held_errors=[_err()],
            ours_payloads={"w": "merged in PR #4242"},
            outcome_labels=("#4242",),
        )
    assert not (tmp_path / MEMORY_FILENAME).exists()


# --- oracle: H3 self-leak guard (the load-bearing validity property) ----------


def test_oracle_rung_writes_clean_payload(tmp_path):
    written = inject_rung_memory(
        tmp_path,
        "oracle",
        held_errors=[_err(file="src/a.ts", line=12, error_class="TS2345")],
        oracle_payload="prior lesson: validate the schema boundary before parsing",
    )
    assert written == tmp_path / MEMORY_FILENAME
    assert "schema boundary" in _memory_text(tmp_path)


def test_oracle_rung_raises_on_full_signature_self_leak(tmp_path):
    held = _err(tool="tsc", file="src/a.ts", line=12, error_class="TS2345")
    with pytest.raises(OracleSelfLeakError):
        inject_rung_memory(
            tmp_path,
            "oracle",
            held_errors=[held],
            # Contains the held bead's OWN full signature -- the answer.
            oracle_payload=f"the failure was {held.signature} on the cursor path",
        )


def test_oracle_rung_raises_on_relaxed_signature_self_leak(tmp_path):
    # The scorer's avoid-axis keys on the RELAXED signature, so a payload that
    # leaks only the relaxed key still hands the agent the answer.
    held = _err(tool="tsc", file="src/sub/a.ts", line=12, error_class="TS2345")
    with pytest.raises(OracleSelfLeakError):
        inject_rung_memory(
            tmp_path,
            "oracle",
            held_errors=[held],
            oracle_payload="avoid recurrence of tsc:a.ts:TS2345 here",
        )


def test_oracle_self_leak_aborts_before_write(tmp_path):
    held = _err()
    with pytest.raises(OracleSelfLeakError):
        inject_rung_memory(
            tmp_path,
            "oracle",
            held_errors=[held],
            oracle_payload=f"known issue: {held.signature}",
        )
    assert not (tmp_path / MEMORY_FILENAME).exists()


def test_oracle_rung_also_guards_outcome_labels(tmp_path):
    from membench.grading import OutcomeLeakError

    with pytest.raises(OutcomeLeakError):
        inject_rung_memory(
            tmp_path,
            "oracle",
            held_errors=[_err()],
            oracle_payload="the canonical fix lives at commit feedface0001",
            outcome_labels=("feedface0001",),
        )


def test_oracle_self_leak_message_names_the_signature(tmp_path):
    held = _err()
    with pytest.raises(OracleSelfLeakError) as exc:
        inject_rung_memory(
            tmp_path, "oracle", held_errors=[held], oracle_payload=f"x {held.signature} y"
        )
    assert held.signature in str(exc.value)


# --- deferred rungs (builtin / ours+builtin) ----------------------------------


@pytest.mark.parametrize("rung", ["builtin", "ours+builtin"])
def test_deferred_rungs_raise(tmp_path, rung):
    with pytest.raises(DeferredRungError):
        inject_rung_memory(tmp_path, rung, held_errors=[_err()])


def test_unknown_rung_raises(tmp_path):
    with pytest.raises(ValueError):
        inject_rung_memory(tmp_path, "nonsense", held_errors=[_err()])


def test_empty_held_errors_is_caller_error(tmp_path):
    # The held-out set is "beads with >=1 trace_error"; an empty set means the
    # oracle guard has nothing to protect -- fail loud rather than write blind.
    with pytest.raises(ValueError):
        inject_rung_memory(tmp_path, "oracle", held_errors=[], oracle_payload="x")
