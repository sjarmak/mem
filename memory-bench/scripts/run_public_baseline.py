#!/usr/bin/env python3
"""The six-arm public baseline driver (mem-r6yzk.5): none / lexical / nemo-embed /
ours / oracle / grouped over the RELEASED record set, three repeats each.

Two entries, and pricing is never the same keystroke as spending:

    --preflight  (DEFAULT) load the released set, price the grid, probe every arm's
                 infrastructure for real, and print the plan. Spends nothing, spawns
                 no agent, needs no token.
    --fire       PAID: buy the grid. Refuses to start unless a model is named
                 explicitly, refuses while ``ANTHROPIC_API_KEY`` is set, refuses
                 without ``CLAUDE_CODE_OAUTH_TOKEN``, and refuses without ``--out``
                 (the resume artifact, the lock, and the parent of the per-cell
                 evidence). The refusal ladder is ``e1_grid._refusal`` — the one
                 place those rules live in this package, deferred to rather than
                 re-derived here.

Run it from ``memory-bench/`` (the package is not pip-installed):

    PYTHONPATH=. python3 scripts/run_public_baseline.py --preflight
    PYTHONPATH=. python3 scripts/run_public_baseline.py --fire --model <id> \
        --out runs/public-baseline.json --store <mem store> --mem-bin <path>

Design notes that are easy to get wrong:

- **Repeats are THIS driver's loop.** ``harbor.grid.run_grid`` takes ``repeat_idx``
  as a caller-supplied LABEL and runs one pass; it contains no repeat loop. The
  repeat therefore belongs to the cell key here — ``(arm, record_id, repeat)`` — so
  a resumed run can tell repeat 2 of a record from repeat 0 of it.
- **Every arm answers the same question with its own retrieved context.** The
  record's question text plus whatever the arm returned is the prompt; the answer is
  graded MECHANICALLY against the record's authored ``expected_values`` /
  ``forbidden_values`` with ``metrics.scorers.states_value`` (word-boundary anchored,
  the ZFC-admissible format-anchored match, not a semantic judgment). No model
  grades anything in this loop.
- **The LLM judge is not wired into this driver at all.** It is report-only by
  construction: ``report.public_baseline.BaselineCell.judge_score`` stays ``None``
  here, and nothing downstream reads it for a pass, a delta, or a gate.
- **The pool an arm is seeded with carries no role signal.** The three evidence
  buckets are merged and re-keyed into the record's published ``candidate_pool.ids``
  order before any arm sees them (``_record_from_payload``). Merging them in bucket
  order puts gold in the low slots of every record, and a policy that takes the first
  k entries it was handed then scores the ceiling without retrieving. Sorting them by
  alias, which this driver did until round 4, is the subtler version of the same
  thing and is measured in ``_record_from_payload``. The published files never had
  either ordering; this driver used to rebuild them.
- **An unavailable arm refuses BEFORE the credential ladder.** ``--fire`` probes
  every named arm's infrastructure first, so a machine that cannot run ``ours`` or
  ``nemo-embed`` learns that without a token, and the real blocker is not hidden
  behind a credential error.
- **Injected-context volume travels per cell, in BOTH units.** Characters are the
  same quantity ``replay.ArmReplayResult.injected_context_chars`` carries and
  ``report.arm_vector.ArmAxisVector`` exposes as ``token_budget_chars``: the summed
  length of the payloads the arm returned. It is NOT on ``EfficiencyMetrics``. Items
  (``BaselineCell.injected_items``) are the second unit, and they are the one the
  design controls: the ranking arms are pinned to ``PUBLIC_TOP_K`` items each, so two
  arms at equal items differ only in WHICH items, and the residual character
  difference between them is a property of the texts they chose. Chars alone cannot
  say that — an arm returning fewer, longer items and an arm returning more, shorter
  ones are one number in that column.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal, Protocol

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_REPO_ROOT))

from membench.memory_systems import build_memory_system  # noqa: E402
from membench.memory_systems.base import MemorySystem, RetrievalRequest  # noqa: E402
from membench.memory_systems.local_stack import (  # noqa: E402
    LocalModelStack,
    LocalStackUnavailableError,
)
from membench.memory_systems.oracle_system import OracleMemory  # noqa: E402
from membench.metrics.scorers import states_value  # noqa: E402
from membench.report.public_baseline import (  # noqa: E402
    ARM_BASELINE,
    BaselineCell,
    build_report,
)
from membench.runner.e1_grid import (  # noqa: E402
    EXIT_HALT,
    EXIT_NO_CORPUS,
    EXIT_OK,
    EXIT_REFUSED,
    QuotaHaltError,
    ResumeMismatchError,
    RigHaltError,
    UnmeasuredStreak,
    _refusal,
    atomic_write_json,
    is_quota_halt,
    out_lock,
    spawn_timeout_of,
    write_json_new,
)
from membench.runner.headless_agent import (  # noqa: E402
    HeadlessAgentError,
    _stream_result_text,
    _stream_usage_tokens,
    resolve_cli_version,
    resolve_model,
    tool_calls_from_stream,
)
from membench.runtime import IdClock, StepContext  # noqa: E402
from membench.schemas.metrics import EfficiencyMetrics  # noqa: E402
from membench.spawn import Runner, run_checked  # noqa: E402
from membench.validity import QueryWork  # noqa: E402

PROTOCOL_VERSION = "public-baseline.v2"

# Retrieval width for every arm that ranks. One value for the whole grid, because an
# arm's width is a treatment: two arms at different k would differ in how much they
# inject as well as in how they choose, and the table could not attribute either.
#
# Six is a width the released corpus admits, and the corpus no longer pins one.
# Measured over all 160 released records: session pools run 20 to 29 and project pools
# run 21 to 30, both around a gold set of exactly 5, because both tiers grade five
# subjects and a project record's five are just authored by more than one sequence. The
# spreads are wide because each subject's group size is drawn (R3b), not fixed. Three
# constraints bound k:
#   * k >= gold (5), or the ceiling is unreachable and every arm is capped below
#     a pass by arithmetic rather than by retrieval;
#   * 2k <= min pool (20), so at least half the pool is left behind and choosing still
#     costs something;
#   * k < min pool, or "retrieve everything" and "retrieve well" are the same policy.
# Every width from 5 to 10 satisfies all three, so the choice inside that band is ours
# rather than the corpus'. Six is held from the release before this one, whose narrower
# pools pinned it at equality, so retrieval numbers stay comparable across the two.
# Widening past 10 would silently turn the ranking arms into the copy-the-whole-pool
# policy the stale traps exist to fail, and a corpus re-draw that narrows the minimum
# session pool below 12 makes this k unusable rather than merely tight.
#
# It was 4 against the v1 pools of 8 and 10. The mem-r6yzk R2 repair widened the
# distractor set and the R1 repair made the gold count vary from 4 to 6, so both numbers
# moved and the old k no longer reached the ceiling on a 6-gold record. The gold count
# then stopped varying: a gold set that grew with the cross-session count was the
# arithmetic that recovered a withheld field, so the graded budget now covers the whole
# set at 5, and the first constraint names one number instead of a maximum.
PUBLIC_TOP_K = 6

# How the `grouped` arm SPLITS that one width: `PUBLIC_PER_GROUP_K` items from each of
# `PUBLIC_GROUPS_K` subject groups. The product is `PUBLIC_TOP_K` exactly, and the
# module-level check below refuses any split where it is not, because the split is the
# only thing that makes grouped-vs-lexical a selection comparison: both arms hand the
# agent six items, so a difference between them is a difference in WHICH six.
#
# Both factors are pinned to what the released corpus actually offers, and both BIND —
# measured over all 160 released records, over the candidate pool the arm buckets:
#   * distinct content groups per record are {5: 80} on each tier, since both tiers
#     grade five subjects, so a `groups_k` of 3 cuts two groups on every one of the 160
#     records rather than only on the wider half;
#   * every group holds at least four candidates on 160/160 records, the smallest size
#     the generator draws, so `per_group_k` of 2 cuts in every group of every record.
# The measured result is exactly 6 items in exactly 3 groups on all 160 records, at 656
# chars against the flat arms' 655, so what separates the two arms is which six items
# they inject and not how many.
#
# That width does not depend on the arm re-asking the delegate on THIS corpus: a single
# fixed 24-row scan (`CANDIDATE_OVERSAMPLE` times the returned width) already fills 3
# groups of 2 on all 160 records, and 22 is the narrowest fixed scan that does, against
# pools running to 30. The re-ask is what makes that a property of the arm rather than
# of the draw. On the release this split was measured against, whose project pools ran
# to 35 candidates, a fixed scan left four of one group's five items below it on
# `world-seed132-task2` and returned 5 items there; how far below the returned width a
# group's second item sits is a property of the corpus, and the next re-draw is another
# draw of it.
#
# It was `per_group_k=1, groups_k=PUBLIC_TOP_K`, which claimed the same width and did
# not have it: a cap of 6 groups over a corpus offering 5 never binds, so the arm
# returns one item per group — 5 items and 534 chars per record against the flat arms'
# 6 items and 655 chars, measured on this release. Every grouped-vs-flat delta under
# that split was a volume difference wearing a selection difference's name.
PUBLIC_GROUPS_K = 3
PUBLIC_PER_GROUP_K = 2

if PUBLIC_PER_GROUP_K * PUBLIC_GROUPS_K != PUBLIC_TOP_K:  # pragma: no cover - import guard
    raise ValueError(
        f"the grouped split {PUBLIC_PER_GROUP_K}x{PUBLIC_GROUPS_K} injects "
        f"{PUBLIC_PER_GROUP_K * PUBLIC_GROUPS_K} items against the flat arms' "
        f"{PUBLIC_TOP_K}; the grid would compare selection and volume at once and "
        "could not attribute either"
    )

# The six arms, in the order the table reads: the floor, two deterministic
# retrievers, the neural baseline, ours, and the ceiling.
PUBLIC_ARMS: tuple[str, ...] = (
    "none",
    "lexical",
    "nemo-embed",
    "ours",
    "oracle",
    "grouped",
)

DEFAULT_REPEATS = 3

# The released corpus lives at the repo root, beside the schema and the validator.
DEFAULT_RELEASE_DIR = _REPO_ROOT.parent / "public" / "data"

# The Decision-7 track the `ours` arm replays under. One value for the whole grid:
# two arms retrieving under different tracks would be two experiments in one table.
DEFAULT_SCOPE = "same_rig_temporal"

# A pricing ESTIMATE, not a measurement: ~4 characters per token for English prose.
# It exists so a reader can see the order of magnitude of a fire before buying it;
# the artifact reports measured tokens from the agent's own usage events.
CHARS_PER_TOKEN_ESTIMATE = 4

# How the six arms actually retrieve. Declared, not inferred: an arm's request shape
# is a property of the arm, and a driver that guessed it would mis-drive a new one.
RetrievalKind = Literal["no-retrieval", "id-exact", "lexical", "embedding", "mem-store"]


class PublicBaselineError(RuntimeError):
    """A driver-level refusal: a malformed release, an unavailable arm, a bad plan."""


@dataclass(frozen=True)
class ArmRequirement:
    """What one arm needs before it can run, and where that need is satisfied."""

    arm: str
    retrieval: RetrievalKind
    # "local-cpu" — pure Python in this process; "local-gpu" — a local embedding
    # model (weights + optional GPU); "local-store" — the private mem store + CLI.
    infrastructure: Literal["local-cpu", "local-gpu", "local-store"]
    note: str

    @property
    def needs_paid_inference(self) -> bool:
        """Whether the ARM itself calls a paid model. None of the six do — the paid
        axis of this grid is the answering agent, which every arm shares, so an arm
        that reported paid inference here would be double-counting it."""
        return False


ARM_REQUIREMENTS: dict[str, ArmRequirement] = {
    "none": ArmRequirement(
        arm="none",
        retrieval="no-retrieval",
        infrastructure="local-cpu",
        note="the no-memory floor; injects nothing",
    ),
    "lexical": ArmRequirement(
        arm="lexical",
        retrieval="lexical",
        infrastructure="local-cpu",
        note="deterministic token-overlap top-k over the record's candidate pool",
    ),
    "nemo-embed": ArmRequirement(
        arm="nemo-embed",
        retrieval="embedding",
        infrastructure="local-gpu",
        note=(
            "in-process NeMo dense embedder (sentence-transformers) + exact cosine top-k; "
            "needs the pinned weights locally, no Ollama daemon"
        ),
    ),
    "ours": ArmRequirement(
        arm="ours",
        retrieval="mem-store",
        infrastructure="local-store",
        note=(
            "failure-triggered retrieval-v1: shells the mem CLI against a store that must "
            "already hold the released records; needs --store and --mem-bin"
        ),
    ),
    "oracle": ArmRequirement(
        arm="oracle",
        retrieval="id-exact",
        infrastructure="local-cpu",
        note="the ceiling: injects exactly the record's gold ids",
    ),
    "grouped": ArmRequirement(
        arm="grouped",
        retrieval="lexical",
        infrastructure="local-cpu",
        note="group-diversified re-selection over the lexical delegate",
    ),
}


# --------------------------------------------------------------------------------------
# The released set
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineRecord:
    """One released bench record, in the shape this driver runs it.

    Built by strict key access over the published JSON: a record missing any field
    below is a malformed release and raises, because a default here would quietly
    run a different experiment (an empty candidate pool scores every arm at the
    floor and looks like a finding)."""

    record_id: str
    tier: str
    origin: str
    question_text: str
    asked_at: str
    scope_id: str
    world_id: str
    source_sha256: str
    # The three evidence buckets merged into one unlabelled pool, keyed in ascending
    # alias order — NOT bucket order. This mapping is what every ranking arm is
    # seeded with, so its iteration order is a channel; see `_record_from_payload`
    # for why the order is the published alias order and nothing else.
    candidates: dict[str, str]
    gold_ids: tuple[str, ...]
    # The stale entries in the pool, whose texts state this record's forbidden values.
    # Carried so a cell can report HOW MANY of them an arm handed the agent: an arm
    # that fails a record by surfacing staleness and one that fails it by retrieving
    # nothing are different failures, and the reward alone cannot tell them apart.
    superseded_ids: tuple[str, ...]
    expected_values: tuple[str, ...]
    forbidden_values: tuple[str, ...]
    query: dict[str, Any]

    @property
    def gold_payloads(self) -> dict[str, str]:
        return {mid: self.candidates[mid] for mid in self.gold_ids}

    def stale_injected(self, payloads: Mapping[str, str]) -> int:
        """How many of this record's stale entries an arm put in front of the agent."""
        return sum(1 for memory_id in self.superseded_ids if memory_id in payloads)

    @property
    def candidate_chars(self) -> int:
        return sum(len(text) for text in self.candidates.values())

    @property
    def gold_chars(self) -> int:
        return sum(len(text) for text in self.gold_payloads.values())

    def query_work(self) -> QueryWork:
        """The `ours` arm's replay query. ``started`` is the D6 boundary and comes
        from the record's own ``question.asked_at`` — the moment the released record
        says the question was asked, never "now"."""
        return QueryWork(
            work_id=str(self.query["id"]),
            rig=self.world_id,
            started=self.asked_at,
            convoy_id=self.query.get("convoy_key"),
            pr=self.query.get("pr_key"),
            external_ref=self.query.get("external_ref_key"),
            parent=self.query.get("parent_key"),
        )


