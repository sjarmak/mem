"""Run the memory-necessity gate over a whole corpus and make the result an artifact.

``necessity_gate`` judges ONE candidate; ``pilot_filter`` owns the accept / reject
arithmetic. This module adds neither — it is the loop that runs the existing gate over
every candidate and records the aggregate so a corpus can ship with its rejection rate
attached instead of a number someone quotes from a console log.

The loop owns one thing the gate cannot know: SCOPE. A project-tier sequence answers
from memory an EARLIER sequence of the same world wrote, so piloting it alone rejects
it for being cross-session (mem-r6yzk B1). ``context_for`` derives that prefix from the
corpus layout — the world dir a sequence was loaded from and its position in that
world's file — and hands it to the gate on a ``GateCandidate``. Session-tier candidates
get an empty context and take the isolated path unchanged.

What the number means depends entirely on WHICH agent ran the pilot, so
``reference_agent`` is a required field of both ``SweepResult`` and the written
artifact. Under the default ``ScriptedAgent`` the pilot is pure set logic over
``available_memory``: the oracle arm is handed the required memory ids and the
no-memory arm is not, so a well-formed sequence separates BY CONSTRUCTION and the
rejection rate measures construction integrity, not task difficulty and not
whether a real agent needs memory. A rejection under that agent is therefore a
real defect signal (the sequence does not even separate when the facts are handed
over); a low rejection rate licenses no claim about a real agent's behaviour. The
field exists so a ScriptedAgent verdict can never be read as a real-agent verdict.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from membench.generators.memory_necessity_gate import (
    GateCandidate,
    NecessityResult,
    necessity_gate,
)
from membench.report.comparison import EPSILON
from membench.runner.agent import Agent, ScriptedAgent
from membench.schemas.sequence import BenchmarkSequence

# Bumped when the shape of the written artifact or the sweep's scope changes. The
# per-sequence decision it reports is versioned by the gate it calls, not here.
# v2: candidates carry a context prefix, so a project-tier sequence is piloted under a
# store shared with its world's earlier sequences, and every arm mean is taken over the
# graded steps rather than the whole sequence (mem-r6yzk B1).
GATE_VERSION = "necessity-sweep.v2"

# The materialised sequences inside one frozen world dir (``write_world`` layout).
SEQUENCES_FILE = "sequences.json"
# Where a corpus's sweep artifact is written, beside the world dirs it covers.
NECESSITY_FILE = "necessity.json"


@dataclass(frozen=True)
class SweepResult:
    """The memory-necessity gate's aggregate verdict over a set of candidates.

    ``accepted`` / ``rejected`` carry every per-sequence ``NecessityResult`` (each
    holding the rewards, delta, epsilon and reason ``pilot_filter`` produced), so
    the aggregate is always traceable back to the decisions that made it.
    ``rejection_rate`` is over CANDIDATES — ``len(rejected) / (len(accepted) +
    len(rejected))`` — never over the accepted set.
    """

    accepted: tuple[NecessityResult, ...]
    rejected: tuple[NecessityResult, ...]
    rejection_rate: float
    epsilon: float
    reference_agent: str
    gate_version: str

    @property
    def n_candidates(self) -> int:
        return len(self.accepted) + len(self.rejected)

    @property
    def n_accepted(self) -> int:
        return len(self.accepted)

    @property
    def n_rejected(self) -> int:
        return len(self.rejected)

    @property
    def by_sequence_id(self) -> dict[str, NecessityResult]:
        """Every decision, accepted and rejected, keyed by the sequence it judged."""
        return {r.sequence_id: r for r in self.accepted + self.rejected}

    @property
    def deltas(self) -> tuple[float, ...]:
        """Every candidate's ``oracle - no_memory``, ascending.

        The spread is what says whether ``epsilon`` decided anything. A rate of 0.0000
        over candidates that all scored the same delta is a corpus that separates
        completely, not a threshold that examined 200 close calls, and the two readings
        are indistinguishable from the rate alone.
        """
        values = tuple(sorted(r.verdict.delta for r in (*self.accepted, *self.rejected)))
        if not values:
            raise ValueError("a sweep over an empty candidate set has no delta distribution")
        return values

    @property
    def margin(self) -> float:
        """How far the closest candidate sat from the threshold that judged it."""
        return self.deltas[0] - self.epsilon


def describe_rate(sweep: SweepResult) -> tuple[str, str, str]:
    """The rejection rate, the margin it hides, and what each one is evidence of.

    Printed by every gate surface (``membench.cli gate-corpus`` and
    ``scripts/gate_public_corpus.py``), rendered here once so the two cannot drift.
    An operator reads one line of gate output and nothing else, so that line has to
    carry the distribution the rate was computed over, and has to say plainly which
    of the two readings the rate supports (mem-r6yzk R4).

    The sentences are fixed. Nothing here branches on how large the margin is: the
    numbers are printed and the reader draws the conclusion.
    """
    deltas = sweep.deltas
    return (
        f"delta = oracle - no_memory over {len(deltas)} candidate(s): min {deltas[0]:.4f}, "
        f"median {median(deltas):.4f}, max {deltas[-1]:.4f}, against epsilon "
        f"{sweep.epsilon:.4f}; the closest candidate sits {sweep.margin:+.4f} from the "
        "threshold.",
        f"What {sweep.rejection_rate:.4f} is evidence of: under {sweep.reference_agent} "
        f"the corpus separates — {sweep.n_accepted} of {sweep.n_candidates} scored higher "
        "with their required memories handed over than without them.",
        "What it is NOT evidence of: that epsilon is doing work (read the margin above "
        "— a margin far from zero means the threshold never came near binding), that the "
        "corpus is hard, or that any real agent needs memory. No model ran.",
    )


def context_for(
    world_sequences: Sequence[BenchmarkSequence], index: int
) -> tuple[BenchmarkSequence, ...]:
    """The prefix the candidate at ``index`` must be piloted alongside.

    A project-tier sequence reads what the world's EARLIER sequences wrote, so its
    pilot replays them first; everything before it in the world's own file is that
    prefix. Any other tier is answerable inside its own scope and gets an empty
    context, which keeps the isolated pilot byte-identical to the v1 path.

    Position is the corpus's own ordering — ``load_corpus_sequences`` preserves world
    dir order and then file order — not a re-derivation from ids.
    """
    if not 0 <= index < len(world_sequences):
        raise IndexError(
            f"candidate index {index} is outside the {len(world_sequences)} sequence(s) "
            "of its world"
        )
    if world_sequences[index].tier != "project":
        return ()
    return tuple(world_sequences[:index])


def sweep_necessity(
    candidates: Sequence[GateCandidate],
    *,
    agent: Agent | None = None,
    epsilon: float = EPSILON,
) -> SweepResult:
    """Run ``necessity_gate`` over every candidate and aggregate the verdicts.

    A pure loop: the admission decision is ``pilot_filter``'s and is not re-derived
    here, and the pilot's SCOPE is the candidate's own (an empty context takes the
    isolated path, a non-empty one the shared-store project path). ``agent`` defaults
    to the deterministic ``ScriptedAgent`` — the same reference agent ``run_sequence``
    defaults to — and its ``agent_config_id`` is recorded on the result, because the
    rejection rate is only interpretable against the agent that produced it.

    Raises ``ValueError`` on an empty candidate set: a rejection rate over zero
    candidates has no value, and returning 0.0 would report a clean corpus where
    there is no corpus at all.
    """
    if not candidates:
        raise ValueError("cannot sweep the necessity gate over an empty candidate set")
    reference_agent = agent if agent is not None else ScriptedAgent()
    accepted: list[NecessityResult] = []
    rejected: list[NecessityResult] = []
    for candidate in candidates:
        result = necessity_gate(candidate, agent=reference_agent, epsilon=epsilon)
        (accepted if result.verdict.accepted else rejected).append(result)
    n_candidates = len(accepted) + len(rejected)
    return SweepResult(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        rejection_rate=len(rejected) / n_candidates,
        epsilon=epsilon,
        reference_agent=reference_agent.agent_config_id,
        gate_version=GATE_VERSION,
    )


@dataclass(frozen=True)
class CorpusCandidate:
    """One candidate sequence and the frozen world dir it was materialised into."""

    world: str
    sequence: BenchmarkSequence


def load_corpus_sequences(corpus_dir: str | Path) -> tuple[CorpusCandidate, ...]:
    """Load every materialised sequence under ``<corpus_dir>/<world>/sequences.json``.

    World dirs are visited in sorted order and sequences in file order, so the sweep
    is reproducible. Raises ``FileNotFoundError`` when the corpus dir is missing or
    holds no ``sequences.json`` — an empty sweep would otherwise report a rejection
    rate for a corpus that was never read.
    """
    base = Path(corpus_dir)
    if not base.is_dir():
        raise FileNotFoundError(f"corpus dir does not exist: {base}")
    candidates: list[CorpusCandidate] = []
    for world_dir in sorted(d for d in base.iterdir() if (d / SEQUENCES_FILE).is_file()):
        raw = json.loads((world_dir / SEQUENCES_FILE).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"{world_dir / SEQUENCES_FILE} is not a list of sequences")
        for item in raw:
            candidates.append(
                CorpusCandidate(
                    world=world_dir.name,
                    sequence=BenchmarkSequence.model_validate(item),
                )
            )
    if not candidates:
        raise FileNotFoundError(f"no {SEQUENCES_FILE} found under {base}")
    return tuple(candidates)


@dataclass(frozen=True)
class CorpusGateReport:
    """A corpus's sweep, plus the world each graded sequence came from."""

    corpus_dir: Path
    sweep: SweepResult
    worlds: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        """The written artifact. ``reference_agent`` is emitted unconditionally: the
        rejection rate is meaningless without it."""
        decisions = self.sweep.by_sequence_id
        return {
            "gate_version": self.sweep.gate_version,
            "epsilon": self.sweep.epsilon,
            "reference_agent": self.sweep.reference_agent,
            "corpus": self.corpus_dir.name,
            "n_candidates": self.sweep.n_candidates,
            "n_accepted": self.sweep.n_accepted,
            "n_rejected": self.sweep.n_rejected,
            "rejection_rate": self.sweep.rejection_rate,
            # Corpus order (world dir, then file order), which is what ``worlds``
            # preserves — so the artifact reads in the same order the sweep ran.
            "per_sequence": [
                {
                    "sequence_id": sequence_id,
                    "world": world,
                    "accepted": decisions[sequence_id].verdict.accepted,
                    "oracle_reward": decisions[sequence_id].verdict.oracle_reward,
                    "no_memory_reward": decisions[sequence_id].verdict.no_memory_reward,
                    "delta": decisions[sequence_id].verdict.delta,
                    # The trial set the two rewards are means over. Without it the
                    # delta is unreadable: the same task reports as discriminating or
                    # not depending on the denominator (mem-r6yzk B1).
                    "scope": decisions[sequence_id].verdict.scope,
                    "reason": decisions[sequence_id].verdict.reason,
                }
                for sequence_id, world in self.worlds.items()
            ],
        }


