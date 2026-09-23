"""The six-arm public-baseline results table (mem-r6yzk.5).

The report the public baseline driver (`scripts/run_public_baseline.py`) hands a
reader. Three properties are load-bearing and each is enforced here rather than
left to the caller:

- **Paired per-task deltas, never a pooled mean.** Every arm replays the SAME
  released records, so an arm's effect is the median of its per-record deltas
  against the `none` arm, with the percentile-bootstrap CI from
  `grading.paired_ci.paired_delta_ci`. A pooled mean (mean over the arm's cells
  minus mean over the baseline's) is a different estimand the moment the record
  set is unbalanced or the per-record spread is skewed, and it is the number an
  arm that helps on one easy record and hurts on five can hide behind. The local
  `paired_deltas` in `harbor/probe_gate.py` is a NAME COLLISION with a different
  contract (it pairs probe outcomes, not task rewards) and is deliberately not
  imported.
- **Injected-context volume is a mandatory column, in BOTH its units.** It is NOT
  a field of `schemas.metrics.EfficiencyMetrics` — that group carries tokens,
  latency, tool calls and cost only. Characters live on the replay result as
  `replay.ArmReplayResult.injected_context_chars` (the sum of the lengths of the
  payloads an arm returned) and are surfaced by `report.arm_vector.ArmAxisVector`
  as `token_budget_chars`; the driver carries the same quantity per cell. Without
  it, an arm that wins by injecting more context is unfalsifiable: its reward
  delta and its cost are both consistent with "retrieved better" and with
  "retrieved everything". ITEMS (`BaselineCell.injected_items`) are the second
  unit and carry what characters cannot: the item count is the width the design
  pins, so two arms at equal items differ only in WHICH items, while two arms at
  equal characters may still have differed in width. `equal_width_pairs` names the
  pairs whose delta is therefore a selection difference, and `to_markdown` prints
  that sentence ABOVE the table — a reader mistakes a confounded delta for a
  quality result by default, not after being warned.
- **A missing repeat is an error.** An arm whose record was measured twice in a
  3-repeat design is not an arm with a slightly noisier mean, it is a different
  design; averaging what arrived would change n per cell silently. Every arm must
  cover every record with exactly `repeats` measured cells or the report raises.

The LLM judge stays OUT of the pass/fail loop: `judge_score` rides on the cell and
is reported in its own column, clearly labeled report-only. Nothing in this module
reads it to decide a pass, a delta, or a gate.

ZFC: pure mechanism — grouping, arithmetic, seeded resampling, formatting. No
semantic judgment, no thresholds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Literal

from membench.grading.paired_ci import (
    POPULATION_PRIMARY,
    PairedDeltaCI,
    Population,
    Statistic,
    paired_delta_ci,
)
from membench.schemas.metrics import EfficiencyMetrics

# The arm every other arm is paired against. Named once: a report built against a
# different baseline is a different claim, and it rides on the artifact below.
ARM_BASELINE = "none"

CellStatus = Literal["ok", "timeout", "error"]

# The cost columns read off EfficiencyMetrics. Listed here (rather than reflected off
# the model) so adding a field to the metrics group cannot silently widen a published
# table, and so a reader sees exactly which six numbers the table reports.
COST_FIELDS: tuple[str, ...] = (
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "wall_clock_latency_ms",
    "tool_calls_total",
    "cost_usd",
)


class MissingRepeatError(ValueError):
    """An arm did not measure every released record exactly `repeats` times.

    Raised rather than reported as a smaller n: the design is fully paired and a
    silently shorter arm publishes a different experiment under this one's name."""


class DuplicateCellError(ValueError):
    """Two cells carry the same (arm, record_id, repeat) key."""


class UnknownArmError(ValueError):
    """The report was asked for an arm the cells do not carry."""