def _record_from_payload(payload: Mapping[str, Any]) -> BaselineRecord:
    """One released line, as the pool an arm is allowed to see.

    The bucket merge is where the answer key leaks back in if it is done naively.
    Reading ``gold``, then ``distractors``, then ``superseded`` into a dict and
    handing that dict to an arm re-labels every candidate by role, because Python
    dicts keep insertion order: gold occupies the low slots of the mapping in every
    single record, and "take the first k entries I was handed" scores a perfect
    ceiling without retrieving anything. That is the exact thing ``public/README.md``
    tells a runner not to do, and the driver was doing it after the exporter had
    already removed the signal.

    So the merged pool is re-keyed into ``candidate_pool.ids`` order, which the
    release publishes as the normative seeding order and the standalone validator
    refuses a record for departing from. Three properties come with taking it from
    the file rather than picking an order here:

    * it is the order a third party gets, verbatim. A driver-private shuffle, or a
      re-sort on any other key, would mean the policy we measure is not the policy an
      external runner measures on the same record;
    * it is drawn per record, by ``public_export.pool_order``, so a memory's position
      is an independent draw in every record it appears in. Ascending alias order, the
      previous choice here, reuses one draw per memory across the whole corpus, and on
      the release where that was measured it accumulated into the first alias being gold
      in 47 records of 160. On this re-drawn release the same rule reads 35 records of
      160 (0.2188) against a per-record chance of 0.2017, p = 0.3191 over 20,000
      permutation draws, which says the accident is a property of the mint and not of
      the rule, and is the reason the rule was replaced rather than re-measured. The
      published order reads 41 in 160 (0.2562) at p = 0.0546;
    * it is auditable from the published files alone, with no extra key material to
      keep, rotate or leak.

    It is deterministic across runs and across processes: the order is read off the
    record, and the membership check below is what ties it to the texts.
    """
    evidence = payload["evidence"]
    merged: dict[str, str] = {}
    # Strict key access, no `.get(...) or {}`: a release missing a bucket is a
    # malformed release, and defaulting it to empty is how the stale trap went
    # missing in the first place — the driver read a `superseded` bucket the
    # exporter never wrote, found nothing, and built a pool that no answer policy
    # could fail on.
    for bucket in ("gold", "distractors", "superseded"):
        for memory_id, text in dict(evidence[bucket]).items():
            key = str(memory_id)
            if key in merged:
                raise PublicBaselineError(
                    f"{payload['record_id']}: candidate {key!r} is filed under two evidence "
                    "buckets, so it has two roles at once; the later bucket's text would "
                    "silently win and the record's trap count would be wrong"
                )
            merged[key] = str(text)
    published_order = [str(memory_id) for memory_id in payload["candidate_pool"]["ids"]]
    if sorted(published_order) != sorted(merged):
        raise PublicBaselineError(
            f"{payload['record_id']}: the pool this driver can build "
            f"({len(merged)} entries with text) is not the record's published "
            f"candidate_pool ({len(set(published_order))} ids); the arms would be "
            "ranking over a different set than the one the leave-one-out cut admitted"
        )
    candidates = {memory_id: merged[memory_id] for memory_id in published_order}
    gold_ids = tuple(str(mid) for mid in evidence["gold_ids"])
    missing = [mid for mid in gold_ids if mid not in candidates]
    if missing:
        raise PublicBaselineError(
            f"{payload['record_id']}: gold ids {missing} have no content in the record's "
            "evidence; the ceiling arm would inject nothing and score as a floor"
        )
    expected = tuple(str(value) for value in payload["answer"]["expected_values"])
    if not expected:
        raise PublicBaselineError(
            f"{payload['record_id']}: answer.expected_values is empty, so no answer can be "
            "graded and every arm would score 0"
        )
    provenance = payload["provenance"]
    return BaselineRecord(
        record_id=str(payload["record_id"]),
        tier=str(payload["tier"]),
        origin=str(payload["origin"]),
        question_text=str(payload["question"]["text"]),
        asked_at=str(payload["question"]["asked_at"]),
        scope_id=str(payload["question"]["scope_id"]),
        world_id=str(provenance["world_id"]),
        source_sha256=str(provenance["source_sha256"]),
        candidates=candidates,
        gold_ids=gold_ids,
        superseded_ids=tuple(str(mid) for mid in evidence["superseded_ids"]),
        expected_values=expected,
        forbidden_values=tuple(str(v) for v in payload["answer"]["forbidden_values"]),
        query=dict(payload["loo"]["query"]),
    )


