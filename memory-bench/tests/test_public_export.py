"""The public export: round-trip it through the standalone validator, and pin the
refusals that make the publication invariants mechanical.

The round-trip is the real gate here. The exporter and the validator were written
against one schema but share no code, so a record that clears both is a record whose
producer and consumer independently agree - which is the only sense in which a
published corpus is checkable by someone who does not have this repo.

The fixture corpus is deliberately BOTH tiers. A session world exercises the ordinary
path; a project world exercises the two things the project tier exists for, that a
record can be answered only with memory an earlier session wrote, and that a task
which wrote its own whole answer is not published under that tier at all.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

from membench.generators.enterprise_workflow import (
    GENERATOR_VERSION,
    GRADED_SUBJECTS_PER_GOAL,
    materialize_project_tier,
    materialize_session_tier,
)
from membench.generators.necessity_sweep import NECESSITY_FILE, SEQUENCES_FILE, gate_corpus
from membench.generators.world_manifest import build_manifest, write_manifest
from membench.grading.leak_guard import IDENTIFYING_KEYS
from membench.public_alias import (
    PUBLIC_ID_PATTERN,
    AliasMap,
    build_alias_map,
    published_internal_ids,
)
from membench.public_clock import ClockDeal, shapes_of, slot_order
from membench.public_export import (
    PUBLIC_SCHEMA_VERSION,
    SLOT_INTERVAL,
    SYNTHETIC_EPOCH,
    AnswerLeakError,
    ForbiddenFieldError,
    NotCrossSessionError,
    PooledOriginError,
    PublicRecord,
    UnestablishedMemoryError,
    _timeline,
    agent_readable,
    assert_publishable,
    build_records,
    export_corpus,
    find_answer_leaks,
    iso_utc,
    published_ids,
    sequence_to_record,
    world_memories,
    write_tier_file,
)
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import EnterpriseWorld, Persona, Project, Team

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "public" / "validator" / "membench_validate.py"
SCHEMA_PATH = REPO_ROOT / "public" / "schema" / "bench-record.v3.schema.json"
PUBLISHED_DIR = REPO_ROOT / "public" / "data"

# A test mint, never the release one. The real seed lives outside the repo and the
# real map under fixtures/mint/; a test that reached for either would couple the
# suite to a secret and publish nothing it could check.
TEST_MINT_SEED = "test-mint-seed-not-the-published-one"

SESSION_SEEDS = (7, 11)
PROJECT_SEEDS = (21, 22)


def _load_validator(name: str = "membench_validate_export") -> Any:
    spec = importlib.util.spec_from_file_location(name, VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator() -> Any:
    return _load_validator()


def _world(seed: int = 7) -> tuple[EnterpriseWorld, Project]:
    world = EnterpriseWorld(
        world_id=f"w{seed}",
        domain="platform",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Platform")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Grace Hopper", role="sre", team_id="t1"),
        ],
        seed=seed,
    )
    project = Project(
        project_id=f"pr{seed}",
        world_id=world.world_id,
        name="Platform hardening",
        goal="Deliver the current platform initiative.",
    )
    return world, project


def _freeze(corpus: Path, seed: int, tier: str) -> None:
    world, project = _world(seed)
    materialize = materialize_session_tier if tier == "session" else materialize_project_tier
    sequences = materialize(world, project, n_tasks=2, facts_per_task=GRADED_SUBJECTS_PER_GOAL)
    world_dir = corpus / str(seed)
    world_dir.mkdir(parents=True)
    (world_dir / "world.json").write_text(world.model_dump_json(indent=2), encoding="utf-8")
    (world_dir / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
    (world_dir / SEQUENCES_FILE).write_text(
        json.dumps([s.model_dump(mode="json") for s in sequences], indent=2), encoding="utf-8"
    )
    write_manifest(
        build_manifest(
            world,
            project,
            sequences,
            nim_model="deterministic:test",
            n_tasks=2,
            facts_per_task=GRADED_SUBJECTS_PER_GOAL,
            tier=tier,  # type: ignore[arg-type]
        ),
        world_dir=world_dir,
    )


@pytest.fixture()
def gated_corpus(tmp_path: Path) -> Path:
    """A four-world corpus - two session, two project - frozen and gated exactly as
    the released one is."""
    corpus = tmp_path / "corpus"
    for seed in SESSION_SEEDS:
        _freeze(corpus, seed, "session")
    for seed in PROJECT_SEEDS:
        _freeze(corpus, seed, "project")
    report = gate_corpus(corpus)
    (corpus / NECESSITY_FILE).write_text(
        json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return corpus


def _sequences(corpus: Path) -> list[BenchmarkSequence]:
    found: list[BenchmarkSequence] = []
    for world_dir in sorted(d for d in corpus.iterdir() if (d / SEQUENCES_FILE).is_file()):
        raw = json.loads((world_dir / SEQUENCES_FILE).read_text(encoding="utf-8"))
        found.extend(BenchmarkSequence.model_validate(item) for item in raw)
    return found


def _aliases(corpus: Path) -> AliasMap:
    internal: set[str] = set()
    for sequence in _sequences(corpus):
        internal.update(published_internal_ids(sequence))
    return build_alias_map(sorted(internal), corpus=corpus.name, mint_seed=TEST_MINT_SEED)


def _records(corpus: Path) -> list[PublicRecord]:
    return build_records(corpus, aliases=_aliases(corpus))


def _of_tier(corpus: Path, tier: str) -> list[PublicRecord]:
    return [record for record in _records(corpus) if record.tier == tier]


# ---------------------------------------------------------------------------------
# round trip
# ---------------------------------------------------------------------------------


def test_export_round_trips_through_the_standalone_validator(
    gated_corpus: Path, tmp_path: Path, validator: Any
) -> None:
    """Export for real, then validate with the file a public consumer would run."""
    out = tmp_path / "public"
    report = export_corpus(gated_corpus, out_dir=out, aliases=_aliases(gated_corpus))

    assert report.n_published == sum(report.counts.values())
    assert report.sha256sums.is_file()
    assert [path.name for path in report.files] == [
        "synthetic-project.jsonl",
        "synthetic-session.jsonl",
    ]

    for path in report.files:
        file_report = validator.validate_file(path, schema_path=SCHEMA_PATH)
        assert file_report["ok"], file_report["failures"]
        assert file_report["origins"] == ["synthetic"]
        assert file_report["n_records"] > 0

    assert (
        validator.main([str(path) for path in report.files] + ["--schema", str(SCHEMA_PATH)]) == 0
    )


def test_sha256sums_covers_every_published_file(gated_corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "public"
    report = export_corpus(gated_corpus, out_dir=out, aliases=_aliases(gated_corpus))
    lines = report.sha256sums.read_text(encoding="utf-8").strip().splitlines()
    published = {name: digest for digest, name in (line.split("  ", 1) for line in lines)}
    assert set(published) == {path.name for path in report.files}
    for path in report.files:
        assert published[path.name] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_only_admitted_sequences_are_published(gated_corpus: Path) -> None:
    """A sequence the necessity gate rejected measures nothing about memory, and a
    project task that answers itself is not of the tier it claims. Both are withheld,
    and every withholding is reported."""
    artifact = json.loads((gated_corpus / NECESSITY_FILE).read_text(encoding="utf-8"))
    admitted = {row["sequence_id"] for row in artifact["per_sequence"] if row["accepted"]}
    rejected = {row["sequence_id"] for row in artifact["per_sequence"] if not row["accepted"]}

    report = export_corpus(
        gated_corpus, out_dir=gated_corpus / "out", aliases=_aliases(gated_corpus)
    )
    published = {
        json.loads(line)["record_id"]
        for path in report.files
        for line in path.read_text(encoding="utf-8").splitlines()
    }
    skipped = {sequence_id for sequence_id, _ in report.skipped}

    assert published.isdisjoint(rejected)
    assert published | skipped == admitted | rejected
    assert published.isdisjoint(skipped)
    assert report.n_published == len(published)


def test_export_refuses_an_ungated_corpus(gated_corpus: Path) -> None:
    """No necessity artifact means no verdict to publish; it must not default to one."""
    (gated_corpus / NECESSITY_FILE).unlink()
    with pytest.raises(FileNotFoundError, match="gate the corpus first"):
        _records(gated_corpus)


def test_export_refuses_a_world_that_does_not_reproduce_its_manifest(gated_corpus: Path) -> None:
    """Provenance names a world by its seed and hash. A world whose files no longer
    re-derive from that seed would publish records nobody can reproduce."""
    world_dir = gated_corpus / str(SESSION_SEEDS[0])
    sequences = json.loads((world_dir / SEQUENCES_FILE).read_text(encoding="utf-8"))
    sequences[0]["steps"][0]["user_request"] += " (edited by hand)"
    (world_dir / SEQUENCES_FILE).write_text(json.dumps(sequences, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="does not reproduce its manifest"):
        _records(gated_corpus)


def test_export_refuses_a_corpus_with_no_mint(gated_corpus: Path) -> None:
    """Minting at export time would give a different id space on every run, so two
    releases of one corpus could not be related. Absence is a refusal."""
    with pytest.raises(FileNotFoundError):
        build_records(gated_corpus)


def test_public_export_refuses_pooling(gated_corpus: Path, tmp_path: Path) -> None:
    """Real and synthetic are never written into one file, and the refusal is a
    raise, not a convention."""
    records = _records(gated_corpus)
    tier = records[0].tier
    real = PublicRecord(
        record_id="real-1",
        origin="real",
        tier=tier,
        payload={**records[0].payload, "record_id": "real-1", "origin": "real"},
    )

    with pytest.raises(PooledOriginError, match="never published together"):
        write_tier_file(tmp_path, [records[0], real], origin="synthetic", tier=tier)

    # A single-origin set whose origin disagrees with the file is the same bug.
    with pytest.raises(PooledOriginError, match="records are origin 'real'"):
        write_tier_file(tmp_path, [real], origin="synthetic", tier=tier)

    other_tier = "project" if tier != "project" else "session"
    mixed_tier = PublicRecord(
        record_id="t-1",
        origin="synthetic",
        tier=other_tier,
        payload={**records[0].payload, "record_id": "t-1", "tier": other_tier},
    )
    with pytest.raises(PooledOriginError, match="one file per tier"):
        write_tier_file(tmp_path, [records[0], mixed_tier], origin="synthetic", tier=tier)

    assert not list(tmp_path.glob("*.jsonl")), "a refused publication must write nothing"


# ---------------------------------------------------------------------------------
# the released tree
# ---------------------------------------------------------------------------------


def test_every_published_record_passes_leak_guard(validator: Any) -> None:
    """The corpus committed under public/data carries no identifying field at any
    depth and no answer value in anything the agent reads before retrieving."""
    assert PUBLISHED_DIR.is_dir(), f"published corpus missing at {PUBLISHED_DIR}"
    files = sorted(PUBLISHED_DIR.glob("*.jsonl"))
    assert files, "no published JSONL under public/data"
    n_records = 0
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            n_records += 1
            assert record["schema_version"] == PUBLIC_SCHEMA_VERSION
            assert validator._forbidden_fields(record) == []
            assert validator.find_leaks(record) == []
            assert tuple(record["leak_guard"]["identifying_keys_checked"]) == IDENTIFYING_KEYS
    assert n_records > 0


def test_published_corpus_validates(validator: Any) -> None:
    """The committed corpus is green under the standalone validator, file by file."""
    for path in sorted(PUBLISHED_DIR.glob("*.jsonl")):
        report = validator.validate_file(path, schema_path=SCHEMA_PATH)
        assert report["ok"], report["failures"] + report["file_problems"]


def test_validator_hard_fails_without_jsonschema(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validation gate that SKIPs when its validator is absent reports green for
    files nobody checked. This one raises."""
    module = _load_validator("membench_validate_nojsonschema")
    monkeypatch.setattr(module, "jsonschema", None)
    record = json.loads(
        next(iter(sorted(PUBLISHED_DIR.glob("*.jsonl"))))
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    with pytest.raises(module.MissingValidatorError, match="does not skip"):
        module.validate_record(record, schema_path=SCHEMA_PATH)
    with pytest.raises(module.MissingValidatorError):
        module.validate_file(sorted(PUBLISHED_DIR.glob("*.jsonl"))[0], schema_path=SCHEMA_PATH)


# ---------------------------------------------------------------------------------
# producer-side guards
# ---------------------------------------------------------------------------------


def test_assert_publishable_catches_a_forbidden_field(gated_corpus: Path) -> None:
    payload = dict(_records(gated_corpus)[0].payload)
    payload["provenance"] = {**payload["provenance"], "base_commit": "deadbeef"}
    with pytest.raises(ForbiddenFieldError, match=r"provenance\.base_commit"):
        assert_publishable(payload)


def test_assert_publishable_catches_an_answer_in_the_question(gated_corpus: Path) -> None:
    payload = dict(_records(gated_corpus)[0].payload)
    leaked = payload["answer"]["expected_values"][0]
    payload["question"] = {**payload["question"], "text": f"What is it? It is {leaked}."}
    with pytest.raises(AnswerLeakError, match=r"question\.text"):
        assert_publishable(payload)


def test_a_stale_text_may_state_a_forbidden_value_but_never_the_answer(
    gated_corpus: Path,
) -> None:
    """The stale bucket is the trap: stating a forbidden value is its job, and the
    leak scan must not refuse it for that. Stating the CURRENT answer is a real leak,
    and is caught."""
    payload = json.loads(json.dumps(_records(gated_corpus)[0].payload))
    stale_id = payload["evidence"]["superseded_ids"][0]
    forbidden = payload["answer"]["forbidden_values"][0]

    payload["evidence"]["superseded"][stale_id] = f"the value was {forbidden}"
    assert find_answer_leaks(payload) == []
    assert f"evidence.superseded.{stale_id}" in agent_readable(payload)

    expected = payload["answer"]["expected_values"][0]
    payload["evidence"]["superseded"][stale_id] = f"it is now {expected}"
    assert find_answer_leaks(payload) == [(f"evidence.superseded.{stale_id}", expected)]


# ---------------------------------------------------------------------------------
# B1 - the project tier is cross-session or it is not published
# ---------------------------------------------------------------------------------


def test_a_project_record_answers_with_memory_another_session_wrote(gated_corpus: Path) -> None:
    """The point of the project tier. At least one gold fact of a published project
    record was established by a DIFFERENT sequence of the same world, so a retriever
    confined to the record's own session cannot answer it."""
    project_records = _of_tier(gated_corpus, "project")
    assert project_records, "the fixture corpus publishes no project record"

    by_world: dict[str, list[BenchmarkSequence]] = {}
    for sequence in _sequences(gated_corpus):
        by_world.setdefault(sequence.sequence_id.rsplit("-", 1)[0], []).append(sequence)

    aliases = _aliases(gated_corpus)
    for record in project_records:
        siblings = by_world[record.record_id.rsplit("-", 1)[0]]
        established = world_memories(siblings)
        own = {
            memory_id
            for memory_id, entry in established.items()
            if entry.sequence_id == record.record_id
        }
        gold = set(record.payload["evidence"]["gold_ids"])
        foreign = gold - {aliases.alias(memory_id) for memory_id in own}
        assert foreign, f"{record.record_id} establishes its whole own answer"
        assert record.payload["provenance"]["cross_session_gold_ids"] == sorted(foreign)


def test_a_project_task_that_answers_itself_is_skipped_not_published(gated_corpus: Path) -> None:
    """A project task needing nothing an earlier session wrote is a session task under
    a project label. It is withheld, with the reason, rather than shipped."""
    report = export_corpus(
        gated_corpus, out_dir=gated_corpus / "out", aliases=_aliases(gated_corpus)
    )
    reasons = dict(report.skipped)
    assert reasons, "no sequence was skipped; the first task of a project world answers itself"
    assert all(
        "establishes every one of its own gold facts" in reason for reason in reasons.values()
    )
    assert all(sequence_id.endswith("-task0") for sequence_id in reasons)


def test_the_cross_session_refusal_is_a_scope_check_not_a_missing_text(gated_corpus: Path) -> None:
    """Handed its world's context, the later task publishes. Handed none, the SAME
    sequence is refused for scope - not for an unanswerable gold, which is a
    different and louder failure."""
    world, project = _world(PROJECT_SEEDS[0])
    sequences = materialize_project_tier(
        world, project, n_tasks=2, facts_per_task=GRADED_SUBJECTS_PER_GOAL
    )
    later = sequences[1]
    necessity = {
        "oracle_reward": 1.0,
        "no_memory_reward": 0.0,
        "delta": 1.0,
        "accepted": True,
        "reason": "test",
    }
    aliases = build_alias_map(
        sorted({mid for s in sequences for mid in published_internal_ids(s)}),
        corpus="scope-check",
        mint_seed=TEST_MINT_SEED,
    )
    kwargs: dict[str, Any] = {
        "world": world,
        "seed": world.seed,
        "generator_version": GENERATOR_VERSION,
        "necessity": necessity,
        "epsilon": 0.05,
        "reference_agent": "scripted-ref",
        "aliases": aliases,
    }

    record = sequence_to_record(later, context=sequences[:1], **kwargs)
    assert len(record.payload["provenance"]["cross_session_gold_ids"]) >= 1

    with pytest.raises(UnestablishedMemoryError, match="nothing in its world writes that text"):
        sequence_to_record(later, context=(), **kwargs)

    with pytest.raises(NotCrossSessionError, match="establishes every one of its own gold"):
        sequence_to_record(sequences[0], context=(), **kwargs)


# ---------------------------------------------------------------------------------
# B3 - the timeline is not an answer key
# ---------------------------------------------------------------------------------


def test_no_published_candidate_carries_supersession_edges(gated_corpus: Path) -> None:
    """v1 published the ids each candidate superseded, which was exactly the stale
    set: subtract it and the gold falls out of a record with the evidence deleted."""
    for record in _records(gated_corpus):
        for candidate in record.payload["loo"]["candidates"]:
            assert "supersedes" not in candidate


def test_schema_does_not_claim_the_clock_preserves_supersession_order() -> None:
    """The public contract must describe the neutral clock the producer emits.

    A stale schema description that promises a supersession-ordered timeline teaches
    consumers the answer-key rule v3 removed, even when the records themselves are
    neutral.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    description = schema["properties"]["loo"]["properties"]["candidates"]["description"]

    assert "Supersession still orders" not in description
    assert "does not preserve supersession order" in description


def test_every_candidate_closes_at_its_own_instant(gated_corpus: Path) -> None:
    """One candidate per slot. A shared instant would say "these were laid down
    together", which on this corpus is the same as saying "these are the distractors".
    """
    for record in _records(gated_corpus):
        loo = record.payload["loo"]
        instants = [c["closed"] for c in loo["candidates"] if c["closed"] is not None]
        assert len(set(instants)) == len(instants)
        assert sorted(instants) == [
            iso_utc(SYNTHETIC_EPOCH + index * SLOT_INTERVAL) for index in range(len(instants))
        ]
        assert loo["boundary"] == iso_utc(SYNTHETIC_EPOCH + len(instants) * SLOT_INTERVAL)
        assert max(instants) < loo["boundary"]


def _supersession_edges(sequences: list[BenchmarkSequence]) -> list[tuple[str, str]]:
    """``(replaced, replacement)`` for every supersession a step of ``sequences`` declares."""
    edges: list[tuple[str, str]] = []
    for sequence in sequences:
        for step in sequence.steps:
            if not step.superseded_memory_ids or not step.expected_memory_writes:
                continue
            for written in step.expected_memory_writes:
                edges.extend((replaced, written) for replaced in step.superseded_memory_ids)
    return edges


def test_the_slot_order_carries_no_supersession_signal(gated_corpus: Path) -> None:
    """mem-r6yzk R3b. The published close order used to be a linear extension of the
    supersession partial order, so every replaced value was dated before the value that
    replaced it. That was an answer key at both ends of the timeline: it pushed the
    stale bucket toward the earliest slot and locked it out of the latest one, and
    ``tests/test_public_corpus_contract.py`` has the rates it bought a solver.

    The order is a uniform permutation now, so a supersession edge is as likely to run
    backwards on the clock as forwards. Both orientations have to appear or the old
    invariant is still standing.
    """
    aliases = _aliases(gated_corpus)
    edges = [
        (aliases.alias(replaced), aliases.alias(replacement))
        for replaced, replacement in _supersession_edges(_sequences(gated_corpus))
    ]
    assert edges, "the fixture corpus has no supersession chain to look at"

    forwards = backwards = 0
    depths: set[int] = set()
    for record in _records(gated_corpus):
        payload = record.payload
        closed = {
            str(c["id"]): str(c["closed"])
            for c in payload["loo"]["candidates"]
            if c["closed"] is not None
        }
        for earlier, later in edges:
            if earlier in closed and later in closed:
                forwards += closed[earlier] < closed[later]
                backwards += closed[earlier] > closed[later]
        by_instant = sorted(closed, key=lambda mid: closed[mid])
        depths.update(
            by_instant.index(mid) for mid in payload["evidence"]["gold_ids"] if mid in closed
        )
    assert forwards + backwards > 0, "no published record carries a chain, so nothing was checked"
    assert backwards > 0, (
        f"every one of {forwards} published supersession edges still runs forwards on the "
        "clock; the close order is a linear extension again and the stale bucket is "
        "readable off the timestamps"
    )
    assert forwards > 0, (
        f"all {backwards} edges run backwards, which is its own fixed rule; the order is "
        "meant to be uniform, not reversed"
    )
    assert len(depths) > 1, "the gold sits at one fixed depth in every record"


def test_the_timeline_is_a_uniform_permutation_of_the_pool() -> None:
    """At the mechanism. The published order is built by ``slot_order``, which deals the
    gold's slot INSIDE its subject group and interleaves the groups at random. Dealing a
    slot is exactly the kind of rule that could pin a member somewhere, so the mechanism
    has to show it does not: over a fresh deal per trial, every member still reaches
    every slot and every member's mean slot still sits at the centre.

    The bound is three standard errors of a uniform draw over the trials below, so an
    order that pins any member anywhere fails it.
    """
    groups = {"alpha": ["g-a", "d-a1", "d-a2"], "beta": ["g-b", "d-b1", "d-b2"]}
    pool = [memory_id for members in groups.values() for memory_id in members]
    trials = 4000
    slots: dict[str, list[int]] = {memory_id: [] for memory_id in pool}
    for seed in range(trials):
        # A fresh single-record deal per trial: the deal is keyed, so a different key is
        # a different draw, and the trials sweep the deal as well as the interleaving.
        deal = ClockDeal(
            shapes_of("rec", "session", groups, []),
            mint_seed=f"mechanism-{seed}",
        )
        timeline = _timeline(
            slot_order(
                pool,
                record_id="rec",
                groups=groups,
                gold_ids=["g-a", "g-b"],
                stale_ids=[],
                deal=deal,
                rng=random.Random(seed),
            )
        )
        order = sorted(timeline.closed, key=lambda mid: timeline.closed[mid])
        assert sorted(order) == sorted(pool)
        assert len(set(timeline.closed.values())) == len(pool), "two members share an instant"
        assert max(timeline.closed.values()) < timeline.boundary
        for slot, memory_id in enumerate(order):
            slots[memory_id].append(slot)

    centre = (len(pool) - 1) / 2
    # sd of one uniform draw over 0..n-1, divided by sqrt(trials).
    spread = (sum((k - centre) ** 2 for k in range(len(pool))) / len(pool)) ** 0.5
    tolerance = 3 * spread / trials**0.5
    for memory_id, seen in slots.items():
        assert set(seen) == set(range(len(pool))), f"{memory_id} never reaches every slot"
        mean_slot = sum(seen) / trials
        assert abs(mean_slot - centre) <= tolerance, (
            f"{memory_id} sits at mean slot {mean_slot:.3f}, off the {centre:.3f} centre by "
            f"more than {tolerance:.3f}"
        )


# ---------------------------------------------------------------------------------
# B4 - the stale text ships with the stale id
# ---------------------------------------------------------------------------------


def test_every_superseded_id_is_published_with_its_text(gated_corpus: Path) -> None:
    """A stale id with no text is a pool entry a consumer cannot build, which is a
    trap that cannot fire."""
    for record in _records(gated_corpus):
        evidence = record.payload["evidence"]
        assert sorted(evidence["superseded"]) == sorted(evidence["superseded_ids"])
        assert all(text.strip() for text in evidence["superseded"].values())
        assert evidence["superseded_ids"], "the fixture corpus has no stale values to trap with"


def test_a_stale_id_with_no_text_anywhere_is_refused() -> None:
    """Refused, not dropped. Dropping it would publish a record whose forbidden values
    name a value the pool does not contain, so every answer policy would pass."""
    world, project = _world(SESSION_SEEDS[0])
    sequences = materialize_session_tier(world, project, n_tasks=2, facts_per_task=3)
    target = next(s for s in sequences if s.steps[-1].superseded_memory_ids)
    goal = target.steps[-1]
    broken = target.model_copy(
        update={
            "steps": [
                *target.steps[:-1],
                goal.model_copy(update={"superseded_memory_ids": ["m-never-written"]}),
            ]
        }
    )
    aliases = build_alias_map(
        sorted({mid for s in sequences for mid in published_internal_ids(s)} | {"m-never-written"}),
        corpus="stale-check",
        mint_seed=TEST_MINT_SEED,
    )
    with pytest.raises(UnestablishedMemoryError, match="its world never wrote"):
        sequence_to_record(
            broken,
            world=world,
            seed=world.seed,
            generator_version=GENERATOR_VERSION,
            necessity={
                "oracle_reward": 1.0,
                "no_memory_reward": 0.0,
                "delta": 1.0,
                "accepted": True,
                "reason": "test",
            },
            epsilon=0.05,
            reference_agent="scripted-ref",
            aliases=aliases,
        )


def test_the_pool_is_exactly_the_three_evidence_buckets(gated_corpus: Path) -> None:
    """Every pool id has text, every text has a pool slot, and no id plays two roles.
    This is what lets a consumer rebuild the arm's whole world from the record."""
    for record in _records(gated_corpus):
        payload = record.payload
        pool = set(payload["candidate_pool"]["ids"])
        gold = set(payload["evidence"]["gold_ids"])
        distractors = set(payload["evidence"]["distractors"])
        stale = set(payload["evidence"]["superseded_ids"])
        assert gold | distractors | stale == pool
        assert gold.isdisjoint(distractors)
        assert gold.isdisjoint(stale)
        assert distractors.isdisjoint(stale)
        assert payload["question"]["asked_at"] == payload["loo"]["boundary"]


# ---------------------------------------------------------------------------------
# B2c - published ids say nothing
# ---------------------------------------------------------------------------------


def test_every_published_id_is_a_minted_alias(gated_corpus: Path) -> None:
    for record in _records(gated_corpus):
        for memory_id in published_ids(record.payload):
            assert PUBLIC_ID_PATTERN.match(memory_id), memory_id


def test_no_internal_id_appears_anywhere_in_a_published_record(gated_corpus: Path) -> None:
    """Not in the ids, not in a text, not in provenance. One published preimage would
    pair an alias with the id it was minted from."""
    internal = {
        mid for sequence in _sequences(gated_corpus) for mid in published_internal_ids(sequence)
    }
    assert internal
    for record in _records(gated_corpus):
        blob = json.dumps(record.payload)
        leaked = sorted(memory_id for memory_id in internal if memory_id in blob)
        assert leaked == [], f"{record.record_id} publishes internal ids {leaked}"


def test_the_same_memory_gets_the_same_alias_in_every_record(gated_corpus: Path) -> None:
    """An id is an equality token across the release: two records showing one id mean
    one memory. That is the only thing an id may tell you, and it must be true."""
    aliases = _aliases(gated_corpus)
    established = world_memories(_sequences(gated_corpus))
    by_alias: dict[str, str] = {}
    for record in _records(gated_corpus):
        for memory_id, text in record.payload["evidence"]["gold"].items():
            assert by_alias.setdefault(memory_id, text) == text
    for memory_id, entry in established.items():
        alias = aliases.alias(memory_id)
        if alias in by_alias:
            assert by_alias[alias] == entry.text


# ---------------------------------------------------------------------------------
# the cut is recomputable
# ---------------------------------------------------------------------------------


def test_loo_block_is_recomputable_not_asserted(gated_corpus: Path, validator: Any) -> None:
    """The published cut is re-derived by the validator from the candidates alone, so
    a producer that published a wrong excluded set would be caught."""
    record = _records(gated_corpus)[0].payload
    loo = record["loo"]
    recomputed = validator.exclusion_reasons(loo["candidates"], loo["query"], loo["boundary"])
    assert sorted(recomputed) == sorted(loo["excluded_ids"])
    assert validator.eligible_ids(loo["candidates"], loo["query"], loo["boundary"]) == sorted(
        record["candidate_pool"]["ids"]
    )

    tampered = json.loads(json.dumps(record))
    tampered["candidate_pool"]["ids"] = tampered["candidate_pool"]["ids"][:-1]
    problems = validator.validate_record(tampered, schema_path=SCHEMA_PATH)
    assert any("candidate_pool.ids is not the leave-one-out eligible set" in p for p in problems)


def test_the_validator_catches_a_shared_close_instant(gated_corpus: Path, validator: Any) -> None:
    """The producer never emits one; the validator refuses it anyway, because the
    release is checkable by someone who does not have the producer."""
    record = json.loads(json.dumps(_records(gated_corpus)[0].payload))
    dated = [c for c in record["loo"]["candidates"] if c["closed"] is not None]
    dated[1]["closed"] = dated[0]["closed"]
    problems = validator.validate_record(record, schema_path=SCHEMA_PATH)
    assert any("share close instants" in problem for problem in problems)