@dataclass(frozen=True)
class BaselineCell:
    """One (arm, record, repeat) observation of the public baseline.

    `reward` is the mechanical value-set grade of the agent's answer (the fraction
    of the record's expected values stated, zeroed when a forbidden value is
    stated); `passed` is the all-expected-none-forbidden flag. `status` other than
    "ok" means the cell measured NOTHING — it is not a scored zero and never enters
    a rate."""

    arm: str
    record_id: str
    repeat: int
    status: CellStatus
    paid: bool
    reward: float
    passed: bool
    # The Decision-10 precision guard, per cell, in the two units volume has.
    # Mandatory — see the module docstring. `injected_items` is the count of payloads
    # the arm returned, which is the unit the DESIGN controls (every ranking arm is
    # pinned to one width); `injected_context_chars` is their summed length, which is
    # a property of the texts the arm chose. Neither substitutes for the other: an arm
    # returning three long items and one returning six short ones are the same number
    # of characters, and only the item count says the widths differed.
    injected_items: int
    injected_context_chars: int
    # How many of the record's superseded entries this arm put in front of the agent.
    # Mandatory for the same reason: a failed record tells you the answer was wrong,
    # and only this column tells you whether the arm handed the agent the stale value
    # it then stated. Without it an arm that retrieves nothing and an arm that
    # retrieves the trap are one number.
    stale_injected: int
    efficiency: EfficiencyMetrics
    # Report-only. Never read by any function in this module that produces a pass,
    # a delta, or a gate.
    judge_score: float | None = None
    detail: str = ""

    def to_row(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "record_id": self.record_id,
            "repeat": self.repeat,
            "status": self.status,
            "paid": self.paid,
            "reward": self.reward,
            "passed": self.passed,
            "injected_items": self.injected_items,
            "injected_context_chars": self.injected_context_chars,
            "stale_injected": self.stale_injected,
            "efficiency": self.efficiency.model_dump(),
            "judge_score": self.judge_score,
            "detail": self.detail,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> BaselineCell:
        """Rebuild a cell from its artifact row. Every field is required except the
        two that are genuinely optional on the wire (`judge_score`, `detail`); a row
        missing anything else is a malformed artifact and raises KeyError rather than
        being defaulted into a plausible-looking measurement."""
        status = row["status"]
        if status not in ("ok", "timeout", "error"):
            raise ValueError(f"cell carries unknown status {status!r}")
        return cls(
            arm=str(row["arm"]),
            record_id=str(row["record_id"]),
            repeat=int(row["repeat"]),
            status=status,
            paid=bool(row["paid"]),
            reward=float(row["reward"]),
            passed=bool(row["passed"]),
            injected_items=int(row["injected_items"]),
            injected_context_chars=int(row["injected_context_chars"]),
            stale_injected=int(row["stale_injected"]),
            efficiency=EfficiencyMetrics.model_validate(row["efficiency"]),
            judge_score=(None if row.get("judge_score") is None else float(row["judge_score"])),
            detail=str(row.get("detail", "")),
        )


def cell_key(cell: BaselineCell) -> tuple[str, str, int]:
    return (cell.arm, cell.record_id, cell.repeat)


def arms_of(cells: Iterable[BaselineCell]) -> list[str]:
    """Every arm the cells carry, in sorted order with the baseline arm first."""
    names = sorted({cell.arm for cell in cells})
    if ARM_BASELINE in names:
        names.remove(ARM_BASELINE)
        names.insert(0, ARM_BASELINE)
    return names


def records_of(cells: Iterable[BaselineCell]) -> list[str]:
    return sorted({cell.record_id for cell in cells})


def _check_no_duplicates(cells: Sequence[BaselineCell]) -> None:
    seen: set[tuple[str, str, int]] = set()
    for cell in cells:
        key = cell_key(cell)
        if key in seen:
            raise DuplicateCellError(
                f"two cells carry {key}; neither can be shown to be the one this report keeps"
            )
        seen.add(key)


def rewards_by_record(
    cells: Sequence[BaselineCell],
    arm: str,
    *,
    repeats: int,
    records: Sequence[str] | None = None,
) -> dict[str, float]:
    """This arm's per-record reward — the mean over its repeats — for every record.

    The per-task value that enters the paired delta. Raises `MissingRepeatError`
    when any record is not measured exactly `repeats` times by this arm: an
    unmeasured or absent repeat is a hole in the design, not a smaller sample."""
    if repeats < 1:
        raise ValueError(f"repeats must be >= 1, got {repeats}")
    _check_no_duplicates(cells)
    wanted = list(records) if records is not None else records_of(cells)
    if arm not in {cell.arm for cell in cells}:
        raise UnknownArmError(f"no cells for arm {arm!r}; carried arms: {arms_of(cells)}")
    by_record: dict[str, list[BaselineCell]] = {record: [] for record in wanted}
    for cell in cells:
        if cell.arm != arm or cell.status != "ok":
            continue
        if cell.record_id not in by_record:
            raise MissingRepeatError(
                f"{arm}: cell for record {cell.record_id!r} is outside the reported record set "
                f"{wanted}; it cannot be pooled into this table"
            )
        by_record[cell.record_id].append(cell)
    short = {record: len(rows) for record, rows in by_record.items() if len(rows) != repeats}
    if short:
        detail = "; ".join(f"{record}: {got} of {repeats}" for record, got in sorted(short.items()))
        raise MissingRepeatError(
            f"{arm} did not measure every record {repeats} time(s) ({detail}). A missing repeat "
            "is an error, not a smaller n: the remaining cells would be published as the "
            "designed sample."
        )
    return {record: fmean(cell.reward for cell in rows) for record, rows in by_record.items()}


@dataclass(frozen=True)
class ArmCost:
    """The cost side of one arm: the EfficiencyMetrics columns plus the mandatory
    injected-context volume, which EfficiencyMetrics does not carry, and the mandatory
    stale-injection count, which says what KIND of context the volume was."""

    arm: str
    n_cells: int
    totals: dict[str, float]
    means: dict[str, float]
    injected_items_total: int
    injected_items_mean: float
    injected_context_chars_total: int
    injected_context_chars_mean: float
    stale_injected_total: int
    stale_injected_mean: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "n_cells": self.n_cells,
            "totals": dict(self.totals),
            "means": dict(self.means),
            "injected_items_total": self.injected_items_total,
            "injected_items_mean": self.injected_items_mean,
            "injected_context_chars_total": self.injected_context_chars_total,
            "injected_context_chars_mean": self.injected_context_chars_mean,
            "stale_injected_total": self.stale_injected_total,
            "stale_injected_mean": self.stale_injected_mean,
        }


