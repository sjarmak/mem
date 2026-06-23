"""OpenRath Phase 0 — leak-injection firewall contract test (mem-3zos, TDD red-first).

This module makes the OpenRath ingest firewall an EXECUTABLE, runnable spec *before*
any adapter exists (PRD `docs/prd-openrath-incorporation.md`, "Phase 0"). It ships no
adapter and commits zero architecture — it only pins the contract the Phase 1
projecting adapter must satisfy, and proves the EXISTING validity firewall already
bites on the OpenRath threat shape.

Two test groups:

* **Green (the existing firewall, executable today).** A synthetic OpenRath `Session`
  carries a sentinel outcome value (`commit_sha = 'SENTINELLEAK0000'`) hidden in its
  runtime lineage. These tests prove the EXISTING `grading.leak_guard` /
  `WorkRecordLadderAdapter` firewall recognizes that value as an outcome label and
  refuses to write it into any agent-readable file. No adapter needed; they pass now.

* **Adapter contract (Phase 1, now live).** The `Session -> WorkRecord` / `cut` /
  `MemoryEvent` projection in `membench.openrath.adapter` ships in Phase 1 (mem-m0ak).
  These tests were committed red-first as `xfail(strict=True, raises=ModuleNotFoundError)`
  while the module was absent; landing the adapter resolved the import and made the
  bodies run for real, so the markers were removed and the assertions are now
  genuine, load-bearing passing checks. The assertions in these bodies ARE the
  written spec the adapter satisfies.

ZFC: every assertion is a deterministic firewall / structural check — no semantic
judgment in code.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from types import ModuleType
from typing import Any

import pytest

from membench.grading import OutcomeLeakError, outcome_labels
from membench.grading.leak_guard import _IDENTIFYING_KEYS
from membench.harbor.workrecord_adapter import WorkRecordLadderAdapter

# The planted outcome label. It is high-entropy and unique so any appearance in
# agent-readable text is unambiguously a leak, never incidental vocabulary.
SENTINEL = "SENTINELLEAK0000"

# A known-good runtime lineage SHA — legitimate provenance (`fork_point`), NOT an
# outcome label, so it is allowed to ride into a `cut` event.
GOOD_FORK_SHA = "base0000feedfacecafe0000feedfacecafe0000"

# Phase 1's projecting adapter (PRD Phase 1 — "Pure projecting adapter"). Resolved
# dynamically; it now exists (mem-m0ak), so the import resolves and the contract
# bodies below run for real.
ADAPTER_MODULE = "membench.openrath.adapter"


def _load_adapter() -> ModuleType:
    """Import the Phase 1 projecting adapter."""
    return importlib.import_module(ADAPTER_MODULE)


def _good_session() -> dict[str, Any]:
    """A minimal but representative OpenRath-shaped `Session`: runtime lineage with a
    fork point AND the sentinel outcome value hiding in it, plus a memory-event record
    and token usage. Mirrors the first-class state OpenRath composes (PRD TL;DR)."""
    return {
        "session_id": "sess-openrath-0001",
        # Label-free task framing — the only thing an agent may legitimately read.
        "title": "Fix flaky uploader retry under network jitter",
        "lineage": {
            "fork_point": {"commit": GOOD_FORK_SHA, "branch": "main", "ref_kind": "git-sha"},
            # The held-out outcome label, smuggled into runtime lineage. The projection
            # must route this to outcome ONLY; the firewall must catch it if it escapes.
            "commit_sha": SENTINEL,
        },
        "memory_events": [
            {
                "concrete_tool": "mem.retrieve",
                "operation": "read",
                "backend": "vector_db",
                "query": "uploader retry backoff",
                "retrieved_ids": ["mem-aaa", "mem-bbb"],
                "used": True,
                "timestamp": "2026-06-01T00:00:00Z",
            }
        ],
        "tokens": {"in": 1200, "out": 340},
        "rig": "mem",
        "started": "2026-06-01T00:00:00Z",
    }


def _agent_readable_blob(task_dir: Any) -> str:
    return (task_dir / "instruction.md").read_text() + (task_dir / "task.toml").read_text()


def _record(
    *,
    title: str,
    outcome: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A minimal closed WorkRecord shaped for `WorkRecordLadderAdapter` (mirrors the
    fixture in `test_workrecord_adapter.py`)."""
    rec: dict[str, Any] = {
        "work_id": "w-openrath-1",
        "rig": "mem",
        "title": title,
        "lifecycle": {
            "created": "2026-01-01T00:00:00Z",
            "started": "2026-01-10T00:00:00Z",
            "closed": "2026-02-01T00:00:00Z",
            "status": "closed",
        },
    }
    if outcome is not None:
        rec["outcome"] = dict(outcome)
    return rec


