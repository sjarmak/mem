"""Export a gated corpus as published ``bench-record.v3`` JSONL.

The published artifact is raw text, never a graph: one record per line, one file per
(origin, tier), plus a ``SHA256SUMS`` over the files. The schema lives outside this
package at ``public/schema/bench-record.v3.schema.json`` and is enforced by the
standalone validator at ``public/validator/membench_validate.py``; this module is
the producer side, and it re-runs the guards that matter before it writes anything,
so a bad record never reaches a file for the validator to catch later.

Four invariants are mechanical here rather than conventional:

* **Real and synthetic are never pooled.** ``write_tier_file`` refuses a record set
  holding more than one ``origin``, or a record whose origin or tier disagrees with
  the file it is being written into. The published file name carries both.
* **Nothing identifying is published.** A pull request, a commit sha, a base
  commit, a repository and a transcript path are not fields of a published record;
  ``assert_publishable`` scans the built payload for those names at every depth and
  raises rather than emitting.
* **No published id states its own role.** Every memory id a record carries is a
  keyed alias minted by ``public_alias``, and nothing published alongside it says
  which alias is gold, which is a distractor and which is stale. The producer holds
  the inverse map; a downloader holds a record whose ``evidence`` answers the
  question and whose ``loo`` block lets it recompute the cut, and nothing else.
* **A project-tier record is cross-session or it is not published.** ``tier`` is a
  claim about scope, so a project task whose whole answer it wrote itself is
  skipped (``NotCrossSessionError``) rather than shipped under a tier it does not
  satisfy. ``ExportReport.skipped`` reports every one, so the drop is accounted.

Synthetic records carry a synthetic clock. A ``BenchmarkSequence`` has an ORDER but
no timestamps, and the published leave-one-out block needs instants, so the record's
candidates are laid on a grid: slot *k* is dated ``SYNTHETIC_EPOCH + k *
SLOT_INTERVAL`` and the boundary is one slot past the last. The epoch is deliberately
a round, obviously-synthetic date: these are positions in a sequence, not a claim
about when anything happened.

The ORDER of that grid is a UNIFORM RANDOM PERMUTATION of the pool, drawn from the
corpus's mint seed. It preserves nothing, supersession included, and preserving
nothing is the point: any regularity in the timestamps — distractors seeded together
just before the boundary, a cross-session gold alone at the earliest instant — is a
label a solver reads off the clock without retrieving anything.

Supersession was preserved until mem-r6yzk R3b, and it was such a label at BOTH ends.
Dating every replaced value before the value that replaced it put the stale bucket at
the early end and kept it out of the late end: P(stale | earliest) ran 0.362 (project)
/ 0.438 (session) against base rates of 0.111 / 0.143, and inside a chain's own group
P(latest-closing is gold) ran 0.237 / 0.325 against 0.167. A policy that answered with
each subject's latest-closing candidate scored 0.263 / 0.267 and the staleness trap
zeroed it on 0 of 80 and 0 of 80 records, which is a trap that never fires. Drawing the
extension backwards (the R2 fix) had flattened the late end and left the early end
alone; a permutation closes both.

What that costs is stated in the release README rather than papered over: the published
timeline is positional, not evidential, so an arm that ranks by recency sits at chance.
Staleness is recoverable only from what the memories SAY, which is the thing the
benchmark is for.

ZFC: every field below is a mechanical projection of an object the generators
already authored. No classification, no scoring, no thresholded judgment. The one
random draw is a permutation, seeded and reproducible, not a decision.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from membench.generators.enterprise_workflow import fact_value
from membench.generators.necessity_sweep import NECESSITY_FILE, SEQUENCES_FILE
from membench.generators.world_manifest import read_manifest, verify_world
from membench.grading.leak_guard import IDENTIFYING_KEYS
from membench.metrics.scorers import states_value
from membench.public_alias import AliasMap, load_alias_map
from membench.public_clock import ClockDeal, GroupShape, shapes_of, slot_order, subject_groups
from membench.schemas.sequence import BenchmarkSequence, SequenceStep
from membench.schemas.world import EnterpriseWorld
from membench.validity import (
    QueryWork,
    WorkRef,
    canonical_ts,
    is_sibling,
    loo_bounded,
    supersedes_closure,
)

PUBLIC_SCHEMA_VERSION = "bench-record.v3"
# Must equal ``public/validator/membench_validate.py``'s VALIDATOR_VERSION; the
# parity test asserts it, so a validator bump cannot silently leave published
# records claiming the old one cleared them.
PUBLIC_VALIDATOR_VERSION = "membench-validate.v3"
EXPORTER_VERSION = "public-export.v4"

# The synthetic clock. Round and obviously not a real date.
SYNTHETIC_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)
# One published candidate per slot, so every non-null ``closed`` in a record is
# distinct and the grid carries no "these were written together" grouping.
SLOT_INTERVAL = timedelta(seconds=60)

# Field names a published record must not carry, at any depth. The first three are
# ``leak_guard.IDENTIFYING_KEYS`` (the single source of truth for "answer-revealing"),
# the rest are the corpus-locating fields a public record has no business carrying.
FORBIDDEN_FIELD_NAMES: tuple[str, ...] = (*IDENTIFYING_KEYS, "repo", "jsonl_path")

SHA256SUMS_FILE = "SHA256SUMS"

# The agent-readable path prefix of the stale-value bucket. A superseded text states
# a forbidden value BY CONSTRUCTION - that is what makes it a trap - so the leak scan
# holds it to the expected values only; see ``find_answer_leaks``.
SUPERSEDED_PREFIX = "evidence.superseded."

Origin = Literal["synthetic", "real"]


class PooledOriginError(ValueError):
    """Raised when a publication would put more than one origin, or more than one
    tier, into a single file. Real and synthetic results are never pooled, and the
    refusal is mechanical rather than a naming convention."""


class ForbiddenFieldError(ValueError):
    """Raised when a built payload carries a field a published record must not have."""

    def __init__(self, record_id: str, paths: Sequence[str]) -> None:
        self.record_id = record_id
        self.paths = tuple(paths)
        super().__init__(f"record {record_id!r} carries forbidden field(s): {', '.join(paths)}")


class AnswerLeakError(ValueError):
    """Raised when an answer value appears in a field the agent can read before it
    retrieves anything. Such a record measures no memory at all."""

    def __init__(self, record_id: str, offenders: Sequence[tuple[str, str]]) -> None:
        self.record_id = record_id
        self.offenders = tuple(offenders)
        detail = ", ".join(f"{value!r} in {where}" for where, value in offenders)
        super().__init__(f"record {record_id!r} leaks an answer value: {detail}")


class UnestablishedMemoryError(ValueError):
    """Raised when a record's gold names memory that NOTHING in its world ever wrote.

    The cross-session case is not this error: a project task reads what an earlier
    sequence of its world established, and the exporter is handed that context. This
    fires only when the text exists nowhere in the world, which makes the record
    unanswerable at any scope and is a generator defect, not a scope question."""


class NotCrossSessionError(ValueError):
    """Raised when a project-tier sequence establishes its whole own answer.

    ``tier`` is a published claim about the scope an answer spans. A project task
    that needs nothing an earlier session wrote is a session task wearing a project
    label, and publishing it would let a session-scoped retriever score the
    project tier. It is skipped, and ``ExportReport.skipped`` says so."""


def iso_utc(moment: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SS.sssZ`` - the one published spelling of an instant,
    matching ``validity.canonical_ts``'s output so the cut is a sound string
    comparison."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_sha256(payload: Any) -> str:
    """SHA-256 over canonical JSON (sorted keys), the same hashing the world
    manifest uses, so a record's ``source_sha256`` is stable across serializer
    key-order changes."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def forbidden_fields(node: Any, path: str = "") -> list[str]:
    """Every path at which a forbidden field name appears, at any depth. Names are
    matched EXACTLY, so ``pr_key`` (an opaque equality token) is not a hit."""
    found: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key in FORBIDDEN_FIELD_NAMES:
                found.append(here)
            found.extend(forbidden_fields(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(forbidden_fields(value, f"{path}[{index}]"))
    return found


def agent_readable(payload: Mapping[str, Any]) -> dict[str, str]:
    """What the agent can read before retrieving: the question, every distractor
    seeded into the pool, and every stale value the pool carries.

    Gold text is excluded on purpose - the gold IS the answer, and having to retrieve
    it is the whole task. The distractor and superseded texts are in because they are
    IN THE POOL: an arm that retrieves indiscriminately hands them to the agent, so
    anything they state is something the agent can read without having retrieved the
    right thing."""
    readable = {"question.text": str(payload["question"]["text"])}
    for memory_id, text in payload["evidence"]["distractors"].items():
        readable[f"evidence.distractors.{memory_id}"] = str(text)
    for memory_id, text in payload["evidence"]["superseded"].items():
        readable[f"{SUPERSEDED_PREFIX}{memory_id}"] = str(text)
    return readable


def find_answer_leaks(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every ``(where, value)`` answer value that a field the agent reads BEFORE
    retrieving already states.

    The match is ``metrics.scorers.states_value``: word-boundary anchored, the exact
    rule the answer itself is graded by. ``leak_guard``'s scan is a case-insensitive
    substring, which is right for a high-entropy identifier but wrong here - an
    authored value is a short enum token, and ``v2`` is a substring of the authored
    subject ``checkout_v2 feature flag state`` without the question stating ``v2`` at
    all. Grading and this guard use one rule, so a record this guard clears is a
    record whose question does not score itself.

    The superseded bucket is held to the EXPECTED values only. A stale text states a
    forbidden value by construction - being the wrong answer in the pool is its whole
    job - so scanning it for forbidden values would refuse every well-formed record.
    It is still scanned for expected values: a stale text that states the current
    answer hands it over, which is a real leak and a generator defect."""
    expected = [value for value in dict.fromkeys(payload["answer"]["expected_values"]) if value]
    forbidden = [value for value in dict.fromkeys(payload["answer"]["forbidden_values"]) if value]
    both = list(dict.fromkeys([*expected, *forbidden]))
    offenders: list[tuple[str, str]] = []
    for where, content in agent_readable(payload).items():
        scan = expected if where.startswith(SUPERSEDED_PREFIX) else both
        offenders.extend(
            (where, value) for value in scan if value.strip() and states_value(content, value)
        )
    return offenders


def assert_publishable(payload: Mapping[str, Any]) -> None:
    """Run the producer-side guards over a built payload. Raises rather than
    emitting: a record that fails here must never reach a file."""
    record_id = str(payload["record_id"])
    paths = forbidden_fields(payload)
    if paths:
        raise ForbiddenFieldError(record_id, paths)
    offenders = find_answer_leaks(payload)
    if offenders:
        raise AnswerLeakError(record_id, offenders)


@dataclass(frozen=True)
class PublicRecord:
    """One publishable record: its routing fields, plus the payload written verbatim."""

    record_id: str
    origin: Origin
    tier: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Established:
    """One memory a world wrote, and what writing it means for the published record.

    ``sequence_id`` is what makes the cross-session check possible: a gold id whose
    establishing sequence is not the record's own is memory an earlier SESSION wrote,
    which is the property the project tier claims."""

    memory_id: str
    text: str
    sequence_id: str


def _goal_step(sequence: BenchmarkSequence) -> SequenceStep:
    """The step the published question is taken from: the last one, which is the
    only step carrying an outcome check in every materialised sequence."""
    if not sequence.steps:
        raise ValueError(f"sequence {sequence.sequence_id!r} has no steps")
    goal = sequence.steps[-1]
    if not goal.outcome_checks:
        raise ValueError(
            f"sequence {sequence.sequence_id!r} has no outcome check on its final step; "
            "there is no graded question to publish"
        )
    return goal


def world_memories(sequences: Sequence[BenchmarkSequence]) -> dict[str, Established]:
    """Every memory the given sequences establish, id -> what established it.

    The sequences are the record's world in order: the context (the earlier sequences
    of the same world) followed by the record's own. What a step declares superseded is
    read by the runner as the staleness annotation it grades against; it is not read
    here, because the published close order no longer depends on supersession (see the
    module docstring).
    """
    established: dict[str, Established] = {}
    for sequence in sequences:
        for step in sequence.steps:
            for memory_id, text in step.expected_memory_writes.items():
                prior = established.get(memory_id)
                if prior is not None:
                    raise ValueError(
                        f"memory {memory_id!r} is established twice in one world "
                        f"({prior.sequence_id} and {sequence.sequence_id}); which text it "
                        "holds at the cut, and which session established it, would both "
                        "be ambiguous"
                    )
                established[memory_id] = Established(
                    memory_id=memory_id,
                    text=text,
                    sequence_id=sequence.sequence_id,
                )
    return established


def _answer(
    goal: SequenceStep, gold_ids: Sequence[str], texts: Mapping[str, str]
) -> dict[str, Any]:
    """The graded answer, read off the goal's own outcome check rather than re-derived.

    A tool-requiring goal grades the tool call's arguments and nothing else, so its
    expected and forbidden values are the action's. A text goal grades the stated
    values, which are the values its gold facts carry."""
    check = goal.outcome_checks[0]
    if check.requires_action:
        action = check.requires_action[0]
        return {
            "expected_values": list(action.arg_values),
            "forbidden_values": list(action.forbidden_values),
            "check_kind": "tool-call",
        }
    return {
        "expected_values": [fact_value(texts[memory_id]) for memory_id in gold_ids],
        "forbidden_values": list(check.forbidden_values),
        "check_kind": "value-set",
    }


@dataclass(frozen=True)
class _Timeline:
    """Where each pool member sits on the synthetic clock, and where the cut falls."""

    closed: Mapping[str, str]
    boundary: str


def _timeline(order: Sequence[str]) -> _Timeline:
    """Lay a finished close ORDER on the synthetic grid, one member per slot.

    The order itself is built by ``public_clock.slot_order``: a uniform interleaving of
    the pool's subject groups, each group internally seated on the corpus-wide balanced
    deal. It used to be a bare uniform shuffle of the pool, and before that a linear
    extension of the supersession partial order; the module docstring records the answer
    keys the extension handed a solver, and ``public_clock`` records the one the bare
    shuffle handed a solver by publishing its own draw.

    The boundary is one slot past the last member, so every candidate closes strictly
    before the question is asked and the published pool is the whole eligible set."""
    if len(order) != len(set(order)):
        raise ValueError("two pool members would take the same slot on the published clock")
    closed = {
        memory_id: iso_utc(SYNTHETIC_EPOCH + index * SLOT_INTERVAL)
        for index, memory_id in enumerate(order)
    }
    return _Timeline(closed=closed, boundary=iso_utc(SYNTHETIC_EPOCH + len(order) * SLOT_INTERVAL))


def _candidate(memory_id: str, closed: str | None) -> dict[str, Any]:
    """One leave-one-out candidate, in the shape the cut reads and nothing more.

    A synthetic memory entry hangs under no convoy, change, branch or epic, so every
    sibling key is null and axes 1-5 never fire on this corpus; the fields are
    published anyway so one shape serves both origins.

    There is no ``supersedes`` field. It was published in ``bench-record.v1`` and it
    was an answer key: the ids a candidate supersedes are exactly the stale ones, so
    the chain heads - the gold - could be read straight off the block with the
    evidence deleted. The cut does not need it here (a goal step supersedes nothing,
    so the query's closure is always empty), and ``_exclusion_axes`` now RAISES if the
    axis ever fires rather than quietly publishing an attribution the schema no longer
    enumerates."""
    return {
        "id": memory_id,
        "closed": closed,
        "convoy_key": None,
        "pr_key": None,
        "external_ref_key": None,
        "parent_key": None,
    }


@dataclass(frozen=True)
class _Loo:
    """A built leave-one-out block and the eligible pool it implies."""

    block: dict[str, Any]
    pool: list[str]


def _work_ref(candidate: Mapping[str, Any], rig: str) -> WorkRef:
    """A published candidate, read back as the LOO projection ``membench.validity``
    operates on. The two key spaces line up by construction: ``convoy_key`` and
    ``parent_key`` are work ids in the clear, and ``pr_key`` / ``external_ref_key``
    are equality tokens, which is all those axes ever compare."""
    return WorkRef(
        work_id=str(candidate["id"]),
        rig=rig,
        closed=candidate["closed"],
        convoy_id=candidate["convoy_key"],
        pr=candidate["pr_key"],
        external_ref=candidate["external_ref_key"],
        supersedes=(),
        parent=candidate["parent_key"],
    )


def _exclusion_axes(ref: WorkRef, query: QueryWork, chain: set[str], boundary: str) -> list[str]:
    """Why `ref` was withheld, by axis. Empty when it is eligible.

    The predicates are the ones ``validity._is_eligible`` applies, in its order, so
    the published attribution cannot disagree with the set it attributes. Two of them
    cannot fire on a published synthetic record, and both raise rather than return an
    axis the schema does not enumerate."""
    axes: list[str] = []
    if ref.closed is None or canonical_ts(ref.closed) >= boundary:
        axes.append("temporal")
    if ref.work_id == query.work_id:
        axes.append("self")
    if ref.work_id in chain:
        # The published candidate carries no supersession edges, so the query's
        # closure is empty and this axis cannot fire. Reaching here means the
        # candidate builder started publishing edges again - which is the leak
        # ``_candidate`` removed - and the axis is no longer in the schema's
        # vocabulary to report.
        raise ValueError(
            f"candidate {ref.work_id!r} is in the supersession closure of "
            f"{query.work_id!r}, which a published candidate can no longer express; "
            "publishing supersession edges is what bench-record.v2 removed"
        )
    if is_sibling(ref, query):
        # A synthetic candidate names no convoy, change, branch or epic, so no
        # sibling axis can fire on this path. Reaching here means the candidate
        # builder started populating those keys without this function learning to
        # name WHICH axis fired, and a published record must never carry an axis
        # label the schema does not enumerate.
        raise ValueError(
            f"candidate {ref.work_id!r} is excluded as a sibling of {query.work_id!r}, "
            "which the synthetic producer does not construct; name the specific axis "
            "(validity.SIBLING_AXES) before publishing it"
        )
    return axes


def _loo(
    goal: SequenceStep,
    *,
    timeline: _Timeline,
    pool_ids: Sequence[str],
    rig: str,
    aliases: AliasMap,
) -> _Loo:
    """The published leave-one-out block, in published (aliased) ids.

    The query is the goal step itself, never closed, so it is excluded on both the
    temporal and self axes - which is why ``excluded_ids`` is never empty.

    The excluded set is not asserted here, it is COMPUTED - by ``validity.loo_bounded``,
    the same function the harness cuts with - so the published block and the harness
    cannot drift apart. Candidates are emitted in ALIAS order: the order they were
    built in is the order they close in, and publishing both would say twice what the
    ``closed`` grid already says once.
    """
    candidates = [
        _candidate(aliases.alias(memory_id), timeline.closed[memory_id]) for memory_id in pool_ids
    ]
    query_id = aliases.alias(goal.step_id)
    candidates.append(_candidate(query_id, None))
    candidates.sort(key=lambda candidate: str(candidate["id"]))

    refs = [_work_ref(candidate, rig) for candidate in candidates]
    query = QueryWork(work_id=query_id, rig=rig, started=timeline.boundary)
    eligible = {ref.work_id for ref in loo_bounded(refs, query)}
    chain = supersedes_closure(refs, query.work_id)
    cut = canonical_ts(timeline.boundary)

    excluded: list[str] = []
    axes: set[str] = set()
    for ref in refs:
        if ref.work_id in eligible:
            continue
        excluded.append(ref.work_id)
        axes.update(_exclusion_axes(ref, query, chain, cut))

    block = {
        "boundary": timeline.boundary,
        "excluded_ids": sorted(set(excluded)),
        "exclusion_axes": sorted(axes),
        "query": {
            "id": query_id,
            "convoy_key": None,
            "pr_key": None,
            "external_ref_key": None,
            "parent_key": None,
        },
        "candidates": candidates,
    }
    return _Loo(block=block, pool=sorted(eligible))


def _scope_id(
    sequence: BenchmarkSequence,
    goal: SequenceStep,
    world: EnterpriseWorld,
    aliases: AliasMap,
) -> str:
    """The id of the scope the answer must span: the step for a prompt-tier record,
    the sequence for a session-tier one, the world for a project-tier one. A step id
    is published as its alias, because the step is also the LOO query."""
    if sequence.tier == "prompt":
        return aliases.alias(goal.step_id)
    if sequence.tier == "project":
        return world.world_id
    return sequence.sequence_id


def pool_order(record_id: str, ids: Iterable[str]) -> list[str]:
    """The NORMATIVE order ``candidate_pool.ids`` is published in.

    A published pool has to ship in some order, and every order is a statement. The
    first release shipped it sorted by alias, which is pseudorandom with respect to
    role for any one memory but is the SAME draw every time that memory appears. A
    handful of low-alias memories that happen to be gold are then counted once per
    record that carries them, and the corpus inherits their accident: on the release
    where this was found, the first alias was gold in 47 records of 160. An arm seeded
    in that order and cut at its head starts above chance for a reason that has nothing
    to do with retrieval.

    Whether that accident SHOWS is itself a draw, and this release is the proof. Its
    mint is a different one, and the same alias rule now reads 35 records of 160
    (0.2188) against a per-record chance of 0.2017, p = 0.3191 over 20,000 permutation
    draws - a clean figure that a re-draw could take away again. So the order is not
    chosen on a measurement: it is drawn per record, HMAC-SHA256 keyed by the record id
    over the alias, ascending, with the alias itself as the tie-break so the order is
    total. Keying by the record id is what removes the SHARING - one memory gets an
    independent draw in every record it appears in, so no memory's accident can
    accumulate across the corpus whatever the mint. The same measurement over this
    order reads 41 in 160 (0.2562) at p = 0.0546.

    Both figures are recomputed from the released tree by ``tests/public_figures.py``
    and asserted against this docstring, so a re-drawn corpus cannot leave them here
    to rot.

    It takes no key material. The record id and the aliases are both published, so a
    downloader recomputes this order from the file in front of them, and
    ``membench_validate.pool_order`` is the vendored copy that does exactly that
    before it will accept a record."""
    return sorted(
        ids,
        key=lambda alias: (
            hmac.new(record_id.encode("utf-8"), alias.encode("utf-8"), hashlib.sha256).hexdigest(),
            alias,
        ),
    )


def _pool_ids(goal: SequenceStep, gold_ids: Sequence[str], sequence_id: str) -> list[str]:
    """The record's whole candidate set: its gold, its distractors, its stale values.

    Published as the pool AND as the three evidence buckets, which is what makes a
    released record self-contained - the harness seeds an arm from the buckets and
    the validator recomputes the cut from the pool, and the two are the same set.

    The three roles must be disjoint. An id that is both gold and a distractor would
    be graded as the answer and seeded as a trap, so it is refused rather than
    silently resolved in one direction."""
    distractor_ids = list(goal.distractor_memories)
    superseded_ids = list(goal.superseded_memory_ids)
    for left_name, left, right_name, right in (
        ("gold", gold_ids, "distractors", distractor_ids),
        ("gold", gold_ids, "superseded", superseded_ids),
        ("distractors", distractor_ids, "superseded", superseded_ids),
    ):
        overlap = sorted(set(left) & set(right))
        if overlap:
            raise ValueError(
                f"sequence {sequence_id!r} publishes {overlap} as both {left_name} and "
                f"{right_name}; a candidate has one role or the record grades its own trap"
            )
    return [*gold_ids, *distractor_ids, *superseded_ids]


@dataclass(frozen=True)
class _Publishable:
    """One sequence resolved to the candidate set it would publish, clock aside.

    Split out of ``sequence_to_record`` because the clock is no longer a per-record
    decision: the balanced deal is taken over every group the CORPUS publishes, so the
    exporter has to know each record's subject groups before it can build any record.
    Pure and deterministic, so ``_build`` resolving a sequence to decide whether it is
    publishable and ``sequence_to_record`` resolving it again to build it cannot drift
    - there is one implementation of "what does this sequence publish"."""

    goal: SequenceStep
    texts: dict[str, str]
    gold_ids: list[str]
    foreign: list[str]
    pool_ids: list[str]
    groups: dict[str, list[str]]

    def shapes(self, sequence: BenchmarkSequence) -> list[GroupShape]:
        return shapes_of(
            sequence.sequence_id,
            sequence.tier,
            self.groups,
            self.goal.superseded_memory_ids,
        )


def _publishable(
    sequence: BenchmarkSequence, *, context: Sequence[BenchmarkSequence]
) -> _Publishable:
    """What ``sequence`` publishes, or the refusal that stops it publishing at all.

    Raises ``UnestablishedMemoryError`` when a gold or stale id has no text anywhere in
    the world, and ``NotCrossSessionError`` when a project-tier record would answer
    entirely from its own sequence."""
    goal = _goal_step(sequence)
    established = world_memories([*context, sequence])
    texts = {memory_id: entry.text for memory_id, entry in established.items()}
    gold_ids = list(goal.outcome_checks[0].requires_memory)
    if not gold_ids:
        raise ValueError(f"sequence {sequence.sequence_id!r} requires no memory at all")
    missing = [memory_id for memory_id in gold_ids if memory_id not in texts]
    if missing:
        raise UnestablishedMemoryError(
            f"sequence {sequence.sequence_id!r} requires memory it never establishes: "
            f"{missing}; nothing in its world writes that text, so the record is "
            "unanswerable at any scope"
        )

    foreign = [
        memory_id
        for memory_id in gold_ids
        if established[memory_id].sequence_id != sequence.sequence_id
    ]
    if sequence.tier == "project" and not foreign:
        raise NotCrossSessionError(
            f"sequence {sequence.sequence_id!r} is tier 'project' but establishes every "
            "one of its own gold facts; a session-scoped retriever would score it, so "
            "publishing it under the project tier would overstate what the tier measures"
        )

    pool_ids = _pool_ids(goal, gold_ids, sequence.sequence_id)
    stale_without_text = [
        memory_id for memory_id in goal.superseded_memory_ids if memory_id not in texts
    ]
    if stale_without_text:
        raise UnestablishedMemoryError(
            f"sequence {sequence.sequence_id!r} names superseded memory "
            f"{stale_without_text} that its world never wrote; the stale value cannot "
            "be published, and a pool without it cannot trap an answer that states it"
        )

    # Gold and stale texts are what the world wrote; a distractor is authored on the
    # goal step and never recorded, so the pool's text map is the union. It is built
    # here rather than at the payload, because the subject partition the clock is
    # balanced inside is a partition of the WHOLE pool.
    pool_texts = {**texts, **goal.distractor_memories}
    textless = [memory_id for memory_id in pool_ids if memory_id not in pool_texts]
    if textless:
        raise UnestablishedMemoryError(
            f"sequence {sequence.sequence_id!r} pools {textless} with no text anywhere; "
            "a candidate with no sentence names no subject, so it belongs to no group"
        )

    return _Publishable(
        goal=goal,
        texts=texts,
        gold_ids=gold_ids,
        foreign=foreign,
        pool_ids=pool_ids,
        groups=subject_groups(pool_ids, pool_texts),
    )


def sequence_to_record(
    sequence: BenchmarkSequence,
    *,
    world: EnterpriseWorld,
    seed: int,
    generator_version: str,
    necessity: Mapping[str, Any],
    epsilon: float,
    reference_agent: str,
    aliases: AliasMap,
    context: Sequence[BenchmarkSequence] = (),
    origin: Origin = "synthetic",
    deal: ClockDeal | None = None,
) -> PublicRecord:
    """Project one materialised sequence onto a publishable ``bench-record.v3``.

    ``necessity`` is the sweep artifact's row for this sequence. ``context`` is the
    earlier sequences of the same world, in world order - the same prefix
    ``necessity_sweep.context_for`` pilots a project-tier candidate under, so the
    published record has the scope its necessity verdict was measured at.

    ``deal`` is the corpus-wide balanced clock ``_build`` takes over every group the
    export publishes. Omitted, the record is dealt as a corpus of ONE - which is what a
    single-record test has, and which balances nothing, because a corpus of one has no
    realised rate to balance. Every published release goes through ``_build``.

    Raises ``UnestablishedMemoryError`` when a gold id has no text anywhere in the
    world, ``NotCrossSessionError`` when a project-tier record would answer entirely
    from its own sequence, and ``assert_publishable`` raises on a forbidden field or
    an answer leak."""
    plan = _publishable(sequence, context=context)
    goal, texts = plan.goal, plan.texts
    gold_ids, foreign, pool_ids = plan.gold_ids, plan.foreign, plan.pool_ids
    if deal is None:
        deal = ClockDeal(plan.shapes(sequence), mint_seed=aliases.mint_seed)

    rng = random.Random(f"{aliases.mint_seed}\x00{sequence.sequence_id}")
    timeline = _timeline(
        slot_order(
            pool_ids,
            record_id=sequence.sequence_id,
            groups=plan.groups,
            gold_ids=gold_ids,
            stale_ids=goal.superseded_memory_ids,
            deal=deal,
            rng=rng,
        )
    )
    published_gold = sorted(gold_ids, key=aliases.alias)
    loo = _loo(
        goal,
        timeline=timeline,
        pool_ids=pool_ids,
        rig=world.world_id,
        aliases=aliases,
    )
    payload: dict[str, Any] = {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "record_id": sequence.sequence_id,
        "origin": origin,
        "tier": sequence.tier,
        "question": {
            "text": goal.user_request,
            "asked_at": loo.block["boundary"],
            "scope_id": _scope_id(sequence, goal, world, aliases),
        },
        "evidence": {
            "gold": {aliases.alias(memory_id): texts[memory_id] for memory_id in published_gold},
            "gold_ids": [aliases.alias(memory_id) for memory_id in published_gold],
            "distractors": {
                aliases.alias(memory_id): text
                for memory_id, text in sorted(
                    goal.distractor_memories.items(), key=lambda item: aliases.alias(item[0])
                )
            },
            "superseded": {
                aliases.alias(memory_id): texts[memory_id]
                for memory_id in sorted(goal.superseded_memory_ids, key=aliases.alias)
            },
            "superseded_ids": sorted(aliases.alias(mid) for mid in goal.superseded_memory_ids),
        },
        "candidate_pool": {"ids": pool_order(sequence.sequence_id, loo.pool)},
        "answer": _answer(goal, published_gold, texts),
        "necessity": {
            "oracle_reward": float(necessity["oracle_reward"]),
            "no_memory_reward": float(necessity["no_memory_reward"]),
            "delta": float(necessity["delta"]),
            "epsilon": epsilon,
            "accepted": bool(necessity["accepted"]),
            "reason": str(necessity["reason"]),
            "reference_agent": reference_agent,
        },
        "loo": loo.block,
        "provenance": {
            "generator_version": generator_version,
            "exporter_version": EXPORTER_VERSION,
            "seed": seed,
            "world_id": world.world_id,
            "sequence_id": sequence.sequence_id,
            # WHICH gold ids a different sequence of this world wrote, not how many.
            # A count is a scalar a downloader can try to re-derive from the rest of
            # the record; the ids are the same claim stated so the validator can check
            # it against the corpus (v2 shipped the count, and the gold set size was
            # that count plus a constant on 160 of 160 records). Aliased and sorted by
            # alias like every other published id list, so it carries no mint order.
            "cross_session_gold_ids": [
                aliases.alias(memory_id) for memory_id in sorted(foreign, key=aliases.alias)
            ],
            "source_sha256": canonical_sha256(sequence.model_dump(mode="json")),
        },
        "leak_guard": {
            "validator_version": PUBLIC_VALIDATOR_VERSION,
            "identifying_keys_checked": list(IDENTIFYING_KEYS),
        },
    }
    assert_publishable(payload)
    return PublicRecord(
        record_id=sequence.sequence_id,
        origin=origin,
        tier=sequence.tier,
        payload=payload,
    )


def tier_filename(origin: str, tier: str) -> str:
    """The published file name. It carries BOTH the origin and the tier, so a pooled
    file has no name it could be written under."""
    return f"{origin}-{tier}.jsonl"


def write_tier_file(
    out_dir: str | Path,
    records: Sequence[PublicRecord],
    *,
    origin: str,
    tier: str,
) -> Path:
    """Write one origin's one tier as JSONL, refusing anything pooled.

    Raises ``PooledOriginError`` when the record set holds more than one origin or
    more than one tier, or when either disagrees with the file being written."""
    if not records:
        raise ValueError(f"nothing to publish for {origin}/{tier}")
    origins = sorted({record.origin for record in records})
    tiers = sorted({record.tier for record in records})
    if len(origins) > 1:
        raise PooledOriginError(
            f"refusing to write {origins} into one file: real and synthetic records "
            "are never published together"
        )
    if len(tiers) > 1:
        raise PooledOriginError(f"refusing to write tiers {tiers} into one file: one file per tier")
    if origins[0] != origin:
        raise PooledOriginError(f"records are origin {origins[0]!r}, file is {origin!r}")
    if tiers[0] != tier:
        raise PooledOriginError(f"records are tier {tiers[0]!r}, file is {tier!r}")

    target = Path(out_dir) / tier_filename(origin, tier)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record.payload, sort_keys=True, ensure_ascii=False) for record in records]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def write_sha256sums(out_dir: str | Path, paths: Sequence[Path]) -> Path:
    """Write ``SHA256SUMS`` over the published files, coreutils format, sorted by
    name so the file is reproducible."""
    base = Path(out_dir)
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(paths, key=lambda p: p.name)
    ]
    target = base / SHA256SUMS_FILE
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


@dataclass(frozen=True)
class ExportReport:
    """What one export produced, including what it deliberately did not publish."""

    corpus_dir: Path
    out_dir: Path
    files: tuple[Path, ...]
    counts: Mapping[str, int]
    sha256sums: Path
    n_candidates: int
    n_published: int
    skipped: tuple[tuple[str, str], ...] = ()


def _load_world(world_dir: Path) -> tuple[EnterpriseWorld, list[BenchmarkSequence]]:
    world = EnterpriseWorld.model_validate_json(
        (world_dir / "world.json").read_text(encoding="utf-8")
    )
    raw = json.loads((world_dir / SEQUENCES_FILE).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{world_dir / SEQUENCES_FILE} is not a list of sequences")
    return world, [BenchmarkSequence.model_validate(item) for item in raw]


def _corpus_aliases(corpus_dir: Path, aliases: AliasMap | None) -> AliasMap:
    """The mint a corpus publishes under: the one passed in, or the corpus's own.

    There is no fallback that mints on the fly. A mint generated at export time would
    be a different map on every run, so two exports of one corpus would publish
    unrelated id spaces and no holder of an earlier release could resolve a later
    one."""
    if aliases is not None:
        return aliases
    return load_alias_map(corpus_dir.name)


def _build(
    corpus_dir: Path,
    *,
    origin: Origin,
    aliases: AliasMap,
) -> tuple[list[PublicRecord], list[tuple[str, str]]]:
    """Every publishable record of a gated corpus, plus what was skipped and why."""
    artifact_path = corpus_dir / NECESSITY_FILE
    if not artifact_path.is_file():
        raise FileNotFoundError(
            f"{artifact_path} not found: gate the corpus first (membench gate-corpus {corpus_dir})"
        )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    rows = {row["sequence_id"]: row for row in artifact["per_sequence"]}
    epsilon = float(artifact["epsilon"])
    reference_agent = str(artifact["reference_agent"])

    admitted: list[tuple[BenchmarkSequence, dict[str, Any]]] = []
    shapes: list[GroupShape] = []
    skipped: list[tuple[str, str]] = []
    world_dirs = sorted(d for d in corpus_dir.iterdir() if (d / SEQUENCES_FILE).is_file())
    for world_dir in world_dirs:
        result = verify_world(world_dir)
        if not result.ok:
            raise ValueError(
                f"{world_dir} does not reproduce its manifest: {'; '.join(result.mismatches)}; "
                "publishing it would ship records whose provenance names a world that "
                "cannot be re-derived"
            )
        manifest = read_manifest(world_dir)
        world, sequences = _load_world(world_dir)
        for index, sequence in enumerate(sequences):
            row = rows.get(sequence.sequence_id)
            if row is None:
                raise ValueError(
                    f"sequence {sequence.sequence_id!r} has no row in {artifact_path}; "
                    "the gate artifact is stale for this corpus"
                )
            if not row["accepted"]:
                skipped.append((sequence.sequence_id, "necessity gate rejected it"))
                continue
            try:
                plan = _publishable(sequence, context=sequences[:index])
            except NotCrossSessionError as exc:
                skipped.append((sequence.sequence_id, str(exc)))
                continue
            shapes.extend(plan.shapes(sequence))
            admitted.append(
                (
                    sequence,
                    {
                        "world": world,
                        "seed": manifest.seed,
                        "generator_version": manifest.workflow_generator_version,
                        "necessity": row,
                        "context": sequences[:index],
                    },
                )
            )

    if not admitted:
        raise ValueError(f"no admitted sequence to publish under {corpus_dir}")

    # The clock is dealt over the WHOLE export, not per record: balancing a group's
    # within-group gold slot is only meaningful against the population of groups it
    # shares a tier, a size and a stale count with. Which is why the pass above resolves
    # every sequence before the pass below builds any record.
    deal = ClockDeal(shapes, mint_seed=aliases.mint_seed)
    records = [
        sequence_to_record(
            sequence,
            epsilon=epsilon,
            reference_agent=reference_agent,
            aliases=aliases,
            origin=origin,
            deal=deal,
            **build,
        )
        for sequence, build in admitted
    ]
    return records, skipped


def build_records(
    corpus_dir: str | Path,
    *,
    origin: Origin = "synthetic",
    aliases: AliasMap | None = None,
) -> list[PublicRecord]:
    """Every publishable record of a gated corpus, in corpus order.

    Only sequences the necessity sweep ADMITTED are published; a rejected sequence
    measures nothing about memory and must not ship. Every world is re-verified
    against its manifest first. Raises ``FileNotFoundError`` when the corpus has not
    been gated - publishing an ungated corpus would ship records with no necessity
    verdict to state - or when it has no mint to publish ids under."""
    base = Path(corpus_dir)
    if not base.is_dir():
        raise FileNotFoundError(f"corpus dir does not exist: {base}")
    records, _ = _build(base, origin=origin, aliases=_corpus_aliases(base, aliases))
    return records


def export_corpus(
    corpus_dir: str | Path,
    *,
    out_dir: str | Path,
    origin: Origin = "synthetic",
    aliases: AliasMap | None = None,
) -> ExportReport:
    """Export a gated corpus to ``<out_dir>/<origin>-<tier>.jsonl`` plus SHA256SUMS."""
    base = Path(corpus_dir)
    if not base.is_dir():
        raise FileNotFoundError(f"corpus dir does not exist: {base}")
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    records, skipped = _build(base, origin=origin, aliases=_corpus_aliases(base, aliases))

    by_tier: dict[str, list[PublicRecord]] = {}
    for record in records:
        by_tier.setdefault(record.tier, []).append(record)

    files = tuple(
        write_tier_file(target_dir, by_tier[tier], origin=origin, tier=tier)
        for tier in sorted(by_tier)
    )
    sums = write_sha256sums(target_dir, files)
    artifact = json.loads((base / NECESSITY_FILE).read_text(encoding="utf-8"))
    return ExportReport(
        corpus_dir=base,
        out_dir=target_dir,
        files=files,
        counts={tier: len(by_tier[tier]) for tier in sorted(by_tier)},
        sha256sums=sums,
        n_candidates=int(artifact["n_candidates"]),
        n_published=len(records),
        skipped=tuple(skipped),
    )


def published_ids(payload: Mapping[str, Any]) -> Iterable[str]:
    """Every memory id a built payload publishes, for a test that wants to assert the
    whole id surface is minted rather than spot-check three fields."""
    evidence = payload["evidence"]
    yield from evidence["gold_ids"]
    yield from evidence["distractors"]
    yield from evidence["superseded_ids"]
    yield from payload["candidate_pool"]["ids"]
    yield from (str(candidate["id"]) for candidate in payload["loo"]["candidates"])
    yield from payload["loo"]["excluded_ids"]
    yield str(payload["loo"]["query"]["id"])


__all__ = [
    "EXPORTER_VERSION",
    "FORBIDDEN_FIELD_NAMES",
    "PUBLIC_SCHEMA_VERSION",
    "PUBLIC_VALIDATOR_VERSION",
    "SHA256SUMS_FILE",
    "SLOT_INTERVAL",
    "SUPERSEDED_PREFIX",
    "SYNTHETIC_EPOCH",
    "AnswerLeakError",
    "Established",
    "ExportReport",
    "ForbiddenFieldError",
    "NotCrossSessionError",
    "PooledOriginError",
    "PublicRecord",
    "UnestablishedMemoryError",
    "agent_readable",
    "assert_publishable",
    "build_records",
    "canonical_sha256",
    "export_corpus",
    "find_answer_leaks",
    "forbidden_fields",
    "iso_utc",
    "pool_order",
    "published_ids",
    "sequence_to_record",
    "tier_filename",
    "world_memories",
    "write_sha256sums",
    "write_tier_file",
]