def arm_costs(cells: Sequence[BaselineCell], arm: str) -> ArmCost:
    """Cost totals and per-cell means over this arm's MEASURED cells.

    Unmeasured cells are excluded from both: a timed-out cell has no honest token
    count, and counting it as zero would make a broken rig look cheap."""
    rows = [cell for cell in cells if cell.arm == arm and cell.status == "ok"]
    if not rows:
        raise UnknownArmError(f"arm {arm!r} has no measured cells to cost")
    totals = {
        field: float(sum(getattr(cell.efficiency, field) for cell in rows)) for field in COST_FIELDS
    }
    means = {field: totals[field] / len(rows) for field in COST_FIELDS}
    items = sum(cell.injected_items for cell in rows)
    volume = sum(cell.injected_context_chars for cell in rows)
    stale = sum(cell.stale_injected for cell in rows)
    return ArmCost(
        arm=arm,
        n_cells=len(rows),
        totals=totals,
        means=means,
        injected_items_total=items,
        injected_items_mean=items / len(rows),
        injected_context_chars_total=volume,
        injected_context_chars_mean=volume / len(rows),
        stale_injected_total=stale,
        stale_injected_mean=stale / len(rows),
    )


def equal_width_pairs(costs: Sequence[ArmCost]) -> list[tuple[str, str]]:
    """The arm pairs whose reward difference is a SELECTION difference: those that
    injected the same mean number of items.

    Two arms at the same item width handed the agent the same amount of retrieval, so
    a delta between them is about WHICH items each chose. Two arms at different widths
    differ in how much they injected as well, and no arithmetic in this module can
    separate the two contributions — the table names the comparable pairs instead of
    letting a reader assume every pair is one.

    Equality is exact and integral: `total_a * n_b == total_b * n_a`, the
    cross-multiplied means, so no float tolerance (which would be a tuned threshold)
    enters. Characters are deliberately NOT part of the test: at equal item width the
    residual character difference IS the selection, and folding it in would make an
    arm that picked shorter texts look incomparable to the one it is being compared
    against. Pairs come back sorted, each arm pair named once."""
    pairs: list[tuple[str, str]] = []
    ordered = sorted(costs, key=lambda cost: cost.arm)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            same = left.injected_items_total * right.n_cells == (
                right.injected_items_total * left.n_cells
            )
            if same:
                pairs.append((left.arm, right.arm))
    return pairs


