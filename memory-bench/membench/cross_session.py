"""Cross-session iteration metrics over the session<->bead join (mem-75t.9 PHASE 3).

Given a bead's sessions ordered by time, this module computes the metrics the
multi-session findings need:

- iterations: session count per bead;
- per-bead summed cost: `grading.probe_direct.extract_efficiency` per transcript
  (turns / tool calls / token sums), summed across sessions;
- redundant-read overlap: files read in session N+1 that session N already read
  (`harbor_exec.project_claude_stream` read-harvest — the thing memory should
  eliminate);
- within-task failure recurrence: a relaxed failure signature
  (`grading.trace_score.relaxed_signature`, the calibrated line-invariant key)
  extracted from session N's tool outputs reappearing in session N+1. The error
  rows come from the canonical `mem extract-errors` extractor, injected as a
  callable so unit tests run with a stub (same seam as
  `harbor.base_rate_spike.make_cli_extractor`).

Everything here is deterministic arithmetic over already-extracted structure
(ZFC: mechanism only); the one judgment-bearing piece — the relaxed signature —
is reused from trace_score, not re-derived.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean
from typing import Any

from membench.grading import TraceErrorRef, relaxed_signature
from membench.grading.probe_direct import extract_efficiency
from membench.harbor.harbor_exec import project_claude_stream

# Same seam as the base-rate spike: stdout/observation text in, canonical
# trace_error rows out. Production wires `make_cli_extractor(mem_bin)`.
ErrorExtractor = Callable[[str], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class SessionView:
    """One session's projection onto the cross-session axes: cost, reads, and
    failure signatures. Token fields are None when the transcript carried no
    usage records (typed absence, never a fake 0)."""

    session_id: str
    transcript_path: str
    start: str | None
    end: str | None
    turns: int
    tool_calls: int
    input_tokens: int | None
    output_tokens: int | None
    files_read: frozenset[str]
    relaxed_signatures: frozenset[str]
    exact_signatures: frozenset[str]


def build_session_view(
    *,
    session_id: str,
    transcript_path: str,
    stream_text: str,
    extractor: ErrorExtractor,
    start: str | None = None,
    end: str | None = None,
) -> SessionView:
    """Project one transcript onto a `SessionView`.

    Reuses the existing projections verbatim: `extract_efficiency` for cost,
    `project_claude_stream` for the read-harvest + tool-observation output, and
    the injected canonical extractor for error rows. The extractor is skipped
    when the session produced no tool output (nothing to parse)."""
    efficiency = extract_efficiency(stream_text)
    projected = project_claude_stream(stream_text)
    output = projected["output"]
    rows = extractor(output) if output.strip() else ()
    refs = [TraceErrorRef.from_mapping(row) for row in rows]
    return SessionView(
        session_id=session_id,
        transcript_path=transcript_path,
        start=start,
        end=end,
        turns=efficiency.turns,
        tool_calls=efficiency.tool_calls,
        input_tokens=efficiency.input_tokens,
        output_tokens=efficiency.output_tokens,
        files_read=frozenset(projected["files_read"]),
        relaxed_signatures=frozenset(relaxed_signature(r) for r in refs),
        exact_signatures=frozenset(r.signature for r in refs),
    )


@dataclass(frozen=True)
class PairMetrics:
    """Consecutive-session (N -> N+1) readout.

    `redundant_read_fraction` is None when session N+1 read nothing (the axis
    does not apply). `recurrence` is None when session N surfaced no failure
    signatures (nothing that COULD recur) — a tri-state, mirroring the
    deterministic term's typed absence in trace_score."""

    prev_session_id: str
    next_session_id: str
    redundant_reads: int
    next_reads: int
    redundant_read_fraction: float | None
    recurrence: bool | None
    recurred_signatures: tuple[str, ...]


