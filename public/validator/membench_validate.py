"""Standalone validator for the published membench bench-record JSONL files.

This file is deliberately self-contained. It imports the Python standard library
and ``jsonschema``, and nothing else. It does not import ``membench``, because a
public consumer cannot get ``membench`` without the rest of the harness: importing
even a leaf module such as ``membench.grading.leak_guard`` executes
``membench/grading/__init__.py``, which eagerly pulls nine sibling modules and,
through them, pydantic and the OpenTelemetry SDK. Vendoring is the cost of that;
``memory-bench/tests/test_public_validator_parity.py`` is what keeps the vendored
copies from rotting, by asserting they still agree with the originals.

What it checks, per record:

1. JSON Schema (``public/schema/bench-record.v3.schema.json``, 2020-12).
2. The forbidden-field scan: no field named ``pr``, ``commit_sha``, ``base_commit``,
   ``repo`` or ``jsonl_path`` at ANY depth. ``IDENTIFYING_KEYS`` below is the
   vendored copy of ``membench.grading.leak_guard.IDENTIFYING_KEYS``.
3. The leak scan: no expected or forbidden answer value appears in an
   agent-readable field (the question text, every distractor text, and every stale
   text). The match is word-boundary anchored, the same rule the answer is graded by
   (``metrics.scorers.states_value``); see ``states_value`` below for why the
   substring rule ``leak_guard.find_outcome_leaks`` uses is wrong for this one.
4. The leave-one-out recomputation: the published ``loo.excluded_ids`` and
   ``candidate_pool.ids`` are re-derived from ``loo.candidates`` and ``loo.query``
   with the strict ``<`` temporal cut and the five-axis sibling rule, and must match
   exactly.
5. The anti-shortcut rules, each closing a way to answer a record without
   retrieving: every non-null ``closed`` in a record is distinct, so the grid never
   groups the candidates that were laid down together; the pool is exactly the union
   of the three evidence buckets, so no id is published without its text and no text
   without a pool slot; the three buckets name disjoint ids, so nothing is both the
   answer and the trap; ``candidate_pool.ids`` is in the normative order
   ``pool_order`` derives from the record id, so a pool reordered to put the gold in
   front of a truncating runner is refused; and the gold is not the newest memory of
   every subject the pool carries (``subject_groups``), so a record that a per-subject
   recency policy answers without reading one text is refused too.
6. The self-report cross-checks: ``necessity.delta`` is the difference its own two
   rewards state and ``necessity.accepted`` is the verdict its own delta and epsilon
   imply; ``provenance.cross_session_gold_ids`` is a canonically ordered subset of the
   gold set, agrees with the tier it certifies, leaves the record the local subjects
   its construction fixes, and never names the subject the record publishes a
   supersession chain for; and a record's identity fields agree with each other -
   ``provenance.sequence_id`` is the record id, ``question.scope_id`` is the world for
   a project record and the sequence for a session one, and
   ``leak_guard.validator_version`` is the guard that goes with the schema version the
   record declares. These are producers' claims about work a downloader cannot repeat,
   so what a record says about itself is held against what the record carries.
7. The agreement layer (``find_disagreements``): the question, the evidence and the
   key still describe ONE task. Checks 1-6 are all satisfied by a record whose gold
   text says one thing and whose key grades another, because each of them reads only
   one side. This one reads both: the key grades as many values as the check kind
   admits, every expected value is carried by a gold text, every gold text carries a
   graded value, the subjects the question names are the subjects the gold answers (in
   both directions), no forbidden value is stated by the gold it would fail, every
   forbidden value is reachable from a published trap on a graded subject, every
   published stale text is itself forbidden, and every distractor is about a subject
   the record grades. It is a READ-time copy of what the producer enforces at build
   time, because a third party's copy of this file was not built by that producer.
8. The one-id-one-memory rule, across the records of a file and across the files of
   one run (``validate_release``): the schema says two records showing the same id
   mean the same memory, so the same id published with two different texts is a
   release that contradicts itself. Record ids are unique on the same grounds, and a
   file is named for the one origin and tier it holds.
9. The scope layer (``scope_problems``), which is the only check that reads a record
   against the OTHER records of its world. A published memory is written by exactly
   one sequence, so a gold the record declares session-local is published in that
   record and nowhere else, and a subject a world shares is cross-session in every
   record of that world that grades it. Both directions are exact, and neither can be
   seen one record at a time.

What agreement CANNOT catch is a value rewritten consistently on both sides -- gold
text and key edited together. Such a record is internally sound; it is simply a
different record from the one that was released. Only the release digest separates
those two, which is what ``public/SHA256SUMS`` is for, and why the tamper suite
in ``memory-bench/tests/test_public_validator_tampers.py`` records that case as
detected by the digest rather than by this file. That digest covers every published
file including this one, so a validator edited in transit to clear the corpus it was
edited for fails the check a downloader runs before running it. The same limit bounds
the question check: a question swapped for one that grades the SAME subjects is a
question the key still agrees with, because the subject clause is the only part of a
question any other field can be read against. A swap that changes the subjects is
caught; one that does not, is not, and is caught by the digest.

The scope layer has a limit of its own, and it is structural rather than incidental.
``cross_session_gold_ids`` cannot be checked COMPLETELY from published fields, because
exactly one published thing attributes a memory to a sequence and it does not reach far
enough. A memory two sequences of a world both need is minted once at world scope, so
it publishes under ONE alias in both of their records; a memory one sequence wrote for
itself is minted at sequence scope and appears in that record alone. A repeated alias
is therefore a proof of cross-session authorship, and it is what checks 8 and 9 run on.
A NON-repeated alias proves nothing either way: the world may have shared it with a
sequence this release does not publish, or with one whose goal never asked for it. On
this release 40 of the 108 cross-session golds repeat and 68 do not, and no
session-local gold repeats at all, so the signal is exact where it exists and simply
absent elsewhere.

What that leaves is a measured residue rather than an unbounded one. Relabelling ONE
gold of one record - the tamper an inflated count used to hide behind - is caught on
199 of the 292 promotions available in this release (0.6815) and 80 of the 108
demotions (0.7407). Neither pooled rate is a detection rate, because each pools two
populations that behave nothing alike, and the pooled demotion figure is the more
misleading of the two. Of the 108 demotions, 57 empty the claim outright and the tier
rule catches every one; the other 51 leave behind a claim a reader would still believe,
and 23 of those are caught (0.4510). Of the 292 promotions, 10 push the record under the
local-gold minimum it states about itself and every one of those is caught; the other
282 are caught 189 times (0.6702). A tamperer who keeps the claim plausible faces those
two rates rather than the pooled ones, and every miss in this release sits in one of
those two populations.

What the sweep asserts is not a rate but an equality. A move is caught EXACTLY when the
release contradicts it somewhere - the alias repeats, a sibling record grades that
subject on the other side of the boundary, the subject is the record's own supersession
chain, or the move breaks a count the record states about itself - and the misses are
exactly the moves nothing published contradicts. On all 400 moves the two sets agree.
The sweep is in ``memory-bench/tests/test_public_corpus_contract.py`` rather than in a
sentence here, so a rule removed from this file breaks that equality in CI. What closes
the rest is ``public/SHA256SUMS``, the same separator that closes the consistent
rewrite: this file certifies that a release is internally consistent, and the digest
certifies that it is the release that was published.

What it does NOT check is which id is which. That is the point: a published id is a
minted alias, and a consumer holding this file plus the release can recompute the
whole cut, seed an arm, and grade a run, while nothing in either file says what role
an id plays. ``evidence`` answers the question; the ids answer nothing.

Nothing SKIPs. A missing ``jsonschema`` raises ``MissingValidatorError``: a
validation gate that passes because its validator is absent reports green for files
nobody checked.

Usage:

    python3 membench_validate.py path/to/synthetic-session.jsonl ...

Requires Python 3.11 or newer (``datetime.UTC``, which the vendored
``canonical_ts`` shares with its original) and ``jsonschema``; nothing else. The
file is held to the harness's own gates -- ruff, black and ``mypy --strict`` are
run over ``public/`` from ``memory-bench/`` so one config governs both trees.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The one third-party dependency. Absence is a hard failure, never a skip. The
# import binds a private name and the public ``jsonschema`` attribute is declared
# ``Any``, because it holds either the module or ``None`` and a test flips it to
# ``None`` to exercise the refusal path; annotating it is what lets a type checker
# accept both without a suppression that goes stale when stubs appear.
jsonschema: Any
try:
    import jsonschema as _jsonschema
except ImportError:  # pragma: no cover - exercised via the monkeypatched attribute
    jsonschema = None
else:
    jsonschema = _jsonschema

VALIDATOR_VERSION = "membench-validate.v3"
SCHEMA_VERSION = "bench-record.v3"

# Vendored from membench.grading.leak_guard. Parity is asserted by
# tests/test_public_validator_parity.py.
IDENTIFYING_KEYS = ("pr", "commit_sha", "base_commit")

# The identifying keys plus the other field names a published record must not
# carry. Matched EXACTLY against field names, at every depth, so a field whose name
# merely contains one of them (``pr_key``, an opaque equality token) is not flagged.
FORBIDDEN_FIELD_NAMES = (*IDENTIFYING_KEYS, "repo", "jsonl_path")

# Vendored from membench.validity.SIBLING_AXES.
SIBLING_AXES = ("convoy", "pr", "external_ref", "epic_parent", "child")

# The axes an exclusion can be attributed to, in report order. ``bench-record.v1``
# also had "supersedes", read off a ``supersedes`` list on each published candidate.
# v2 publishes no supersession edges - the list of ids a candidate superseded was
# exactly the list of stale ones, which handed over the gold - so the axis has
# nothing to fire on and is gone with the field.
EXCLUSION_AXES = ("temporal", "self", *SIBLING_AXES)

# Which published candidate field carries each sibling axis's key. ``convoy_key`` and
# ``parent_key`` are work ids in the clear, in the same id space as the record ids -
# the epic_parent and child axes compare a parent against a record id, so digesting
# them would break the comparison, and a work id is already published as record_id.
# ``pr_key`` and ``external_ref_key`` are opaque equality tokens, because the rule
# only ever compares them and the raw change/branch value must not be published.
_AXIS_KEY = {
    "convoy": "convoy_key",
    "pr": "pr_key",
    "external_ref": "external_ref_key",
    "epic_parent": "parent_key",
}

_DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "bench-record.v3.schema.json"

# The agent-readable path prefix of the stale-value bucket. A stale text states a
# forbidden value by construction, so the leak scan holds that bucket to the expected
# values only; see ``find_leaks``.
SUPERSEDED_PREFIX = "evidence.superseded."

# Vendored from membench.generators.enterprise_workflow.MIN_LOCAL_FACTS: the floor on
# how many of a goal's graded subjects its OWN sequence established. Exactly one subject
# per goal carries the supersession chain, so a goal with a single local subject is a
# goal whose only local subject is the stale one; the generator refuses to build that,
# and ``_cross_session_problems`` refuses to accept a record claiming it. Parity is
# asserted by tests/test_public_validator_parity.py.
MIN_LOCAL_GOLD = 2

# The published id grammar, the same one the schema's ``publicMemoryId`` fixes. Needed
# here because a prompt-tier scope id is a published alias and nothing else checks it.
_PUBLIC_ID_RE = re.compile(r"^k-[0-9a-f]{16}$")

# Vendored from membench.validity: the acceptance grammar for a lifecycle instant.
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:?\d{2})?$"
)

# Vendored VERBATIM from membench.generators.enterprise_workflow._FACT_RE: the inverse
# of the one template every published memory text is minted from,
# ``"<subject> is <value> — by <persona> (<role>) in #<channel>"``. Non-greedy on both
# leading groups, so the subject ends at the FIRST " is " and the value at the FIRST
# " — by ". This is a format-anchored parse of a string the producer minted, not a
# heuristic over prose; a text the template did not produce is refused rather than
# guessed at.
_FACT_RE = re.compile(r"^(?P<prompt>.+?) is (?P<value>.+?) — by (?P<attribution>.+)$", re.DOTALL)

# The inverse of the goal request the generator mints: "<goal> State the current value
# of: <subjects>." for a text answer, and "<goal> Using the tool `<tool>`, apply the
# current value of: <subjects>." for a tool-call one. Anchored on the literal clause
# and on the end of the string, and greedy to the final period, because the subject
# list is the tail of the request. The clause is what makes a question CHECKABLE: it
# names, in the question itself, exactly what the key is allowed to grade.
_QUESTION_SUBJECTS_RE = re.compile(
    r"(?:State|apply) the current value of: (?P<subjects>.+)\.$", re.DOTALL
)

# How the goal request joins the subject list. The authored subjects contain no comma,
# so the split is exact; a subject that did contain one would surface as a subject
# mismatch against the gold, never as a silent pass.
_SUBJECT_SEPARATOR = ", "


class MissingValidatorError(RuntimeError):
    """Raised when ``jsonschema`` is not importable. The validator refuses to run
    rather than report a green file it never schema-checked."""


def _require_jsonschema() -> Any:
    if jsonschema is None:
        raise MissingValidatorError(
            "jsonschema is required to validate published bench records and is not "
            "installed; install it (pip install jsonschema) and re-run. This gate "
            "does not skip."
        )
    return jsonschema


def canonical_ts(value: str) -> str:
    """Canonicalize a lifecycle instant to ``YYYY-MM-DDTHH:MM:SS.sssZ``.

    Vendored from ``membench.validity.canonical_ts``. The cut is a string
    comparison, which is only chronological when every compared value shares one
    shape. Zoneless values are UTC. Raises ``ValueError`` on anything unparseable:
    a malformed instant must fail loudly, never silently reorder the boundary."""
    if _TIMESTAMP_RE.match(value) is None:
        raise ValueError(f"not a recognizable lifecycle timestamp: {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"not a recognizable lifecycle timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    utc = parsed.astimezone(UTC)
    return utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sibling_axes(candidate: Mapping[str, Any], query: Mapping[str, Any]) -> tuple[str, ...]:
    """Every same-work axis on which `candidate` is the query's own work.

    Vendored, disjunct for disjunct, from ``membench.validity.is_sibling``:

    1. ``convoy`` - shares the query's convoy.
    2. ``pr`` - shares the query's change.
    3. ``external_ref`` - shares the query's branch.
    4. ``epic_parent`` - shares the query's parent, OR IS the query's parent.
    5. ``child`` - hangs under the query work. Unconditional: it needs no value on
       the query side beyond ``id``, which is always present.

    Axes 1-4 fire only when the query names a value, so absence never matches
    absence. A candidate naming no parent is unaffected by axes 4-5."""
    fired: list[str] = []
    for axis in ("convoy", "pr", "external_ref"):
        key = _AXIS_KEY[axis]
        if query.get(key) is not None and candidate.get(key) == query.get(key):
            fired.append(axis)
    parent = query.get("parent_key")
    if parent is not None and (candidate.get("parent_key") == parent or candidate["id"] == parent):
        fired.append("epic_parent")
    if candidate.get("parent_key") == query["id"]:
        fired.append("child")
    return tuple(fired)


def is_sibling(candidate: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    """Whether `candidate` is the query's own work on any of the five axes."""
    return bool(sibling_axes(candidate, query))


