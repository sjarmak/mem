"""M3 + M4 — control-condition payload builders (the CI-testable discipline core).

The headline grid needs two brute-force control conditions:

* **raw-trajectory (M3)** — inject the bundle's RAW transcript instead of distilled
  memory. Truncation to a char budget is REPORTED, never silent (premortem lens 5:
  the controls blow the context budget and silently truncate), and keeps the
  transcript TAIL — the resolution lives at the end of a session, so head-keep
  would delete exactly the memory-relevant span (mem-io7c).
* **full-context (M4)** — inject ALL in-scope prior work, LOO-bounded
  (``loo_excluded_work_ids`` withheld), ordered by temporal proximity to the query
  work so truncation drops the LEAST relevant records — the brute-force ceiling
  control.

Both payloads pass through the SAME probe leak guard (``assert_probe_task_clean``)
before they could be baked into an image — the leak-guard verdict is a first-class,
fail-loud signal, not a silent skip (premortem lens 5: the controls trip the LOO
leak guard at scale and the rejections become coverage holes). The guard runs on the
KEPT span (the exact text an agent could see); truncated-away text is never
injected, so it cannot leak. This module is the payload + guard core; wiring the
conditions into the multi-hour Docker grid driver is a separate operational step.
"""

from __future__ import annotations

import pytest

from membench.bundle.replay import ReplayResult
from membench.grading.leak_guard import OutcomeLeakError
from membench.harbor.control_conditions import (
    FULL_CONTEXT,
    RAW_TRAJECTORY,
    InScopeWork,
    assert_control_payload_clean,
    full_context_payload,
    raw_trajectory_payload,
)
from membench.schemas.bundle import BundleEnv, TaskBundle


def _bundle(*, gold_diff_text="", loo=("w1",)):
    file_diffs = (("src/app.py", gold_diff_text),) if gold_diff_text else ()
    return TaskBundle(
        work_id="w1",
        rig="r",
        issue_title="add an endpoint",
        trace_ref="trace.jsonl",
        output=ReplayResult(calls=(), file_diffs=file_diffs, replay_success_rate=1.0),
        env=BundleEnv(repo="repo", base_commit="DEADBEEFCAFE", base_image="img"),
        loo_excluded_work_ids=loo,
    )


def _work(text: str, closed_at: str = "2026-01-01T00:00:00+00:00") -> InScopeWork:
    return InScopeWork(text=text, closed_at=closed_at)


# --------------------------------------------------------------------------- #
# M3 — raw trajectory + truncation never silent, tail kept
# --------------------------------------------------------------------------- #
def test_raw_trajectory_short_payload_not_truncated():
    payload = raw_trajectory_payload(_bundle(), "a short transcript", max_chars=1000)
    assert payload.condition == RAW_TRAJECTORY
    assert payload.truncation.truncated is False
    assert payload.truncation.original_chars == payload.truncation.kept_chars
    # Kept-span offsets cover the whole source when nothing was dropped.
    assert payload.truncation.kept_start == 0
    assert payload.truncation.kept_end == payload.truncation.original_chars
    assert "short transcript" in payload.text


def test_raw_trajectory_truncation_is_reported_never_silent():
    transcript = "x" * 5000
    payload = raw_trajectory_payload(_bundle(), transcript, max_chars=1000)
    assert payload.truncation.truncated is True
    assert payload.truncation.original_chars == 5000
    assert payload.truncation.kept_chars == 1000
    # The fact of truncation is visible in the payload text too (no silent drop).
    assert "truncated" in payload.text.lower()


def test_raw_trajectory_truncation_keeps_tail_and_records_offsets():
    # The resolution lives at the END of a session; head-keep would delete it.
    transcript = "EXPLORATORY-OPENING " + ("x" * 5000) + " THE-RESOLUTION-TAIL"
    payload = raw_trajectory_payload(_bundle(), transcript, max_chars=1000)
    assert "THE-RESOLUTION-TAIL" in payload.text
    assert "EXPLORATORY-OPENING" not in payload.text
    # Offsets pin the kept span to the tail of the source, making it auditable.
    assert payload.truncation.kept_start == len(transcript) - 1000
    assert payload.truncation.kept_end == len(transcript)
    assert payload.truncation.kept_chars == 1000


def test_raw_trajectory_leak_guard_catches_gold_diff():
    bundle = _bundle(gold_diff_text="+    return secret_endpoint()")
    leaking = raw_trajectory_payload(
        bundle, "trace: +    return secret_endpoint()", max_chars=10000
    )
    with pytest.raises(OutcomeLeakError):
        assert_control_payload_clean(leaking, bundle)


