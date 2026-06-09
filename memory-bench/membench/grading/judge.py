"""OSS LLM-judge rubric scorer — the semantic half of the D17 per-rung reward.

`trace_score.py` (mem-apg.3a) answers the *deterministic* question: did the
held-out task's known failure class recur in the fresh run? This module answers
the *semantic* one (architect C2): was the work actually done? It produces the
`rubric_score` term in [0, 1] that `combined_reward` composes with the
deterministic term. The two are complementary by design: a run that avoided the
known failure by doing NOTHING passes the deterministic gate vacuously (its term
is N/A), so the judge's low completion score is what catches it.

Per spec 12.1: deterministic verifiers where possible, an LLM-as-judge ONLY for
the semantic dimension, WITH calibration and spot checks. Three pieces:

  - `Rubric` / `completion_rubric` — a structured rubric (named, weighted criteria)
    the model scores completion quality against. The judgment is the model's; the
    rubric just frames it. There is no keyword/threshold scoring of meaning in this
    module (that would be a ZFC violation — the model IS the delegated judgment).
  - `Judge` (Protocol) with `StubJudge` (deterministic, injectable — the whole
    pipeline and every test run on it, with no model and no network) and
    `OssLlmJudge` (a self-hosted / OSS, OpenAI-compatible LOCAL endpoint; D4/D16
    forbid a paid API, so it refuses a paid host). `score_completion` is the single
    point that validates the returned score into [0, 1] and fails loud otherwise.
  - `Calibration` — a mechanical store of (human label, judge score) pairs that
    reports agreement (mean absolute error + within-tolerance rate). It holds no
    semantic logic: it only stores numbers and aggregates them.

The judge's view is exactly `(task, run_output, rubric)` — there is structurally
no slot for the held-out resolution, so the answer cannot leak into what it scores.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

# Documented default OSS judge (ARCHITECTURE D4/D16): a self-hosted, OpenAI-
# compatible LOCAL endpoint. Default is a vLLM-served Nemotron-class small model on
# loopback; both are overridable via MEMBENCH_JUDGE_BASE_URL / MEMBENCH_JUDGE_MODEL.
DEFAULT_JUDGE_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_JUDGE_MODEL = "nvidia/nemotron-3-nano"
ENV_BASE_URL = "MEMBENCH_JUDGE_BASE_URL"
ENV_MODEL = "MEMBENCH_JUDGE_MODEL"

# Hosts that are paid managed APIs, not self-hosted — the no-paid-API fence (D4/D16).
_PAID_HOST_MARKERS = ("api.openai.com", "api.anthropic.com")


def _require_unit(name: str, value: float) -> None:
    """Fail loud unless `value` is a real number in [0, 1] (NaN included)."""
    if math.isnan(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value}")


@dataclass(frozen=True)
class RubricCriterion:
    """One named completion-quality dimension the model scores against, with a
    positive `weight` (its share of the rubric). `description` is what the model is
    told the criterion means."""

    name: str
    description: str
    weight: float

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError(f"criterion {self.name!r} weight must be > 0, got {self.weight}")


@dataclass(frozen=True)
class Rubric:
    """A structured semantic-completion rubric: the criteria the model scores the
    run output against. The rubric frames the judgment; it does not compute it."""

    criteria: tuple[RubricCriterion, ...]

    def __post_init__(self) -> None:
        if not self.criteria:
            raise ValueError("rubric needs at least one criterion")
        if sum(c.weight for c in self.criteria) <= 0:
            raise ValueError("rubric criterion weights must sum to > 0")

    def as_prompt_block(self) -> str:
        """The rubric rendered for the judge prompt: one line per criterion with its
        weight, so the model scores against the stated dimensions rather than vibes."""
        return "\n".join(
            f"- {c.name} (weight {c.weight:g}): {c.description}" for c in self.criteria
        )


def completion_rubric() -> Rubric:
    """The default semantic-completion-quality rubric (spec 12.1 `completion_quality`).

    Scores whether the run did the held-out task's work — not whether it merely
    avoided the known failure. The criteria are the dimensions that distinguish a
    real solve from a no-op or a partial/sloppy one."""
    return Rubric(
        criteria=(
            RubricCriterion(
                name="goal_addressed",
                description=(
                    "The run output actually attempts the stated task, not an "
                    "unrelated or empty action."
                ),
                weight=0.4,
            ),
            RubricCriterion(
                name="work_completeness",
                description=(
                    "The task is carried through to a finished state rather than "
                    "left partial or stubbed."
                ),
                weight=0.4,
            ),
            RubricCriterion(
                name="solution_soundness",
                description=(
                    "The approach taken is coherent and plausibly correct for the "
                    "stated goal, not a superficial or evasive change."
                ),
                weight=0.2,
            ),
        )
    )


class Judge(Protocol):
    """Scores semantic completion quality of a run against a rubric, in [0, 1].

    The view is exactly `(task, run_output, rubric)`: the task instruction, the
    agent's final answer / transcript summary, and the rubric. No held-out
    resolution is passed, so the judge cannot be told the answer."""

    def score(self, task: str, run_output: str, rubric: Rubric) -> float: ...


def score_completion(judge: Judge, task: str, run_output: str, rubric: Rubric) -> float:
    """Score `run_output` and validate the result into [0, 1], failing loud otherwise.

    The single validation point so each `Judge` impl does not re-check the range; a
    judge that returns out-of-range or NaN is a bug surfaced here, not silently
    clamped."""
    raw = judge.score(task, run_output, rubric)
    _require_unit("rubric_score", raw)
    return raw


@dataclass(frozen=True)
class StubJudge:
    """A deterministic, injectable judge: NO model, NO network. The whole pipeline
    and every test run on this. Supply exactly one of `fixed` (a constant score) or
    `fn` (a pure scoring function over the same view a real judge sees)."""

    fixed: float | None = None
    fn: Callable[[str, str, Rubric], float] | None = None

    def __post_init__(self) -> None:
        if (self.fixed is None) == (self.fn is None):
            raise ValueError("StubJudge needs exactly one of fixed or fn")

    def score(self, task: str, run_output: str, rubric: Rubric) -> float:
        if self.fn is not None:
            return self.fn(task, run_output, rubric)
        assert self.fixed is not None  # narrowed by __post_init__
        return self.fixed


_SCORE_RE = re.compile(r'"score"\s*:\s*(-?\d+(?:\.\d+)?)')

_PROMPT_TEMPLATE = """\
You are grading how completely an agent did a task. Score ONLY semantic completion
quality against the rubric below. Output strict JSON: {{"score": <float 0..1>}}.

