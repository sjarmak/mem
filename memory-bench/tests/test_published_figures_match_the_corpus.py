"""Every figure the public tree states about itself is the figure the corpus reads.

The release argues for its own validity with numbers: the pool order is defended by a
permutation test, the seeding instruction is defended by what a first-k policy scores
against chance. Those numbers ship in the README a downloader reads, the JSON schema
that is the contract, the standalone validator they run, the exporter that draws the
order and the baseline driver they copy. Nothing re-derived them, so when round 4
re-drew the corpus the round-3 figures stayed: "49 records of 160 (0.3063) ... p =
0.0067" was still shipping in four files against a tree that reads 47 of 160 (0.2938)
at p = 0.0026, and the README's chance-pass rate was the count of heads that hold the
gold, which is not what the grader asks for and overstated chance by about two-fold.

A stale figure in a benchmark's own contract is not cosmetic. It is the document that
tells a third party how hard the task is and what the order is evidence of, and a
reader who checks it against the data finds the benchmark contradicting itself.

So the numbers are computed once, in ``tests/public_figures.py``, from the released
tree, and every file that states one has to state that rendering verbatim. A re-draw
that moves a figure fails here until the prose is rewritten, and prose rewritten to
something the corpus does not say fails here too.
"""

from __future__ import annotations

import re

import pytest

from tests.public_figures import Claim, claims, published_first_k, published_ranks

# The figures that shipped before round 4 re-drew the corpus. They are named here, not
# as an example, but because each one was a false statement in a released file and the
# cheapest way to reintroduce one is to restore a docstring from history.
#
# A bare rate cannot stay on this list forever. "0.2188" was the round-3 published-order
# first-is-gold rate and was retired with the rest of that sentence; round 5 re-drew the
# corpus again and it came back as a LIVE figure, the ascending-alias rate this release
# measures at 35 records of 160 (0.2188). A string that is both retired and current
# tests nothing, so it is dropped rather than carried as a permanent false positive. The
# sentence it belonged to is still caught: "35 in 160" and "p = 0.5374" are the two
# halves that were only ever true of the retired measurement.
RETIRED_FIGURES = (
    "49 records of 160",
    "0.3063",
    "0.2192",
    "p = 0.0067",
    "35 in 160",
    "p = 0.5374",
)

# The shipped files, in the order a downloader meets them. ``tests/public_figures.py``
# is exempt: it quotes the retired figures to say they are retired.
_SEARCHED = ("README", "SCHEMA", "VALIDATOR", "EXPORTER", "BASELINE")


def _flat(text: str) -> str:
    """Collapse whitespace, so a figure split across a wrapped line still matches."""
    return re.sub(r"\s+", " ", text)


def _claims() -> tuple[Claim, ...]:
    computed = claims()
    assert computed, "no published figure is being checked, so this file asserts nothing"
    return computed


@pytest.mark.parametrize("claim", _claims(), ids=lambda claim: claim.name)
def test_every_file_that_states_a_figure_states_the_corpus_figure(claim: Claim) -> None:
    """The load-bearing check: each rendering appears verbatim where it is supposed to."""
    assert claim.files, f"{claim.name} is computed and then stated nowhere"
    needle = _flat(claim.text)
    for path in claim.files:
        assert path.exists(), f"{path} is named as stating {claim.name} and does not exist"
        haystack = _flat(path.read_text(encoding="utf-8"))
        assert needle in haystack, (
            f"{path.name} does not state the corpus figure for {claim.name}.\n"
            f"  the corpus reads: {claim.text}\n"
            "  Recompute with `python tests/public_figures.py` and rewrite the prose; do "
            "not edit the figure to match the file."
        )


@pytest.mark.parametrize("claim", _claims(), ids=lambda claim: claim.name)
def test_a_drifted_figure_would_not_match(claim: Claim) -> None:
    """The check above has to be reading the digits, not the sentence around them.

    Every digit in the rendering is perturbed in turn and the perturbed sentence must
    be absent from every file that carries the real one. Without this, a rendering that
    happened to contain no figure at all -- or a match that had quietly degraded to a
    substring of the surrounding prose -- would pass the test above on every file.
    """
    digits = [index for index, character in enumerate(claim.text) if character.isdigit()]
    assert digits, f"{claim.name} renders no digit, so it is not a figure"
    for index in digits:
        original = claim.text[index]
        drifted = _flat(
            claim.text[:index] + ("1" if original == "0" else "0") + claim.text[index + 1 :]
        )
        if drifted == _flat(claim.text):
            continue
        for path in claim.files:
            haystack = _flat(path.read_text(encoding="utf-8"))
            assert drifted not in haystack, (
                f"{path.name} contains a figure one digit away from {claim.name} "
                f"({drifted!r}), so a match there says nothing about which one it read"
            )


@pytest.mark.parametrize("figure", RETIRED_FIGURES)
def test_no_shipped_file_still_states_a_retired_figure(figure: str) -> None:
    """A figure from a corpus that no longer exists is a claim about nothing."""
    import tests.public_figures as module

    for name in _SEARCHED:
        path = getattr(module, name)
        text = path.read_text(encoding="utf-8")
        assert figure not in text, (
            f"{path.name} still states {figure!r}, a round-3 figure measured on a corpus "
            "this tree replaced"
        )


def test_the_corpus_the_figures_are_measured_on_is_the_released_one() -> None:
    """The figures are only worth pinning if they came from the shipped records.

    ``public_figures`` reads ``public/data`` and scores with the baseline driver's own
    grader. This asserts the sample it summed over is the whole release rather than
    whatever subset happened to parse, so a half-read corpus cannot quietly produce a
    figure that then gets written into the contract.
    """
    ranks = published_ranks()
    policy = published_first_k()
    corpus = ranks["corpus"]
    assert corpus.n_records == policy.n_records, (
        f"the rank figures cover {corpus.n_records} records and the policy figures "
        f"{policy.n_records}; they are not measurements of one release"
    )
    tiers = {label: stat.n_records for label, stat in ranks.items() if label != "corpus"}
    assert sum(tiers.values()) == corpus.n_records, (
        f"the tiers hold {tiers} and the corpus {corpus.n_records}: a record is filed "
        "under no tier or under two"
    )
    assert len(tiers) == 2, f"the release is published in two tiers, not {sorted(tiers)}"