def exclusion_reasons(
    candidates: Sequence[Mapping[str, Any]],
    query: Mapping[str, Any],
    boundary: str,
) -> dict[str, tuple[str, ...]]:
    """Every excluded candidate id, mapped to the axes that excluded it.

    A candidate survives only when it closed STRICTLY before `boundary`, is not the
    query itself, and is the query's own work on no sibling axis."""
    cut = canonical_ts(boundary)
    reasons: dict[str, tuple[str, ...]] = {}
    for candidate in candidates:
        axes: list[str] = []
        closed = candidate.get("closed")
        if closed is None or canonical_ts(closed) >= cut:
            axes.append("temporal")
        if candidate["id"] == query["id"]:
            axes.append("self")
        axes.extend(sibling_axes(candidate, query))
        if axes:
            reasons[candidate["id"]] = tuple(axes)
    return reasons


def eligible_ids(
    candidates: Sequence[Mapping[str, Any]],
    query: Mapping[str, Any],
    boundary: str,
) -> list[str]:
    """The leave-one-out eligible ids, sorted. The only door to the pool."""
    excluded = exclusion_reasons(candidates, query, boundary)
    return sorted(c["id"] for c in candidates if c["id"] not in excluded)


def pool_order(record_id: str, ids: Iterable[str]) -> list[str]:
    """The normative order ``candidate_pool.ids`` must be published in.

    Vendored from ``membench.public_export.pool_order``; the parity test asserts the
    two agree over every published record.

    A pool ships in some order and every order is a claim. Merged in bucket order the
    claim is plainly false - gold, then distractors, then superseded puts the answer in
    the low slots of every record - and that is the order the first release shipped.
    Sorted by alias it is false more quietly: an alias is one fixed pseudorandom draw
    per memory, reused in every record that carries that memory, so a few low-alias
    memories that happen to be gold are counted again and again. On the release where
    that was first measured, alias order put gold first in 47 records of 160; on this
    re-drawn one the same rule reads 35 records of 160 (0.2188) against a per-record
    chance of 0.2017, p = 0.3191 over 20,000 permutation draws. That is not a repair,
    it is a different draw of the same accident, and a corpus is not entitled to the
    luck. Keying by the record id gives each memory an independent position in every
    record it appears in, so no one memory's accident can accumulate at all, and the
    published order reads 41 in 160 (0.2562) at p = 0.0546.

    HMAC-SHA256 under the record id, ascending over the hex digest, tie-broken on the
    alias so the order is total. No key material: both inputs are published, so any
    holder of a record recomputes this and checks it."""
    return sorted(
        ids,
        key=lambda alias: (
            hmac.new(record_id.encode("utf-8"), alias.encode("utf-8"), hashlib.sha256).hexdigest(),
            alias,
        ),
    )