def gate_corpus(
    corpus_dir: str | Path,
    *,
    agent: Agent | None = None,
    epsilon: float = EPSILON,
) -> CorpusGateReport:
    """Sweep the necessity gate over a frozen corpus and report it per sequence.

    Each sequence is gated at the scope its world gives it: the candidates are grouped
    by world dir, in corpus order, and ``context_for`` supplies a project-tier
    sequence's earlier siblings as the prefix its pilot replays.
    """
    candidates = load_corpus_sequences(corpus_dir)
    worlds = {c.sequence.sequence_id: c.world for c in candidates}
    if len(worlds) != len(candidates):
        raise ValueError(
            f"duplicate sequence_id across {corpus_dir}: "
            f"{len(candidates)} sequences, {len(worlds)} distinct ids"
        )
    by_world: dict[str, list[BenchmarkSequence]] = {}
    for candidate in candidates:
        by_world.setdefault(candidate.world, []).append(candidate.sequence)
    gate_candidates = [
        GateCandidate(sequence=seq, context=context_for(world_sequences, index))
        for world_sequences in by_world.values()
        for index, seq in enumerate(world_sequences)
    ]
    # Corpus order: dicts preserve insertion, and candidates arrive world dir by world
    # dir, so the grouped list is the same order load_corpus_sequences produced.
    sweep = sweep_necessity(gate_candidates, agent=agent, epsilon=epsilon)
    return CorpusGateReport(corpus_dir=Path(corpus_dir), sweep=sweep, worlds=worlds)


def write_necessity_artifact(report: CorpusGateReport, *, path: str | Path | None = None) -> Path:
    """Write the sweep artifact (default ``<corpus>/necessity.json``)."""
    out = Path(path) if path is not None else report.corpus_dir / NECESSITY_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return out


__all__ = [
    "GATE_VERSION",
    "NECESSITY_FILE",
    "SEQUENCES_FILE",
    "CorpusCandidate",
    "CorpusGateReport",
    "SweepResult",
    "context_for",
    "gate_corpus",
    "load_corpus_sequences",
    "sweep_necessity",
    "write_necessity_artifact",
]
