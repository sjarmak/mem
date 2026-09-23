"""Drift guard for the vendored copies in ``public/validator/membench_validate.py``.

The public validator cannot import ``membench``: importing even a leaf module such
as ``membench.grading.leak_guard`` executes ``membench/grading/__init__.py``, which
eagerly pulls nine sibling modules and, through them, pydantic and the OpenTelemetry
SDK. So it vendors six things - the identifying keys, the five-axis sibling rule,
the lifecycle-instant canonicalizer, the word-boundary value match, the two
format-anchored parsers the agreement layer reads subjects with, and the keyed
permutation that fixes the pool order - and without this file the copies rot the
first time an original changes.

Where a vendored rule has a real corpus to run against, the check ENUMERATES it
rather than sampling: the value match is compared on every (text, value) pair the
release actually grades, and the subject parse on every published memory text and
every published question. A spot-check agrees on the five cases somebody thought of;
the release is what the validator is run against.

The axis checks ENUMERATE rather than spot-check, and they check the rule's
BEHAVIOUR, not just its constants. An adversarial pass found the validator
documenting five axes and, at the time, deciding on four: `child` was named in
``SIBLING_AXES`` and in the schema's ``exclusion_axes`` enum while no disjunct
implemented it. Every name-level assertion in this file passed through that,
because a vendored NAME list agreeing with the original says nothing about which
disjuncts exist. ``test_every_axis_fires_in_the_vendored_rule`` is the one that
bites: it makes each axis fire alone, so a deleted disjunct fails on the axis it
deleted.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

from membench.generators import enterprise_workflow
from membench.generators.enterprise_workflow import fact_subject, materialize_world
from membench.grading.leak_guard import IDENTIFYING_KEYS
from membench.metrics.scorers import states_value
from membench.public_export import PUBLIC_VALIDATOR_VERSION, pool_order
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team
from membench.validity import SIBLING_AXES, QueryWork, WorkRef, is_sibling

PUBLIC_DIR = Path(__file__).resolve().parents[2] / "public"
VALIDATOR_PATH = PUBLIC_DIR / "validator" / "membench_validate.py"
PUBLISHED_FILES = ("synthetic-session.jsonl", "synthetic-project.jsonl")


def _load_validator() -> Any:
    """Import the standalone validator by path, the way a public consumer would run
    it: no package, no membench on the import path."""
    spec = importlib.util.spec_from_file_location("membench_validate_parity", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator() -> Any:
    return _load_validator()


@pytest.fixture(scope="module")
def published() -> list[dict[str, Any]]:
    """Every published record of the release, both tiers.

    The enumerating checks below run against this rather than against invented
    fixtures: the corpus the validator is pointed at is the one its vendored rules
    have to agree with."""
    records: list[dict[str, Any]] = []
    for name in PUBLISHED_FILES:
        path = PUBLIC_DIR / "data" / name
        assert path.is_file(), f"published corpus missing at {path}"
        records.extend(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
        )
    assert records, "the published corpus is empty; the enumerations below would be vacuous"
    return records


def test_validator_file_exists() -> None:
    assert VALIDATOR_PATH.is_file(), f"vendored validator missing at {VALIDATOR_PATH}"


def test_identifying_keys_match(validator: Any) -> None:
    """The vendored answer-revealing field list is the real one, verbatim."""
    assert validator.IDENTIFYING_KEYS == IDENTIFYING_KEYS


def test_forbidden_field_names_cover_identifying_keys(validator: Any) -> None:
    """Every identifying key is refused as a published field name, plus the
    corpus-locating names a public record has no business carrying."""
    assert set(IDENTIFYING_KEYS) <= set(validator.FORBIDDEN_FIELD_NAMES)
    assert {"repo", "jsonl_path"} <= set(validator.FORBIDDEN_FIELD_NAMES)


# The axis tuple, written out. Asserting only `vendored == original` passes when
# BOTH sides lose an axis together, which is exactly what a copy-paste of a
# shortened list does; pinning the literal makes that a test failure and a
# deliberate edit here.
_AXES = ("convoy", "pr", "external_ref", "epic_parent", "child")


def test_sibling_axis_names_match(validator: Any) -> None:
    assert SIBLING_AXES == _AXES, "the REAL axis tuple changed; update _AXES deliberately"
    assert validator.SIBLING_AXES == _AXES


def test_exclusion_axes_enumerate_the_sibling_axes(validator: Any) -> None:
    """The report vocabulary is the two structural axes plus every sibling axis, in
    that order. A sibling axis missing here can never be reported even when the rule
    fires it.

    ``supersedes`` was a third structural axis in v1 and is deliberately absent: a
    published candidate no longer carries supersession edges, so there is no closure to
    exclude on, and an axis nothing can fire is an exclusion a record could claim while
    the recomputed cut never makes it."""
    report_vocabulary = ("temporal", "self", *_AXES)
    assert report_vocabulary == validator.EXCLUSION_AXES
    assert "supersedes" not in validator.EXCLUSION_AXES


def test_published_schema_enumerates_the_same_exclusion_axes(validator: Any) -> None:
    """The schema a downloader validates against, and the validator that ships
    beside it, name one vocabulary. The schema is resolved from the validator's own
    ``SCHEMA_VERSION``, so a version bump cannot leave this pointing at the old file."""
    schema_path = VALIDATOR_PATH.parents[1] / "schema" / f"{validator.SCHEMA_VERSION}.schema.json"
    assert schema_path.is_file(), f"no schema for {validator.SCHEMA_VERSION} at {schema_path}"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    published = schema["properties"]["loo"]["properties"]["exclusion_axes"]["items"]["enum"]
    assert tuple(published) == validator.EXCLUSION_AXES


def test_validator_version_matches_the_stamp_records_carry(validator: Any) -> None:
    """A published record names the validator that cleared it; a bump on one side
    only would leave records claiming a version that never ran. The literal is pinned
    for the same reason the axis tuple is: both sides moving together is the drift this
    file exists to catch."""
    assert validator.VALIDATOR_VERSION == PUBLIC_VALIDATOR_VERSION == "membench-validate.v3"


# (name, ref kwargs, query kwargs, expected) - one row per axis plus the null-safety
# cases. ``rig`` and ``started`` are LOO-irrelevant here but required by the real
# dataclasses, so they are filled with constants.
_SIBLING_CASES: tuple[tuple[str, dict[str, Any], dict[str, Any], bool], ...] = (
    ("convoy", {"convoy_id": "cv-1"}, {"convoy_id": "cv-1"}, True),
    ("convoy-differs", {"convoy_id": "cv-1"}, {"convoy_id": "cv-2"}, False),
    ("pr", {"pr": "p-9"}, {"pr": "p-9"}, True),
    ("external_ref", {"external_ref": "b-9"}, {"external_ref": "b-9"}, True),
    ("epic_parent-shared", {"parent": "epic-1"}, {"parent": "epic-1"}, True),
    ("epic_parent-is-the-epic", {"work_id": "epic-1"}, {"parent": "epic-1"}, True),
    ("child", {"parent": "q-1"}, {"work_id": "q-1"}, True),
    ("parentless-vs-parented-query", {}, {"parent": "epic-1"}, False),
    ("parentless-vs-parentless-query", {}, {}, False),
    (
        "unrelated",
        {"convoy_id": "cv-1", "parent": "epic-2"},
        {"parent": "epic-1"},
        False,
    ),
)


def _ref(**kwargs: Any) -> WorkRef:
    fields: dict[str, Any] = {
        "work_id": "r-1",
        "rig": "mem",
        "closed": "2026-01-01T00:00:00Z",
    }
    fields.update(kwargs)
    return WorkRef(**fields)


def _query(**kwargs: Any) -> QueryWork:
    fields: dict[str, Any] = {
        "work_id": "q-1",
        "rig": "mem",
        "started": "2026-02-01T00:00:00Z",
    }
    fields.update(kwargs)
    return QueryWork(**fields)


def _published(ref: WorkRef) -> dict[str, Any]:
    """The published candidate projection of a WorkRef - the shape the vendored rule
    reads. ``convoy_key`` / ``parent_key`` are work ids in the clear, ``pr_key`` /
    ``external_ref_key`` are equality tokens, and equality is all either axis tests.

    No ``supersedes``: the published candidate carries none, and a fixture richer than
    the release would let a rule that read one pass here and fail on real data."""
    return {
        "id": ref.work_id,
        "closed": ref.closed,
        "convoy_key": ref.convoy_id,
        "pr_key": ref.pr,
        "external_ref_key": ref.external_ref,
        "parent_key": ref.parent,
    }


def _published_query(query: QueryWork) -> dict[str, Any]:
    return {
        "id": query.work_id,
        "convoy_key": query.convoy_id,
        "pr_key": query.pr,
        "external_ref_key": query.external_ref,
        "parent_key": query.parent,
    }


@pytest.mark.parametrize(
    ("name", "ref_kwargs", "query_kwargs", "expected"),
    _SIBLING_CASES,
    ids=[case[0] for case in _SIBLING_CASES],
)
def test_vendored_sibling_rule_agrees(
    validator: Any,
    name: str,
    ref_kwargs: dict[str, Any],
    query_kwargs: dict[str, Any],
    expected: bool,
) -> None:
    """The vendored rule and ``validity.is_sibling`` decide every case the same way,
    including the epic-parent and child axes the real rule grew in this epic."""
    ref = _ref(**ref_kwargs)
    query = _query(**query_kwargs)
    assert is_sibling(ref, query) is expected, f"{name}: the REAL rule changed"
    assert (
        validator.is_sibling(_published(ref), _published_query(query)) is expected
    ), f"{name}: the vendored rule disagrees with validity.is_sibling"


def test_case_table_covers_every_axis() -> None:
    """A new axis on the real rule must arrive with a row here, or this fails loud."""
    covered = {name.split("-")[0] for name, _, _, _ in _SIBLING_CASES}
    assert set(SIBLING_AXES) <= covered


# One minimal (ref kwargs, query kwargs) pair per axis, each chosen so that axis
# and no other can fire. Keyed by axis name so the completeness check below is an
# enumeration of SIBLING_AXES, not a count.
_AXIS_TRIGGERS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "convoy": ({"convoy_id": "cv-1"}, {"convoy_id": "cv-1"}),
    "pr": ({"pr": "p-9"}, {"pr": "p-9"}),
    "external_ref": ({"external_ref": "b-9"}, {"external_ref": "b-9"}),
    "epic_parent": ({"parent": "epic-1"}, {"parent": "epic-1"}),
    "child": ({"parent": "q-1"}, {"work_id": "q-1"}),
}


def test_every_axis_has_a_trigger() -> None:
    """An axis with no trigger below is an axis nothing proves is implemented."""
    assert tuple(_AXIS_TRIGGERS) == SIBLING_AXES


@pytest.mark.parametrize("axis", _AXIS_TRIGGERS, ids=list(_AXIS_TRIGGERS))
def test_every_axis_fires_in_the_vendored_rule(validator: Any, axis: str) -> None:
    """Each axis fires, alone, in the vendored rule and in the original.

    This is the assertion a missing disjunct cannot survive. Comparing name lists
    does not reach it: `child` was published in ``SIBLING_AXES`` and in the
    schema's enum while the vendored rule had no disjunct for it, so every
    name-level check agreed and the published ``exclusion_axes`` could never
    report an axis the cut silently did not take."""
    ref_kwargs, query_kwargs = _AXIS_TRIGGERS[axis]
    ref = _ref(**ref_kwargs)
    query = _query(**query_kwargs)
    assert is_sibling(ref, query) is True, f"{axis}: the REAL rule no longer fires it"
    fired = validator.sibling_axes(_published(ref), _published_query(query))
    assert fired == (axis,), (
        f"{axis}: the vendored rule reported {fired} — an axis that cannot fire is "
        "an exclusion the published record claims and the recomputed cut never makes"
    )


def test_vendored_states_value_agrees_on_the_adversarial_cases(validator: Any) -> None:
    """The hand-picked cases: a value inside a larger identifier, a value that is a
    suffix of another, a multi-word value. These are kept alongside the release
    enumeration because a release need not contain the pair that separates two
    plausible match rules."""
    cases = (
        ("the checkout_v2 feature flag state", "v2"),
        ("the supported API version is v2", "v2"),
        ("the production deploy timeout is 15s", "15s"),
        ("the production deploy timeout is 15s", "5s"),
        ("retention is 30 days", "30 days"),
    )
    for text, value in cases:
        assert validator.states_value(text, value) is states_value(text, value), (
            text,
            value,
        )


def test_vendored_states_value_agrees_on_every_pair_the_release_grades(
    validator: Any, published: list[dict[str, Any]]
) -> None:
    """The enumeration: every published text against every value the same record's
    key carries. This is the whole decision surface the leak scan and the agreement
    layer stand on, so a divergence anywhere in it is a divergence on real data."""
    compared = 0
    for record in published:
        values = [
            *record["answer"]["expected_values"],
            *record["answer"]["forbidden_values"],
        ]
        texts = [
            record["question"]["text"],
            *validator.published_texts(record).values(),
        ]
        for text in texts:
            for value in values:
                assert validator.states_value(text, value) is states_value(text, value), (
                    text,
                    value,
                )
                compared += 1
    assert compared > 1000, f"only {compared} pairs compared; the release shrank unexpectedly"


def test_vendored_fact_template_is_verbatim(validator: Any) -> None:
    """The memory template's inverse is copied, not paraphrased. A paraphrase that
    happens to agree on today's corpus is exactly the drift this file exists for."""
    assert validator._FACT_RE.pattern == enterprise_workflow._FACT_RE.pattern
    assert validator._FACT_RE.flags == enterprise_workflow._FACT_RE.flags


