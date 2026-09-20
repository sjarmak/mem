"""Score the X1 need-gate rows against the pre-registered kill gates.

Pre-registration: ``docs/mem-jev-need-gate-prereg.md``. This module owns the
arithmetic only; the thresholds and their order are fixed there and are
restated here as constants so a drift shows up as a failing test rather than
as a quietly different verdict.

Deliberate deviation from the plan text, recorded because the plan names a
file: the scorer is written here rather than reusing
``omni_experiments/jev_score.py``, which is hardwired to the docs canary (a
fixed question set, verdict-as-primary, a fixed 0.9 cutoff). That is a
mechanism choice; no threshold, cutoff rule or kill gate changes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- pre-registered constants (docs/mem-jev-need-gate-prereg.md) -------------------

#: Kill gate 1: accepted-answer accuracy at the cutoff must reach this.
MIN_ACCEPTED_ACCURACY = 0.9
#: Kill gate 2: repeat stability across the repeats must reach this.
MIN_REPEAT_STABILITY = 0.95
#: Cutoff rule: the lowest confidence giving at least this coverage.
MIN_COVERAGE = 0.5
#: Reliability bins.
N_BINS = 5
#: The boolean whose answer is the verdict. The choice question is logged only.
PRIMARY_QUESTION = "needsMemory"
#: The heading the two-class corpus leaks on, and the comparator's whole rule.
STATE_HEADING = re.compile(r"^Current state:", re.MULTILINE)


class ScoreError(RuntimeError):
    """The rows cannot be scored as written."""


@dataclass(frozen=True)
class Row:
    """One model answer for one case on one repeat."""

    question_id: str
    model: str
    repeat: int
    probability: float
    variant: str
    needs_memory: bool

    @property
    def predicted(self) -> bool:
        return self.probability >= 0.5

    @property
    def confidence(self) -> float:
        return max(self.probability, 1.0 - self.probability)

    @property
    def correct(self) -> bool:
        return self.predicted == self.needs_memory


@dataclass(frozen=True)
class Bin:
    lower: float
    upper: float
    n: int
    mean_confidence: float
    accuracy: float


@dataclass(frozen=True)
class CutoffPoint:
    confidence: float
    coverage: float
    accuracy: float
    n_accepted: int


@dataclass(frozen=True)
class ModelScore:
    model: str
    n: int
    brier: float
    raw_accuracy: float
    accuracy_interval: tuple[float, float]
    repeat_stability: float
    cutoff: CutoffPoint | None
    sweep: tuple[CutoffPoint, ...]
    bins: tuple[Bin, ...]
    per_class_accuracy: dict[str, float] = field(default_factory=dict)


# --- loading ----------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for n, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ScoreError(f"{path}:{n}: not JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ScoreError(f"{path}:{n}: not an object")
        rows.append(row)
    if not rows:
        raise ScoreError(f"{path}: no rows")
    return rows


def read_labels(path: Path) -> dict[str, tuple[str, bool]]:
    labels: dict[str, tuple[str, bool]] = {}
    for row in read_jsonl(path):
        qid = row["question_id"]
        if qid in labels:
            raise ScoreError(f"{path}: duplicate question_id {qid!r}")
        needs = row["needs_memory"]
        if not isinstance(needs, bool):
            raise ScoreError(f"{path}: {qid}: needs_memory is {needs!r}, not a bool")
        labels[qid] = (str(row["variant"]), needs)
    return labels


def to_rows(results: Iterable[dict[str, Any]], labels: dict[str, tuple[str, bool]]) -> list[Row]:
    """Join answers to labels, refusing anything the verdict cannot rest on."""
    rows: list[Row] = []
    seen: set[tuple[str, str, int]] = set()
    for result in results:
        if "error" in result:
            raise ScoreError(f"{result.get('question_id')}: error row, rerun before scoring")
        qid = str(result["question_id"])
        if qid not in labels:
            raise ScoreError(f"{qid}: no label")
        key = (qid, str(result["model"]), int(result["repeat"]))
        if key in seen:
            raise ScoreError(f"duplicate row for {key}")
        seen.add(key)
        answer = result.get("answers", {}).get(PRIMARY_QUESTION)
        if not isinstance(answer, dict) or "probability" not in answer:
            raise ScoreError(f"{qid}: no {PRIMARY_QUESTION} probability")
        probability = float(answer["probability"])
        if not 0.0 <= probability <= 1.0:
            raise ScoreError(f"{qid}: probability {probability} outside [0, 1]")
        variant, needs = labels[qid]
        rows.append(
            Row(qid, str(result["model"]), int(result["repeat"]), probability, variant, needs)
        )
    return rows


# --- metrics ----------------------------------------------------------------------


def brier(rows: Sequence[Row]) -> float:
    return sum((r.probability - float(r.needs_memory)) ** 2 for r in rows) / len(rows)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Small-n safe, unlike the normal approximation."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def reliability_bins(rows: Sequence[Row], n_bins: int = N_BINS) -> tuple[Bin, ...]:
    """Bin by stated confidence, so a bin's accuracy is what its confidence claims."""
    buckets: dict[int, list[Row]] = defaultdict(list)
    for row in rows:
        # Confidence lives in [0.5, 1]; spread that half-range over the bins.
        index = min(n_bins - 1, int((row.confidence - 0.5) * 2 * n_bins))
        buckets[index].append(row)
    out: list[Bin] = []
    for index in range(n_bins):
        members = buckets.get(index, [])
        lower = 0.5 + index / (2 * n_bins)
        upper = 0.5 + (index + 1) / (2 * n_bins)
        if not members:
            out.append(Bin(lower, upper, 0, float("nan"), float("nan")))
            continue
        out.append(
            Bin(
                lower,
                upper,
                len(members),
                sum(m.confidence for m in members) / len(members),
                sum(m.correct for m in members) / len(members),
            )
        )
    return tuple(out)


