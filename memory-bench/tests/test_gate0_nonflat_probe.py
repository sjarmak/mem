"""Tests for `scripts/gate0_nonflat_probe.py` (mem-72sj Gate-0 non-flatness reporter).

Pure-arithmetic post-hoc analyzer over a `summary-3arm-graded.json`. The invariant
under test is the PRD R1 noise-pass guard: the split-half rank-stability rho must NOT
report a spurious ``rho=1.0 / non-flat`` when a split-half has no arm separation — the
ordering of a flat half is only the alphabetical tiebreak, and correlating it would
masquerade as a stable ranking. Loaded from its file path (the run_gate_probe idiom).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gate0_nonflat_probe.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("gate0_nonflat_probe", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate0_nonflat_probe"] = module
    spec.loader.exec_module(module)
    return module


g0 = _load_script()


def _cell(score: float) -> dict:
    return {"score_direct": score}


def _bundle(work_id: str, none: float, ours: float, builtin: float) -> dict:
    return {
        "work_id": work_id,
        "none-clean": _cell(none),
        "ours": _cell(ours),
        "builtin": _cell(builtin),
    }


def test_flat_split_half_is_not_a_spurious_non_flat_pass() -> None:
    # The real n8-dashboard shape: ours == none-clean == 0 everywhere; builtin passes
    # exactly ONE bundle, which lands in the first half. The second half is entirely
    # flat (all arms 0). The pre-guard code reported rho=1.0 (tiebreak coincidence)
    # and verdict_non_flat=True — a noise pass. The guard must call it FLAT.
    per_bundle = [
        _bundle("b0", 0.0, 0.0, 1.0),
        _bundle("b1", 0.0, 0.0, 0.0),
        _bundle("b2", 0.0, 0.0, 0.0),
        _bundle("b3", 0.0, 0.0, 0.0),
    ]
    report = g0.analyze({"per_bundle": per_bundle})

    assert report["split_half_flat"] is True
    assert report["split_half_rho"] is None
    assert report["verdict_non_flat"] is False
    assert "flat half" in report["verdict_reason"]


def test_genuine_non_flat_ranking_passes() -> None:
    # Both halves separate the arms in the SAME order (ours > builtin > none-clean):
    # a real, reproducible non-flat ranking. rho=1.0 here is legitimate.
    per_bundle = [
        _bundle("b0", 0.0, 1.0, 0.5),
        _bundle("b1", 0.1, 0.9, 0.4),
        _bundle("b2", 0.0, 1.0, 0.6),
        _bundle("b3", 0.2, 0.8, 0.5),
    ]
    report = g0.analyze({"per_bundle": per_bundle})

    assert report["split_half_flat"] is False
    assert report["split_half_rho"] == 1.0
    assert report["degenerate_span"] is False
    assert report["verdict_non_flat"] is True
    assert report["full_ranking_best_to_worst"][0] == "ours"


def test_degenerate_full_span_is_uncomputable() -> None:
    # Every arm tied across every bundle -> the full-pool span is degenerate; no
    # ranking exists to correlate (honest-null), independent of the split-half guard.
    per_bundle = [_bundle(f"b{i}", 0.3, 0.3, 0.3) for i in range(4)]
    report = g0.analyze({"per_bundle": per_bundle})

    assert report["degenerate_span"] is True
    assert report["split_half_rho"] is None
    assert report["verdict_non_flat"] is False
    assert "degenerate span" in report["verdict_reason"]
