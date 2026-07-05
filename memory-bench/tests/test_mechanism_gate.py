"""Pre-run mechanism-fires gate (mem-xe2p): the CSB run-validity discipline port.

Before a grid burns agent legs, a zero-cost smoke must show the mechanism under
test actually FIRES — its covariate exceeds zero on at least one anchor. The
69-leg Option-A grid (mem-lvp.24) returned a null on a mechanism that never
engaged once; this gate refuses that grid at the entrypoint for the cost of a
mechanical scan. Ported from CodeScaleBench as a composition: the
``harbor_run_guarded`` scaffold (cheap preflight, structural refusal, explicit
logged override) with the ``mcp_never_used`` predicate (covariate == 0
everywhere ⇒ refuse) as the preflight body. The gate block rides OUTSIDE
``MetricsBundle`` / ``GridConditionResult.metrics()`` (the safety_gates
precedent), enforced structurally below.
"""

from __future__ import annotations

import pytest

from membench.grading.mechanism_gate import (
    MechanismNeverFiredError,
    compute_mechanism_fires,
    enforce_mechanism_fires,
)


# --------------------------------------------------------------------------- #
# Structural discipline: the gate block is NOT in metrics()
# --------------------------------------------------------------------------- #
def _all_field_names(model_cls) -> set[str]:
    from pydantic import BaseModel

    names: set[str] = set()
    for name, field in model_cls.model_fields.items():
        names.add(name)
        ann = field.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            names |= _all_field_names(ann)
    return names


_MECHANISM_KEYS = {
    "mechanism_fires",
    "mechanism",
    "covariate",
    "fired_anchor_ids",
    "override_reason",
}


def test_mechanism_gate_absent_from_metrics_bundle():
    from membench.schemas.metrics import MetricsBundle

    fields = _all_field_names(MetricsBundle)
    leaked = _MECHANISM_KEYS & fields
    assert not leaked, f"gate leaked into MetricsBundle: {leaked}"


def test_mechanism_gate_absent_from_grid_condition_metrics():
    from membench.grading.probe_direct import ProbeEfficiency
    from membench.harbor.bundle_grid import GridConditionResult

    gcr = GridConditionResult(
        work_id="w",
        condition="none",
        score_direct=1.0,
        score_artifact=1.0,
        direct_mode="repro",
        repro_passed=True,
        repro_error=None,
        efficiency=ProbeEfficiency(input_tokens=1, output_tokens=1, turns=1, tool_calls=1),
        candidate_files=(),
    )
    keys = set(gcr.metrics())
    assert not (_MECHANISM_KEYS & keys), f"gate leaked into metrics(): {_MECHANISM_KEYS & keys}"


# --------------------------------------------------------------------------- #
# The readout: fires iff the covariate exceeds zero on at least one anchor
# --------------------------------------------------------------------------- #
def test_fires_when_any_anchor_shows_nonzero_covariate():
    gate = compute_mechanism_fires(
        mechanism="tier1_exact_signature_retrieval",
        covariate="signature_overlap",
        observations={"mem-a": 0.0, "mem-b": 2.0, "mem-c": 0.0},
    )
    assert gate.fired is True
    assert gate.fired_anchor_ids == ("mem-b",)
    assert gate.overridden is False
    assert gate.override_reason is None
    # Every anchor tried is recorded, in observation order, with its value.
    assert tuple(a.anchor_id for a in gate.anchors) == ("mem-a", "mem-b", "mem-c")
    assert tuple(a.fired for a in gate.anchors) == (False, True, False)


def test_never_fired_reason_names_mechanism_covariate_and_every_anchor_tried():
    gate = compute_mechanism_fires(
        mechanism="tier1_exact_signature_retrieval",
        covariate="signature_overlap",
        observations={"mem-a": 0.0, "mem-b": 0.0},
    )
    assert gate.fired is False
    assert gate.fired_anchor_ids == ()
    for token in ("tier1_exact_signature_retrieval", "signature_overlap", "mem-a", "mem-b"):
        assert token in gate.reason


def test_zero_anchors_fails_closed():
    # An empty smoke proves nothing — no anchors is a refusal, never a pass.
    gate = compute_mechanism_fires(
        mechanism="tier1_exact_signature_retrieval",
        covariate="signature_overlap",
        observations={},
    )
    assert gate.fired is False
    assert "no anchors" in gate.reason


# --------------------------------------------------------------------------- #
# The structural seam: enforce refuses the grid, loudly
# --------------------------------------------------------------------------- #
def test_enforce_raises_loud_when_mechanism_never_fires():
    with pytest.raises(MechanismNeverFiredError) as exc:
        enforce_mechanism_fires(
            mechanism="tier1_exact_signature_retrieval",
            covariate="signature_overlap",
            observations={"mem-a": 0.0, "mem-b": 0.0},
        )
    message = str(exc.value)
    for token in ("tier1_exact_signature_retrieval", "signature_overlap", "mem-a", "mem-b"):
        assert token in message
    # The refusal carries the full gate block for the caller's report.
    assert exc.value.gate.fired is False


def test_enforce_returns_the_gate_when_the_mechanism_fired():
    gate = enforce_mechanism_fires(
        mechanism="tier1_exact_signature_retrieval",
        covariate="signature_overlap",
        observations={"mem-a": 1.0},
    )
    assert gate.fired is True
    assert gate.overridden is False


def test_override_rescues_a_failing_gate_and_is_recorded():
    gate = enforce_mechanism_fires(
        mechanism="tier1_exact_signature_retrieval",
        covariate="signature_overlap",
        observations={"mem-a": 0.0},
        override_reason="diagnosing an empty substrate",
    )
    assert gate.fired is False
    assert gate.overridden is True
    assert gate.override_reason == "diagnosing an empty substrate"
    assert "diagnosing an empty substrate" in gate.reason


def test_override_is_not_consumed_by_a_passing_gate():
    gate = enforce_mechanism_fires(
        mechanism="tier1_exact_signature_retrieval",
        covariate="signature_overlap",
        observations={"mem-a": 3.0},
        override_reason="should never be needed",
    )
    assert gate.fired is True
    assert gate.overridden is False
    assert gate.override_reason is None


def test_blank_override_reason_does_not_override():
    # The override must be EXPLICIT — whitespace is not a logged reason.
    with pytest.raises(MechanismNeverFiredError):
        enforce_mechanism_fires(
            mechanism="tier1_exact_signature_retrieval",
            covariate="signature_overlap",
            observations={"mem-a": 0.0},
            override_reason="   ",
        )
