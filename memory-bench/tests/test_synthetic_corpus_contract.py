"""Synthetic-corpus contract test (mem-ifm2, TDD red-first).

The synthetic generator already exists (`generators.synthetic_task` →
`BenchmarkSequence`, gated by `generators.memory_necessity_gate`). The GAP: its
output is not yet wired as a DURABLE, firewall-guarded corpus the eval consumes the
SAME way it consumes real WorkRecords (`corpus.load_corpus` → `validity`). This module
makes that contract an EXECUTABLE, runnable spec before the wiring exists — mirroring
the mem-3zos OpenRath Phase 0 pattern. It ships no new substrate and commits zero
eval-design.

Two test groups:

* **Green (the existing reader + firewall + LOO + necessity gate, executable today).**
  These pin the real-corpus invariants on a synthetic-SHAPED record built inline and
  on the real generator's offline necessity verdict. No new wiring needed; they pass
  now and prove the existing machinery already enforces the contract on synthetic
  shapes.

* **Red (the Phase-1 materializer wiring).** `generators.synthetic_corpus` does not
  exist yet, so these are `xfail(strict=True, raises=ModuleNotFoundError)`: today they
  fail because the materializer is absent (the correct TDD red) and the suite stays
  green; when the wiring ships, the import resolves, the bodies run for real, and a
  strict xfail turns an XPASS into a hard failure — forcing whoever lands it to delete
  the marker and own the now-live contract. The assertion bodies ARE the written spec.

HARD GUARD (reserved for Stephanie — NOT decided here): whether synthetic records
SHARE the real WorkRecord schema/store or live in a SEPARATE synthetic corpus. This
test asserts only what the bead mandates as invariants — that the artifact is
loadable by the EXISTING reader projection (`work_ref_from_record` / `load_corpus`),
LOO-boundable, and firewall-separated — and never asserts WHERE it is persisted or
that it co-resides in `.mem/store.db`. The materializer's output is exercised through
the existing reader/firewall only. See the close note + morning ledger.

ZFC: every assertion is a deterministic firewall / structural / temporal check.
"""

from __future__ import annotations

import importlib
import json
from types import ModuleType
from typing import Any

import pytest

from membench.corpus import load_corpus
from membench.generators.memory_necessity_gate import memory_necessity_gate
from membench.generators.synthetic_task import generate_synthetic_sequence
from membench.grading import OutcomeLeakError, outcome_labels
from membench.harbor.workrecord_adapter import WorkRecordLadderAdapter
from membench.validity import QueryWork, assert_no_leak, loo_bounded, work_ref_from_record

# A high-entropy outcome value: any appearance outside the outcome field is an
# unambiguous leak. `commit_sha` is in the firewall's identifying-key set, so the
# existing leak guard recognizes it (mirrors the real-corpus / mem-3zos shape).
SENTINEL = "SENTINELSYNTH000"

# The Phase-1 materializer (the gap this bead pins). Resolved dynamically so this
# module collects cleanly while the wiring is still absent.
MATERIALIZER_MODULE = "membench.generators.synthetic_corpus"

phase1_wiring = pytest.mark.xfail(
    reason=f"{MATERIALIZER_MODULE} (synthetic→corpus materializer) is Phase-1 wiring; "
    "mem-ifm2 is the failing-first contract",
    strict=True,
    raises=ModuleNotFoundError,
)


def _load_materializer() -> ModuleType:
    """Import the Phase-1 synthetic→corpus materializer. Raises `ModuleNotFoundError`
    today — the TDD red the `phase1_wiring` marker expects."""
    return importlib.import_module(MATERIALIZER_MODULE)