def release_files(release_dir: Path) -> list[Path]:
    return sorted(release_dir.glob("*.jsonl"))


def load_release(release_dir: Path) -> list[BaselineRecord]:
    """Every released record under ``release_dir``, sorted by record id.

    A missing directory, an absent ``.jsonl``, or a duplicate record id raises: this
    is the SET the baseline is defined over, and a run that quietly measured a subset
    of it would publish a different experiment under the released set's name."""
    if not release_dir.is_dir():
        raise PublicBaselineError(f"released set not found: {release_dir} is not a directory")
    files = release_files(release_dir)
    if not files:
        raise PublicBaselineError(f"no .jsonl files under {release_dir}: nothing to measure")
    records: dict[str, BaselineRecord] = {}
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PublicBaselineError(f"{path}:{lineno}: not JSON ({exc})") from exc
            try:
                record = _record_from_payload(payload)
            except KeyError as exc:
                raise PublicBaselineError(
                    f"{path}:{lineno}: released record is missing {exc} — malformed release"
                ) from exc
            if record.record_id in records:
                raise PublicBaselineError(
                    f"{path}:{lineno}: record id {record.record_id!r} appears twice in the "
                    "released set; a duplicated record would be double-counted"
                )
            records[record.record_id] = record
    return [records[key] for key in sorted(records)]


