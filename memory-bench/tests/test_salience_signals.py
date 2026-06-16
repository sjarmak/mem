"""M8 — the shared SalienceSignals keystone (pure arithmetic, no model/network).

The signal bank is consumed by the consolidation write-gate/sampler (S1), the
foraging stop controller (N1), and the compaction priority. Per PRD M8 we build
ONLY the two signals the first consumer needs — novelty (Jaccard near-duplicate
distance) and similarity decay-slope — and enforce the no-LLM/no-network contract
as a test, because the whole point of a salience *signal* (vs a salience *judge*)
is that it costs ≪ one model call.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from membench.signals import SalienceSignals


def test_import_surface():
    sig = SalienceSignals()
    assert callable(sig.novelty)
    assert callable(sig.decay_slope)
    assert callable(sig.jaccard)


# --------------------------------------------------------------------------- #
# Jaccard
# --------------------------------------------------------------------------- #
def test_jaccard_identical_is_one():
    sig = SalienceSignals()
    assert sig.jaccard("use snake_case here", "use snake_case here") == 1.0


def test_jaccard_disjoint_is_zero():
    sig = SalienceSignals()
    assert sig.jaccard("alpha beta", "gamma delta") == 0.0


def test_jaccard_partial_overlap():
    sig = SalienceSignals()
    # {a,b,c} vs {b,c,d} -> intersection=2 (b,c), union=4 (a,b,c,d) -> 0.5
    assert sig.jaccard("a b c", "b c d") == 0.5


def test_jaccard_both_empty_is_one():
    # Two empty items are trivially identical near-duplicates (documented edge).
    sig = SalienceSignals()
    assert sig.jaccard("", "") == 1.0


def test_jaccard_one_empty_is_zero():
    sig = SalienceSignals()
    assert sig.jaccard("alpha", "") == 0.0


# --------------------------------------------------------------------------- #
# Novelty (1 - max Jaccard to any existing item) — the write-gate signal
# --------------------------------------------------------------------------- #
def test_novelty_against_nothing_is_max():
    # Nothing to be a duplicate of ⇒ fully novel.
    sig = SalienceSignals()
    assert sig.novelty("anything", []) == 1.0


def test_novelty_exact_duplicate_is_zero():
    sig = SalienceSignals()
    assert sig.novelty("repeated lesson", ["unrelated", "repeated lesson"]) == 0.0


def test_novelty_picks_the_nearest_existing():
    sig = SalienceSignals()
    # nearest existing is {a,b,c} → Jaccard({a,b},{a,b,c}) = 2/3 → novelty = 1/3
    nov = sig.novelty("a b", ["a b c", "x y z"])
    assert nov == pytest.approx(1.0 / 3.0)


# --------------------------------------------------------------------------- #
# Similarity decay-slope — the foraging stop signal
# --------------------------------------------------------------------------- #
def test_decay_slope_flat_is_zero():
    sig = SalienceSignals()
    assert sig.decay_slope([0.7, 0.7, 0.7]) == 0.0


def test_decay_slope_descending_is_positive():
    sig = SalienceSignals()
    # mean per-rank drop of [0.9, 0.6, 0.3] = (0.9-0.3)/2 = 0.3
    assert sig.decay_slope([0.9, 0.6, 0.3]) == pytest.approx(0.3)


def test_decay_slope_unsorted_input_is_sorted_descending_first():
    sig = SalienceSignals()
    # Same multiset as above, scrambled — the slope keys on the ranking, not order.
    assert sig.decay_slope([0.3, 0.9, 0.6]) == pytest.approx(0.3)


def test_decay_slope_single_or_empty_is_zero():
    sig = SalienceSignals()
    assert sig.decay_slope([0.5]) == 0.0
    assert sig.decay_slope([]) == 0.0


# --------------------------------------------------------------------------- #
# The no-LLM / no-network contract (M8 acceptance: "enforced by a test")
# --------------------------------------------------------------------------- #
def test_signals_make_no_network_call(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("SalienceSignals must not open a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    sig = SalienceSignals()
    # All three signals compute under a poisoned socket ⇒ no network.
    assert sig.jaccard("a b", "a c") >= 0.0
    assert sig.novelty("a b", ["a c"]) >= 0.0
    assert sig.decay_slope([0.9, 0.1]) >= 0.0


def test_module_imports_no_llm_or_http_client():
    # Source-level enforcement: the keystone may not pull in a model/HTTP client.
    src = Path(__file__).resolve().parents[1] / "membench" / "signals" / "salience.py"
    text = src.read_text(encoding="utf-8")
    for banned in ("anthropic", "openai", "httpx", "requests", "urllib.request"):
        assert banned not in text, f"SalienceSignals must not import {banned!r}"
