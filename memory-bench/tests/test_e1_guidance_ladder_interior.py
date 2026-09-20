"""The E1 interior-480 headline, re-derived from the committed leg files.

The numbers in ``docs/mem-eg850-guidance-ladder-result.md`` and in the fire's
own README are restated here as constants and recomputed from
``results/e1-guidance-ladder/interior-480/summary.json.legs/``. A drift in
either direction reds this file rather than letting the prose and the evidence
part company.

Counting rule (the one used for every number below): a leg counts as measured
when ``status == "ok"``, and as calling when ``memory_calls > 0``. The
discrimination margin is a GOAL-LEG quantity: ``role == "goal"``, 40 legs per
(rung, variant) cell, 80 scored legs per rung out of 160 leg files.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from tests.paths import REPO

LEGS = REPO / "results" / "e1-guidance-ladder" / "interior-480" / "summary.json.legs"
SUMMARY = REPO / "results" / "e1-guidance-ladder" / "interior-480" / "summary.json"

RUNGS = ("R1", "R2", "R3")
N_LEGS = 480
N_PER_CELL = 40

# Goal-leg calling counts per (rung, variant), as recounted over all 480 legs.
GOAL_CALLING = {
    ("R1", "necessary"): 32,
    ("R1", "unnecessary"): 15,
    ("R2", "necessary"): 40,
    ("R2", "unnecessary"): 33,
    ("R3", "necessary"): 39,
    ("R3", "unnecessary"): 27,
}

# The headline: d = P(call | necessary) - P(call | unnecessary) on goal legs.
GOAL_MARGIN = {"R1": 0.4250, "R2": 0.1750, "R3": 0.3000}

# What summary.json publishes: the same margin pooled over both roles, which
# halves it because the establish leg's margin is exactly 0.0 at every rung.
POOLED_MARGIN = {"R1": 0.2125, "R2": 0.0875, "R3": 0.1500}

# The monotonicity gate's subject: the necessary-half rate across both roles.
NECESSARY_RATE = {"R1": 0.8750, "R2": 1.0000, "R3": 0.9875}

VERB_CENSUS = {"native_read": 457, "native_write": 136}
HOOK_REACHES = 931


@pytest.fixture(scope="module")
def legs() -> list[dict[str, object]]:
    if not LEGS.is_dir():
        pytest.skip(f"interior-480 leg files not present at {LEGS}")
    files = sorted(LEGS.glob("*.json"))
    return [json.loads(path.read_text()) for path in files]


def _rate(legs: list[dict[str, object]], rung: str, variant: str, role: str | None) -> float:
    cell = [
        leg
        for leg in legs
        if leg["rung"] == rung
        and leg["variant"] == variant
        and (role is None or leg["role"] == role)
        and leg["status"] == "ok"
    ]
    assert cell, f"no measured legs for {rung}/{variant}/{role}"
    calling = sum(1 for leg in cell if int(leg["memory_calls"]) > 0)
    return calling / len(cell)


def test_all_480_legs_present_and_ok(legs: list[dict[str, object]]) -> None:
    assert len(legs) == N_LEGS
    assert {leg["status"] for leg in legs} == {"ok"}
    assert {leg["rung"] for leg in legs} == set(RUNGS)
    assert {leg["role"] for leg in legs} == {"establish", "goal"}


def test_goal_leg_cells_are_40_each(legs: list[dict[str, object]]) -> None:
    shape = Counter((leg["rung"], leg["variant"], leg["role"]) for leg in legs)
    for rung in RUNGS:
        for variant in ("necessary", "unnecessary"):
            for role in ("establish", "goal"):
                assert shape[(rung, variant, role)] == N_PER_CELL


def test_goal_leg_calling_counts(legs: list[dict[str, object]]) -> None:
    counted = {
        (rung, variant): sum(
            1
            for leg in legs
            if leg["rung"] == rung
            and leg["variant"] == variant
            and leg["role"] == "goal"
            and leg["status"] == "ok"
            and int(leg["memory_calls"]) > 0
        )
        for rung in RUNGS
        for variant in ("necessary", "unnecessary")
    }
    assert counted == GOAL_CALLING


@pytest.mark.parametrize("rung", RUNGS)
def test_goal_leg_discrimination_margin(legs: list[dict[str, object]], rung: str) -> None:
    margin = _rate(legs, rung, "necessary", "goal") - _rate(legs, rung, "unnecessary", "goal")
    assert margin == pytest.approx(GOAL_MARGIN[rung], abs=1e-9)


@pytest.mark.parametrize("rung", RUNGS)
def test_establish_leg_margin_is_exactly_zero(legs: list[dict[str, object]], rung: str) -> None:
    """Both halves of a twin get a byte-identical establish leg by construction."""
    margin = _rate(legs, rung, "necessary", "establish") - _rate(
        legs, rung, "unnecessary", "establish"
    )
    assert margin == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("rung", RUNGS)
def test_pooled_margin_is_half_the_goal_margin(legs: list[dict[str, object]], rung: str) -> None:
    pooled = _rate(legs, rung, "necessary", None) - _rate(legs, rung, "unnecessary", None)
    assert pooled == pytest.approx(POOLED_MARGIN[rung], abs=1e-9)
    assert pooled == pytest.approx(GOAL_MARGIN[rung] / 2, abs=1e-9)


def test_no_goal_leg_wrote(legs: list[dict[str, object]]) -> None:
    goal = [leg for leg in legs if leg["role"] == "goal"]
    assert len(goal) == 240
    assert sum(int(leg["write_calls"]) for leg in goal) == 0


def test_every_call_was_native_and_no_bd_verb_appeared(legs: list[dict[str, object]]) -> None:
    census = Counter(verb for leg in legs for verb in leg["verbs"])
    assert dict(census) == VERB_CENSUS
    assert sum(int(leg["hook_reaches"]) for leg in legs) == HOOK_REACHES


def test_native_memory_was_not_pinned_off(legs: list[dict[str, object]]) -> None:
    """A caveat that travels with the numbers, pinned so it cannot drift."""
    assert not any(leg["native_memory_pinned_off"] for leg in legs)


def test_summary_json_agrees_with_the_recount() -> None:
    if not SUMMARY.is_file():
        pytest.skip(f"interior-480 summary not present at {SUMMARY}")
    summary = json.loads(SUMMARY.read_text())
    gates = summary["call_rate_gates"]
    published = gates["discrimination"]["margin_by_rung"]
    for rung in RUNGS:
        assert published[rung] == pytest.approx(POOLED_MARGIN[rung], abs=1e-9)
        assert gates["monotonicity"]["call_rate_by_rung"][rung] == pytest.approx(
            NECESSARY_RATE[rung], abs=1e-9
        )
    assert summary["cli_version"] == "2.1.260"
    assert summary["execution_protocol"] == 2
    assert summary["corpus_fingerprint"] == "c5f13bbe8c877a2b"
    assert summary["paid"] is True


def test_monotonicity_gate_is_recorded_as_failed() -> None:
    """The gate fails on one leg of eighty. It stays red; it is not waved off."""
    if not SUMMARY.is_file():
        pytest.skip(f"interior-480 summary not present at {SUMMARY}")
    mono = json.loads(SUMMARY.read_text())["call_rate_gates"]["monotonicity"]
    assert mono["monotone"] is False
    assert mono["comparable"] is True
    assert mono["tolerance"] == 0.0
    assert mono["violation_pairs"] == ["R2->R3"]
    assert mono["violations"][0]["drop"] == pytest.approx(1 / 80, abs=1e-9)