def test_vendored_fact_subject_agrees_on_every_published_text(
    validator: Any, published: list[dict[str, Any]]
) -> None:
    """Every published memory text, through both parsers. All three buckets, because
    the agreement layer reads subjects off gold, stale and distractor alike."""
    seen = 0
    for record in published:
        for memory_id, text in validator.published_texts(record).items():
            assert validator.fact_subject(text) == fact_subject(text), (memory_id, text)
            seen += 1
    assert seen > 1000, f"only {seen} texts parsed; the release shrank unexpectedly"


def test_both_fact_parsers_refuse_the_same_non_template_text(validator: Any) -> None:
    """Refusal is part of the contract: a text the template did not mint has no
    subject to read, and a parser that guessed one would be the heuristic the
    agreement layer is built to avoid."""
    for text in ("no separator at all", "subject is value, unattributed", ""):
        with pytest.raises(ValueError):
            fact_subject(text)
        with pytest.raises(ValueError):
            validator.fact_subject(text)


def _world(seed: int = 7) -> EnterpriseWorld:
    return EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(
                persona_id="p1",
                name="Ada Lovelace",
                role="staff-engineer",
                team_id="t1",
            ),
            Persona(persona_id="p2", name="Grace Hopper", role="qa-engineer", team_id="t1"),
        ],
        channels=[Channel(channel_id="c1", name="kernels", kind="chat")],
        seed=seed,
    )