def judge_mean(cells: Sequence[BaselineCell], arm: str) -> float | None:
    """The mean judge score over this arm's measured cells, or None when no cell
    carries one. Report-only: nothing downstream reads it."""
    scores = [
        cell.judge_score
        for cell in cells
        if cell.arm == arm and cell.status == "ok" and cell.judge_score is not None
    ]
    return fmean(scores) if scores else None


@dataclass(frozen=True)
class ArmRow:
    """One arm's line of the table."""

    arm: str
    n_records: int
    reward_mean: float
    reward_by_record: dict[str, float]
    # None for the baseline arm itself, which is not paired against itself.
    delta: PairedDeltaCI | None
    cost: ArmCost
    judge_score_mean: float | None

    def to_dict(self) -> dict[str, Any]:
        delta = (
            None
            if self.delta is None
            else {
                "delta": self.delta.delta,
                "ci_low": self.delta.ci_low,
                "ci_high": self.delta.ci_high,
                "n_pairs": self.delta.n_pairs,
                "n_imputed_zero": self.delta.n_imputed_zero,
                "population": self.delta.population,
                "statistic": self.delta.statistic,
            }
        )
        return {
            "arm": self.arm,
            "n_records": self.n_records,
            "reward_mean": self.reward_mean,
            "reward_by_record": dict(self.reward_by_record),
            "paired_delta_vs_baseline": delta,
            "cost": self.cost.to_dict(),
            "judge_score_mean": self.judge_score_mean,
        }


@dataclass(frozen=True)
class BaselineReport:
    baseline_arm: str
    repeats: int
    population: Population
    statistic: Statistic
    records: list[str]
    rows: list[ArmRow]

    @property
    def equal_width_pairs(self) -> list[tuple[str, str]]:
        """Which arm pairs this table compares at equal injected width."""
        return equal_width_pairs([row.cost for row in self.rows])

    def _comparability_line(self) -> str:
        """The one sentence that says which deltas are selection differences.

        Printed above the table, not below it: the confounded reading of a
        grouped-vs-lexical delta is the DEFAULT one, and a caveat under the numbers is
        read after the mistake has been made."""
        pairs = self.equal_width_pairs
        if not pairs:
            return (
                "**Comparable at equal volume: no pair.** Every arm injected a different "
                "mean number of items, so every delta in this table mixes selection with "
                "volume."
            )
        named = ", ".join(f"`{left}`↔`{right}`" for left, right in pairs)
        return (
            f"**Comparable at equal volume:** {named}. Those pairs injected the same mean "
            "number of items, so a delta between them is a difference in WHICH items were "
            "retrieved. Every other pair differs in volume as well as in selection."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": "public-baseline.v1",
            "baseline_arm": self.baseline_arm,
            "repeats": self.repeats,
            "population": self.population,
            "statistic": self.statistic,
            "n_records": len(self.records),
            "records": list(self.records),
            "judge": "report-only — never a pass/fail gate",
            "equal_width_pairs": [list(pair) for pair in self.equal_width_pairs],
            "arms": [row.to_dict() for row in self.rows],
        }

    def to_markdown(self) -> str:
        head = (
            f"# Public baseline — six arms vs `{self.baseline_arm}`",
            "",
            (
                f"_{len(self.records)} released record(s), {self.repeats} repeat(s) per cell. "
                f"Deltas are PAIRED per record ({self.statistic} of the per-record differences, "
                f"95% percentile bootstrap, population `{self.population}`) — never a pooled "
                "mean. `injected items` and `injected chars` are the Decision-10 volume guard: "
                "an arm that wins by injecting more must show it here. Only arms at the same "
                "injected-item width are comparable on selection quality; the rest differ in "
                "volume too, and this table cannot attribute which moved the number. The judge "
                "column is REPORT-ONLY and gates nothing._"
            ),
            "",
            self._comparability_line(),
            "",
            (
                "| arm | records | reward (mean) | paired Δ vs baseline [95% CI] | injected items"
                " (mean) | injected chars (mean) | total tok | in tok | out tok | wall ms |"
                " tool calls | cost usd | judge (report-only) |"
            ),
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        )
        lines = list(head)
        for row in self.rows:
            delta = (
                "baseline"
                if row.delta is None
                else (
                    f"{row.delta.delta:+.3f} [{row.delta.ci_low:+.3f}, {row.delta.ci_high:+.3f}]"
                    f" (n={row.delta.n_pairs})"
                )
            )
            judge = "—" if row.judge_score_mean is None else f"{row.judge_score_mean:.3f}"
            means = row.cost.means
            lines.append(
                f"| {row.arm} | {row.n_records} | {row.reward_mean:.3f} | {delta} | "
                f"{row.cost.injected_items_mean:.3f} | "
                f"{row.cost.injected_context_chars_mean:.1f} | "
                f"{means['total_tokens']:.1f} | {means['input_tokens']:.1f} | "
                f"{means['output_tokens']:.1f} | {means['wall_clock_latency_ms']:.1f} | "
                f"{means['tool_calls_total']:.2f} | {means['cost_usd']:.4f} | {judge} |"
            )
        return "\n".join(lines)