def _synthetic_record(
    *,
    work_id: str = "synthetic-incident-runbook-seed0",
    title: str = "Write the incident postmortem",
    started: str = "2026-03-01T00:00:00Z",
    closed: str = "2026-03-02T00:00:00Z",
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A synthetic corpus record in the EXISTING WorkRecord-JSON projection shape the
    reader consumes (`test_corpus._record` mirror). Built inline for the green tests —
    this is the shape the Phase-1 materializer must emit, not a decision that synthetic
    records share the real store."""
    return {
        "work_id": work_id,
        "rig": "synthetic",
        "title": title,
        "lifecycle": {"created": "2026-03-01T00:00:00Z", "started": started, "closed": closed},
        "links": {"supersedes": []},
        "outcome": outcome if outcome is not None else {"commit_sha": SENTINEL},
    }


def _agent_readable_blob(task_dir: Any) -> str:
    return (task_dir / "instruction.md").read_text() + (task_dir / "task.toml").read_text()


# --------------------------------------------------------------------------- #
# Green — existing reader + firewall + LOO + necessity gate, executable today
# --------------------------------------------------------------------------- #
def test_necessity_gate_yields_an_offline_outcome_label() -> None:
    # The synthetic outcome label (admission + the two rewards) is computed
    # deterministically with NO model — it belongs on the label side of the
    # field-separation boundary, never in agent-readable input.
    seq = generate_synthetic_sequence(seed=0)
    result = memory_necessity_gate(seq)
    assert isinstance(result.verdict.accepted, bool)
    assert result.verdict.oracle_reward >= result.verdict.no_memory_reward


def test_synthetic_record_is_loo_boundable_like_the_real_corpus() -> None:
    # A synthetic record carries started/closed, so the temporal leave-one-out reader
    # bounds it exactly like a real WorkRecord: eligible only when closed strictly
    # precedes the query's start.
    ref = work_ref_from_record(_synthetic_record(closed="2026-03-02T00:00:00Z"))
    after = QueryWork(work_id="B", rig="synthetic", started="2026-04-01T00:00:00Z")
    before = QueryWork(work_id="B", rig="synthetic", started="2026-03-01T12:00:00Z")
    assert [r.work_id for r in loo_bounded([ref], after)] == [ref.work_id]
    assert loo_bounded([ref], before) == []  # closed after the boundary → withheld
    assert ref.closed is not None  # temporal field present, like the real corpus


def test_synthetic_record_loadable_by_the_same_corpus_reader() -> None:
    # The SAME reader path that loads real records (`corpus.load_corpus` →
    # `work_ref_from_record`) accepts a synthetic record — no bespoke loader.
    record = _synthetic_record()
    corpus = load_corpus("synthetic.db", runner=lambda _args: {"records": [record]})
    assert [r.work_id for r in corpus] == [record["work_id"]]
    # And the harness LOO re-check accounts for it (no leak when it is in the pool).
    q = QueryWork(work_id="B", rig="synthetic", started="2026-04-01T00:00:00Z")
    assert_no_leak([record["work_id"]], corpus, q)


def test_firewall_separates_synthetic_outcome_from_agent_readable(tmp_path: Any) -> None:
    # Field separation: the sentinel confined to outcome.commit_sha passes leak-safe
    # through the agent-readable task builder and never reaches a task file …
    clean = _synthetic_record(title="Write the incident postmortem")
    created = WorkRecordLadderAdapter(clean, tmp_path / "clean").run()
    assert created
    for task_dir in created:
        assert SENTINEL not in _agent_readable_blob(task_dir)
    # … but the EXISTING firewall raises if that outcome value reaches the title.
    leaked = _synthetic_record(title=f"postmortem of {SENTINEL}")
    with pytest.raises(OutcomeLeakError):
        WorkRecordLadderAdapter(leaked, tmp_path / "leaked").run()
    assert SENTINEL in outcome_labels(leaked)  # recognized as an outcome label


# --------------------------------------------------------------------------- #
# Red — the Phase-1 synthetic→corpus materializer (xfail until the wiring ships)
# --------------------------------------------------------------------------- #
@phase1_wiring
def test_materialize_record_is_loo_boundable() -> None:
    mat = _load_materializer()
    seq = generate_synthetic_sequence(seed=0)
    necessity = memory_necessity_gate(seq)
    record = mat.materialize_record(
        seq, necessity, started="2026-03-01T00:00:00Z", closed="2026-03-02T00:00:00Z"
    )
    ref = work_ref_from_record(record)
    assert ref.closed is not None and ref.closed > "2026-03-01T00:00:00Z"
    after = QueryWork(work_id="B", rig=ref.rig, started="2026-04-01T00:00:00Z")
    assert ref.work_id in {r.work_id for r in loo_bounded([ref], after)}


@phase1_wiring
def test_materialize_routes_outcome_label_into_outcome_only() -> None:
    mat = _load_materializer()
    seq = generate_synthetic_sequence(seed=0)
    necessity = memory_necessity_gate(seq)
    record = mat.materialize_record(
        seq,
        necessity,
        started="2026-03-01T00:00:00Z",
        closed="2026-03-02T00:00:00Z",
        outcome_sentinel=SENTINEL,
    )
    # The sentinel lands ONLY in the outcome field; nowhere an agent could read it
    # (schema-agnostic: not in the record with outcome removed).
    assert SENTINEL in json.dumps(record["outcome"])
    non_outcome = {k: v for k, v in record.items() if k != "outcome"}
    assert SENTINEL not in json.dumps(non_outcome)
    # The offline necessity verdict is carried on the label side, not invented.
    assert json.dumps(record["outcome"])  # non-empty outcome


@phase1_wiring
def test_materialize_positive_path_loads_through_the_same_reader() -> None:
    mat = _load_materializer()
    seq = generate_synthetic_sequence(seed=0)
    necessity = memory_necessity_gate(seq)
    # Positive path: a known-good synthetic task materializes SUCCESSFULLY and the SAME
    # corpus reader loads it (distinguishes "rejected a leak" from "failed to parse").
    record = mat.materialize_record(
        seq, necessity, started="2026-03-01T00:00:00Z", closed="2026-03-02T00:00:00Z"
    )
    corpus = load_corpus("synthetic.db", runner=lambda _args: {"records": [record]})
    assert [r.work_id for r in corpus] == [record["work_id"]]


@phase1_wiring
def test_materialized_record_is_leak_safe_through_the_ladder(tmp_path: Any) -> None:
    mat = _load_materializer()
    seq = generate_synthetic_sequence(seed=0)
    necessity = memory_necessity_gate(seq)
    # End-to-end: the materialized record passes the EXISTING agent-readable firewall —
    # the synthetic outcome never reaches a task file.
    record = mat.materialize_record(
        seq,
        necessity,
        started="2026-03-01T00:00:00Z",
        closed="2026-03-02T00:00:00Z",
        outcome_sentinel=SENTINEL,
    )
    created = WorkRecordLadderAdapter(record, tmp_path).run()
    assert created
    for task_dir in created:
        assert SENTINEL not in _agent_readable_blob(task_dir)
