"""The paid-call cap for the Jev need-gate (mem-xh9vb) must fail CLOSED.

The pre-registration fixes a hard cap of 5,000 calls counted in a file checked
before every call. These tests pin the three ways that promise can be broken: the
cap not holding, a corrupt ledger being read as zero, and the count being taken
after the call instead of before.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev_gate.call_budget import (
    HARD_CAP,
    BudgetCorruptError,
    BudgetExhaustedError,
    reserve,
    used,
)


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    return tmp_path / "jev-call-budget.json"


def test_pre_registered_cap_is_five_thousand() -> None:
    """The cap is a pre-registered constant; changing it is a new pre-registration."""
    assert HARD_CAP == 5_000


def test_absent_ledger_starts_at_zero(ledger: Path) -> None:
    assert used(ledger) == 0


def test_reservations_accumulate(ledger: Path) -> None:
    reserve(3, ledger=ledger)
    reserve(4, ledger=ledger)
    assert used(ledger) == 7


def test_reservation_is_written_before_the_call_returns(ledger: Path) -> None:
    """Reserve-then-call: the charge is on disk the moment reserve() returns, so a
    call that crashes mid-flight is still counted. Over-counting is the safe side."""
    reserve(1, ledger=ledger)
    assert json.loads(ledger.read_text())["used"] == 1


def test_cap_refuses_the_crossing_reservation(ledger: Path) -> None:
    reserve(5, ledger=ledger, cap=6)
    with pytest.raises(BudgetExhaustedError):
        reserve(2, ledger=ledger, cap=6)


def test_refused_reservation_leaves_the_ledger_untouched(ledger: Path) -> None:
    reserve(5, ledger=ledger, cap=6)
    with pytest.raises(BudgetExhaustedError):
        reserve(2, ledger=ledger, cap=6)
    assert used(ledger) == 5


def test_exact_cap_is_allowed_and_then_closed(ledger: Path) -> None:
    reserve(6, ledger=ledger, cap=6)
    assert used(ledger) == 6
    with pytest.raises(BudgetExhaustedError):
        reserve(1, ledger=ledger, cap=6)


@pytest.mark.parametrize(
    "corrupt",
    ['{"used": -1}', '{"used": "many"}', '{"used": true}', "{}", "not json at all"],
)
def test_corrupt_ledger_refuses_rather_than_reading_as_zero(ledger: Path, corrupt: str) -> None:
    """A ledger that cannot be trusted must block the call, not permit an unbounded
    run. Reading a damaged counter as zero is how a hard cap silently stops existing."""
    ledger.write_text(corrupt)
    with pytest.raises(BudgetCorruptError):
        used(ledger)
    with pytest.raises(BudgetCorruptError):
        reserve(1, ledger=ledger)


def test_zero_or_negative_reservation_is_rejected(ledger: Path) -> None:
    for n in (0, -1):
        with pytest.raises(ValueError):
            reserve(n, ledger=ledger)