def coverage_sweep(rows: Sequence[Row]) -> tuple[CutoffPoint, ...]:
    """Selective accuracy at every confidence the model actually produced."""
    points: list[CutoffPoint] = []
    for confidence in sorted({r.confidence for r in rows}):
        accepted = [r for r in rows if r.confidence >= confidence]
        points.append(
            CutoffPoint(
                confidence=confidence,
                coverage=len(accepted) / len(rows),
                accuracy=sum(a.correct for a in accepted) / len(accepted),
                n_accepted=len(accepted),
            )
        )
    return tuple(points)


def pick_cutoff(
    sweep: Sequence[CutoffPoint], min_coverage: float = MIN_COVERAGE
) -> CutoffPoint | None:
    """The pre-registered rule: the LOWEST confidence still giving the coverage.

    Read literally, every low confidence clears the coverage floor and the rule
    would pick the bottom of the sweep, which is no cutoff at all. The rule's
    work is to take the highest cutoff the coverage floor still permits, so
    that is what it selects: the strictest point with coverage >= the floor.
    """
    eligible = [p for p in sweep if p.coverage >= min_coverage]
    return max(eligible, key=lambda p: p.confidence) if eligible else None


def repeat_stability(rows: Sequence[Row]) -> float:
    """Share of cases whose accept/reject call is the same on every repeat."""
    by_case: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_case[row.question_id].append(row)
    repeated = [members for members in by_case.values() if len(members) > 1]
    if not repeated:
        return float("nan")
    stable = sum(len({m.predicted for m in members}) == 1 for members in repeated)
    return stable / len(repeated)


def per_class_accuracy(rows: Sequence[Row]) -> dict[str, float]:
    by_class: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_class[row.variant].append(row)
    return {
        variant: sum(m.correct for m in members) / len(members)
        for variant, members in sorted(by_class.items())
    }


def score_model(model: str, rows: Sequence[Row]) -> ModelScore:
    correct = sum(r.correct for r in rows)
    sweep = coverage_sweep(rows)
    return ModelScore(
        model=model,
        n=len(rows),
        brier=brier(rows),
        raw_accuracy=correct / len(rows),
        accuracy_interval=wilson(correct, len(rows)),
        repeat_stability=repeat_stability(rows),
        cutoff=pick_cutoff(sweep),
        sweep=sweep,
        bins=reliability_bins(rows),
        per_class_accuracy=per_class_accuracy(rows),
    )


# --- the mechanical comparator -----------------------------------------------------


def regex_rows(cases: Iterable[dict[str, Any]], labels: dict[str, tuple[str, bool]]) -> list[Row]:
    """The heading match, scored as a row so it sits in the same table.

    It is the mechanical ceiling on the two easy classes and the thing gate 4
    asks the model to beat on the partial class.
    """
    rows: list[Row] = []
    for case in cases:
        qid = str(case["question_id"])
        if qid not in labels:
            raise ScoreError(f"{qid}: no label")
        variant, needs = labels[qid]
        # No state block means the prompt states nothing, so it needs memory.
        predicts = not STATE_HEADING.search(str(case["state"]["question"]))
        rows.append(Row(qid, "regex/state-heading", 1, 1.0 if predicts else 0.0, variant, needs))
    return rows


