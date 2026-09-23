#!/usr/bin/env python3
"""Run the fidelity gate and its competitor over the off-episode probe pairs.

Each authored packet ships a supported record, `probes/supported.txt`, and two
lure records that are that same file with one declared background claim added:

  version-token  the claim carries a token the evidence lacks, so a plain string
                 search finds it too
  token-free     the claim carries no such token at all, so no token matcher can
                 reach it however it is tuned, and the citation check is the only
                 thing that can

Both records of both pairs also go through `token_baseline`, the corpus-independent
grep the gate has to beat. Running the gate alone answers "does it reject a lure",
which was never the question -- the question is whether it rejects one the free
competitor cannot, and that is the pre-registered stop rule this reports at the end
(`membench/fidelity/stop_rule.py`).

What one pair can show, in order of what it would mean:

  accept / reject       the pair discriminates off-episode
  accept / accept       the gate does not see this intrusion
  reject / reject       the packet rejects a record inside its own boundary and is
                        unusable off-episode as authored
  reject / accept       something is wrong with the pair, not with the gate

`--draws` defaults to 1, and that default is currently wrong. It was 5, on
mem-h1k6w's finding that two runs of the same three pairs, same model pin and same
greedy settings, disagreed on all three. It was dropped to 1 on 2026-09-06, when
all six pairs of the current set returned the identical outcome in all five draws
(`tests/fixtures/offepisode_packets/RUN-2026-09-06-5draw.log`) and the repeats
looked like copies rather than samples.

The 2026-09-07 rerun falsified that (`RUN-2026-09-07-1draw.log`). Both probes of a
packet read the same `probes/supported.txt`, and the extract prompt is a pure
function of the record text and the packet, so those two calls are the same
question asked twice inside one process. For `pg-index-lock` they came back with
entirely different claim decompositions, and the record was accepted under one
probe and rejected under the other -- in the same draw. Greedy decoding at this pin
is not reproducible, so a single draw is one sample and the default understates
what it is reading. Raise `--draws` before treating a run as settled; the
aggregation below already reads the outcomes strictly, counting a lure as caught
only if the gate rejected it in *every* draw, because a pair that discriminates in
one draw of five does not discriminate.

Every draw's model turns are recorded under each probe's
`probes/recordings/<probe_id>/draw-N/`, so any verdict can be replayed and
re-verified with no daemon.

    uv run python scripts/run_offepisode_probes.py
    uv run python scripts/run_offepisode_probes.py --packet pg-index-lock --draws 5
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from membench.bbon.comparative_judge import ComparativeJudgeError
from membench.fidelity.claims import ExtractionFormatError
from membench.fidelity.extract import ClaimExtractor
from membench.fidelity.gate import FidelityGate, GateDecision
from membench.fidelity.local_extractor import ReplyRecorder, build_judge
from membench.fidelity.offepisode_corpus import OffEpisodePacket, ProbePair, load_all
from membench.fidelity.packet import normalize
from membench.fidelity.stop_rule import PairOutcome, evaluate
from membench.fidelity.token_baseline import baseline_decide
from membench.fidelity.transcript import record, save_transcript

DEFAULT_PACKET_ROOT = Path("tests/fixtures/offepisode_packets")

# The two records of a pair, and the file each is read from.
VARIANTS = ("supported", "lure")

# What a pair did in one draw. Named rather than printed inline because these are
# counted across draws, and a tally of free-text sentences is not a tally.
DISCRIMINATES = "discriminates"
UNNAMED = "discriminates, rejection names no declared token"
NO_DISCRIMINATION = "no discrimination, the lure was accepted"
UNUSABLE = "unusable, the supported record was rejected too"
INVERTED = "inverted, supported rejected and lure accepted"
NO_RESULT = "no result, a record produced no usable extraction"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet-root", type=Path, default=DEFAULT_PACKET_ROOT)
    parser.add_argument(
        "--packet",
        action="append",
        default=None,
        help="run only this packet id; repeatable (default: all of them)",
    )
    parser.add_argument(
        "--draws",
        type=int,
        default=1,
        help="how many times to run each pair; the outcomes across draws are the result",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=900.0,
        help="per-record daemon timeout; a full evidence packet plus a long "
        "structured reply runs past the judge default on a local box",
    )
    args = parser.parse_args()

    packets = load_all(args.packet_root)
    if args.packet:
        wanted = set(args.packet)
        packets = tuple(p for p in packets if p.packet_id in wanted)
        missing = wanted - {p.packet_id for p in packets}
        if missing:
            parser.error(f"no such packet: {', '.join(sorted(missing))}")

    judge = build_judge(args.timeout_s)
    judge.preflight()
    recorder = ReplyRecorder(judge)
    gate = FidelityGate(extractor=ClaimExtractor(complete=recorder, model=judge.model))

    pairs = [(packet, probe) for packet in packets for probe in packet.probes]
    outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    verdicts: dict[str, list[dict[str, GateDecision | None]]] = defaultdict(list)

    for draw in range(1, args.draws + 1):
        print(f"\n########## draw {draw} of {args.draws}")
        for packet, probe in pairs:
            key = f"{packet.packet_id}/{probe.probe_id}"
            print(f"\n=== {key} ({packet.domain}) ===")
            print(f"    reaches for: {probe.reaches_for}")
            decisions = {v: _run_one(gate, recorder, packet, probe, v, draw) for v in VARIANTS}
            label, detail = _classify(probe, decisions)
            print(f"  pair: {detail}")
            outcomes[key][label] += 1
            verdicts[key].append(decisions)

    if args.draws > 1:
        _report_draws(outcomes, args.draws)
    _report_stop_rule(pairs, verdicts)


def _run_one(
    gate: FidelityGate,
    recorder: ReplyRecorder,
    packet: OffEpisodePacket,
    probe: ProbePair,
    variant: str,
    draw: int,
) -> GateDecision | None:
    """One record through the gate and through the baseline, recorded either way.

    A reply the parser cannot read is a rejected write, not a dead run: it is
    reported and the run continues, so one unusable extraction does not hide the
    other half of the pair. The recording is kept either way, which is how a
    format failure gets diagnosed at all.
    """
    record_id = f"{packet.packet_id}__{probe.probe_id}__{variant}"
    text = probe.supported if variant == "supported" else probe.lure
    print(f"  {variant}: {baseline_decide(text, packet.packet).summary()}")
    recorder.reset()
    out = _recordings_dir(probe, draw)
    try:
        extraction = gate.extractor.extract(record_id, text, packet.packet)
    except (ComparativeJudgeError, ExtractionFormatError) as exc:
        _record(out, variant, recorder)
        print(f"  {variant}: no verdict -- {exc}")
        return None
    decision = gate.decide(extraction, packet.packet)
    repairs = _record(out, variant, recorder)
    repaired = f" (after {repairs} repair)" if repairs else ""
    print(f"  {decision.summary()}{repaired}")
    return decision


def _classify(probe: ProbePair, decisions: dict[str, GateDecision | None]) -> tuple[str, str]:
    """What the pair showed, and whether the rejection was for the right reason.

    On a token-detectable pair a rejection over some unrelated token is not the
    result the pair was built for -- it would read as a pass while testing nothing
    about the intrusion -- so the tokens the gate named are checked against the
    ones the manifest declared. A token-free pair declares none, and there is
    nothing to check: the rejection reasons are printed by `decision.summary()`
    and a reader wanting to know whether the citation check did the work reads
    them there.
    """
    supported, lure = decisions["supported"], decisions["lure"]
    if supported is None or lure is None:
        return NO_RESULT, NO_RESULT
    if supported.accepted and not lure.accepted:
        if not probe.token_detectable:
            return DISCRIMINATES, "DISCRIMINATES -- token-free lure, no token to name"
        named = _declared_tokens_named(probe, lure)
        if named:
            return DISCRIMINATES, f"DISCRIMINATES -- rejection names {', '.join(sorted(named))}"
        return UNNAMED, UNNAMED
    if supported.accepted:
        return NO_DISCRIMINATION, NO_DISCRIMINATION
    if not lure.accepted:
        reasons = "; ".join(v.claim_text for v in supported.unsupported)
        return UNUSABLE, f"{UNUSABLE}: {reasons}"
    return INVERTED, INVERTED


def _report_draws(outcomes: dict[str, Counter[str]], draws: int) -> None:
    """The tally per pair, which is the thing a spend decision reads.

    Printed as counts and never as a rate: three draws of a pair is not a
    percentage, and writing it as one invites the next reader to compare it with
    a number that was measured over a different count.
    """
    print(f"\n########## across {draws} draws")
    for key, counter in outcomes.items():
        parts = ", ".join(f"{n}x {label}" for label, n in counter.most_common())
        print(f"  {key}: {parts}")


def _report_stop_rule(
    pairs: list[tuple[OffEpisodePacket, ProbePair]],
    verdicts: dict[str, list[dict[str, GateDecision | None]]],
) -> None:
    """Aggregate the draws into one outcome per pair, then apply the rule.

    Unanimity in both directions, and deliberately not symmetric in what it
    forgives: the supported record has to have been accepted every time, and the
    lure has to have been rejected every time. Anything short of that is the
    coin-flip behaviour the draw sweep was built to expose, and crediting it would
    let a gate pass the rule by being noisy rather than by being right.
    """
    outcomes = []
    for packet, probe in pairs:
        draws = verdicts[f"{packet.packet_id}/{probe.probe_id}"]
        gate_supported = _unanimous(draws, "supported", accepted=True)
        gate_lure = _unanimous(draws, "lure", accepted=False)
        outcomes.append(
            PairOutcome(
                packet_id=packet.packet_id,
                probe_id=probe.probe_id,
                token_detectable=probe.token_detectable,
                gate_accepts_supported=gate_supported,
                gate_accepts_lure=None if gate_lure is None else not gate_lure,
                baseline_accepts_supported=baseline_decide(probe.supported, packet.packet).accepted,
                baseline_accepts_lure=baseline_decide(probe.lure, packet.packet).accepted,
            )
        )
    print("\n##########")
    print(evaluate(tuple(outcomes)).report())


def _unanimous(
    draws: list[dict[str, GateDecision | None]], variant: str, accepted: bool
) -> bool | None:
    """Did every draw of this record land on ``accepted``? None if any had no verdict."""
    seen = [draw[variant] for draw in draws]
    if not seen or any(decision is None for decision in seen):
        return None
    return all(decision.accepted is accepted for decision in seen if decision is not None)


def _declared_tokens_named(probe: ProbePair, decision: GateDecision) -> set[str]:
    """The manifest's betraying tokens that appear in some unsupported claim."""
    unsupported = normalize(" ".join(v.claim_text for v in decision.unsupported))
    return {literal for literal in probe.betraying_literals if normalize(literal) in unsupported}


def _recordings_dir(probe: ProbePair, draw: int) -> Path:
    out = probe.recordings_root / f"draw-{draw}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _record(out: Path, variant: str, recorder: ReplyRecorder) -> int:
    """Write every model turn of this record, and return the repair count.

    The whole call sequence, not the reply the verdict came from. A recording of
    one reply cannot be replayed through the extractor, so it cannot re-derive
    the verdict it is filed under -- and while the second pass was being wired in
    that one reply silently became the audit's one-line answer, with the audit's
    calls counted as repairs.
    """
    transcript = record(recorder.prompts, recorder.replies, model=recorder.judge.model)
    save_transcript(out / f"{variant}.json", transcript)
    return transcript.repairs


if __name__ == "__main__":
    main()