TASK:
{task}

AGENT RUN OUTPUT:
{run_output}

RUBRIC (score the run output against these criteria):
{rubric_block}

Return only the JSON object."""


@dataclass
class OssLlmJudge:
    """A judge backed by a self-hosted / OSS, OpenAI-compatible LOCAL endpoint.

    Defaults to a loopback vLLM endpoint and a documented OSS model, both overridable
    via `MEMBENCH_JUDGE_BASE_URL` / `MEMBENCH_JUDGE_MODEL`. It refuses a paid managed
    host (D4/D16) at construction. Prompt assembly and score parsing are pure and
    unit-tested; `score` makes the actual HTTP call and is never exercised in tests."""

    base_url: str = ""
    model: str = ""
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        self.base_url = self.base_url or os.environ.get(ENV_BASE_URL, DEFAULT_JUDGE_BASE_URL)
        self.model = self.model or os.environ.get(ENV_MODEL, DEFAULT_JUDGE_MODEL)
        lowered = self.base_url.lower()
        if any(marker in lowered for marker in _PAID_HOST_MARKERS):
            raise ValueError(
                f"judge base_url {self.base_url!r} is a paid managed host; the judge "
                "must be OSS/self-hosted (D4/D16) — point it at a local endpoint"
            )

    def build_prompt(self, task: str, run_output: str, rubric: Rubric) -> str:
        """The rubric-grounded judge prompt. Pure — assembles the model input from
        the task, the run output, and the rubric criteria."""
        return _PROMPT_TEMPLATE.format(
            task=task, run_output=run_output, rubric_block=rubric.as_prompt_block()
        )

    def parse_score(self, content: str) -> float:
        """Extract the `score` field from a model reply and validate it into [0, 1].

        Tolerant of surrounding prose (the model may wrap the JSON in text), but a
        missing or out-of-range score fails loud — a malformed judge reply is a real
        error, not a 0.0 to be swallowed."""
        match = _SCORE_RE.search(content)
        if match is None:
            raise ValueError(f"judge reply has no 'score' field: {content!r}")
        value = float(match.group(1))
        _require_unit("rubric_score", value)
        return value

    def score(self, task: str, run_output: str, rubric: Rubric) -> float:
        """Call the local endpoint's chat-completions API and parse the score.

        Validation lives in `score_completion`; this returns the model's raw score
        (already range-checked by `parse_score`)."""
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "user", "content": self.build_prompt(task, run_output, rubric)}
                ],
                "temperature": 0.0,
            }
        ).encode()
        # base_url is fenced to a self-hosted host in __post_init__.
        request = Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode())
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"judge endpoint {self.base_url!r} call failed: {exc}") from exc
        content = body["choices"][0]["message"]["content"]
        return self.parse_score(content)


@dataclass(frozen=True)
class CalibrationReport:
    """The agreement summary over a labeled calibration set: how close the judge's
    scores sit to the human labels."""

    n: int
    mean_abs_error: float
    within_tolerance_rate: float


@dataclass
class Calibration:
    """A mechanical spot-check store: record (human label, judge score) pairs, then
    report agreement. No semantic logic — it only validates the inputs into [0, 1],
    stores them, and aggregates. `tolerance` is the per-sample allowed gap counted as
    agreement."""

    tolerance: float
    _pairs: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 <= self.tolerance <= 1.0:
            raise ValueError(f"tolerance must be in [0, 1], got {self.tolerance}")

    def record(self, *, label: float, judge_score: float) -> None:
        """Store one labeled spot-check observation; both values must be in [0, 1]."""
        _require_unit("label", label)
        _require_unit("judge_score", judge_score)
        self._pairs.append((label, judge_score))

    def report(self) -> CalibrationReport:
        """Mean absolute error and the fraction of samples within `tolerance`."""
        if not self._pairs:
            raise ValueError("calibration set is empty — record at least one sample first")
        errors = [abs(label - score) for label, score in self._pairs]
        within = sum(1 for e in errors if e <= self.tolerance)
        n = len(errors)
        return CalibrationReport(
            n=n,
            mean_abs_error=sum(errors) / n,
            within_tolerance_rate=within / n,
        )
