"""Forward-capture projection — the Phase-0 firewall boundary (PRD §Phase 0).

A runtime `Session`-shaped dict is the OpenRath inversion of mem's field-separated
graph: it bundles label-free join keys, a memory-event payload, AND outcome lineage
(`pr`/`commit_sha`/`base_commit`) into one value that erases field provenance.
This module is the ONLY projecting boundary that re-establishes the separation:

- `project_session_to_record` routes the outcome identifiers ONLY into the
  label-side `record['outcome']` — the leak-side the firewall scans against
  (`grading.leak_guard.outcome_labels`). They never travel anywhere else.
- `project_memory_event` projects ONLY the leak-safe, label-free memory-event
  fields a worker may read, under a STRICT ALLOW-LIST: an unrecognized field
  RAISES (`ForwardCaptureFieldError`) rather than being silently dropped, so a
  producer that grows a novel (possibly outcome-correlated) column is a loud
  failure. This mirrors the TS `MemoryEventSchema.strict()` allow-list
  (src/schemas/memory-event.ts) — the TS schema is the source of truth for the
  worker-readable shape; this is the Python projection of the same field set.
- `worker_readable_text` renders exactly what a worker could see from a captured
  record, so the caller can run it through `assert_no_outcome_leak` — the contract
  is enforced by the existing guard, not re-implemented here.

ZFC: pure mechanism — key routing and allow-list filtering, no semantic judgment.
"""

from __future__ import annotations

from typing import Any

# The worker-readable memory-event fields — the leak-safe allow-list, drawn from
# the canonical TS `MemoryEventSchema` (src/schemas/memory-event.ts). Outcome
# identifiers are deliberately absent: a captured memory-event is PRE-OUTCOME and
# carries no label by construction.
_MEMORY_EVENT_FIELDS = frozenset(
    {
        "id",
        "session",
        "work_id",
        "op",
        "backend",
        "memory_ref",
        "used_in",
        "concrete_tool",
        "payload",
        "source",
        "occurred_at",
        "created_at",
    }
)

# The outcome identifiers — kept in lockstep with `leak_guard._IDENTIFYING_KEYS`,
# the high-entropy values a held-out bead must never expose. Routed label-side only.
_OUTCOME_KEYS = ("pr", "commit_sha", "base_commit")

# The Session keys the projector recognizes. A novel top-level Session field is
# also a strict-allow-list violation (same firewall principle as the event fields).
_SESSION_FIELDS = frozenset({"work_id", "rig", "started", "closed", "outcome", "memory_event"})


class ForwardCaptureFieldError(ValueError):
    """Raised when a Session / memory-event payload carries a field outside the
    leak-safe allow-list — a validity failure (the field may be outcome-correlated
    and unscanned), never silently dropped."""

    def __init__(self, where: str, unknown: list[str]) -> None:
        self.where = where
        self.unknown = unknown
        super().__init__(
            f"{where} carries fields outside the forward-capture allow-list: "
            f"{', '.join(sorted(unknown))}"
        )


def project_session_to_record(session: dict[str, Any]) -> dict[str, Any]:
    """Project a runtime Session dict into a field-separated WorkRecord-shaped dict.

    Outcome identifiers route ONLY into `record['outcome']` (the label-side the
    firewall scans). Label-free join keys (`work_id`/`rig`/`started`/`closed`) stay
    on the record body. A novel top-level Session field RAISES (strict allow-list)."""
    unknown = [k for k in session if k not in _SESSION_FIELDS]
    if unknown:
        raise ForwardCaptureFieldError("Session", unknown)
    outcome_in = session.get("outcome") or {}
    return {
        "work_id": session["work_id"],
        "rig": session["rig"],
        "lifecycle": {
            "started": session.get("started"),
            "closed": session.get("closed"),
        },
        "outcome": {key: outcome_in[key] for key in _OUTCOME_KEYS if key in outcome_in},
    }


def project_memory_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a memory-event payload onto the worker-readable allow-list.

    A field outside `_MEMORY_EVENT_FIELDS` RAISES (it could smuggle an unscanned,
    outcome-correlated column past the firewall). Outcome identifiers can never
    appear because they are not in the allow-list — a payload that names one is an
    unknown field and RAISES, not a silent drop."""
    unknown = [k for k in payload if k not in _MEMORY_EVENT_FIELDS]
    if unknown:
        raise ForwardCaptureFieldError("memory_event", unknown)
    return dict(payload)


def worker_readable_text(record: dict[str, Any]) -> str:
    """The agent-readable text a worker could see from a captured record: the
    label-free join keys only. The outcome block is NEVER rendered — it exists on
    the record solely as the label-side the firewall scans against."""
    lifecycle = record.get("lifecycle") or {}
    parts = [
        f"work_id={record.get('work_id', '')}",
        f"rig={record.get('rig', '')}",
        f"started={lifecycle.get('started', '')}",
    ]
    return "\n".join(parts)