def release_fingerprint(records: Sequence[BaselineRecord]) -> str:
    """The digest of the released set a run measured, in a stable order.

    Part of the resume identity for the reason the model is: a re-exported corpus
    changes what a cell means, and pooling old cells into it would land two
    measurements in one table."""
    digest = sha256()
    for record in sorted(records, key=lambda r: r.record_id):
        digest.update(f"{record.record_id}\0{record.source_sha256}\0".encode())
    return digest.hexdigest()


# --------------------------------------------------------------------------------------
# Infrastructure probes
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """What a real check of one dependency found. ``available`` is never assumed:
    an unavailable arm refuses a fire rather than being skipped into a smaller grid."""

    subject: str
    available: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "available": self.available, "detail": self.detail}


def probe_ollama(
    stack: LocalModelStack, *, fetch: Callable[[str], bytes] | None = None
) -> ProbeResult:
    """Whether the shared Ollama daemon is up with its pinned models.

    NONE of the six arms in this grid need it (``nemo-embed`` embeds in process and
    ``mem0`` is not in the set), so this is reported, not required — an operator
    reading the plan should not have to infer from silence that the daemon is
    irrelevant here."""
    try:
        stack.preflight(require_chat=True, fetch=fetch)
    except LocalStackUnavailableError as exc:
        return ProbeResult(subject="ollama", available=False, detail=str(exc))
    return ProbeResult(
        subject="ollama",
        available=True,
        detail=(
            f"daemon at {stack.ollama_base_url} has {stack.ollama_embedding_model} and "
            f"{stack.chat_model} (not required by any arm in this grid)"
        ),
    )


def probe_arm(
    arm: str,
    *,
    stack: LocalModelStack,
    store_path: Path | None,
    mem_bin: Path | None,
) -> ProbeResult:
    """A real availability check for one arm's retrieval infrastructure."""
    requirement = ARM_REQUIREMENTS.get(arm)
    if requirement is None:
        raise PublicBaselineError(f"unknown arm {arm!r}; this grid runs {list(ARM_REQUIREMENTS)}")
    if requirement.infrastructure == "local-cpu":
        return ProbeResult(subject=arm, available=True, detail="pure Python, in process")
    if requirement.infrastructure == "local-gpu":
        if find_spec("sentence_transformers") is None:
            return ProbeResult(
                subject=arm,
                available=False,
                detail=(
                    "sentence_transformers is not importable, so the pinned embedder "
                    f"{stack.nemo_embedding_model!r} cannot be loaded. `pip install "
                    "sentence-transformers` (and fetch the weights) before firing."
                ),
            )
        torch = "torch present" if find_spec("torch") is not None else "torch NOT importable"
        return ProbeResult(
            subject=arm,
            available=find_spec("torch") is not None,
            detail=(
                f"sentence_transformers present, {torch}; pinned model "
                f"{stack.nemo_embedding_model!r} is downloaded on first use "
                "(no Ollama daemon involved)"
            ),
        )
    # local-store: the `ours` arm needs a mem store that already holds these records,
    # plus the built mem CLI it shells.
    problems: list[str] = []
    if store_path is None:
        problems.append("--store was not given")
    elif not store_path.exists():
        problems.append(f"store {store_path} does not exist")
    if mem_bin is None:
        problems.append("--mem-bin was not given")
    elif not (mem_bin.exists() and os.access(mem_bin, os.X_OK)):
        problems.append(f"mem CLI {mem_bin} is missing or not executable")
    if problems:
        return ProbeResult(
            subject=arm,
            available=False,
            detail=(
                "; ".join(problems)
                + ". `ours` retrieves over the work-audit store by work id, so the released "
                "records must be loaded into a store before this arm can answer for them."
            ),
        )
    return ProbeResult(
        subject=arm,
        available=True,
        detail=f"store {store_path} and mem CLI {mem_bin} are present",
    )


def probe_all(
    arms: Sequence[str],
    *,
    stack: LocalModelStack,
    store_path: Path | None,
    mem_bin: Path | None,
) -> dict[str, ProbeResult]:
    return {
        arm: probe_arm(arm, stack=stack, store_path=store_path, mem_bin=mem_bin) for arm in arms
    }


# --------------------------------------------------------------------------------------
# Pricing (--preflight)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmPlan:
    arm: str
    cells: int
    requirement: ArmRequirement
    probe: ProbeResult
    est_prompt_chars: int
    est_prompt_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "cells": self.cells,
            "retrieval": self.requirement.retrieval,
            "infrastructure": self.requirement.infrastructure,
            "arm_needs_paid_inference": self.requirement.needs_paid_inference,
            "note": self.requirement.note,
            "available": self.probe.available,
            "probe": self.probe.detail,
            "est_prompt_chars_ceiling": self.est_prompt_chars,
            "est_prompt_tokens_ceiling": self.est_prompt_tokens,
        }


@dataclass(frozen=True)
class PreflightPlan:
    release_dir: str
    release_fingerprint: str
    n_records: int
    repeats: int
    arms: list[ArmPlan]
    shared_probes: list[ProbeResult]
    unavailable: list[str]

    @property
    def total_cells(self) -> int:
        return sum(plan.cells for plan in self.arms)

    @property
    def est_prompt_tokens(self) -> int:
        return sum(plan.est_prompt_tokens for plan in self.arms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "release_dir": self.release_dir,
            "release_fingerprint": self.release_fingerprint,
            "n_records": self.n_records,
            "repeats": self.repeats,
            "total_cells": self.total_cells,
            "est_prompt_tokens_ceiling": self.est_prompt_tokens,
            "chars_per_token_estimate": CHARS_PER_TOKEN_ESTIMATE,
            "paid_axis": (
                "every cell spawns ONE answering agent run; no arm in this grid calls a "
                "paid model itself"
            ),
            "arms": [plan.to_dict() for plan in self.arms],
            "shared_probes": [probe.to_dict() for probe in self.shared_probes],
            "unavailable_arms": list(self.unavailable),
        }