def build_report(
    cells: Sequence[BaselineCell],
    *,
    repeats: int,
    baseline: str = ARM_BASELINE,
    population: Population = POPULATION_PRIMARY,
    statistic: Statistic = "median",
    n_resamples: int = 5000,
    seed: int = 0,
) -> BaselineReport:
    """The results table over a completed run's cells.

    Every arm must cover the same record set with `repeats` measured cells each —
    the design is fully paired, so anything else is a hole, not a sample (see
    `rewards_by_record`). Deltas are formed per record against `baseline`; the
    baseline arm's own row carries no delta."""
    if not cells:
        raise ValueError("build_report: no cells — there is nothing to report")
    _check_no_duplicates(cells)
    arms = arms_of(cells)
    if baseline not in arms:
        raise UnknownArmError(
            f"baseline arm {baseline!r} has no cells; carried arms: {arms}. A table with no "
            "baseline can only report pooled levels, which is the comparison this report refuses."
        )
    records = records_of(cells)
    rewards = {arm: rewards_by_record(cells, arm, repeats=repeats, records=records) for arm in arms}

    rows: list[ArmRow] = []
    for arm in arms:
        per_record = rewards[arm]
        delta = (
            None
            if arm == baseline
            else paired_delta_ci(
                rewards[baseline],
                per_record,
                population=population,
                statistic=statistic,
                n_resamples=n_resamples,
                seed=seed,
            )
        )
        rows.append(
            ArmRow(
                arm=arm,
                n_records=len(per_record),
                reward_mean=fmean(per_record.values()),
                reward_by_record=dict(per_record),
                delta=delta,
                cost=arm_costs(cells, arm),
                judge_score_mean=judge_mean(cells, arm),
            )
        )
    return BaselineReport(
        baseline_arm=baseline,
        repeats=repeats,
        population=population,
        statistic=statistic,
        records=records,
        rows=rows,
    )


__all__ = [
    "ARM_BASELINE",
    "COST_FIELDS",
    "ArmCost",
    "ArmRow",
    "BaselineCell",
    "BaselineReport",
    "DuplicateCellError",
    "MissingRepeatError",
    "UnknownArmError",
    "arm_costs",
    "arms_of",
    "build_report",
    "cell_key",
    "equal_width_pairs",
    "judge_mean",
    "records_of",
    "rewards_by_record",
]
