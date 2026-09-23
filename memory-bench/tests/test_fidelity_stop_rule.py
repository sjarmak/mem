"""The stop rule, tested for what it refuses to pass.

A stop rule that cannot fail is the defect this one replaces, so most of these
build a run that looks like a pass in one respect and check that it is still
reported as unmet. The one passing case is here to prove the rule is reachable at
all -- a rule nothing can satisfy is as useless as one nothing can fail.
"""

from __future__ import annotations

from membench.fidelity.stop_rule import MIN_PAIRS, MIN_TOKEN_FREE_PAIRS, PairOutcome, evaluate


def pair(
    name: str,
    *,
    token_detectable: bool,
    gate_supported: bool | None = True,
    gate_lure: bool | None = False,
    baseline_lure: bool = True,
) -> PairOutcome:
    """One outcome, defaulting to the shape a passing pair has."""
    return PairOutcome(
        packet_id=name,
        probe_id="token-free" if not token_detectable else "version-token",
        token_detectable=token_detectable,
        gate_accepts_supported=gate_supported,
        gate_accepts_lure=gate_lure,
        baseline_accepts_supported=True,
        baseline_accepts_lure=baseline_lure,
    )


def passing_run() -> tuple[PairOutcome, ...]:
    """Six pairs, three token-free, gate catches all of them, baseline catches none."""
    return tuple(
        pair(f"packet-{i}", token_detectable=detectable)
        for i in range(3)
        for detectable in (True, False)
    )


def test_the_registered_threshold_is_reachable() -> None:
    verdict = evaluate(passing_run())
    assert verdict.passed
    assert verdict.pairs == MIN_PAIRS
    assert verdict.token_free_pairs == MIN_TOKEN_FREE_PAIRS
    assert len(verdict.gate_rejected_token_free) == 3
    assert verdict.baseline_rejected_token_free == ()
    assert verdict.failures == ()
    assert "MET" in verdict.report()


def test_catching_every_token_detectable_lure_and_no_token_free_one_fails() -> None:
    """The whole point, stated as a test.

    This is a gate that rejects all three grep-visible lures and misses all three
    lures grep cannot see. It looks like 3/6, which under the old rule read as a
    result. It buys nothing over a string search, so the rule has to refuse it.
    """
    outcomes = tuple(
        (
            outcome
            if outcome.token_detectable
            else pair(outcome.packet_id, token_detectable=False, gate_lure=True)
        )
        for outcome in passing_run()
    )
    verdict = evaluate(outcomes)
    assert not verdict.passed
    assert verdict.gate_rejected_token_free == ()
    assert any("strictly more" in failure for failure in verdict.failures)


def test_tying_the_baseline_on_token_free_lures_fails() -> None:
    """Strictly more, not at least as many.

    If a token-free record ever picked up a version-shaped token the baseline
    would catch it too, and a gate matching that is not distinguishable from the
    grep on the axis the rule cares about.
    """
    outcomes = tuple(
        (
            outcome
            if outcome.token_detectable
            else pair(outcome.packet_id, token_detectable=False, baseline_lure=False)
        )
        for outcome in passing_run()
    )
    verdict = evaluate(outcomes)
    assert not verdict.passed
    assert len(verdict.baseline_rejected_token_free) == 3
    assert any("strictly more" in failure for failure in verdict.failures)


def test_one_rejected_supported_record_fails_the_whole_run() -> None:
    """A gate that rejects a record inside the support boundary is not conservative.

    It is unusable: every packet would read as discriminating because it rejects
    everything, and the lure verdicts would carry no information at all.
    """
    outcomes = (pair("packet-0", token_detectable=True, gate_supported=False), *passing_run()[1:])
    verdict = evaluate(outcomes)
    assert not verdict.passed
    assert verdict.gate_rejected_supported == ("packet-0/version-token",)
    assert any("supported record" in failure for failure in verdict.failures)


def test_a_missing_extraction_is_not_counted_as_either_verdict() -> None:
    """None is a missing observation, and the rule refuses to guess which way it went.

    Counting it as an acceptance would penalize a daemon hiccup; counting it as a
    rejection would let one crash the gate into a pass. It fails the run instead,
    which is the only reading that cannot be gamed by an unreliable run.
    """
    outcomes = (pair("packet-0", token_detectable=False, gate_lure=None), *passing_run()[1:])
    verdict = evaluate(outcomes)
    assert not verdict.passed
    assert verdict.incomplete_pairs == ("packet-0/token-free",)
    assert "no usable extraction" in verdict.report()


def test_too_few_pairs_fails_however_well_they_went() -> None:
    """Quorum first. Two perfect token-free catches are two draws, not a result."""
    outcomes = passing_run()[:4]
    verdict = evaluate(outcomes)
    assert not verdict.passed
    assert verdict.pairs == 4
    assert any(str(MIN_PAIRS) in failure for failure in verdict.failures)


def test_too_few_token_free_pairs_fails_even_at_full_pair_count() -> None:
    """Six pairs of the catchable kind is six repeats of the question grep answers."""
    outcomes = tuple(pair(f"packet-{i}", token_detectable=True) for i in range(6))
    verdict = evaluate(outcomes)
    assert not verdict.passed
    assert verdict.token_free_pairs == 0
    assert any("token-free pairs" in failure for failure in verdict.failures)


def test_every_unmet_condition_is_reported_rather_than_the_first() -> None:
    """A run that fails four ways should say so once, not across four reruns.

    Reporting only the first unmet condition would have the next run fix it and
    hit the second, which turns one diagnosis into four provider sessions.
    """
    outcomes = (
        pair("packet-0", token_detectable=True, gate_supported=False),
        pair("packet-1", token_detectable=True),
    )
    verdict = evaluate(outcomes)
    assert not verdict.passed
    assert len(verdict.failures) == 4
    joined = " | ".join(verdict.failures)
    for expected in (
        "the rule requires 6",
        "token-free pairs",
        "supported record",
        "strictly more",
    ):
        assert expected in joined