def test_raw_trajectory_clean_transcript_passes_guard():
    bundle = _bundle(gold_diff_text="+    return secret_endpoint()")
    clean = raw_trajectory_payload(bundle, "trace: the agent explored the repo", max_chars=10000)
    assert_control_payload_clean(clean, bundle)  # does not raise


def test_raw_trajectory_leak_guard_runs_on_kept_span():
    # The guard checks what the agent could SEE. A gold-diff quote in the
    # truncated-away head is never injected, so it cannot leak; the same quote
    # in the kept tail fails loud.
    bundle = _bundle(gold_diff_text="+    return secret_endpoint()")
    quote = "+    return secret_endpoint()"
    dropped_head = raw_trajectory_payload(bundle, quote + ("x" * 5000), max_chars=1000)
    assert_control_payload_clean(dropped_head, bundle)  # quote was dropped, no leak
    kept_tail = raw_trajectory_payload(bundle, ("x" * 5000) + quote, max_chars=1000)
    with pytest.raises(OutcomeLeakError):
        assert_control_payload_clean(kept_tail, bundle)


# --------------------------------------------------------------------------- #
# M4 — full context, LOO-bounded, temporal-proximity ordered
# --------------------------------------------------------------------------- #
def test_full_context_is_loo_bounded():
    bundle = _bundle(loo=("w1", "sibling-2"))
    in_scope = {
        "w1": _work("the bundle's own work (must be withheld)"),
        "sibling-2": _work("a sibling (must be withheld)"),
        "prior-9": _work("legit prior work to inject"),
        "prior-3": _work("more legit prior work"),
    }
    payload = full_context_payload(bundle, in_scope, max_chars=100000)
    assert payload.condition == FULL_CONTEXT
    assert "prior-9" in payload.text and "prior-3" in payload.text
    # LOO-excluded ids are withheld by id key — neither own work nor sibling appears.
    assert "withheld" not in payload.text


def test_full_context_orders_by_temporal_proximity():
    # All in-scope work closed strictly before the query work (LOO), so proximity
    # to the query work is recency: latest closed_at first.
    bundle = _bundle(loo=("w1",))
    in_scope = {
        "prior-old": _work("oldest record", "2025-01-01T00:00:00+00:00"),
        "prior-new": _work("newest record", "2026-06-01T00:00:00+00:00"),
        "prior-mid": _work("middle record", "2025-08-01T00:00:00+00:00"),
    }
    payload = full_context_payload(bundle, in_scope, max_chars=100000)
    assert (
        payload.text.index("prior-new")
        < payload.text.index("prior-mid")
        < payload.text.index("prior-old")
    )


def test_full_context_equal_timestamps_tiebreak_on_work_id():
    bundle = _bundle(loo=("w1",))
    when = "2026-01-01T00:00:00+00:00"
    in_scope = {
        "prior-b": _work("bbb", when),
        "prior-a": _work("aaa", when),
        "prior-c": _work("ccc", when),
    }
    payload = full_context_payload(bundle, in_scope, max_chars=100000)
    assert (
        payload.text.index("prior-a")
        < payload.text.index("prior-b")
        < payload.text.index("prior-c")
    )


def test_full_context_truncation_reported():
    bundle = _bundle(loo=("w1",))
    in_scope = {"prior-1": _work("y" * 5000), "prior-2": _work("z" * 5000)}
    payload = full_context_payload(bundle, in_scope, max_chars=2000)
    assert payload.truncation.truncated is True
    assert payload.truncation.kept_chars <= 2000


def test_full_context_truncation_drops_least_relevant():
    # Most-relevant-first ordering + head-keep: the budget squeezes out the
    # temporally distant records, never the closest ones.
    bundle = _bundle(loo=("w1",))
    in_scope = {
        "prior-old": _work("OLDEST-" + "o" * 200, "2025-01-01T00:00:00+00:00"),
        "prior-new": _work("NEWEST-" + "n" * 200, "2026-06-01T00:00:00+00:00"),
    }
    payload = full_context_payload(bundle, in_scope, max_chars=150)
    assert "NEWEST-" in payload.text
    assert "OLDEST-" not in payload.text
    # Head of the ordered body is kept; offsets say so.
    assert payload.truncation.kept_start == 0
    assert payload.truncation.kept_end == 150


def test_full_context_leak_guard_runs_on_payload():
    bundle = _bundle(gold_diff_text="+    leaked_gold_line()")
    in_scope = {"prior-9": _work("prior work that quotes +    leaked_gold_line() verbatim")}
    leaking = full_context_payload(bundle, in_scope, max_chars=100000)
    with pytest.raises(OutcomeLeakError):
        assert_control_payload_clean(leaking, bundle)
