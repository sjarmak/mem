"""The retro validation: does the gate reproduce the campaign's own verdicts?

Seven real memory writes, one shared hash-verified evidence packet, and verdicts
that independent reviewers reached from sealed evidence. Every model turn of every
run was recorded, so what runs here is the whole gate -- `evaluate`, not `decide`:
the recorded replies are handed back to `ClaimExtractor` in order, through a
scripted completion that refuses any call the recording does not answer. A red
test means the gate's rules or its call sequence changed, not that a daemon
answered differently. Re-measure against a model with

    python scripts/measure_fidelity_replication.py --draws 2 --freeze \\
        --out results/fidelity-replication-YYYYMMDD

Scope, plainly: 7 records, one episode, one claim shape, and a base set that a
one-token baseline also separates (pinned below). Clearing this would qualify the
gate for a live trial. It does not measure production reliability, and as of the
2-draw sweep of 2026-09-07 the gate does not clear it: 7 of 9 in every draw, with
one supported record accepted, one rejected record accepted, and one of the two
single-edit faults still failing to move with its edit. The numbers below are
that sweep, not a target.

They are a distribution, not a score. The fixture holds every draw of the sweep
rather than the draw that read best, so a record that moved between draws says so
here instead of averaging into an accuracy. In this sweep nothing moved: two draws
of the same nine records in one process gave two identical results. That is not a
property of the setup -- greedy sampling is pinned (temperature 0, top_k 1, a
fixed seed) and the off-episode sweep of the same day still produced two different
decompositions of one identical prompt inside one process -- so the agreement is
a measurement about these nine records, and a future draw that disagrees is a
finding rather than a broken test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from membench.fidelity.claims import ExtractionFormatError, assert_covers, parse_extraction
from membench.fidelity.extract import ClaimExtractor
from membench.fidelity.gate import FidelityGate, GateDecision
from membench.fidelity.retro_corpus import RetroCorpus, load
from membench.fidelity.transcript import ScriptedCompletion, load_transcript

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "bd_capture_fidelity"
EXTRACTIONS = FIXTURE_DIR / "extractions"

ACCEPT, REJECT, UNUSABLE = "accept", "reject", "unusable"

# Read once at collection so the parametrized cases are named per record and draw.
_CORPUS = load(FIXTURE_DIR)
DRAWS = sorted(p.name for p in EXTRACTIONS.iterdir() if p.is_dir())

TEXTS = {r.record_id: r.text for r in _CORPUS.records}
TEXTS.update({f.fault_id: f.text for f in _CORPUS.faults})

EXPECTED = {r.record_id: (ACCEPT if r.supported else REJECT) for r in _CORPUS.records}
EXPECTED.update({f.fault_id: (ACCEPT if f.expected_accept else REJECT) for f in _CORPUS.faults})

# What each record did across the two draws of results/fidelity-replication-20260907.
# Written as counts so a record that splits its draws cannot be read as a verdict.
VERDICTS: dict[str, dict[str, int]] = {
    "dev01-explicit": {REJECT: 2},
    "dev02-current": {ACCEPT: 2},
    "dev02-explicit": {REJECT: 2},
    "dev03-current": {REJECT: 2},
    "dev03-explicit": {ACCEPT: 2},
    "conf-r1": {REJECT: 2},
    "conf-r2": {REJECT: 2},
    "dev03-explicit__version-history-injected": {REJECT: 2},
    "dev01-explicit__version-claims-stripped": {REJECT: 2},
}

# Of the draws where a base record was rejected, how many rejected over a literal
# the reviewers themselves named. A rejection for some other reason is not a hit.
NAMED_THE_REASON = {
    "dev01-explicit": 2,
    "dev02-explicit": 2,
    "dev03-current": 2,
    "conf-r1": 2,
    "conf-r2": 2,
}

# No record now leaves part of itself unaccounted for. dev03-explicit used to: line
# 9 is an indented continuation of the code block claimed on line 8, the model
# claimed 8 and 10 and neither claimed nor skipped 9, and the coverage rule
# discarded the whole extraction -- which, on the corpus's only supported record,
# is what left the earlier sweep with no true positive at all. It survives here as
# an empty set rather than a deleted test: the set is what a new hole in the
# extractor would arrive in.
UNCOVERED: set[str] = set()


@pytest.fixture(scope="module")
def corpus() -> RetroCorpus:
    return _CORPUS


def _decide(record_id: str, draw: str, corpus: RetroCorpus) -> GateDecision:
    """Replay one recorded run through the live path and take its verdict.

    `evaluate`, not `decide`. The half this used to skip is where the repair loop
    and both second-pass asks live, and skipping it meant a change in any of them
    could not turn this suite red: measured once as a live rejection replaying as
    an accept. `assert_spent` closes the other direction -- a gate that stopped
    making one of the recorded calls would still parse every reply it did use.
    """
    transcript = load_transcript(EXTRACTIONS / draw / f"{record_id}.json")
    replay = ScriptedCompletion(transcript)
    gate = FidelityGate(extractor=ClaimExtractor(complete=replay, model=transcript.model))
    decision = gate.evaluate(record_id, TEXTS[record_id], corpus.packet)
    replay.assert_spent()
    return decision


def _verdicts(corpus: RetroCorpus) -> dict[str, dict[str, int]]:
    """Every record replayed in every draw, tallied by outcome.

    An extraction the parser refuses is counted under its own name rather than as
    a rejection. Folding it into the rejections would turn a gate that could not
    read a record into a gate that caught something in it.
    """
    tally: dict[str, dict[str, int]] = {record_id: {} for record_id in TEXTS}
    for draw in DRAWS:
        for record_id in TEXTS:
            try:
                decision = _decide(record_id, draw, corpus)
            except ExtractionFormatError:
                outcome = UNUSABLE
            else:
                outcome = ACCEPT if decision.accepted else REJECT
            tally[record_id][outcome] = tally[record_id].get(outcome, 0) + 1
    return tally


def test_the_fixture_is_present_and_whole(corpus: RetroCorpus) -> None:
    assert len(corpus.records) == 7
    assert sum(r.supported for r in corpus.records) == 1
    assert corpus.packet.names() == ("PRIOR_SOURCE_PACKET.md", "PRIOR_TASK.md")
    assert len(DRAWS) >= 2, "one draw is a sample of this extractor, not a measurement"
    expected = set(TEXTS)
    for draw in DRAWS:
        recorded = {p.stem for p in (EXTRACTIONS / draw).glob("*.json")}
        assert expected <= recorded, f"{draw}: nothing recorded for {sorted(expected - recorded)}"


def test_the_recorded_verdicts_are_the_ones_the_sweep_measured(corpus: RetroCorpus) -> None:
    """The whole distribution, per record, pinned.

    This is the assertion the frozen single draw could not make. A change that
    moves any record in any draw shows up here as the record it moved, not as a
    shifted accuracy with nothing to point at.
    """
    assert _verdicts(corpus) == VERDICTS


def test_the_base_set_has_one_true_positive_and_one_false_accept(corpus: RetroCorpus) -> None:
    """The confusion matrix over the base set, 7 records by 2 draws.

    Kept as a test and not as prose because the shape of the misses is the
    finding. The previous sweep read tp=0: the corpus's only supported record
    produced no usable extraction at all, so a gate that rejected everything
    scored well on a corpus that is six-sevenths rejections while being worth
    nothing to a writer. That record now yields a usable extraction and is
    accepted, which is the first true positive this corpus has produced. What
    did not move is dev02-current, accepted in every draw of both sweeps against
    a reviewer rejection -- one false accept, and the same one as before.
    """
    matrix = {"tp": 0, "tn": 0, "fp": 0, "fn": 0, UNUSABLE: 0}
    for record in _CORPUS.records:
        want = EXPECTED[record.record_id]
        for outcome, count in VERDICTS[record.record_id].items():
            if outcome == UNUSABLE:
                matrix[UNUSABLE] += count
            elif outcome == want:
                matrix["tp" if want == ACCEPT else "tn"] += count
            else:
                matrix["fp" if outcome == ACCEPT else "fn"] += count
    assert matrix == {"tp": 2, "tn": 10, "fp": 2, "fn": 0, UNUSABLE: 0}
    assert sum(matrix.values()) == len(_CORPUS.records) * len(DRAWS)


def test_a_rejection_names_the_reason_the_reviewers_gave(corpus: RetroCorpus) -> None:
    """Rejecting for the wrong reason is not a hit.

    A record can reject in every draw and reach the reviewers' literal in only
    some of them, and an accuracy computed over verdicts alone would not show it.
    In this sweep every rejection of a base record named a reviewer literal, which
    is the one part of the result that has come out well in both sweeps.
    """
    named: dict[str, int] = {}
    for draw in DRAWS:
        for record in corpus.records:
            try:
                decision = _decide(record.record_id, draw, corpus)
            except ExtractionFormatError:
                continue
            if decision.accepted:
                continue
            haystack = " ".join(f"{v.claim_text} {v.detail}" for v in decision.unsupported)
            hit = any(literal in haystack for literal in record.discriminating_literals)
            named[record.record_id] = named.get(record.record_id, 0) + int(hit)
    assert named == NAMED_THE_REASON


def test_one_of_the_two_single_edit_faults_demonstrates_a_flip(corpus: RetroCorpus) -> None:
    """The faults were built to show the verdict tracks the edit. One now does.

    `version-history-injected` is accept-then-reject across its one edit: its base
    record dev03-explicit is accepted in both draws and the injected variant is
    rejected in both, which is the pair separating on the edit and nothing else.
    In the previous sweep the same fault rejected in all five draws with a base
    record that was unusable in all five, so there was no accepted verdict for the
    injection to flip -- the fault did not change, the extractor did.

    `version-claims-stripped` still shows nothing. It removes the very claims the
    reviewers rejected dev01-explicit for and is rejected anyway in both draws,
    which says that rejection does not depend on them. It is recorded here as it
    behaved; a fault that cannot separate its own pair is not evidence.
    """
    stripped = "dev01-explicit__version-claims-stripped"
    injected = "dev03-explicit__version-history-injected"
    assert EXPECTED[stripped] == ACCEPT and VERDICTS[stripped] == {REJECT: 2}
    assert VERDICTS["dev01-explicit"] == {REJECT: 2}
    assert VERDICTS[injected] == {REJECT: 2} and VERDICTS["dev03-explicit"] == {ACCEPT: 2}


def test_no_record_leaves_part_of_itself_unaccounted_for() -> None:
    """A verdict computed from a reply that skipped part of the record is not a
    verdict about that record. Measured: with no coverage rule the model returned
    12 claims for a record whose two version-history sentences -- the entire reason
    reviewers rejected it -- appeared in none of them, and the gate accepted it.
    The set is empty in this sweep and asserted as a set anyway, because a new hole
    in the extractor would arrive here as a name. That the rule still refuses a
    reply that drops a line is pinned directly, on a hand-built extraction, by
    `test_assert_covers_rejects_a_dropped_line_and_names_it`; this test is about
    which real recorded replies it refuses, which is now none of them."""
    uncovered = set()
    for draw in DRAWS:
        for record_id, text in TEXTS.items():
            transcript = load_transcript(EXTRACTIONS / draw / f"{record_id}.json")
            extraction = parse_extraction(
                record_id=record_id,
                model=transcript.model,
                reply=transcript.extraction_reply(),
            )
            try:
                assert_covers(extraction, text)
            except ExtractionFormatError:
                uncovered.add(record_id)
    assert uncovered == UNCOVERED


def test_every_record_was_audited_against_the_same_packet(corpus: RetroCorpus) -> None:
    """The comparison is only meaningful because all 8 opportunity sessions were
    handed identical evidence; `build` refuses to emit a fixture otherwise."""
    meta = json.loads((FIXTURE_DIR / "corpus.json").read_text())
    assert meta["packet"] == dict(corpus.packet.digests)


def test_the_base_set_is_also_separated_by_a_one_token_baseline(corpus: RetroCorpus) -> None:
    """Calibration, kept in the suite on purpose. Every rejected record contains
    the string 3.11 and the accepted one does not, so `"3.11" in text` scores 7 of
    7 on the base set, against the gate's 6 of 7. Clearing the base set is
    therefore necessary and not sufficient; the fault variants and an unseen-write
    trial are what would carry evidence, and neither does yet."""
    baseline = {r.record_id: ("3.11" not in r.text) for r in corpus.records}
    assert baseline == {r.record_id: r.supported for r in corpus.records}
