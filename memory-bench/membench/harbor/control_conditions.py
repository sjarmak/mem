"""M3 + M4 — control-condition payload builders + the on-payload leak guard.

Two brute-force controls for the headline grid, built as the distilled-memory arm's
opposites:

* **raw-trajectory (M3)** — the bundle's RAW transcript, undistilled. The injected
  ceiling on "what was in the trajectory".
* **full-context (M4)** — ALL in-scope prior work, ``loo_excluded_work_ids`` withheld.
  The brute-force ceiling on "what prior work could help".

Both honour two disciplines the premortem (lens 5) named as the controls' failure
modes: truncation to a char budget is REPORTED, never silent; and every payload is
run through the SAME probe leak guard (``assert_probe_task_clean``) before it could
reach an agent — a raw transcript or a prior-work dump is far likelier to quote the
gold diff / base_commit than a distilled lesson, so the guard verdict is a
fail-loud, first-class signal.

These are the payload + guard core (pure, CI-testable). Baking a control payload
into the Harbor image and adding the condition to the multi-hour Docker grid driver
reuses the existing ``inject_context`` / ``_bake_memory_into_env`` path and is a
separate operational step.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from membench.harbor.probe_gate import assert_probe_task_clean
from membench.schemas.bundle import TaskBundle

RAW_TRAJECTORY = "raw-trajectory"
FULL_CONTEXT = "full-context"

# The injected payload lands in this single agent-readable file (the same target the
# distilled-memory arm uses), so one leak-guard call covers the whole payload.
_PAYLOAD_FILE = "MEMORY.md"


class PayloadTruncation(BaseModel):
    """How much of the source survived the char budget. ``truncated`` makes the drop
    explicit so a coverage hole can never read as full coverage."""

    model_config = ConfigDict(frozen=True)

    original_chars: int
    kept_chars: int
    truncated: bool


class ControlPayload(BaseModel):
    """One control condition's injectable text + its truncation record."""

    model_config = ConfigDict(frozen=True)

    condition: str
    text: str
    truncation: PayloadTruncation


def _truncate(text: str, max_chars: int) -> tuple[str, PayloadTruncation]:
    if max_chars < 0:
        raise ValueError(f"max_chars must be >= 0, got {max_chars}")
    if len(text) <= max_chars:
        return text, PayloadTruncation(
            original_chars=len(text), kept_chars=len(text), truncated=False
        )
    kept = text[:max_chars]
    return kept, PayloadTruncation(original_chars=len(text), kept_chars=len(kept), truncated=True)


def _wrap(header: str, body: str, truncation: PayloadTruncation) -> str:
    note = (
        f"\n\n[TRUNCATED: kept {truncation.kept_chars} of {truncation.original_chars} chars]"
        if truncation.truncated
        else ""
    )
    return f"# {header}\n\n{body}{note}\n"


def raw_trajectory_payload(
    bundle: TaskBundle, transcript_text: str, *, max_chars: int
) -> ControlPayload:
    """M3: the bundle's raw transcript as injected context. Truncation to
    ``max_chars`` is recorded AND surfaced in the payload text — never silent."""
    body, truncation = _truncate(transcript_text, max_chars)
    text = _wrap(f"Raw trajectory for {bundle.work_id}", body, truncation)
    return ControlPayload(condition=RAW_TRAJECTORY, text=text, truncation=truncation)


def full_context_payload(
    bundle: TaskBundle, in_scope: Mapping[str, str], *, max_chars: int
) -> ControlPayload:
    """M4: all in-scope prior work, LOO-bounded. Records whose work_id is in
    ``loo_excluded_work_ids`` (own work + siblings) are withheld BY ID before the
    payload is built, so the LOO boundary is mechanical, not a content heuristic."""
    excluded = set(bundle.loo_excluded_work_ids)
    kept = {wid: text for wid, text in in_scope.items() if wid not in excluded}
    body = "\n\n".join(f"## {wid}\n{text}" for wid, text in sorted(kept.items()))
    body, truncation = _truncate(body, max_chars)
    text = _wrap(f"Full in-scope prior work for {bundle.work_id}", body, truncation)
    return ControlPayload(condition=FULL_CONTEXT, text=text, truncation=truncation)


def assert_control_payload_clean(payload: ControlPayload, bundle: TaskBundle) -> None:
    """Run the probe leak guard on a control payload before it could be baked. A raw
    transcript / prior-work dump that quotes the gold diff, base_commit, or a
    verification marker raises ``OutcomeLeakError`` — the run fails loud, the payload
    is never silently scrubbed."""
    assert_probe_task_clean({_PAYLOAD_FILE: payload.text}, bundle)