def _injected_chars_ceiling(arm: str, record: BaselineRecord) -> int:
    """The MOST memory text this arm could inject for this record.

    A ceiling rather than a measurement: a ranking arm's real volume depends on what
    it retrieves, which is the run. Labeled as a ceiling everywhere it is printed."""
    retrieval = ARM_REQUIREMENTS[arm].retrieval
    if retrieval == "no-retrieval":
        return 0
    if retrieval == "id-exact":
        return record.gold_chars
    return record.candidate_chars


def price(
    records: Sequence[BaselineRecord],
    *,
    arms: Sequence[str],
    repeats: int,
    probes: Mapping[str, ProbeResult],
    shared_probes: Sequence[ProbeResult] = (),
    release_dir: Path = DEFAULT_RELEASE_DIR,
) -> PreflightPlan:
    if repeats < 1:
        raise PublicBaselineError(f"--repeats must be >= 1, got {repeats}")
    if not records:
        raise PublicBaselineError("the released set is empty; there is nothing to price")
    plans: list[ArmPlan] = []
    for arm in arms:
        requirement = ARM_REQUIREMENTS[arm]
        chars = (
            sum(
                len(record.question_text) + _injected_chars_ceiling(arm, record)
                for record in records
            )
            * repeats
        )
        plans.append(
            ArmPlan(
                arm=arm,
                cells=len(records) * repeats,
                requirement=requirement,
                probe=probes[arm],
                est_prompt_chars=chars,
                est_prompt_tokens=-(-chars // CHARS_PER_TOKEN_ESTIMATE),
            )
        )
    return PreflightPlan(
        release_dir=str(release_dir),
        release_fingerprint=release_fingerprint(records),
        n_records=len(records),
        repeats=repeats,
        arms=plans,
        shared_probes=list(shared_probes),
        unavailable=[arm for arm in arms if not probes[arm].available],
    )


# --------------------------------------------------------------------------------------
# The run (--fire)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentAnswer:
    """One answering agent run's result, as this driver measures it."""

    text: str
    input_tokens: int
    output_tokens: int
    tool_calls: int
    wall_clock_latency_ms: float
    # An OAuth-subscription run is not metered per call, and a set ANTHROPIC_API_KEY
    # is REFUSED before any cell runs, so this is 0.0 on the path this driver
    # supports. It is carried rather than dropped because the column is part of the
    # published table and a metered runtime behind the same seam can fill it.
    cost_usd: float = 0.0
    raw_stream: str = ""


class AnswerAgent(Protocol):
    """The answering seam. Injected so the whole pipeline is testable without an
    agent, a token, or a network."""

    def __call__(self, prompt: str, *, record: BaselineRecord, arm: str) -> AgentAnswer: ...


def build_prompt(record: BaselineRecord, payloads: Mapping[str, str]) -> str:
    """The prompt one cell sends: the record's question, plus this arm's retrieved
    memory verbatim. Identical across arms except for the memory block — the arm IS
    the treatment, so nothing else may differ between two arms' prompts."""
    if not payloads:
        memory = "No memory was retrieved for this task."
    else:
        lines = [f"- [{memory_id}] {text}" for memory_id, text in payloads.items()]
        memory = "Retrieved memory:\n" + "\n".join(lines)
    return (
        f"{memory}\n\n"
        f"Task: {record.question_text}\n\n"
        "Answer with the current value of each item asked for. State each value "
        "literally and do not state superseded values."
    )


def grade(answer_text: str, record: BaselineRecord) -> tuple[float, bool]:
    """The mechanical value-set grade: the fraction of expected values stated, and
    whether the answer stated all of them and none of the forbidden ones.

    Stating a forbidden (superseded) value zeroes the reward — staleness is
    reward-bearing by construction in this corpus, so an answer that hedges by
    listing both the old and new value is not a partial success."""
    if any(states_value(answer_text, value) for value in record.forbidden_values):
        return 0.0, False
    hits = sum(1 for value in record.expected_values if states_value(answer_text, value))
    return hits / len(record.expected_values), hits == len(record.expected_values)


ArmFactory = Callable[[str], MemorySystem]


def default_arm_factory(
    *,
    store_path: Path | None = None,
    mem_bin: Path | None = None,
) -> ArmFactory:
    """Build one arm by name for this grid. Unknown names raise through the registry
    factory (never a silent substitution), and `ours` is refused without its store
    rather than constructed into a failure at first retrieve.

    Every arm that ranks is built at `PUBLIC_TOP_K`, EXPLICITLY. The registry's own
    default is 10, which is wider than this corpus' smaller pool: an arm left at the
    default returns every candidate that overlaps the query at all, which is not
    retrieval, and the table would read three ranking arms as tied at the ceiling
    while none of them had chosen anything.

    `grouped` is built at the SAME item width as the flat arms rather than at its own
    (`PUBLIC_PER_GROUP_K * PUBLIC_GROUPS_K == PUBLIC_TOP_K`), so the question the
    published grouped-vs-lexical delta answers is: **given six items of retrieval
    width, does spending them across three subject groups beat spending them on the
    six top-ranked candidates?** It is NOT "does grouping beat ranking at any width" —
    no cell in this grid varies the width, and a reader who wants that has to buy a
    width sweep. The report's `injected items (mean)` column is what makes the
    equal-width premise checkable from the artifact rather than from this docstring;
    `report.public_baseline.equal_width_pairs` names the pairs it holds for."""

    def factory(arm: str) -> MemorySystem:
        if arm == "ours":
            if store_path is None or mem_bin is None:
                raise PublicBaselineError(
                    "the `ours` arm needs --store (a mem store holding the released records) "
                    "and --mem-bin (the built mem CLI)"
                )
            return build_memory_system("ours", store_path=str(store_path), mem_bin=str(mem_bin))
        if arm in ("lexical", "nemo-embed"):
            return build_memory_system(arm, top_k=PUBLIC_TOP_K)
        if arm == "grouped":
            # PUBLIC_PER_GROUP_K items from each of PUBLIC_GROUPS_K subject groups:
            # the SAME item width as the flat arms (the product is PUBLIC_TOP_K),
            # spent on coverage instead of depth. Both factors bind on this corpus;
            # `test_grouped_system` measures that against the released records rather
            # than taking this comment's word for it.
            return build_memory_system(
                "grouped",
                base=build_memory_system("lexical", top_k=PUBLIC_TOP_K),
                per_group_k=PUBLIC_PER_GROUP_K,
                groups_k=PUBLIC_GROUPS_K,
            )
        return build_memory_system(arm)

    return factory


def retrieve_for(
    arm: MemorySystem,
    arm_name: str,
    record: BaselineRecord,
    ctx: StepContext,
    *,
    scope: str = DEFAULT_SCOPE,
) -> dict[str, str]:
    """Drive one arm over one record in the request shape that arm actually serves.

    The shapes are not interchangeable: the id-exact ceiling takes ``requested_ids``,
    the ranking arms take ``query_text`` over a seeded candidate pool, and `ours`
    takes ``query_work`` + ``scope``. Driving an arm through the wrong one is how a
    ceiling silently becomes a floor."""
    retrieval = ARM_REQUIREMENTS[arm_name].retrieval
    arm.reset(ctx.trial_id)
    if retrieval == "no-retrieval":
        return dict(arm.retrieve(RetrievalRequest(query_text=record.question_text), ctx).payloads)
    if retrieval == "id-exact":
        if not isinstance(arm, OracleMemory):
            raise PublicBaselineError(
                f"{arm_name!r} is declared id-exact but is not an OracleMemory; the harness "
                "cannot inject ground truth into it"
            )
        arm.load(record.gold_payloads)
        request = RetrievalRequest(
            query_text=record.question_text, requested_ids=list(record.gold_ids)
        )
        return dict(arm.retrieve(request, ctx).payloads)
    if retrieval == "mem-store":
        request = RetrievalRequest(query_work=record.query_work(), scope=scope)
        return dict(arm.retrieve(request, ctx).payloads)
    # lexical / embedding: the candidate pool is the arm's whole world for this
    # record. Seeded (not written) so the seeding never counts as agent retention.
    arm.seed(dict(record.candidates), StepContext(ctx.trial_id, ctx.session_id, "seed", IdClock()))
    return dict(arm.retrieve(RetrievalRequest(query_text=record.question_text), ctx).payloads)


@dataclass(frozen=True)
class ClaudeAnswerAgent:
    """The paid answering agent: one ``claude -p`` per cell, on the OAuth
    subscription. Constructed only on the ``--fire`` path, after the refusal ladder.

    The argv mirrors ``headless_agent.HeadlessClaudeAgent.argv_for``'s shape for the
    flags that matter here (stream-json so usage and tool calls are readable,
    ``--strict-mcp-config`` so a project MCP server cannot hang the boot), and the
    resolved model is passed explicitly — an unpinned paid run is refused upstream."""

    model: str
    timeout_s: float = 900.0
    runner: Runner = subprocess.run

    def argv_for(self, prompt: str) -> list[str]:
        argv = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--strict-mcp-config",
        ]
        resolved = resolve_model(self.model)
        if resolved:
            argv += ["--model", resolved]
        return argv

    def __call__(self, prompt: str, *, record: BaselineRecord, arm: str) -> AgentAnswer:
        started = time.monotonic()
        completed = run_checked(
            self.argv_for(prompt),
            what="claude -p for the public baseline",
            not_found_hint="install the Claude Code CLI to run the answering agent",
            timeout_s=self.timeout_s,
            error=HeadlessAgentError,
            runner=self.runner,
        )
        elapsed_ms = (time.monotonic() - started) * 1000.0
        stream = completed.stdout or ""
        input_tokens, output_tokens = _stream_usage_tokens(stream)
        return AgentAnswer(
            text=_stream_result_text(stream),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=len(tool_calls_from_stream(stream)),
            wall_clock_latency_ms=elapsed_ms,
            raw_stream=stream,
        )


