"""Shuffled/placebo donor selection (mem-hhto) — the selection core.

The ``shuffled`` condition injects the ours payload retrieved for a DIFFERENT
bundle: equal-volume irrelevant memory, same rendering and leak guards as
``ours``. The only new logic is donor selection, and it must be:

- deterministic given the bundle set (no randomness, input-order independent);
- other-repo constrained (a same-rig payload talks about the recipient's own
  codebase, so a same-repo placebo would be partially relevant);
- volume-matched (the closest rendered payload size, ties broken by work_id);
- explicit about fallbacks — a same-repo donor is allowed only when no
  other-repo donor exists, and the reason is RECORDED in the selection.
"""

from __future__ import annotations

import pytest

from membench.bundle.replay import ReplayResult
from membench.harbor.shuffled_condition import (
    SHUFFLED,
    ShuffledSelection,
    payload_chars,
    select_donor,
)
from membench.schemas.bundle import BundleEnv, TaskBundle


def _bundle(work_id: str, *, repo: str = "repo-a", loo: tuple[str, ...] = ()) -> TaskBundle:
    return TaskBundle(
        work_id=work_id,
        rig=repo,
        issue_title="add an endpoint",
        trace_ref="trace.jsonl",
        output=ReplayResult(calls=(), file_diffs=(), replay_success_rate=1.0),
        env=BundleEnv(repo=repo, base_commit="DEADBEEFCAFE", base_image="img"),
        loo_excluded_work_ids=(work_id, *loo),
    )


def test_shuffled_condition_name() -> None:
    assert SHUFFLED == "shuffled"


def test_payload_chars_is_the_rendered_memory_volume() -> None:
    # The exact join build_probe_task writes to memory/MEMORY.md.
    assert payload_chars({"a": "xx", "b": "yyy"}) == len("xx\nyyy")
    assert payload_chars({}) == 0


def test_select_donor_avoids_same_repo_even_when_closer_in_volume() -> None:
    recipient = _bundle("w-1", repo="repo-a")
    same_repo = _bundle("w-2", repo="repo-a")
    other_repo = _bundle("w-3", repo="repo-b")
    payloads = {
        "w-1": {"p": "x" * 100},
        "w-2": {"p": "x" * 100},  # perfect volume match, but same repo
        "w-3": {"p": "x" * 500},
    }
    selection = select_donor(recipient, [recipient, same_repo, other_repo], payloads)
    assert selection.donor_work_id == "w-3"
    assert selection.donor_repo == "repo-b"
    assert selection.recipient_repo == "repo-a"
    assert selection.fallback_reason is None


def test_select_donor_volume_matches_closest_among_other_repo() -> None:
    recipient = _bundle("w-1", repo="repo-a")
    donors = [
        _bundle("w-2", repo="repo-b"),
        _bundle("w-3", repo="repo-b"),
        _bundle("w-4", repo="repo-c"),
    ]
    payloads = {
        "w-1": {"p": "x" * 100},
        "w-2": {"p": "x" * 300},
        "w-3": {"p": "x" * 120},
        "w-4": {"p": "x" * 50},
    }
    selection = select_donor(recipient, [recipient, *donors], payloads)
    assert selection.donor_work_id == "w-3"
    # The volume-match accounting is recorded for analysis.
    assert selection.recipient_chars == 100
    assert selection.donor_chars == 120


def test_select_donor_is_deterministic_and_input_order_independent() -> None:
    recipient = _bundle("w-1", repo="repo-a")
    donors = [_bundle("w-2", repo="repo-b"), _bundle("w-3", repo="repo-b")]
    payloads = {
        "w-1": {"p": "x" * 100},
        "w-2": {"p": "x" * 90},  # distance 10
        "w-3": {"p": "x" * 110},  # distance 10 — tie broken by sorted work_id
    }
    bundles = [recipient, *donors]
    first = select_donor(recipient, bundles, payloads)
    assert first.donor_work_id == "w-2"
    assert select_donor(recipient, list(reversed(bundles)), payloads) == first
    assert select_donor(recipient, bundles, payloads) == first


def test_select_donor_same_repo_fallback_records_the_reason() -> None:
    recipient = _bundle("w-1", repo="repo-a")
    donors = [_bundle("w-2", repo="repo-a"), _bundle("w-3", repo="repo-a")]
    payloads = {
        "w-1": {"p": "x" * 100},
        "w-2": {"p": "x" * 400},
        "w-3": {"p": "x" * 110},
    }
    selection = select_donor(recipient, [recipient, *donors], payloads)
    assert selection.donor_work_id == "w-3"  # still volume-matched
    assert selection.donor_repo == "repo-a"
    assert selection.fallback_reason is not None
    assert "other-repo" in selection.fallback_reason


def test_select_donor_skips_self_loo_siblings_and_empty_payload_donors() -> None:
    recipient = _bundle("w-1", repo="repo-a", loo=("w-sibling",))
    sibling = _bundle("w-sibling", repo="repo-b")
    shares_work = _bundle("w-4", repo="repo-b", loo=("w-1",))  # LOO-excludes the recipient
    empty = _bundle("w-5", repo="repo-b")
    legit = _bundle("w-6", repo="repo-b")
    payloads = {
        "w-1": {"p": "x" * 100},
        "w-sibling": {"p": "x" * 100},
        "w-4": {"p": "x" * 100},
        "w-5": {},
        "w-6": {"p": "x" * 900},
    }
    selection = select_donor(recipient, [recipient, sibling, shares_work, empty, legit], payloads)
    assert selection.donor_work_id == "w-6"


def test_select_donor_requires_a_recipient_payload() -> None:
    recipient = _bundle("w-1", repo="repo-a")
    donor = _bundle("w-2", repo="repo-b")
    payloads: dict[str, dict[str, str]] = {"w-1": {}, "w-2": {"p": "x" * 100}}
    with pytest.raises(ValueError, match="volume"):
        select_donor(recipient, [recipient, donor], payloads)


def test_select_donor_requires_a_donor_pool() -> None:
    recipient = _bundle("w-1", repo="repo-a")
    payloads = {"w-1": {"p": "x" * 100}}
    with pytest.raises(ValueError, match="donor"):
        select_donor(recipient, [recipient], payloads)


def test_selection_is_a_frozen_provenance_record() -> None:
    selection = ShuffledSelection(
        work_id="w-1",
        donor_work_id="w-2",
        recipient_repo="repo-a",
        donor_repo="repo-b",
        recipient_chars=100,
        donor_chars=120,
    )
    with pytest.raises(Exception, match="frozen"):
        selection.donor_work_id = "w-9"  # type: ignore[misc]