def pair_metrics(prev: SessionView, nxt: SessionView) -> PairMetrics:
    """The redundant-read and recurrence readout for one consecutive pair."""
    redundant = prev.files_read & nxt.files_read
    recurred = prev.relaxed_signatures & nxt.relaxed_signatures
    return PairMetrics(
        prev_session_id=prev.session_id,
        next_session_id=nxt.session_id,
        redundant_reads=len(redundant),
        next_reads=len(nxt.files_read),
        redundant_read_fraction=(len(redundant) / len(nxt.files_read) if nxt.files_read else None),
        recurrence=bool(recurred) if prev.relaxed_signatures else None,
        recurred_signatures=tuple(sorted(recurred)),
    )


def _sum_optional(values: Iterable[int | None]) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


@dataclass(frozen=True)
class BeadCrossSession:
    """One bead's full cross-session readout: time-ordered sessions, consecutive
    pair metrics, and summed cost."""

    work_id: str
    sessions: tuple[SessionView, ...]
    pairs: tuple[PairMetrics, ...]
    total_turns: int
    total_tool_calls: int
    total_input_tokens: int | None
    total_output_tokens: int | None

    @property
    def iterations(self) -> int:
        return len(self.sessions)


def bead_cross_session(work_id: str, views: Iterable[SessionView]) -> BeadCrossSession:
    """Order a bead's sessions by start time (unknown starts last, then by id
    for determinism) and compute pair + cost metrics."""
    ordered = tuple(sorted(views, key=lambda v: (v.start is None, v.start or "", v.session_id)))
    pairs = tuple(pair_metrics(a, b) for a, b in pairwise(ordered))
    return BeadCrossSession(
        work_id=work_id,
        sessions=ordered,
        pairs=pairs,
        total_turns=sum(v.turns for v in ordered),
        total_tool_calls=sum(v.tool_calls for v in ordered),
        total_input_tokens=_sum_optional(v.input_tokens for v in ordered),
        total_output_tokens=_sum_optional(v.output_tokens for v in ordered),
    )


def aggregate_metrics(beads: Sequence[BeadCrossSession]) -> dict[str, Any]:
    """The population summary the findings doc reports.

    Recurrence is computed over ELIGIBLE pairs only (session N surfaced at least
    one failure signature); rates are None when the denominator is empty rather
    than a fake 0.0."""
    all_pairs = [pair for bead in beads for pair in bead.pairs]
    fractions = [
        p.redundant_read_fraction for p in all_pairs if p.redundant_read_fraction is not None
    ]
    eligible = [p for p in all_pairs if p.recurrence is not None]
    recurrent = [p for p in eligible if p.recurrence]

    beads_with_eligible = [b for b in beads if any(p.recurrence is not None for p in b.pairs)]
    beads_with_recurrence = [b for b in beads if any(p.recurrence for p in b.pairs)]

    histogram: dict[int, int] = {}
    for bead in beads:
        histogram[bead.iterations] = histogram.get(bead.iterations, 0) + 1

    return {
        "n_beads": len(beads),
        "iterations_histogram": dict(sorted(histogram.items())),
        "n_pairs": len(all_pairs),
        "pairs_with_next_reads": len(fractions),
        "mean_redundant_read_fraction": fmean(fractions) if fractions else None,
        "pairs_with_any_redundant_read": sum(1 for p in all_pairs if p.redundant_reads > 0),
        "recurrence_eligible_pairs": len(eligible),
        "recurrent_pairs": len(recurrent),
        "pair_recurrence_rate": len(recurrent) / len(eligible) if eligible else None,
        "beads_with_eligible_pair": len(beads_with_eligible),
        "beads_with_recurrence": len(beads_with_recurrence),
        "bead_recurrence_rate": (
            len(beads_with_recurrence) / len(beads_with_eligible) if beads_with_eligible else None
        ),
        "total_turns": sum(b.total_turns for b in beads),
        "total_tool_calls": sum(b.total_tool_calls for b in beads),
        "total_input_tokens": _sum_optional(b.total_input_tokens for b in beads),
        "total_output_tokens": _sum_optional(b.total_output_tokens for b in beads),
    }