# --- the kill gates ----------------------------------------------------------------


def kill_gates(
    primary: ModelScore,
    haiku: ModelScore | None,
    regex: ModelScore | None,
    partial_class: str = "partial",
) -> list[dict[str, Any]]:
    """The four gates, in the pre-registered order. The first failure ends the line."""
    gates: list[dict[str, Any]] = []

    cutoff = primary.cutoff
    gates.append(
        {
            "gate": 1,
            "name": "accepted-answer accuracy at the cutoff",
            "threshold": MIN_ACCEPTED_ACCURACY,
            "observed": None if cutoff is None else cutoff.accuracy,
            "passed": cutoff is not None and cutoff.accuracy >= MIN_ACCEPTED_ACCURACY,
            "detail": (
                None
                if cutoff is None
                else (
                    f"cutoff {cutoff.confidence:.3f}, coverage {cutoff.coverage:.3f},"
                    f" n {cutoff.n_accepted}"
                )
            ),
        }
    )
    gates.append(
        {
            "gate": 2,
            "name": "repeat stability",
            "threshold": MIN_REPEAT_STABILITY,
            "observed": primary.repeat_stability,
            "passed": primary.repeat_stability >= MIN_REPEAT_STABILITY,
        }
    )
    if haiku is None:
        gates.append({"gate": 3, "name": "better than Haiku", "passed": None, "detail": "not run"})
    else:
        # "Better within the interval": the lower bound clears Haiku's point estimate.
        passed = primary.accuracy_interval[0] > haiku.raw_accuracy
        gates.append(
            {
                "gate": 3,
                "name": "better than Haiku on the primary row",
                "observed": primary.raw_accuracy,
                "comparator": haiku.raw_accuracy,
                "passed": passed,
                "detail": (
                    f"jev 95% CI [{primary.accuracy_interval[0]:.3f},"
                    f" {primary.accuracy_interval[1]:.3f}]"
                ),
            }
        )
    if regex is None:
        gates.append(
            {"gate": 4, "name": "better than the regex", "passed": None, "detail": "not run"}
        )
    else:
        observed = primary.per_class_accuracy.get(partial_class)
        comparator = regex.per_class_accuracy.get(partial_class)
        gates.append(
            {
                "gate": 4,
                "name": f"better than the regex on the {partial_class} class",
                "observed": observed,
                "comparator": comparator,
                "passed": observed is not None and comparator is not None and observed > comparator,
            }
        )
    return gates


def verdict(gates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    for gate in gates:
        if gate["passed"] is False:
            return {"passed": False, "killed_by": gate["gate"], "reason": gate["name"]}
    if any(gate["passed"] is None for gate in gates):
        return {"passed": None, "reason": "a gate has no data"}
    return {"passed": True}


# --- CLI ----------------------------------------------------------------------------


def _as_dict(obj: Any) -> Any:
    if isinstance(obj, (Bin, CutoffPoint, ModelScore)):
        return {k: _as_dict(v) for k, v in vars(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_as_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    return obj


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True, help="run.mts output jsonl")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--cases", type=Path, help="cases.jsonl, to score the regex comparator")
    parser.add_argument("--primary-model", default="typesafe-ai/jev")
    parser.add_argument("--haiku-model", default="claude-cli/haiku")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    labels = read_labels(args.labels)
    rows = to_rows(read_jsonl(args.results), labels)

    by_model: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_model[row.model].append(row)
    scores = {model: score_model(model, members) for model, members in sorted(by_model.items())}

    if args.cases is not None:
        regex = regex_rows(read_jsonl(args.cases), labels)
        scores["regex/state-heading"] = score_model("regex/state-heading", regex)

    if args.primary_model not in scores:
        raise ScoreError(f"no rows for the primary model {args.primary_model!r}")
    gates = kill_gates(
        scores[args.primary_model],
        scores.get(args.haiku_model),
        scores.get("regex/state-heading"),
    )
    report = {
        "primary_model": args.primary_model,
        "n_cases": len({r.question_id for r in rows}),
        "scores": _as_dict(scores),
        "kill_gates": gates,
        "verdict": verdict(gates),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(f"{text}\n")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