CellKey = tuple[str, str, int]


def grid_keys(
    records: Sequence[BaselineRecord], arms: Sequence[str], repeats: int
) -> list[CellKey]:
    """Every cell this grid runs, in a stable order. The repeat is part of the key
    because the repeat loop is THIS driver's (``harbor.grid.run_grid`` takes
    ``repeat_idx`` as a label and runs one pass)."""
    if repeats < 1:
        raise PublicBaselineError(f"repeats must be >= 1, got {repeats}")
    return [
        (arm, record.record_id, repeat)
        for arm in arms
        for record in sorted(records, key=lambda r: r.record_id)
        for repeat in range(repeats)
    ]


def run_cell(
    record: BaselineRecord,
    arm_name: str,
    repeat: int,
    *,
    arm_factory: ArmFactory,
    answer: AnswerAgent,
    paid: bool,
    scope: str = DEFAULT_SCOPE,
) -> BaselineCell:
    """One (arm, record, repeat): retrieve, ask, grade. Retrieval failures raise —
    an arm that cannot retrieve is a broken cell, not a zero-reward measurement."""
    arm = arm_factory(arm_name)
    ctx = StepContext(
        trial_id=f"{arm_name}-{record.record_id}-{repeat}",
        session_id=record.record_id,
        step_id="goal",
        clock=IdClock(),
    )
    payloads = retrieve_for(arm, arm_name, record, ctx, scope=scope)
    prompt = build_prompt(record, payloads)
    result = answer(prompt, record=record, arm=arm_name)
    reward, passed = grade(result.text, record)
    efficiency = EfficiencyMetrics(
        total_tokens=result.input_tokens + result.output_tokens,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        tool_calls_total=result.tool_calls,
        wall_clock_latency_ms=result.wall_clock_latency_ms,
        cost_usd=result.cost_usd,
    )
    return BaselineCell(
        arm=arm_name,
        record_id=record.record_id,
        repeat=repeat,
        status="ok",
        paid=paid,
        reward=reward,
        passed=passed,
        injected_items=len(payloads),
        injected_context_chars=sum(len(text) for text in payloads.values()),
        stale_injected=record.stale_injected(payloads),
        efficiency=efficiency,
        judge_score=None,  # report-only column; this driver never populates a judge
        detail="",
    )


def _unmeasured(
    key: CellKey, *, status: Literal["timeout", "error"], detail: str, paid: bool
) -> BaselineCell:
    """A cell that spawned an agent and measured nothing. Explicitly NOT a scored
    zero: the report refuses to publish a grid containing one, and the status is
    typed so "ok" cannot reach here at all."""
    arm_name, record_id, repeat = key
    return BaselineCell(
        arm=arm_name,
        record_id=record_id,
        repeat=repeat,
        status=status,
        paid=paid,
        reward=0.0,
        passed=False,
        injected_items=0,
        injected_context_chars=0,
        stale_injected=0,
        efficiency=EfficiencyMetrics(),
        detail=detail,
    )