def _project(seed: int = 7) -> Project:
    return Project(
        project_id=f"world-seed{seed}-project",
        world_id=f"world-seed{seed}",
        name="Acme initiative",
        goal="Reconcile the launch config.",
    )


def _established(sequences: list[BenchmarkSequence]) -> dict[str, str]:
    return {
        memory_id: text
        for sequence in sequences
        for step in sequence.steps
        for memory_id, text in step.expected_memory_writes.items()
    }


@pytest.mark.parametrize("tool_requiring", [False, True], ids=["value-set", "tool-call"])
def test_vendored_question_parse_recovers_the_generator_subject_list(
    validator: Any, tool_requiring: bool
) -> None:
    """The question parser is pinned to the generator that writes the question.

    ``graded_prompts`` grows in lockstep with ``required_ids``, so the subjects the
    request names are the subjects of the goal's required memories, in order. Both
    request spellings are covered, including the tool-call one the current release
    has no record of: a parser that only ever saw the published spelling would fail
    closed on the first tool-requiring release."""
    sequences = materialize_world(
        _world(), _project(), n_tasks=2, facts_per_task=3, tool_requiring=tool_requiring
    )
    texts = _established(sequences)
    for sequence in sequences:
        goal = sequence.steps[-1]
        expected = tuple(
            fact_subject(texts[memory_id])
            for memory_id in goal.outcome_checks[0].requires_memory
            if memory_id in texts
        )
        assert expected, f"{sequence.sequence_id}: no required memory to read a subject off"
        assert validator.question_subjects(goal.user_request) == expected