# --------------------------------------------------------------------------- #
# Green — the EXISTING firewall, executable today (no adapter required)
# --------------------------------------------------------------------------- #
def test_commit_sha_is_a_recognized_outcome_identifier() -> None:
    # The firewall's identifying-key set already covers commit_sha, so the sentinel
    # planted in lineage IS an outcome label the guard will scan for.
    assert "commit_sha" in _IDENTIFYING_KEYS
    assert SENTINEL in outcome_labels({"outcome": {"commit_sha": SENTINEL}})


def test_existing_firewall_raises_when_sentinel_reaches_agent_readable(tmp_path: Any) -> None:
    # The threat: the sentinel lands in the agent-readable title (a mis-projection).
    # The existing ladder adapter must REFUSE — raise and leave nothing on disk.
    rec = _record(title=f"port of {SENTINEL}", outcome={"commit_sha": SENTINEL})
    with pytest.raises(OutcomeLeakError):
        WorkRecordLadderAdapter(rec, tmp_path).run()
    assert list(tmp_path.iterdir()) == []  # validate-all-then-write: no partial output


def test_existing_firewall_passes_when_sentinel_confined_to_outcome(tmp_path: Any) -> None:
    # The safe shape: the sentinel is confined to outcome.commit_sha and the title is
    # label-free. The firewall lets the task through AND the sentinel never appears in
    # any agent-readable file — distinguishing "leak-safe" from "rejected".
    rec = _record(title="Fix flaky uploader retry", outcome={"commit_sha": SENTINEL})
    created = WorkRecordLadderAdapter(rec, tmp_path).run()
    assert created
    for task_dir in created:
        assert SENTINEL not in _agent_readable_blob(task_dir)


# --------------------------------------------------------------------------- #
# The Phase 1 projecting-adapter contract (live now that the adapter has shipped)
# --------------------------------------------------------------------------- #
def test_project_routes_commit_sha_into_outcome_only() -> None:
    adapter = _load_adapter()
    record = adapter.project_session_to_record(_good_session())
    # (a) the sentinel is routed ONLY into outcome.commit_sha …
    assert record["outcome"]["commit_sha"] == SENTINEL
    # … and nowhere an agent could read it: the title and every non-outcome field.
    assert SENTINEL not in record["title"]
    non_outcome = {k: v for k, v in record.items() if k != "outcome"}
    assert SENTINEL not in json.dumps(non_outcome)


def test_project_drops_memory_event_payload_from_record() -> None:
    adapter = _load_adapter()
    record = adapter.project_session_to_record(_good_session())
    # The Session's memory-event payload (retrieved_ids etc.) is NOT folded into the
    # WorkRecord — it is field-separated out, projected only via project_memory_events.
    blob = json.dumps(record)
    assert "memory_events" not in record
    assert "mem-aaa" not in blob and "mem-bbb" not in blob


def test_project_fork_point_into_cut_event_positive_path() -> None:
    adapter = _load_adapter()
    cuts = adapter.project_cut_events(_good_session())
    # Positive path: a known-good fork_point SUCCESSFULLY becomes a provenance `cut`
    # event (kind/ref/ref_kind), proving the adapter parses, not just rejects.
    assert len(cuts) == 1
    cut = cuts[0]
    assert cut["kind"] == "cut"
    assert cut["ref"] == GOOD_FORK_SHA
    assert cut["ref_kind"] == "git-sha"


def test_project_memory_event_positive_path() -> None:
    from membench.schemas.memory_event import MemoryEvent

    adapter = _load_adapter()
    events = adapter.project_memory_events(_good_session())
    # Positive path: a known-good memory-event record SUCCESSFULLY becomes a typed
    # MemoryEvent (distinguishes "parsed a good event" from "failed to parse").
    assert len(events) == 1
    assert isinstance(events[0], MemoryEvent)
    assert events[0].retrieved_ids == ["mem-aaa", "mem-bbb"]


def test_project_rejects_novel_session_field() -> None:
    adapter = _load_adapter()
    # Allow-list, not deny-list: an unrecognized top-level field is an unaudited
    # channel and must RAISE, never be silently dropped (silent drop = an exfiltration
    # path nobody reviewed).
    hostile = _good_session()
    hostile["exfil_blob"] = SENTINEL
    with pytest.raises(ValueError):
        adapter.project_session_to_record(hostile)


def test_projected_record_is_leak_safe_through_the_ladder(tmp_path: Any) -> None:
    adapter = _load_adapter()
    # End-to-end composition: the projected record must pass the EXISTING ladder
    # firewall with the sentinel confined to outcome and absent from every task file.
    record = adapter.project_session_to_record(_good_session())
    created = WorkRecordLadderAdapter(record, tmp_path).run()
    assert created
    for task_dir in created:
        assert SENTINEL not in _agent_readable_blob(task_dir)