def run_cells(
    records: Sequence[BaselineRecord],
    keys: Sequence[CellKey],
    *,
    arm_factory: ArmFactory,
    answer: AnswerAgent,
    paid: bool,
    landed: Sequence[BaselineCell] = (),
    on_cell: Callable[[BaselineCell], None] | None = None,
    scope: str = DEFAULT_SCOPE,
) -> list[BaselineCell]:
    """Run every cell in ``keys`` that ``landed`` does not already hold, one at a
    time, persisting through ``on_cell`` as each one completes.

    Two halts, and they mean different things: a quota refusal is not a defect and
    nothing further can be bought, so the run keeps what it has and stops; a run of
    consecutive unmeasured cells is a broken rig, and spending the rest of the
    authorization on it would buy a grid of holes."""
    by_id = {record.record_id: record for record in records}
    done = {(cell.arm, cell.record_id, cell.repeat) for cell in landed}
    cells = list(landed)
    streak = UnmeasuredStreak()
    for key in keys:
        if key in done:
            continue
        arm_name, record_id, repeat = key
        record = by_id.get(record_id)
        if record is None:
            raise PublicBaselineError(
                f"the grid names cell {key} but the released set carries no record {record_id!r}"
            )
        try:
            cell = run_cell(
                record,
                arm_name,
                repeat,
                arm_factory=arm_factory,
                answer=answer,
                paid=paid,
                scope=scope,
            )
        except HeadlessAgentError as exc:
            if is_quota_halt(exc):
                raise QuotaHaltError(
                    f"the account refused at cell {key}: {exc}. {len(cells)} cell(s) kept; "
                    "nothing further can be bought until it resets."
                ) from exc
            timed_out = spawn_timeout_of(exc) is not None
            cell = _unmeasured(
                key,
                status="timeout" if timed_out else "error",
                detail=str(exc),
                paid=paid,
            )
            cells.append(cell)
            if on_cell is not None:
                on_cell(cell)
            if streak.unmeasured():
                raise RigHaltError(
                    f"{streak.count} consecutive cell(s) measured nothing, through {key}: the "
                    "rig is broken, not flaky. Fix it before spending the rest of the grid."
                ) from exc
            continue
        streak.measured()
        cells.append(cell)
        if on_cell is not None:
            on_cell(cell)
    return cells


# --------------------------------------------------------------------------------------
# Resume identity + artifact
# --------------------------------------------------------------------------------------