def _forbidden_fields(node: Any, path: str = "") -> list[str]:
    """Every path at which a forbidden field name appears, at any depth."""
    found: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key in FORBIDDEN_FIELD_NAMES:
                found.append(here)
            found.extend(_forbidden_fields(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_forbidden_fields(value, f"{path}[{index}]"))
    return found


def agent_readable(record: Mapping[str, Any]) -> dict[str, str]:
    """The fields an agent can read BEFORE it retrieves anything: the question, every
    distractor seeded into the pool, and every stale text the pool carries.

    Gold text is deliberately not here - the gold IS the answer, and the agent is
    supposed to have to retrieve it. The distractor and stale texts are here because
    they are IN THE POOL: an arm that retrieves indiscriminately puts them in front
    of the agent, so anything they state is something the agent can read without
    having retrieved the right thing."""
    readable: dict[str, str] = {}
    question = record.get("question")
    if isinstance(question, Mapping) and isinstance(question.get("text"), str):
        readable["question.text"] = question["text"]
    evidence = record.get("evidence")
    if isinstance(evidence, Mapping):
        for bucket, prefix in (
            ("distractors", "evidence.distractors."),
            ("superseded", SUPERSEDED_PREFIX),
        ):
            texts = evidence.get(bucket)
            if isinstance(texts, Mapping):
                for key, text in texts.items():
                    if isinstance(text, str):
                        readable[f"{prefix}{key}"] = text
    return readable


def states_value(text: str, value: str) -> bool:
    """True when `text` states `value` as a standalone token run.

    Vendored from ``membench.metrics.scorers.states_value``: word-boundary anchored,
    so a value never matches inside a larger identifier. This is the rule the answer
    is GRADED by, which is why the leak scan uses it too. ``leak_guard``'s scan is a
    case-insensitive substring, correct for a high-entropy identifier and wrong for a
    short authored enum token: ``v2`` is a substring of the authored subject
    ``checkout_v2 feature flag state`` without that text stating ``v2`` at all."""
    return re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text) is not None


