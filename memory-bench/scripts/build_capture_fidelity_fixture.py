#!/usr/bin/env python3
"""Regenerate the capture-fidelity retro fixture (mem-747nj).

Two stages, deliberately separable:

  --records     lift the 7 labelled records, the shared evidence packet and the
                2 fault mutations out of a bd-reliability-campaign results tree.
                Needs the campaign tree; needs no model.

  --extract     run the claim extractor over every record and fault variant
                against the local Ollama stack, and record each raw reply.
                Needs a daemon; needs no campaign tree.

The recorded replies are what CI replays. Keeping the model call out of the test
run is not a convenience: it makes a stored verdict reproducible, so a red test
means the gate's rules changed rather than that the daemon answered differently
today. Re-running --extract is how the gate is re-measured against a model, and
it is expected to move numbers; that is a measurement, not a regression.

    python scripts/build_capture_fidelity_fixture.py --records --extract \\
        --campaign-root ../memory-bench/results/bd-reliability-campaign-20260905
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from membench.bbon.comparative_judge import ComparativeJudgeError
from membench.bbon.local_stack_judge import LocalStackComparativeJudge
from membench.fidelity.claims import ExtractionFormatError
from membench.fidelity.extract import ClaimExtractor
from membench.fidelity.gate import FidelityGate
from membench.fidelity.local_extractor import MAX_ATTEMPTS, ReplyRecorder, build_judge
from membench.fidelity.retro_corpus import build, load
from membench.fidelity.transcript import Transcript, record, save_transcript

DEFAULT_FIXTURE = Path("tests/fixtures/bd_capture_fidelity")
DEFAULT_CAMPAIGN = Path("results/bd-reliability-campaign-20260905")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--campaign-root", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--records", action="store_true", help="rebuild records + packet")
    parser.add_argument("--extract", action="store_true", help="re-run the model extractions")
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=900.0,
        help="per-record daemon timeout; a full evidence packet plus a long "
        "structured reply runs past the judge default on a local box",
    )
    args = parser.parse_args()
    if not (args.records or args.extract):
        parser.error("nothing to do: pass --records, --extract, or both")

    if args.records:
        build(
            campaign_root=args.campaign_root,
            fixture_dir=args.fixture_dir,
            labels=args.fixture_dir / "labels.json",
        )
        print(f"records + packet + faults written to {args.fixture_dir}")

    if args.extract:
        _extract(args.fixture_dir, args.timeout_s)


def _extract(
    fixture_dir: Path, timeout_s: float, judge: LocalStackComparativeJudge | None = None
) -> None:
    corpus = load(fixture_dir)
    judge = judge or build_judge(timeout_s)
    judge.preflight()
    recorder = ReplyRecorder(judge)
    extractor = ClaimExtractor(complete=recorder, model=judge.model)
    gate = FidelityGate(extractor=extractor)
    # Staged, then swapped in at the end. Two properties are needed at once and
    # only a staging directory gives both: every file in the committed fixture
    # comes from one run (a mix of two is a verdict nothing reached), and a run
    # that dies part-way leaves the previous fixture intact. An earlier version
    # cleared the live directory first and had the second property backwards --
    # an aborted run destroyed a good fixture and left nothing in its place,
    # which is how one was actually lost.
    out = fixture_dir / "extractions.staging"
    final = fixture_dir / "extractions"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    texts = {r.record_id: r.text for r in corpus.records}
    texts.update({f.fault_id: f.text for f in corpus.faults})
    failures = 0
    for record_id, text in texts.items():
        # The extractor drives the calls, so the fixture measures the gate's real
        # behaviour -- repair round included. Building the prompt here instead
        # would record a first reply the gate would never have settled for.
        recorder.reset()
        try:
            extraction = extractor.extract(record_id, text, corpus.packet)
        except ComparativeJudgeError as exc:
            failures += 1
            if recorder.replies:  # the repair call died; the turns so far are worth keeping
                _record(out, record_id, recorder)
            print(f"{record_id}: no extraction after {MAX_ATTEMPTS} attempts -- {exc}")
            continue
        # A reply the parser cannot read, or one that leaves part of the record
        # unaccounted for, is a rejected write rather than a dead batch: report it
        # and keep going, so one bad block does not hide the other results. The
        # recording is kept, which is how a format failure gets diagnosed at all.
        except ExtractionFormatError as exc:
            failures += 1
            _record(out, record_id, recorder)
            print(f"{record_id}: reject (unusable extraction) {exc}")
            continue
        transcript = _record(out, record_id, recorder)
        repaired = f" (after {transcript.repairs} repair)" if transcript.repairs else ""
        print(f"{gate.decide(extraction, corpus.packet).summary()}{repaired}")
    if failures:
        print(f"{failures} of {len(texts)} records produced no usable extraction")
    _swap_in(out, final, expected=len(texts))


def _swap_in(staging: Path, final: Path, *, expected: int) -> None:
    """Replace the committed fixture with the staged one, or refuse and say so.

    Refused unless the run recorded every record. A recording is written even for
    a reply the parser could not read -- that is a result, and a useful one -- so
    a missing file means the daemon never answered for that record, and a fixture
    short of one is a corpus with a hole rather than a measurement with a
    rejection in it. The half-run is left in place under its staging name so the
    replies it did get can still be read.

    The previous fixture surviving that refusal is the property the whole staging
    arrangement exists for: an earlier version cleared the live directory first,
    and a run that aborted destroyed a good fixture and left nothing in its place.
    """
    recorded = len(list(staging.glob("*.json")))
    if recorded < expected:
        print(
            f"{recorded} of {expected} records recorded -- keeping the previous "
            f"fixture at {final} and leaving this run at {staging}"
        )
        return
    if final.exists():
        shutil.rmtree(final)
    staging.rename(final)
    print(f"{recorded} recordings written to {final}")


def _record(out: Path, record_id: str, recorder: ReplyRecorder) -> Transcript:
    """Write every turn the run took, labelled, so a replay can retake them.

    Storing the last reply alone is what made the fixture score a different gate
    than the one that runs: with the second pass wired in, the last reply is the
    recovery ask's, and the calls that produced the verdict were never replayed at
    all. ``repairs`` stays in the file as a read-off of the phases, because a
    rising count is the signal that the prompt, not the gate, needs work -- and it
    now counts only the re-asks, not the audit's two calls.
    """
    transcript = record(recorder.prompts, recorder.replies, model=recorder.judge.model)
    save_transcript(out / f"{record_id}.json", transcript)
    return transcript


if __name__ == "__main__":
    main()
