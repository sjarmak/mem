"""Hard cap on paid Jev calls (mem-xh9vb, pre-registration "Paid-API exception").

Stephanie's 2026-09-19 ruling: Jev calls are in scope as paid under a hard cap of
5,000 calls, counted in a file the caller checks BEFORE every call. The count is
deterministic and never derived from the gateway cost field, which reported zero on
the free tier in the mtg probe and therefore cannot be trusted as a meter.

The counter fails CLOSED: an unreadable, corrupt or unwritable ledger raises rather
than silently permitting a call. Reserve-then-call ordering means a crashed call is
still charged against the cap; over-counting is the safe direction.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

HARD_CAP = 5_000
DEFAULT_LEDGER = Path(
    os.environ.get(
        "JEV_CALL_LEDGER",
        Path(__file__).resolve().parents[2] / ".mem" / "jev-call-budget.json",
    )
)


class BudgetExhaustedError(RuntimeError):
    """The pre-registered hard cap would be exceeded by this reservation."""


class BudgetCorruptError(RuntimeError):
    """The ledger could not be read as a trustworthy count."""


@dataclass(frozen=True)
class Reservation:
    """The state of the cap after a successful reservation."""

    used: int
    remaining: int
    cap: int


def _read(ledger: Path) -> int:
    if not ledger.exists():
        return 0
    try:
        raw = json.loads(ledger.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BudgetCorruptError(f"{ledger}: ledger unreadable, refusing to call: {exc}") from exc
    used = raw.get("used")
    if not isinstance(used, int) or isinstance(used, bool) or used < 0:
        raise BudgetCorruptError(f"{ledger}: 'used' is {used!r}, not a non-negative int")
    return used


def used(ledger: Path | None = None) -> int:
    """Calls charged so far. Raises BudgetCorruptError rather than guessing."""
    return _read(ledger or DEFAULT_LEDGER)


def reserve(n: int = 1, ledger: Path | None = None, cap: int = HARD_CAP) -> Reservation:
    """Charge ``n`` calls against the cap BEFORE making them.

    Raises BudgetExhaustedError if the reservation would cross the cap, leaving the
    ledger untouched. Written atomically so a crash mid-write cannot corrupt it.
    """
    if n < 1:
        raise ValueError(f"reservation must be at least 1 call, got {n}")
    path = ledger or DEFAULT_LEDGER
    before = _read(path)
    after = before + n
    if after > cap:
        raise BudgetExhaustedError(
            f"{path}: {before} of {cap} calls used; reserving {n} more would reach "
            f"{after} and cross the pre-registered hard cap. Raising the cap is a new "
            f"pre-registration, not an edit."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"used": after, "cap": cap}, indent=2) + "\n")
    os.replace(tmp, path)
    return Reservation(used=after, remaining=cap - after, cap=cap)