def resume_identity(
    *,
    model: str,
    cli_version: str,
    release: str,
    arms: Sequence[str],
    repeats: int,
    scope: str,
) -> dict[str, Any]:
    """Everything a partial ``--out`` must match before its cells may be pooled into
    this run. The arm set and the repeat count are in here because both change what
    the table means: a partial bought at 1 repeat is not a third of a 3-repeat grid,
    and a partial bought without `ours` cannot be completed into one that has it."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "model": resolve_model(model) or "cli-default",
        "cli_version": cli_version,
        "release_fingerprint": release,
        "arms": list(arms),
        "repeats": repeats,
        "scope": scope,
    }


def admissible_cells(
    summary: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
    grid: Sequence[CellKey],
) -> list[BaselineCell]:
    """The cells a partial artifact contributes to a resumed run.

    Every identity field must match, and a blank field on either side is a mismatch
    rather than a pass. Unmeasured cells are dropped so the resume is a chance to buy
    them; a duplicate key and a key outside this grid REFUSE rather than filter."""
    blank = [field_name for field_name, value in identity.items() if not value]
    if blank:
        raise ResumeMismatchError(
            f"this run cannot state its own {', '.join(blank)}; refusing to match an artifact "
            "against an identity it does not have"
        )
    got = {field_name: summary.get(field_name) for field_name in identity}
    if got != dict(identity):
        differing = {f: (got[f], identity[f]) for f in identity if got[f] != identity[f]}
        detail = "; ".join(f"{f}: artifact={a!r} run={b!r}" for f, (a, b) in differing.items())
        raise ResumeMismatchError(
            f"partial artifact was produced by a different run ({detail}). Not resuming into it."
        )
    allowed = set(grid)
    seen: set[CellKey] = set()
    kept: list[BaselineCell] = []
    for row in summary.get("cells", ()):
        cell = BaselineCell.from_row(row)
        key = (cell.arm, cell.record_id, cell.repeat)
        if key in seen:
            raise ResumeMismatchError(
                f"partial artifact carries {key} twice; two runs wrote it and neither row can "
                "be shown to be the one this grid should keep"
            )
        seen.add(key)
        if key not in allowed:
            raise ResumeMismatchError(
                f"partial artifact carries {key}, which is not a cell of this grid; it cannot "
                "be pooled into these rates"
            )
        if cell.status != "ok" or not cell.paid:
            continue
        kept.append(cell)
    return kept


def artifact(
    cells: Sequence[BaselineCell], *, identity: Mapping[str, Any], grid_size: int
) -> dict[str, Any]:
    return dict(identity) | {
        "grid_size": grid_size,
        "n_cells": len(cells),
        "cells": [cell.to_row() for cell in cells],
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

_PLAN_ONLY = (
    "This was a PREFLIGHT: it priced the grid, probed the infrastructure, and spent nothing.\n"
    "  buy the grid : PYTHONPATH=. python3 scripts/run_public_baseline.py --fire "
    "--model <id> --out <path>\n"
    "The paid line needs CLAUDE_CODE_OAUTH_TOKEN, an unset ANTHROPIC_API_KEY, and a pinned "
    "--model, and it spends real money."
)


def _selected_arms(raw: str) -> list[str]:
    arms = [name.strip() for name in raw.split(",") if name.strip()]
    unknown = [arm for arm in arms if arm not in ARM_REQUIREMENTS]
    if unknown:
        raise PublicBaselineError(
            f"unknown arm(s) {unknown}; this grid runs {list(ARM_REQUIREMENTS)}"
        )
    if not arms:
        raise PublicBaselineError("--arms selected nothing")
    if ARM_BASELINE not in arms:
        raise PublicBaselineError(
            f"--arms must include the {ARM_BASELINE!r} baseline: every reported delta is paired "
            "against it, and a table without it can only report pooled levels"
        )
    return arms


def _preflight(args: argparse.Namespace, records: Sequence[BaselineRecord]) -> int:
    arms = _selected_arms(args.arms)
    stack = LocalModelStack.from_env()
    probes = probe_all(arms, stack=stack, store_path=args.store, mem_bin=args.mem_bin)
    plan = price(
        records,
        arms=arms,
        repeats=args.repeats,
        probes=probes,
        shared_probes=[probe_ollama(stack)],
        release_dir=args.release_dir,
    )
    print(json.dumps(plan.to_dict(), indent=2))
    print(_PLAN_ONLY, file=sys.stderr)
    if plan.unavailable:
        print(
            f"NOT READY: {plan.unavailable} cannot run on this machine; a fire would refuse "
            "rather than silently drop them from the grid.",
            file=sys.stderr,
        )
    return EXIT_OK


def unavailable_arms(args: argparse.Namespace) -> list[str]:
    """The named arms whose infrastructure is not actually here, each with the probe
    detail that says why.

    Called on the ``--fire`` path BEFORE the credential ladder. An arm whose embedder
    is not installed, or whose store was never passed, cannot be bought no matter how
    the account is authenticated, and refusing on the token first would report the
    wrong blocker to an operator who then goes and fixes the token."""
    arms = _selected_arms(args.arms)
    stack = LocalModelStack.from_env()
    probes = probe_all(arms, stack=stack, store_path=args.store, mem_bin=args.mem_bin)
    return [f"{arm}: {probes[arm].detail}" for arm in arms if not probes[arm].available]


def _fire(args: argparse.Namespace, records: Sequence[BaselineRecord]) -> int:
    arms = _selected_arms(args.arms)
    try:
        cli_version = resolve_cli_version()
    except HeadlessAgentError as exc:
        print(f"REFUSING to run: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    identity = resume_identity(
        model=args.model,
        cli_version=cli_version,
        release=release_fingerprint(records),
        arms=arms,
        repeats=args.repeats,
        scope=args.scope,
    )
    grid = grid_keys(records, arms, args.repeats)
    out: Path = args.out

    landed: list[BaselineCell] = []
    if out.exists():
        try:
            prior = json.loads(out.read_text(encoding="utf-8"))
            landed = admissible_cells(prior, identity=identity, grid=grid)
        except ResumeMismatchError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return EXIT_REFUSED
        except (ValueError, KeyError, TypeError) as exc:
            print(f"{out}: not a readable partial artifact: {exc}", file=sys.stderr)
            return EXIT_REFUSED

    print(
        json.dumps(
            {
                **identity,
                "grid_size": len(grid),
                "resumed_cells": len(landed),
                "remaining_cells": len(grid) - len(landed),
            },
            indent=2,
        ),
        file=sys.stderr,
    )

    cells_dir = out.with_name(f"{out.name}.cells")
    cells_dir.mkdir(parents=True, exist_ok=True)
    kept: list[BaselineCell] = list(landed)

    def _persist() -> None:
        atomic_write_json(out, artifact(kept, identity=identity, grid_size=len(grid)))

    def _record_cell(cell: BaselineCell) -> None:
        if cell not in kept:
            kept.append(cell)
        stem = f"{cell.arm}-{cell.record_id}-{cell.repeat}"
        write_json_new(cells_dir / f"{stem}.json", cell.to_row())
        print(
            f"[{len(kept)}/{len(grid)}] {cell.arm}/{cell.record_id}#{cell.repeat} "
            f"{cell.status} reward={cell.reward:.3f} injected={cell.injected_context_chars}",
            file=sys.stderr,
            flush=True,
        )
        _persist()

    agent = ClaudeAnswerAgent(model=args.model, timeout_s=args.timeout_s)
    factory = default_arm_factory(store_path=args.store, mem_bin=args.mem_bin)
    try:
        cells = run_cells(
            records,
            grid,
            arm_factory=factory,
            answer=agent,
            paid=True,
            landed=landed,
            on_cell=_record_cell,
            scope=args.scope,
        )
    except (QuotaHaltError, RigHaltError) as exc:
        _persist()
        print(f"HALT: {exc}", file=sys.stderr)
        print(
            f"{len(kept)} cell(s) kept in {out}; re-run the same command to resume.",
            file=sys.stderr,
        )
        return EXIT_HALT

    atomic_write_json(out, artifact(cells, identity=identity, grid_size=len(grid)))
    report = build_report(cells, repeats=args.repeats)
    report_path = out.with_name(f"{out.stem}-report.json")
    atomic_write_json(report_path, report.to_dict())
    out.with_name(f"{out.stem}-report.md").write_text(report.to_markdown(), encoding="utf-8")
    print(report.to_markdown())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE_DIR)
    parser.add_argument(
        "--arms",
        default=",".join(PUBLIC_ARMS),
        help=f"comma-separated arms (default: {','.join(PUBLIC_ARMS)})",
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument(
        "--scope", default=DEFAULT_SCOPE, help="the D7 retrieval track the `ours` arm replays under"
    )
    parser.add_argument("--store", type=Path, default=None, help="mem store for the `ours` arm")
    parser.add_argument("--mem-bin", type=Path, default=None, help="built mem CLI for `ours`")
    parser.add_argument("--model", default="", help="the model a PAID run executes under")
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="DEFAULT: price the grid and probe the infrastructure; spends nothing",
    )
    parser.add_argument(
        "--fire",
        action="store_true",
        help=(
            "PAID: buy the grid. Separate from --preflight on purpose: pricing and spending "
            "must not be the same keystroke."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "where the run artifact is written; cells land as they are bought and an existing "
            "file here is RESUMED when it matches this run. Required by --fire"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.preflight and args.fire:
        parser.error("--preflight prices and --fire spends; run them one at a time")

    try:
        records = load_release(args.release_dir)
    except PublicBaselineError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_NO_CORPUS

    if not args.fire:
        try:
            return _preflight(args, records)
        except PublicBaselineError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return EXIT_REFUSED

    # Availability first, credentials second: an arm that is not installed here is a
    # blocker no token can clear, and a fire that refused on the token would send the
    # operator after the wrong one.
    try:
        blocked = unavailable_arms(args)
    except PublicBaselineError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if blocked:
        for detail in blocked:
            print(f"REFUSING to run: {detail}", file=sys.stderr)
        return EXIT_REFUSED

    refusal = _refusal(dry_run=False, model=args.model)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED
    if args.out is None:
        print(
            "REFUSING to spend: --out is required. It is the resume artifact, the lock and the "
            "parent of the per-cell evidence; without it a halt loses every cell bought so far "
            "and a re-run re-buys the whole grid.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    try:
        with out_lock(args.out):
            return _fire(args, records)
    except ResumeMismatchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except PublicBaselineError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ARM_REQUIREMENTS",
    "CHARS_PER_TOKEN_ESTIMATE",
    "DEFAULT_RELEASE_DIR",
    "DEFAULT_REPEATS",
    "PUBLIC_ARMS",
    "AgentAnswer",
    "AnswerAgent",
    "ArmPlan",
    "ArmRequirement",
    "BaselineRecord",
    "ClaudeAnswerAgent",
    "PreflightPlan",
    "ProbeResult",
    "PublicBaselineError",
    "admissible_cells",
    "artifact",
    "build_parser",
    "build_prompt",
    "default_arm_factory",
    "grade",
    "grid_keys",
    "load_release",
    "main",
    "price",
    "probe_all",
    "probe_arm",
    "probe_ollama",
    "release_fingerprint",
    "resume_identity",
    "retrieve_for",
    "run_cell",
    "run_cells",
    "unavailable_arms",
]
