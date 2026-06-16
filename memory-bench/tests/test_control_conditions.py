"""M3 + M4 — control-condition payload builders (the CI-testable discipline core).

The headline grid needs two brute-force control conditions:

* **raw-trajectory (M3)** — inject the bundle's RAW transcript instead of distilled
  memory. Truncation to a char budget is REPORTED, never silent (premortem lens 5:
  the controls blow the context budget and silently truncate).
* **full-context (M4)** — inject ALL in-scope prior work, LOO-bounded
  (``loo_excluded_work_ids`` withheld) — the brute-force ceiling control.

Both payloads pass through the SAME probe leak guard (``assert_probe_task_clean``)
before they could be baked into an image — the leak-guard verdict is a first-class,
fail-loud signal, not a silent skip (premortem lens 5: the controls trip the LOO
leak guard at scale and the rejections become coverage holes). This module is the
payload + guard core; wiring the conditions into the multi-hour Docker grid driver
is a separate operational step.
"""

from __future__ import annotations

import pytest

from membench.bundle.replay import ReplayResult
from membench.grading.leak_guard import OutcomeLeakError
from membench.harbor.control_conditions import (
    FULL_CONTEXT,
    RAW_TRAJECTORY,
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


# --------------------------------------------------------------------------- #
# M3 — raw trajectory + truncation never silent
# --------------------------------------------------------------------------- #
def test_raw_trajectory_short_payload_not_truncated():
    payload = raw_trajectory_payload(_bundle(), "a short transcript", max_chars=1000)
    assert payload.condition == RAW_TRAJECTORY
    assert payload.truncation.truncated is False
    assert payload.truncation.original_chars == payload.truncation.kept_chars
    assert "short transcript" in payload.text


def test_raw_trajectory_truncation_is_reported_never_silent():
    transcript = "x" * 5000
    payload = raw_trajectory_payload(_bundle(), transcript, max_chars=1000)
    assert payload.truncation.truncated is True
    assert payload.truncation.original_chars == 5000
    assert payload.truncation.kept_chars == 1000
    # The fact of truncation is visible in the payload text too (no silent drop).
    assert "truncated" in payload.text.lower()


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


# --------------------------------------------------------------------------- #
# M4 — full context, LOO-bounded
# --------------------------------------------------------------------------- #
def test_full_context_is_loo_bounded():
    bundle = _bundle(loo=("w1", "sibling-2"))
    in_scope = {
        "w1": "the bundle's own work (must be withheld)",
        "sibling-2": "a sibling (must be withheld)",
        "prior-9": "legit prior work to inject",
        "prior-3": "more legit prior work",
    }
    payload = full_context_payload(bundle, in_scope, max_chars=100000)
    assert payload.condition == FULL_CONTEXT
    assert "prior-9" in payload.text and "prior-3" in payload.text
    # LOO-excluded ids are withheld by id key — neither own work nor sibling appears.
    assert "withheld" not in payload.text


def test_full_context_truncation_reported():
    bundle = _bundle(loo=("w1",))
    in_scope = {"prior-1": "y" * 5000, "prior-2": "z" * 5000}
    payload = full_context_payload(bundle, in_scope, max_chars=2000)
    assert payload.truncation.truncated is True
    assert payload.truncation.kept_chars <= 2000


def test_full_context_leak_guard_runs_on_payload():
    bundle = _bundle(gold_diff_text="+    leaked_gold_line()")
    in_scope = {"prior-9": "prior work that quotes +    leaked_gold_line() verbatim"}
    leaking = full_context_payload(bundle, in_scope, max_chars=100000)
    with pytest.raises(OutcomeLeakError):
        assert_control_payload_clean(leaking, bundle)
