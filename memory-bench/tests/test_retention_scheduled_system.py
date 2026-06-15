"""S3 — the RetentionScheduledMemory arm + its scheduled-disposition contract.

The retention arm is the disposition-oracle sibling of the S1 consolidation arm:
the hot path classifies a record at write (cheap, no model), all disposition work is
deferred to an offline ``consolidate()`` sweep that applies a deterministic
class→disposition policy. Contract:

* a record is assigned a *class* at write; the class drives its scheduled
  disposition (``permanent`` / ``review`` / ``archive`` / ``destroy``).
* the sweep is reversible-until-archived: a ``destroy`` soft-tombstones (recoverable
  — ``restore`` works); an ``archive`` crosses the irreversibility boundary
  (``restore`` raises). There is no hard-delete primitive (a source-scan test).
* legal-hold / PIN is an override: a held record is pinned live and the sweep never
  destroys or archives it, regardless of class.
* an UNKNOWN class is retained (conservative default — never destroy what the
  schedule cannot classify).
* the sweep IS the ``ConsolidationCapable`` ``consolidate()`` pass, so the arm is
  reachable from the sequence runner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from membench.memory_systems import build_memory_system
from membench.memory_systems.consolidation import ConsolidationCapable
from membench.memory_systems.retention_scheduled_system import RetentionScheduledMemory
from membench.runtime import IdClock, StepContext


def _ctx(trial="t-1", step="s"):
    return StepContext(trial_id=trial, session_id="sess", step_id=step, clock=IdClock())


def _write(arm: RetentionScheduledMemory, mid: str, record_class: str, *, content: str = "x"):
    arm.assign_class(mid, record_class)
    arm.write(mid, content, _ctx())


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_arm_is_consolidation_capable():
    assert isinstance(RetentionScheduledMemory(), ConsolidationCapable)


def test_factory_builds_the_arm():
    arm = build_memory_system("retention_scheduled")
    assert isinstance(arm, RetentionScheduledMemory)


# --------------------------------------------------------------------------- #
# Class at write → scheduled disposition at sweep
# --------------------------------------------------------------------------- #
def test_permanent_and_review_stay_live():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    _write(arm, "p1", "permanent")
    _write(arm, "r1", "needs_review")
    arm.consolidate(_ctx())
    assert arm.state_of("p1") == "active"
    assert arm.state_of("r1") == "active"
    assert set(arm.live_ids()) >= {"p1", "r1"}


def test_destroy_soft_tombstones_and_is_recoverable():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    _write(arm, "e1", "expired")
    res = arm.consolidate(_ctx())
    assert arm.state_of("e1") == "tombstoned"
    assert "e1" in res.tombstoned_ids
    # Tombstone is soft: content is still re-derivable (reachability), and the id
    # carries a recoverable citation.
    assert arm.is_live("e1") is True
    assert "e1" in arm.recoverable_ids()


def test_archive_crosses_the_irreversibility_boundary():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    _write(arm, "c1", "cold")
    arm.consolidate(_ctx())
    assert arm.state_of("c1") == "archived"
    assert "c1" in arm.archived_ids()
    # Archived content is retained for audit (reachable) but out of the live set and
    # out of the recoverable set.
    assert arm.is_live("c1") is True
    assert "c1" not in arm.live_ids()
    assert "c1" not in arm.recoverable_ids()


# --------------------------------------------------------------------------- #
# Reversible-until-archived
# --------------------------------------------------------------------------- #
def test_restore_reverses_a_tombstone():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    _write(arm, "e1", "expired")
    arm.consolidate(_ctx())
    arm.restore("e1")
    assert arm.state_of("e1") == "active"
    assert "e1" in arm.live_ids()


def test_restore_raises_past_the_archive_boundary():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    _write(arm, "c1", "cold")
    arm.consolidate(_ctx())
    with pytest.raises(ValueError, match="archived"):
        arm.restore("c1")


# --------------------------------------------------------------------------- #
# Legal-hold / PIN override
# --------------------------------------------------------------------------- #
def test_legal_hold_class_pins_live_through_the_sweep():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    # A record whose class would otherwise archive, but it is under legal hold.
    arm.assign_class("h1", "cold")
    arm.place_hold("h1")
    arm.write("h1", "x", _ctx())
    arm.consolidate(_ctx())
    assert arm.state_of("h1") == "active"
    assert "h1" in arm.live_ids()


def test_legal_hold_record_class_auto_holds():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    _write(arm, "lh", "legal_hold")
    arm.consolidate(_ctx())
    assert arm.state_of("lh") == "active"


# --------------------------------------------------------------------------- #
# Unknown class → retained
# --------------------------------------------------------------------------- #
def test_unknown_class_is_retained_not_destroyed():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    _write(arm, "u1", "totally_unrecognized_class")
    res = arm.consolidate(_ctx())
    assert arm.state_of("u1") == "active"
    assert "u1" in arm.live_ids()
    assert "u1" not in res.tombstoned_ids


# --------------------------------------------------------------------------- #
# Version table (supersession)
# --------------------------------------------------------------------------- #
def test_write_appends_a_version_never_overwrites_history():
    arm = RetentionScheduledMemory()
    arm.reset("t")
    arm.assign_class("v1", "permanent")
    arm.write("v1", "first", _ctx())
    arm.write("v1", "second", _ctx())
    assert arm.versions("v1") == ("first", "second")


# --------------------------------------------------------------------------- #
# No hard-delete primitive (structural reversibility, ZFC)
# --------------------------------------------------------------------------- #
def test_no_hard_delete_primitive_in_the_arm_source():
    src = (
        Path(__file__).resolve().parents[1]
        / "membench"
        / "memory_systems"
        / "retention_scheduled_system.py"
    ).read_text(encoding="utf-8")
    for banned in ("os.remove", ".unlink(", "shutil.rmtree", "del self."):
        assert banned not in src, f"hard-delete primitive {banned!r} reachable in arm"