def find_leaks(record: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every ``(where, value)`` answer-value leak into an agent-readable field.

    Blank values are ignored so they cannot match every document. Matching is
    ``states_value``, so a record this scan clears is a record whose question does
    not already score itself.

    The stale bucket is scanned for EXPECTED values only. A stale text states a
    forbidden value by construction - being the wrong answer in the pool is its whole
    job - so scanning it for forbidden values would fail every well-formed record. A
    stale text that states the CURRENT answer is still a leak, and still caught."""
    answer = record.get("answer")
    if not isinstance(answer, Mapping):
        return []

    def _values(field: str) -> list[str]:
        entries = answer.get(field)
        if not isinstance(entries, list):
            return []
        return [v for v in entries if isinstance(v, str) and v.strip()]

    expected = list(dict.fromkeys(_values("expected_values")))
    both = list(dict.fromkeys([*expected, *_values("forbidden_values")]))
    return [
        (where, value)
        for where, content in agent_readable(record).items()
        for value in (expected if where.startswith(SUPERSEDED_PREFIX) else both)
        if states_value(content, value)
    ]


def fact_subject(content: str) -> str:
    """The SUBJECT a published memory text states a value for.

    Vendored from ``membench.generators.enterprise_workflow.fact_subject``, off the
    same ``_FACT_RE``. Raises ``ValueError`` on content the template did not produce:
    a text whose subject cannot be read is a text no agreement rule can check, and
    guessing one would be the heuristic this layer exists to avoid."""
    match = _FACT_RE.fullmatch(content)
    if match is None:
        raise ValueError(f"not a published fact-shaped memory text: {content!r}")
    return match.group("prompt")


def question_subjects(text: str) -> tuple[str, ...]:
    """The subjects a published question names, in the order it names them.

    Raises ``ValueError`` when the question carries no subject clause. That is a
    finding, not a reason to skip: a question that does not say what it asks for
    cannot be checked against the key that grades it, and a validator that fell
    silent there would accept any question swapped in for any other."""
    match = _QUESTION_SUBJECTS_RE.search(text)
    if match is None:
        raise ValueError(
            f"names no graded subjects; expected a trailing 'the current value of: "
            f"<subject>, <subject>.' clause, got {text!r}"
        )
    return tuple(match.group("subjects").split(_SUBJECT_SEPARATOR))


def find_disagreements(record: Mapping[str, Any]) -> list[str]:
    """Every way `record`'s question, evidence and key fail to describe one task.

    The structural, leak and leave-one-out checks each read one side of the record,
    so all three pass a record whose gold says ``30s`` and whose key grades ``99s``.
    These rules read both sides. They are the READ-time copy of what the producer
    already enforces at build time, and they exist because a third party's copy of a
    published file was not built by that producer.

    Matching is ``states_value`` throughout - the rule the run is graded by - so a
    value this layer calls carried is a value the scorer would score."""
    answer = record["answer"]
    evidence = record["evidence"]
    gold: Mapping[str, str] = evidence["gold"]
    raw_expected = list(answer["expected_values"])
    expected = [value for value in dict.fromkeys(raw_expected) if value.strip()]
    forbidden = [value for value in dict.fromkeys(answer["forbidden_values"]) if value.strip()]
    problems: list[str] = []

    # The arity the check kind fixes, and the reason it is checked at all: several rules
    # below read only ONE kind, so ``check_kind`` is a switch a tamper can throw to turn
    # them off. A value-set key states the value of every gold it grades, one for one; a
    # tool-call key states the single argument the action carries. Neither arity is
    # reachable from the other, so the switch cannot be thrown quietly.
    if answer["check_kind"] == "value-set" and len(raw_expected) != len(gold):
        problems.append(
            f"answer.check_kind is 'value-set' with {len(raw_expected)} expected value(s) "
            f"against {len(gold)} gold memories; a value-set key grades the value of each "
            "gold it requires, so the two counts are the same number or the key is not "
            "the kind it says it is"
        )
    if answer["check_kind"] == "tool-call" and len(raw_expected) != 1:
        problems.append(
            f"answer.check_kind is 'tool-call' with {len(raw_expected)} expected value(s); "
            "a tool-call key grades one action argument, so anything else is the key and "
            "the check kind describing different records"
        )

    for value in expected:
        if not any(states_value(text, value) for text in gold.values()):
            problems.append(
                f"answer.expected_values states {value!r}, which no gold text states; the "
                "key grades a value the evidence does not carry, so retrieving all of the "
                "gold still fails the record"
            )
    if answer["check_kind"] == "value-set":
        # A value-set record grades exactly the values its gold carries, so a gold id
        # carrying none of them earns nothing when retrieved. A tool-call record grades
        # one action argument while still REQUIRING the other reads, so the same rule
        # would reject every well-formed one.
        for memory_id in sorted(gold):
            if not any(states_value(gold[memory_id], value) for value in expected):
                problems.append(
                    f"gold memory {memory_id} states none of answer.expected_values; a gold "
                    "id that carries no graded value is padding, and an arm that retrieves "
                    "it is rewarded for nothing"
                )

    graded_subjects: dict[str, str] = {}
    for memory_id in sorted(gold):
        try:
            graded_subjects[memory_id] = fact_subject(gold[memory_id])
        except ValueError as exc:
            problems.append(f"evidence.gold[{memory_id}]: {exc}")
    answered = set(graded_subjects.values())

    try:
        asked = set(question_subjects(record["question"]["text"]))
    except ValueError as exc:
        problems.append(f"question.text {exc}")
    else:
        if len(graded_subjects) == len(gold):
            for subject in sorted(asked - answered):
                problems.append(
                    f"question.text asks for {subject!r} and no gold text answers it; the "
                    "record grades an answer the evidence cannot support"
                )
            for subject in sorted(answered - asked):
                problems.append(
                    f"evidence.gold answers {subject!r} and question.text never asks for "
                    "it; the key grades a subject the agent was never asked about"
                )

    traps = {**evidence["superseded"], **evidence["distractors"]}
    for value in forbidden:
        for memory_id in sorted(gold):
            if states_value(gold[memory_id], value):
                problems.append(
                    f"answer.forbidden_values states {value!r} and gold memory {memory_id} "
                    "states it too; the record fails an answer that reads its own gold"
                )
        carriers = [text for text in traps.values() if states_value(text, value)]
        if not carriers:
            problems.append(
                f"answer.forbidden_values states {value!r}, which no published superseded "
                "or distractor text states; nothing in the pool can draw an answer into "
                "it, so the staleness grade is dead on this record"
            )
            continue
        carrier_subjects: list[str] = []
        for text in carriers:
            try:
                carrier_subjects.append(fact_subject(text))
            except ValueError:
                # A trap outside the published template states the value and names no
                # readable subject. It is still a live trap - it sits in the pool and
                # says the forbidden thing - so reachability holds and the subject
                # constraint simply has nothing to read on it.
                continue
        if carrier_subjects and not any(subject in answered for subject in carrier_subjects):
            problems.append(
                f"answer.forbidden_values states {value!r} and the only published text "
                f"stating it is about {sorted(set(carrier_subjects))}, which the gold does "
                "not answer; a trap on an ungraded subject tempts nobody"
            )

    # The converse, and the reason it is separate: the rule above walks the forbidden
    # list, so it says nothing at all when that list is empty - and an empty list is a
    # record whose staleness axis is not graded while the trap still ships in the pool
    # and still tempts an arm. Every arm's reward then inflates, on a record that passes
    # every other check. So walk the traps instead: a published stale value is a value
    # the record must fail an answer for, or it is not stale, it is just a second value
    # of the same subject sitting in the pool.
    for memory_id in sorted(evidence["superseded"]):
        text = evidence["superseded"][memory_id]
        if not any(states_value(text, value) for value in forbidden):
            problems.append(
                f"evidence.superseded[{memory_id}] states no value in "
                "answer.forbidden_values; the record publishes it as the trap the answer "
                "is graded against, so an answer that reads it must fail and this one "
                "would score"
            )

    # Every distractor is authored for a subject the goal grades - that is what makes it
    # a distractor rather than an unrelated fact. A pool entry about an ungraded subject
    # is a candidate no arm has to rule out, so the record is easier than the one that
    # was released, and nothing else here reads distractor text against the gold.
    for memory_id in sorted(evidence["distractors"]):
        try:
            subject = fact_subject(evidence["distractors"][memory_id])
        except ValueError as exc:
            problems.append(f"evidence.distractors[{memory_id}]: {exc}")
            continue
        if answered and subject not in answered:
            problems.append(
                f"evidence.distractors[{memory_id}] is about {subject!r}, which no gold "
                "text answers; a distractor on an ungraded subject is not a distractor, "
                "and a pool carrying it is a shorter pool than the record specifies"
            )

    return problems


def _necessity_problems(necessity: Mapping[str, Any]) -> list[str]:
    """Every way the necessity block contradicts its own numbers.

    The gate ran two arms over the candidate sequence before publication and wrote the
    verdict into the record. A downloader cannot repeat that run, so the verdict is a
    claim - and a claim with its own working shown, because the block publishes both
    rewards, the difference and the threshold. Three of those four fix the fourth
    (``membench.generators.pilot_filter``: ``delta = oracle - no_memory`` and
    ``accepted = delta > epsilon``), so a flipped verdict or a flipped delta sign is
    arithmetic this file can redo.

    The tolerance is 1e-9, which is float-repr slack on values in [0, 1] and nothing
    like the 0.05 epsilon the gate decides on. A delta off by more than that is an
    edited number, not a rounding artifact."""
    oracle = float(necessity["oracle_reward"])
    no_memory = float(necessity["no_memory_reward"])
    delta = float(necessity["delta"])
    epsilon = float(necessity["epsilon"])
    accepted = bool(necessity["accepted"])
    problems: list[str] = []

    if abs(delta - (oracle - no_memory)) > 1e-9:
        problems.append(
            f"necessity.delta {delta!r} is not oracle_reward {oracle!r} minus "
            f"no_memory_reward {no_memory!r} ({oracle - no_memory!r}); the record states "
            "a margin its own rewards do not produce"
        )
    if accepted != (delta > epsilon):
        problems.append(
            f"necessity.accepted is {accepted} but delta {delta!r} against epsilon "
            f"{epsilon!r} implies {delta > epsilon}; the gate admits a candidate exactly "
            "when the oracle beats no-memory by more than epsilon, so the verdict and "
            "the numbers it was read from disagree"
        )
    if not accepted:
        problems.append("necessity.accepted is false; a rejected record must not be published")
    return problems


def _cross_session_problems(record: Mapping[str, Any]) -> list[str]:
    """Every way ``provenance.cross_session_gold_ids`` contradicts the record carrying it.

    The field names WHICH gold memories another sequence of the same world wrote. It
    replaced a count in v3, because the count was re-derivable: the generator added a
    goal's cross-session decisions on top of a fixed local budget, so the count was
    ``len(evidence.gold_ids)`` minus a constant on every record of the release. The
    generator now takes them out of that budget, the gold set is a constant width, and
    the claim ships as ids a validator can read against the rest of the corpus.

    What this function checks is everything the record alone fixes. The ids are a
    subset of the gold set, in the alias order every published id list uses, so a
    reordered list cannot smuggle a different reading of "first". They agree with the
    tier, which is the claim they exist to certify - a project record is cross-session
    or it is not a project record, and a session record is answered inside its own
    sequence by definition. They leave at least ``MIN_LOCAL_GOLD`` of the gold set
    local, which the construction fixes and which bounds the inflation this file cannot
    otherwise see. And they never name the subject the record publishes a supersession
    chain for: a chain is authored inside one sequence, so its surviving version is that
    sequence's own gold on either tier.

    Reading them against the rest of the corpus is ``scope_problems``; the module
    docstring says what neither can reach."""
    cross_ids = list(record["provenance"]["cross_session_gold_ids"])
    tier = record["tier"]
    gold: Mapping[str, str] = record["evidence"]["gold"]
    gold_ids = list(record["evidence"]["gold_ids"])
    problems: list[str] = []

    outside = sorted(set(cross_ids) - set(gold_ids))
    if outside:
        problems.append(
            f"provenance.cross_session_gold_ids names {outside}, which evidence.gold_ids "
            "does not; it names a subset of the gold set, so the record claims "
            "cross-session evidence it does not publish"
        )
    if cross_ids != sorted(cross_ids):
        problems.append(
            "provenance.cross_session_gold_ids is not in ascending alias order; every "
            "published id list is, and an order that is not fixed is an order a producer "
            "can use to say something the schema does not describe"
        )
    if tier == "project" and not cross_ids:
        problems.append(
            "provenance.cross_session_gold_ids is empty on a project-tier record; the "
            "project tier is the claim that another sequence of the world wrote part of "
            "the answer, and an empty list says this record's own session wrote all of it"
        )
    if tier != "project" and cross_ids:
        problems.append(
            f"provenance.cross_session_gold_ids names {sorted(cross_ids)} on a {tier}-tier "
            "record; only a project-tier record spans sequences, so a non-empty list here "
            "is the tier and the provenance naming two different scopes"
        )

    local_ids = [memory_id for memory_id in gold_ids if memory_id not in set(cross_ids)]
    if len(local_ids) < MIN_LOCAL_GOLD:
        problems.append(
            f"provenance.cross_session_gold_ids leaves {len(local_ids)} of the "
            f"{len(gold_ids)} gold memories written by the record's own sequence; every "
            f"goal grades at least {MIN_LOCAL_GOLD} subjects its own sequence established, "
            "so a list this long claims cross-session evidence the construction cannot "
            "produce"
        )

    # The chain's subject is local by construction: a supersession chain is written by
    # one sequence, version by version, and the record publishes the earlier versions as
    # evidence.superseded. So whichever gold answers that subject is that sequence's own,
    # on either tier, and naming it cross-session contradicts the trap the record ships.
    stale_subjects: set[str] = set()
    for text in record["evidence"]["superseded"].values():
        try:
            stale_subjects.add(fact_subject(text))
        except ValueError:
            continue
    for memory_id in sorted(set(cross_ids) & set(gold)):
        try:
            subject = fact_subject(gold[memory_id])
        except ValueError:
            continue
        if subject in stale_subjects:
            problems.append(
                f"provenance.cross_session_gold_ids names {memory_id}, which answers "
                f"{subject!r}, and the record publishes a superseded value for that same "
                "subject; a supersession chain is written inside one sequence, so its "
                "surviving version is that sequence's own gold"
            )
    return problems


def _identity_problems(record: Mapping[str, Any]) -> list[str]:
    """Every way a record's identity fields disagree with each other.

    Three relations the producer fixes and a reader can check without leaving the
    record: the provenance names the record it is attached to; ``question.scope_id`` is
    the world for a project record, the sequence for a session one and the (aliased)
    step for a prompt one, because the scope id IS the scope the answer must span; and
    the guard version goes with the schema version, which the schema pins to a constant.

    Unchecked, each is a free label. A project record relabelled into a scope of its own
    makes every cross-record check in ``scope_problems`` vacuous, which is the failure
    mode these exist to close: a scope layer is only as good as the scope ids it groups
    by."""
    problems: list[str] = []
    provenance = record["provenance"]
    if provenance["sequence_id"] != record["record_id"]:
        problems.append(
            f"provenance.sequence_id {provenance['sequence_id']!r} != record_id "
            f"{record['record_id']!r}; a record is published for exactly the sequence it "
            "was projected from"
        )
    scope_id = record["question"]["scope_id"]
    tier = record["tier"]
    if tier == "project" and scope_id != provenance["world_id"]:
        problems.append(
            f"question.scope_id {scope_id!r} != provenance.world_id "
            f"{provenance['world_id']!r} on a project-tier record; the scope a project "
            "answer spans is the world, so a scope id that is not the world id groups the "
            "record with records it shares no memories with"
        )
    if tier == "session" and scope_id != provenance["sequence_id"]:
        problems.append(
            f"question.scope_id {scope_id!r} != provenance.sequence_id "
            f"{provenance['sequence_id']!r} on a session-tier record; a session answer "
            "spans its own sequence and nothing wider"
        )
    if tier == "prompt" and _PUBLIC_ID_RE.fullmatch(scope_id) is None:
        problems.append(
            f"question.scope_id {scope_id!r} is not a published id on a prompt-tier "
            "record; a prompt answer spans one step, and the step is published as its "
            "alias like every other id"
        )
    declared = record["leak_guard"]["validator_version"]
    if declared != VALIDATOR_VERSION:
        problems.append(
            f"leak_guard.validator_version {declared!r} != {VALIDATOR_VERSION!r}; the "
            f"record declares schema {SCHEMA_VERSION}, and the guard is versioned with "
            "the schema, so a record of this schema was cleared by this guard or by none"
        )
    return problems


def _schema(path: str | Path | None = None) -> dict[str, Any]:
    """The published schema. ``MEMBENCH_PUBLIC_SCHEMA`` overrides the default
    location for a consumer who vendored the pair elsewhere."""
    if path is not None:
        chosen = Path(path)
    else:
        chosen = Path(os.environ.get("MEMBENCH_PUBLIC_SCHEMA", _DEFAULT_SCHEMA))
    if not chosen.is_file():
        raise FileNotFoundError(f"bench-record schema not found: {chosen}")
    loaded: dict[str, Any] = json.loads(chosen.read_text(encoding="utf-8"))
    return loaded


def validate_record(
    record: Mapping[str, Any], *, schema_path: str | Path | None = None
) -> list[str]:
    """Every problem with `record`, as plain sentences. Empty means it is publishable.

    Raises ``MissingValidatorError`` when ``jsonschema`` is absent."""
    js = _require_jsonschema()
    schema = _schema(schema_path)
    problems: list[str] = []

    # The field scan runs BEFORE the schema, and it is the only check that does.
    # It reads nothing but key names at arbitrary depth, so it is safe on a record of
    # any shape - and it has to run on a malformed one, because a record carrying
    # ``commit_sha`` is malformed BY carrying it. Behind the schema it was dead code:
    # every object in bench-record.v3 sets ``additionalProperties: false`` with bucket
    # keys pinned to the alias pattern, so a forbidden name always raised a schema
    # error first and returned before the scan was reached. Replacing the scan with a
    # no-op left this validator's whole suite green, which is why the scan runs first:
    # a check the schema shadows is a check nothing is measuring.
    for path in _forbidden_fields(record):
        problems.append(f"forbidden field published at {path}")

    validator = js.Draft202012Validator(schema)
    schema_problems: list[str] = []
    for error in sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        schema_problems.append(f"schema: {location}: {error.message}")
    if schema_problems:
        # The remaining checks index into a shape the schema just rejected; reporting
        # their KeyErrors as findings would bury the real one.
        return [*problems, *schema_problems]

    declared = tuple(record["leak_guard"]["identifying_keys_checked"])
    if declared != IDENTIFYING_KEYS:
        problems.append(
            f"leak_guard.identifying_keys_checked {declared} != this validator's "
            f"{IDENTIFYING_KEYS}; the producer and the validator disagree on which "
            "fields are answer-revealing"
        )

    for where, value in find_leaks(record):
        problems.append(f"answer value {value!r} leaks into agent-readable {where}")

    loo = record["loo"]
    if loo["boundary"] != record["question"]["asked_at"]:
        problems.append(
            f"loo.boundary {loo['boundary']!r} != question.asked_at "
            f"{record['question']['asked_at']!r}; the cut must be taken when the "
            "question is asked"
        )
    try:
        reasons = exclusion_reasons(loo["candidates"], loo["query"], loo["boundary"])
    except ValueError as exc:
        problems.append(f"loo: {exc}")
        return problems

    published_excluded = set(loo["excluded_ids"])
    if published_excluded != set(reasons):
        missing = sorted(set(reasons) - published_excluded)
        extra = sorted(published_excluded - set(reasons))
        problems.append(
            "loo.excluded_ids does not match the recomputed cut"
            + (f"; unexcluded but ineligible: {missing}" if missing else "")
            + (f"; excluded but eligible: {extra}" if extra else "")
        )

    recomputed_axes = sorted({axis for axes in reasons.values() for axis in axes})
    if sorted(loo["exclusion_axes"]) != recomputed_axes:
        problems.append(
            f"loo.exclusion_axes {sorted(loo['exclusion_axes'])} != recomputed "
            f"{recomputed_axes}"
        )

    pool = record["candidate_pool"]["ids"]
    expected_pool = eligible_ids(loo["candidates"], loo["query"], loo["boundary"])
    if sorted(pool) != expected_pool:
        problems.append(
            "candidate_pool.ids is not the leave-one-out eligible set "
            f"({len(pool)} published, {len(expected_pool)} eligible)"
        )
    elif pool != pool_order(record["record_id"], pool):
        # Order, not membership. README.md publishes this list as the order an arm is
        # seeded in, so a runner that takes the head of it is taking the head of a
        # permutation the record fixes. A pool re-sorted to lead with the gold turns
        # any first-k policy into a perfect retriever, and that reorder is invisible to
        # every membership check above.
        problems.append(
            "candidate_pool.ids is not in the normative order pool_order() derives "
            "from record_id; the published list is the order an arm is seeded in, so a "
            "reordered pool hands a truncating runner a different set than the record "
            "specifies"
        )

    instants = [c["closed"] for c in loo["candidates"] if c["closed"] is not None]
    if len(set(instants)) != len(instants):
        shared = sorted({ts for ts in instants if instants.count(ts) > 1})
        problems.append(
            f"loo.candidates share close instants {shared}; a repeated instant groups "
            "the candidates that were laid down together, which is a label a solver "
            "can read off the grid without retrieving anything"
        )

    pool_set = set(pool)
    evidence = record["evidence"]
    if sorted(evidence["gold_ids"]) != sorted(evidence["gold"]):
        problems.append("evidence.gold_ids and evidence.gold name different ids")
    if sorted(evidence["superseded_ids"]) != sorted(evidence["superseded"]):
        problems.append(
            "evidence.superseded_ids and evidence.superseded name different ids; every "
            "stale id must be published with the stale text, or a consumer cannot build "
            "the pool the answer is graded against"
        )

    buckets = {
        "gold_ids": set(evidence["gold_ids"]),
        "distractors": set(evidence["distractors"]),
        "superseded_ids": set(evidence["superseded_ids"]),
    }
    for name, ids in buckets.items():
        outside = sorted(ids - pool_set)
        if outside:
            problems.append(f"evidence.{name} names ids outside the candidate pool: {outside}")
    unbacked = sorted(pool_set - set().union(*buckets.values()))
    if unbacked:
        problems.append(
            f"candidate_pool.ids names {unbacked} that no evidence bucket carries text "
            "for; a consumer could not put those entries in the pool"
        )
    names = list(buckets)
    for left, right in ((a, b) for i, a in enumerate(names) for b in names[i + 1 :]):
        overlap = sorted(buckets[left] & buckets[right])
        if overlap:
            problems.append(
                f"{overlap} is published as both evidence.{left} and evidence.{right}; "
                "a candidate has one role or the record grades its own trap"
            )

    problems.extend(_recency_problems(record))

    problems.extend(_necessity_problems(record["necessity"]))
    problems.extend(_cross_session_problems(record))
    problems.extend(_identity_problems(record))

    problems.extend(find_disagreements(record))

    return problems


def published_texts(record: Mapping[str, Any]) -> dict[str, str]:
    """Every published memory id in `record`, mapped to its text.

    The three evidence buckets name disjoint ids (``validate_record`` enforces it),
    so one mapping carries all of them without a collision inside a record."""
    evidence = record["evidence"]
    texts: dict[str, str] = {}
    for bucket in ("gold", "distractors", "superseded"):
        entries = evidence.get(bucket)
        if isinstance(entries, Mapping):
            for memory_id, text in entries.items():
                if isinstance(memory_id, str) and isinstance(text, str):
                    texts[memory_id] = text
    return texts


def subject_groups(record: Mapping[str, Any]) -> dict[str, list[str]]:
    """The published pool, partitioned by the subject each candidate's text states.

    A record grades several subjects at once, and the pool carries several memories
    per subject: the current value, the distractors authored against it, and on one
    subject the superseded chain. The partition is what makes the recency shortcut
    below expressible at all. "Keep the most recent" is a policy an arm applies WITHIN
    a subject, because applied across subjects it returns one memory and the record
    asks for several.

    Raises ``ValueError`` on a pool entry this file cannot group: a published id with
    no text, or a text the published template did not produce (via ``fact_subject``).
    Both are already findings of their own in ``validate_record``; what matters here is
    that the grouping REFUSES rather than guesses, because a guessed group decides the
    shortcut check by heuristic."""
    texts = published_texts(record)
    groups: dict[str, list[str]] = {}
    for memory_id in record["candidate_pool"]["ids"]:
        if memory_id not in texts:
            raise ValueError(f"{memory_id} is published in the pool with no text")
        groups.setdefault(fact_subject(texts[memory_id]), []).append(memory_id)
    return groups


def _recency_problems(record: Mapping[str, Any]) -> list[str]:
    """The per-subject recency shortcut: gold that is every subject's newest memory.

    The record publishes a close instant for every candidate, because a consumer needs
    them to rebuild the leave-one-out cut. That makes "take the newest memory of each
    subject the question asks about" a policy an arm can run on the published grid
    alone, without reading a single text and without retrieving anything. On a corpus
    where the current value is always the last one written, that policy IS the answer
    key, and every arm scores the same perfect run.

    The rule is an equality, not a rate: the check fires when the newest member of
    every subject group covers the WHOLE gold set. That is the point at which the
    policy answers the record; anything short of it leaves a gold the policy misses,
    and an arm still has to retrieve. On the release this file ships with, the policy
    reaches at most four of the five golds on any record, which is why a bound would
    have been a threshold to tune and this is not one.

    The instants are read from ``loo.candidates`` rather than recomputed: they are the
    same instants ``exclusion_reasons`` cuts on, so a record that survives this check
    survives it on the grid a consumer actually gets."""
    try:
        groups = subject_groups(record)
    except ValueError as exc:
        return [
            f"candidate_pool: {exc}, so the per-subject recency check cannot be run on "
            "this record"
        ]

    closed = {c["id"]: c.get("closed") for c in record["loo"]["candidates"]}
    undated = sorted(
        memory_id for ids in groups.values() for memory_id in ids if closed.get(memory_id) is None
    )
    if undated:
        return [
            f"candidate_pool names {undated}, which loo.candidates gives no close "
            "instant; the per-subject recency check cannot be run on this record"
        ]

    newest = {
        max(ids, key=lambda memory_id: (canonical_ts(str(closed[memory_id])), memory_id))
        for ids in groups.values()
    }
    if set(record["evidence"]["gold_ids"]) <= newest:
        return [
            f"every gold memory is the newest of its subject group ({len(groups)} "
            f"groups over {len(record['candidate_pool']['ids'])} candidates); an arm "
            "that keeps the latest memory per subject answers this record without "
            "reading any text, so the record grades recency rather than retrieval"
        ]
    return []


def _text_conflicts(
    seen: dict[str, tuple[str, str]], record_id: str, texts: Mapping[str, str]
) -> list[str]:
    """Report, and accumulate into `seen`, every id republished with a new text.

    The schema's one guarantee about an id is that two records showing it mean the
    same memory. An id carrying two texts breaks that: a consumer assembling a pool
    across records would seed two different memories under one key, and whichever
    text the record's own key grades, the other record's grade is now wrong.

    `seen` maps id -> (the text first seen, the record that published it)."""
    problems: list[str] = []
    for memory_id in sorted(texts):
        text = texts[memory_id]
        prior = seen.get(memory_id)
        if prior is None:
            seen[memory_id] = (text, record_id)
        elif prior[0] != text:
            problems.append(
                f"memory {memory_id} is published with two different texts: "
                f"{prior[1]} carries {prior[0]!r} and {record_id} carries {text!r}; "
                "one published id means one memory"
            )
    return problems


def scope_claims(record: Mapping[str, Any]) -> dict[str, Any]:
    """What the scope layer reads off one record, so a release can be checked in one pass.

    A compact projection rather than the record itself: ``validate_release`` has to hold
    one of these per published record while it cross-reads them, and it has no use for
    the pools or the prose."""
    cross_ids = set(record["provenance"]["cross_session_gold_ids"])
    gold: Mapping[str, str] = record["evidence"]["gold"]

    def subjects(ids: Iterable[str]) -> list[str]:
        found: set[str] = set()
        for memory_id in ids:
            text = gold.get(memory_id)
            if text is None:
                continue
            try:
                found.add(fact_subject(text))
            except ValueError:
                continue
        return sorted(found)

    local_ids = [
        memory_id for memory_id in record["evidence"]["gold_ids"] if memory_id not in cross_ids
    ]
    return {
        "record_id": record["record_id"],
        "scope_id": record["question"]["scope_id"],
        "cross_subjects": subjects(cross_ids),
        "local_subjects": subjects(local_ids),
        "local_ids": sorted(local_ids),
        "all_ids": sorted(published_texts(record)),
    }


def scope_problems(claims: Sequence[Mapping[str, Any]]) -> list[str]:
    """Every way the records of one world contradict each other about who wrote what.

    This is the only layer that reads a record against its siblings, and it is what
    ``provenance.cross_session_gold_ids`` is checkable against at all. Two exact
    consequences of "a published memory is written by exactly one sequence":

    * A gold a record declares session-local was written by that record's own sequence,
      so it is published in that record and in no other. An id declared local that turns
      up anywhere else in the release is either not local or not one memory, and both
      readings contradict the release.
    * A world's shared subjects are drawn OUT of the pool its sequences draw local
      subjects from, so within a scope a subject is world-shared or it is nobody's but
      its own sequence's - never both. A subject declared cross-session in one record of
      a scope and session-local in another is a world that shared a subject it also kept.

    Both have no false positives on a corpus the producer built, and neither can be seen
    one record at a time. What they do NOT do is decide an arbitrary id; the module
    docstring says why that is not available to this file, and measures what is left."""
    problems: list[str] = []
    owner: dict[str, str] = {}
    for claim in claims:
        for memory_id in claim["local_ids"]:
            owner[memory_id] = claim["record_id"]
    for claim in sorted(claims, key=lambda c: str(c["record_id"])):
        for memory_id in claim["all_ids"]:
            holder = owner.get(memory_id)
            if holder is not None and holder != claim["record_id"]:
                problems.append(
                    f"memory {memory_id} is published by {claim['record_id']} and declared "
                    f"a session-local gold of {holder}; a memory has one writing sequence, "
                    "so a gold only that sequence wrote appears only in its own record"
                )

    by_scope: dict[str, list[Mapping[str, Any]]] = {}
    for claim in claims:
        by_scope.setdefault(str(claim["scope_id"]), []).append(claim)
    for scope_id in sorted(by_scope):
        members = sorted(by_scope[scope_id], key=lambda c: str(c["record_id"]))
        cross_by_subject: dict[str, str] = {}
        local_by_subject: dict[str, str] = {}
        for claim in members:
            for subject in claim["cross_subjects"]:
                cross_by_subject.setdefault(subject, str(claim["record_id"]))
            for subject in claim["local_subjects"]:
                local_by_subject.setdefault(subject, str(claim["record_id"]))
        for subject in sorted(set(cross_by_subject) & set(local_by_subject)):
            problems.append(
                f"scope {scope_id} grades {subject!r} as cross-session in "
                f"{cross_by_subject[subject]} and as session-local in "
                f"{local_by_subject[subject]}; a world shares a subject or keeps it, and "
                "the subjects its sequences draw from locally are the ones it did not share"
            )
    return problems


def _expected_filename(origins: Sequence[str], tiers: Sequence[str]) -> str | None:
    """``<origin>-<tier>.jsonl`` when the file holds exactly one of each, else None.

    A file that pools origins or tiers is reported by that finding instead; naming it
    would be reporting the same defect twice under a worse sentence."""
    if len(origins) != 1 or len(tiers) != 1:
        return None
    return f"{origins[0]}-{tiers[0]}.jsonl"


def validate_file(path: str | Path, *, schema_path: str | Path | None = None) -> dict[str, Any]:
    """Validate one published JSONL file and report it.

    The report carries ``ok``, the record count, the origins and tiers seen, and one
    entry per failing line. A file holding more than one origin is itself a finding:
    real and synthetic records are never pooled."""
    _require_jsonschema()
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"published file not found: {target}")

    failures: list[dict[str, Any]] = []
    origins: set[str] = set()
    tiers: set[str] = set()
    n_records = 0
    file_problems: list[str] = []
    seen_texts: dict[str, tuple[str, str]] = {}
    claims: list[dict[str, Any]] = []
    record_lines: dict[str, int] = {}

    for line_no, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            file_problems.append(f"line {line_no}: blank line in a JSONL file")
            continue
        n_records += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            failures.append({"line": line_no, "record_id": None, "problems": [f"not JSON: {exc}"]})
            continue
        if not isinstance(record, dict):
            failures.append(
                {
                    "line": line_no,
                    "record_id": None,
                    "problems": ["record is not an object"],
                }
            )
            continue
        problems = validate_record(record, schema_path=schema_path)
        if isinstance(record.get("origin"), str):
            origins.add(record["origin"])
        if isinstance(record.get("tier"), str):
            tiers.add(record["tier"])
        if not problems:
            # Only a record the per-record checks cleared has an evidence shape worth
            # cross-reading; a record that failed them would report the same defect twice.
            file_problems.extend(
                _text_conflicts(seen_texts, str(record.get("record_id")), published_texts(record))
            )
            claims.append(scope_claims(record))
            record_id = str(record["record_id"])
            first_line = record_lines.setdefault(record_id, line_no)
            if first_line != line_no:
                file_problems.append(
                    f"record {record_id} is published twice, on lines {first_line} and "
                    f"{line_no}; a record id names one task, and a release that carries it "
                    "twice scores that task twice"
                )
        if problems:
            failures.append(
                {
                    "line": line_no,
                    "record_id": record.get("record_id"),
                    "problems": problems,
                }
            )

    if n_records == 0:
        file_problems.append("file holds no records")
    if len(origins) > 1:
        file_problems.append(
            f"file pools {len(origins)} origins ({sorted(origins)}); real and synthetic "
            "records are never published together"
        )
    if len(tiers) > 1:
        file_problems.append(f"file pools {len(tiers)} tiers ({sorted(tiers)}); one file per tier")

    # The file name is part of the published contract, not a convenience: it is the only
    # place the origin and the tier are stated OUTSIDE the records, so a record relabelled
    # from synthetic to real (or from project to session) in every line of a file is
    # otherwise a file that agrees with itself about a lie.
    expected_name = _expected_filename(sorted(origins), sorted(tiers))
    if expected_name is not None and target.name != expected_name:
        file_problems.append(
            f"file is named {target.name!r} and holds {sorted(origins)[0]}/{sorted(tiers)[0]} "
            f"records, which are published as {expected_name!r}; the name states the origin "
            "and the tier, so a name and its contents disagreeing is one of them relabelled"
        )

    file_problems.extend(scope_problems(claims))

    return {
        "path": str(target),
        "n_records": n_records,
        "n_failed": len(failures),
        "origins": sorted(origins),
        "tiers": sorted(tiers),
        "file_problems": file_problems,
        "failures": failures,
        "memory_texts": {memory_id: text for memory_id, (text, _) in seen_texts.items()},
        "scope_claims": claims,
        "ok": not failures and not file_problems,
    }


def validate_release(
    paths: Sequence[str | Path], *, schema_path: str | Path | None = None
) -> dict[str, Any]:
    """Validate every file of one release together.

    A file is self-contained, but an id is not: the tiers of one release share the
    memories a project-tier question reaches back for. Validating the files
    separately therefore cannot see an id republished with a different text across
    them, which is the one thing the schema promises an id means. The same holds for
    the scope layer and for record ids: a world's records are split across files the
    moment its tiers are, and a per-file pass would call each half consistent."""
    reports = [validate_file(path, schema_path=schema_path) for path in paths]
    seen: dict[str, tuple[str, str]] = {}
    release_problems: list[str] = []
    for report in reports:
        release_problems.extend(_text_conflicts(seen, report["path"], report["memory_texts"]))

    already = {problem for report in reports for problem in report["file_problems"]}
    pooled = [claim for report in reports for claim in report["scope_claims"]]
    release_problems.extend(problem for problem in scope_problems(pooled) if problem not in already)

    where: dict[str, str] = {}
    for report in reports:
        for claim in report["scope_claims"]:
            record_id = str(claim["record_id"])
            prior = where.setdefault(record_id, report["path"])
            if prior != report["path"]:
                release_problems.append(
                    f"record {record_id} is published in both {prior} and {report['path']}; "
                    "a record id names one task, and a release that carries it twice scores "
                    "that task twice"
                )
    return {
        "reports": reports,
        "release_problems": release_problems,
        "ok": all(report["ok"] for report in reports) and not release_problems,
    }


def _print_report(report: Mapping[str, Any]) -> None:
    status = "OK" if report["ok"] else "FAIL"
    print(
        f"{status} {report['path']}: {report['n_records']} record(s), "
        f"{report['n_failed']} failing, origins={report['origins']}, tiers={report['tiers']}"
    )
    for problem in report["file_problems"]:
        print(f"  file: {problem}")
    for failure in report["failures"]:
        head = f"  line {failure['line']} ({failure['record_id']}):"
        for problem in failure["problems"]:
            print(f"{head} {problem}")


def main(argv: Sequence[str] | None = None) -> int:
    """``python3 membench_validate.py FILE...`` - 0 when every file is publishable."""
    parser = argparse.ArgumentParser(
        prog="membench_validate",
        description="Validate published membench bench-record JSONL files.",
    )
    parser.add_argument("files", nargs="+", help="published .jsonl file(s)")
    parser.add_argument(
        "--schema",
        default=None,
        help="path to bench-record.v3.schema.json (default: ../schema beside this file)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    release = validate_release(args.files, schema_path=args.schema)
    for report in release["reports"]:
        _print_report(report)
    for problem in release["release_problems"]:
        print(f"  release: {problem}")
    return 0 if release["ok"] else 1


__all__ = [
    "EXCLUSION_AXES",
    "FORBIDDEN_FIELD_NAMES",
    "IDENTIFYING_KEYS",
    "MIN_LOCAL_GOLD",
    "SCHEMA_VERSION",
    "SIBLING_AXES",
    "SUPERSEDED_PREFIX",
    "VALIDATOR_VERSION",
    "MissingValidatorError",
    "agent_readable",
    "canonical_ts",
    "eligible_ids",
    "exclusion_reasons",
    "fact_subject",
    "find_disagreements",
    "find_leaks",
    "is_sibling",
    "pool_order",
    "published_texts",
    "question_subjects",
    "scope_claims",
    "scope_problems",
    "sibling_axes",
    "states_value",
    "subject_groups",
    "validate_file",
    "validate_record",
    "validate_release",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