def test_vendored_question_parse_agrees_with_the_published_gold(
    validator: Any, published: list[dict[str, Any]]
) -> None:
    """Every published question, against the subjects the REAL parser reads off that
    record's gold. Compared as sets: the exporter republishes gold_ids in minted-alias
    order, so the published order is deliberately not the order the question names."""
    for record in published:
        asked = set(validator.question_subjects(record["question"]["text"]))
        answered = {fact_subject(text) for text in record["evidence"]["gold"].values()}
        assert asked == answered, record["record_id"]


def test_vendored_question_parse_refuses_a_question_with_no_subject_clause(
    validator: Any,
) -> None:
    """A question that names no subjects is refused, not silently accepted. Falling
    silent there would let any question stand in for any other."""
    with pytest.raises(ValueError):
        validator.question_subjects("Deliver the current initiative.")


def test_the_validator_vendors_no_supersession_closure(validator: Any) -> None:
    """The vendored closure is gone, not merely unused. It read a published
    ``supersedes`` list, and that list was the stale set: a consumer could subtract it
    from the pool and recover the gold from a record with the evidence stripped. A
    helper left behind would invite the field back."""
    assert not hasattr(validator, "supersedes_closure")
    assert "supersedes_closure" not in validator.__all__


def test_vendored_canonical_ts_agrees(validator: Any) -> None:
    """Both sides canonicalize to one spelling, or the strict cut is not a
    chronological comparison at all."""
    from membench.validity import canonical_ts

    for value in (
        "2026-01-02 03:04:05",
        "2026-01-02T03:04:05Z",
        "2026-01-02T03:04:05+02:00",
    ):
        assert validator.canonical_ts(value) == canonical_ts(value)
    with pytest.raises(ValueError):
        validator.canonical_ts("2026-01-02")


def test_vendored_pool_order_agrees_on_every_published_record(
    validator: Any, published: list[dict[str, Any]]
) -> None:
    """The order a pool ships in is a rule BOTH sides run: the exporter writes it and
    the validator refuses a record that is not in it. Drift between the copies makes
    the producer publish records its own checker rejects, or makes the checker accept
    an order the producer never writes, and a third party seeded in that order is
    running a different benchmark.

    Enumerated over the release AND over a shuffle of each pool, because agreeing on a
    list that already arrives in order is agreement no implementation could fail.
    """
    assert "pool_order" in validator.__all__, (
        "the vendored permutation is not in the validator's public surface, and the "
        "README tells a downloader to recompute the order with it"
    )
    rng = random.Random(4242)
    for record in published:
        record_id = str(record["record_id"])
        ids = [str(alias) for alias in record["candidate_pool"]["ids"]]
        assert validator.pool_order(record_id, ids) == pool_order(record_id, ids), record_id
        shuffled = ids[:]
        rng.shuffle(shuffled)
        assert validator.pool_order(record_id, shuffled) == pool_order(
            record_id, shuffled
        ), record_id
