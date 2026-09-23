#!/usr/bin/env python3
"""Run the retro corpus N times and record what the gate said each time (mem-h1k6w).

One draw is not a verdict. The gate scored 9 of 9 on a frozen draw, then 4 of 9
and 6 of 9 on two draws of the same records against the same labels, and which
off-episode pair discriminates moved between draws as well. Greedy decoding is
pinned -- temperature 0, top_p 1.0, top_k 1, a fixed seed -- and pinning it does
not make this stack reproducible, so the honest unit of measurement is a
distribution over repeats rather than whichever draw was run last.

Every draw is kept in full: the transcripts, so a draw can be replayed exactly,
and the verdict, so the spread can be read without a daemon. `--fixture-dir`
writes the same draws into the retro fixture, which is what makes the regression
suite score the distribution instead of one sample.

    python scripts/measure_fidelity_replication.py --draws 5 \\
        --out results/fidelity-replication-20260906
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from membench.bbon.comparative_judge import ComparativeJudgeError
from membench.fidelity.claims import ExtractionFormatError
from membench.fidelity.extract import ClaimExtractor
from membench.fidelity.gate import FidelityGate, GateDecision
from membench.fidelity.local_extractor import ReplyRecorder, build_judge
from membench.fidelity.retro_corpus import RetroCorpus, load
from membench.fidelity.transcript import record, save_transcript

DEFAULT_FIXTURE = Path("tests/fixtures/bd_capture_fidelity")

# A draw that produced no usable extraction is neither an accept nor a reject.
# Folding it into either would be the reporting bug this script exists to stop:
# an unusable draw counted as a rejection reads as the gate catching something.
UNUSABLE = "unusable"


@dataclass(frozen=True)
class Outcome:
    record_id: str
    draw: int
    verdict: str
    summary: str
    named_reviewer_literal: bool | None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--draws", type=int, default=5)
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="also write the draws into the fixture, replacing what is there",
    )
    args = parser.parse_args()

    corpus = load(args.fixture_dir)
    judge = build_judge(args.timeout_s)
    judge.preflight()
    recorder = ReplyRecorder(judge)
    gate = FidelityGate(extractor=ClaimExtractor(complete=recorder, model=judge.model))

    texts = {r.record_id: r.text for r in corpus.records}
    texts.update({f.fault_id: f.text for f in corpus.faults})

    args.out.mkdir(parents=True, exist_ok=True)
    outcomes: list[Outcome] = []
    for draw in range(1, args.draws + 1):
        draw_dir = args.out / f"draw-{draw}"
        draw_dir.mkdir(exist_ok=True)
        print(f"=== draw {draw} of {args.draws} ===", flush=True)
        for record_id, text in texts.items():
            outcomes.append(_one(gate, recorder, corpus, draw_dir, draw, record_id, text))
    (args.out / "verdicts.json").write_text(
        json.dumps([o.__dict__ for o in outcomes], indent=1) + "\n"
    )
    report = _report(corpus, outcomes, args.draws, judge.model)
    (args.out / "REPORT.md").write_text(report)
    print(report)
    if args.freeze:
        _freeze(args.out, args.fixture_dir / "extractions", draws=args.draws)


def _one(
    gate: FidelityGate,
    recorder: ReplyRecorder,
    corpus: RetroCorpus,
    draw_dir: Path,
    draw: int,
    record_id: str,
    text: str,
) -> Outcome:
    """One record, one draw, kept whatever it does.

    A daemon fault and a reply the parser cannot read are both results here, and
    both are recorded: a run that dropped them would report the distribution of
    the draws that happened to work, which is a different and friendlier number.
    """
    recorder.reset()
    try:
        decision = gate.evaluate(record_id, text, corpus.packet)
    except (ExtractionFormatError, ComparativeJudgeError) as exc:
        _save(draw_dir, record_id, recorder)
        print(f"{record_id}: {UNUSABLE} -- {exc}", flush=True)
        return Outcome(record_id, draw, UNUSABLE, str(exc), None)
    _save(draw_dir, record_id, recorder)
    print(decision.summary(), flush=True)
    return Outcome(
        record_id=record_id,
        draw=draw,
        verdict="accept" if decision.accepted else "reject",
        summary=decision.summary(),
        named_reviewer_literal=_named_reviewer_literal(corpus, record_id, decision),
    )


def _named_reviewer_literal(
    corpus: RetroCorpus, record_id: str, decision: GateDecision
) -> bool | None:
    """Did a rejection object to what the reviewers objected to?

    Rejecting for the wrong reason is not a pass, and over repeated draws it is
    the number most likely to hide inside an accuracy: a record can reject in
    every draw and reach the reviewers' token in only some of them.
    """
    record = next((r for r in corpus.records if r.record_id == record_id), None)
    if record is None or decision.accepted:
        return None
    haystack = " ".join(f"{v.claim_text} {v.detail}" for v in decision.unsupported)
    return any(literal in haystack for literal in record.discriminating_literals)


def _save(draw_dir: Path, record_id: str, recorder: ReplyRecorder) -> None:
    save_transcript(
        draw_dir / f"{record_id}.json",
        record(recorder.prompts, recorder.replies, model=recorder.judge.model),
    )


def _report(corpus: RetroCorpus, outcomes: list[Outcome], draws: int, model: str) -> str:
    """A table per record, because the aggregate hides which record moved."""
    expected = {r.record_id: ("accept" if r.supported else "reject") for r in corpus.records}
    expected.update(
        {f.fault_id: ("accept" if f.expected_accept else "reject") for f in corpus.faults}
    )
    lines = [
        f"# Retro replication, {draws} draws",
        "",
        f"Model: `{model}`, greedy decoding, one process, drawn back to back.",
        "",
        "| record | expected | accept | reject | unusable | matched |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    per_draw: dict[int, int] = {}
    for record_id, want in expected.items():
        mine = [o for o in outcomes if o.record_id == record_id]
        counts = {v: sum(1 for o in mine if o.verdict == v) for v in ("accept", "reject", UNUSABLE)}
        matched = counts[want]
        lines.append(
            f"| {record_id} | {want} | {counts['accept']} | {counts['reject']} | "
            f"{counts[UNUSABLE]} | {matched}/{draws} |"
        )
        for o in mine:
            per_draw[o.draw] = per_draw.get(o.draw, 0) + (1 if o.verdict == want else 0)
    lines += ["", "| draw | matched |", "| --- | --- |"]
    for draw in sorted(per_draw):
        lines.append(f"| {draw} | {per_draw[draw]}/{len(expected)} |")
    return "\n".join(lines) + "\n"


def _freeze(out: Path, extractions: Path, *, draws: int) -> None:
    """Replace the fixture with these draws, all of them, or refuse.

    All of them is the point. Freezing the draw that scored best is how a
    non-replicating instrument comes to look like a replicating one, so the
    fixture takes the sweep as it ran.
    """
    staged = [out / f"draw-{d}" for d in range(1, draws + 1)]
    missing = [d for d in staged if not d.is_dir()]
    if missing:
        print(f"not freezing: {', '.join(d.name for d in missing)} missing")
        return
    if extractions.exists():
        shutil.rmtree(extractions)
    extractions.mkdir(parents=True)
    for draw_dir in staged:
        shutil.copytree(draw_dir, extractions / draw_dir.name)
    print(f"{draws} draws frozen into {extractions}")


if __name__ == "__main__":
    main()
