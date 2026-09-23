"""mem-r6yzk B2 — a published id must carry no role, under an ACTUAL attack.

The centrepiece here is ``test_brute_force_recovers_every_internal_id`` and its twin.
They run the same attack a solver would: take the namespaces every published record
states in its provenance, take the label vocabulary this repo's generator is written
in, recompute the id for every pair, and intersect with the published id space. Against
the ids the first export published that recovers every one of them — gold, stale and
distractor alike, each labelled by the guess that produced it. Against minted aliases
it recovers none, including when the attacker knows the alias scheme and guesses at
the seed.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import pytest

from membench.generators.enterprise_workflow import (
    _SHARED_CANDIDATES,
    materialize_project_tier,
    materialize_session_tier,
)
from membench.generators.opaque_ids import OPAQUE_ID_PATTERN, opaque_memory_id
from membench.public_alias import (
    ALIAS_SCHEMA_VERSION,
    MINT_DIR,
    PUBLIC_ID_PATTERN,
    AliasMap,
    UnknownAliasError,
    UnknownInternalIdError,
    _alias_for,
    build_alias_map,
    mint_path,
    published_internal_ids,
    read_alias_map,
    write_alias_map,
)
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team

CORPUS = "worlds-public-v1"
MINT_SEED = "test-mint-seed-3f9a1c"

# What an attacker can read off this repo: the generator's label vocabulary. Every
# spelling it mints, plus distractor indices and chain versions past the ones currently
# drawn, because a vocabulary is cheap to over-guess. Any of the seven subjects can
# carry a world's cross-session decision, and that spelling is prefixed ``shared-``.
_LABELS: tuple[str, ...] = tuple(
    label
    for subject in _SHARED_CANDIDATES
    for label in (
        subject.key,
        f"shared-{subject.key}",
        *(f"{subject.key}-distractor{n}" for n in range(8)),
        *(f"shared-{subject.key}-distractor{n}" for n in range(8)),
        *(f"{subject.key}-v{version}" for version in range(1, 6)),
    )
)


def _world(seed: int) -> tuple[EnterpriseWorld, Project]:
    world = EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Grace Hopper", role="reliability", team_id="t1"),
        ],
        channels=[Channel(channel_id="c1", name="kernels", kind="chat")],
        seed=seed,
    )
    project = Project(
        project_id=f"world-seed{seed}-project",
        world_id=world.world_id,
        name="Acme initiative",
        goal="Reconcile the launch config.",
    )
    return world, project


def _corpus() -> list[BenchmarkSequence]:
    """A corpus the size of the real one: 40 project worlds and 10 session worlds."""
    sequences: list[BenchmarkSequence] = []
    for seed in range(40):
        sequences.extend(materialize_project_tier(*_world(seed), n_tasks=3, seed=seed))
    for seed in range(40, 50):
        sequences.extend(materialize_session_tier(*_world(seed), n_tasks=3, seed=seed))
    return sequences


def _memory_ids(sequences: list[BenchmarkSequence]) -> set[str]:
    """Every MEMORY id the corpus holds (step ids excluded: the attack below is over
    the label vocabulary, and only memory ids are minted from it)."""
    ids: set[str] = set()
    for sequence in sequences:
        for step in sequence.steps:
            ids.update(step.expected_memory_writes)
            ids.update(step.expected_memory_reads)
            ids.update(step.distractor_memories)
            ids.update(step.superseded_memory_ids)
            for check in step.outcome_checks:
                ids.update(check.requires_memory)
    return ids


def _published_namespaces(sequences: list[BenchmarkSequence]) -> set[str]:
    """The scopes a published record states outright: its sequence id (provenance
    ``sequence_id``) and its world id (provenance ``world_id``)."""
    namespaces: set[str] = set()
    for sequence in sequences:
        namespaces.add(sequence.sequence_id)
        namespaces.add(sequence.sequence_id.rsplit("-task", 1)[0])
    return namespaces


def _brute_force(namespaces: set[str]) -> dict[str, str]:
    """The attack: every published namespace crossed with every known label, hashed the
    way the generator hashes it. Maps the guessed id back to the LABEL that produced it,
    which is the role the attacker learns."""
    return {
        opaque_memory_id(namespace, label): label for namespace in namespaces for label in _LABELS
    }


def test_brute_force_recovers_every_internal_id_and_its_role() -> None:
    sequences = _corpus()
    internal = _memory_ids(sequences)
    assert len(internal) >= 1000, len(internal)

    guesses = _brute_force(_published_namespaces(sequences))
    recovered = internal & set(guesses)
    assert recovered == internal, f"{len(recovered)}/{len(internal)}"

    # And the guess LABELS the id. Over each goal's candidate pool the attacker reads
    # off a gold/distractor partition, and it is the true one — which is the answer the
    # benchmark is asking the agent for.
    for sequence in sequences:
        goal = sequence.steps[-1]
        (check,) = goal.outcome_checks
        gold = set(check.requires_memory)
        distractors = set(goal.distractor_memories)
        pool = gold | distractors
        labelled = {memory_id: guesses[memory_id] for memory_id in pool if memory_id in guesses}
        assert set(labelled) == pool, sequence.sequence_id
        called_distractor = {
            memory_id
            for memory_id, label in labelled.items()
            if re.search(r"-distractor\d+$", label)
        }
        assert called_distractor == distractors, sequence.sequence_id
        assert pool - called_distractor == gold, sequence.sequence_id


def test_brute_force_recovers_nothing_from_the_minted_id_space() -> None:
    """The repair, as a measurement: the SAME attack, the same corpus, against the ids
    that actually ship. N of N becomes 0 of N."""
    sequences = _corpus()
    internal = _memory_ids(sequences)
    amap = build_alias_map(internal, corpus=CORPUS, mint_seed=MINT_SEED)
    published = set(amap.public_ids)
    assert len(published) == len(internal)

    guesses = _brute_force(_published_namespaces(sequences))
    before = len(internal & set(guesses))
    after = len(published & set(guesses))
    assert before == len(internal), f"{before}/{len(internal)}"
    assert after == 0, f"{after}/{len(published)}"


def test_brute_force_recovers_nothing_when_the_attacker_knows_the_scheme() -> None:
    """Kerckhoffs: the alias derivation is in this file, so the attack that matters is
    the one that knows it and has to guess the seed."""
    sequences = _corpus()
    internal = _memory_ids(sequences)
    amap = build_alias_map(internal, corpus=CORPUS, mint_seed=MINT_SEED)
    published = set(amap.public_ids)

    guessable = ("", "0", "1", "seed", "secret", "membench", "mint", "public", CORPUS, "2026")
    assert MINT_SEED not in guessable
    for guess in guessable:
        forged = {_alias_for(CORPUS, guess, memory_id) for memory_id in internal}
        assert published & forged == set(), guess


def test_a_published_id_is_never_an_id_of_the_old_shape() -> None:
    amap = build_alias_map(_memory_ids(_corpus()), corpus=CORPUS, mint_seed=MINT_SEED)
    for public_id in amap.public_ids:
        assert PUBLIC_ID_PATTERN.match(public_id), public_id
        assert not OPAQUE_ID_PATTERN.match(public_id), public_id


def test_the_gold_alias_holds_no_systematic_position_in_the_pool() -> None:
    """A published pool is a sorted list of ids. If minting pushed gold ids toward one
    end, the sort order would be the tell the ids no longer are."""
    sequences = _corpus()
    amap = build_alias_map(_memory_ids(sequences), corpus=CORPUS, mint_seed=MINT_SEED)
    ranks: collections.Counter[int] = collections.Counter()
    for sequence in sequences:
        goal = sequence.steps[-1]
        (check,) = goal.outcome_checks
        gold = set(check.requires_memory)
        pool = sorted(amap.alias(memory_id) for memory_id in gold | set(goal.distractor_memories))
        for memory_id in gold:
            ranks[pool.index(amap.alias(memory_id))] += 1
    total = sum(ranks.values())
    assert total > 100
    assert len(ranks) >= 6, dict(ranks)
    assert max(ranks.values()) / total <= 0.25, dict(ranks)


def test_minting_is_reproducible_and_order_independent() -> None:
    ids = ["b-two", "a-one", "c-three"]
    first = build_alias_map(ids, corpus=CORPUS, mint_seed=MINT_SEED)
    again = build_alias_map(reversed(ids), corpus=CORPUS, mint_seed=MINT_SEED)
    assert dict(first.aliases) == dict(again.aliases)
    # An alias depends only on its own internal id, so adding one never moves another.
    grown = build_alias_map([*ids, "d-four"], corpus=CORPUS, mint_seed=MINT_SEED)
    for internal_id in ids:
        assert grown.alias(internal_id) == first.alias(internal_id)


def test_a_different_seed_or_corpus_mints_a_different_id_space() -> None:
    ids = ["a-one", "b-two"]
    base = build_alias_map(ids, corpus=CORPUS, mint_seed=MINT_SEED)
    other_seed = build_alias_map(ids, corpus=CORPUS, mint_seed=f"{MINT_SEED}-x")
    other_corpus = build_alias_map(ids, corpus="worlds-public-v2", mint_seed=MINT_SEED)
    assert set(base.public_ids).isdisjoint(other_seed.public_ids)
    assert set(base.public_ids).isdisjoint(other_corpus.public_ids)


def test_resolution_fails_closed_in_both_directions() -> None:
    amap = build_alias_map(["a-one"], corpus=CORPUS, mint_seed=MINT_SEED)
    assert amap.internal(amap.alias("a-one")) == "a-one"
    # Passing an unminted id through would publish exactly the reversible id the mint
    # exists to remove, and would do it silently.
    with pytest.raises(UnknownInternalIdError):
        amap.alias(opaque_memory_id("world-seed0", "charter"))
    with pytest.raises(UnknownAliasError):
        amap.internal("k-0000000000000000")


def test_minting_requires_a_seed_and_a_non_empty_id_set() -> None:
    with pytest.raises(ValueError, match="no default"):
        build_alias_map(["a-one"], corpus=CORPUS, mint_seed="")
    with pytest.raises(ValueError, match="empty id set"):
        build_alias_map([], corpus=CORPUS, mint_seed=MINT_SEED)


def test_the_mint_file_round_trips_and_is_written_sorted_by_alias(tmp_path: Path) -> None:
    amap = build_alias_map(_memory_ids(_corpus()), corpus=CORPUS, mint_seed=MINT_SEED)
    path = write_alias_map(amap, path=tmp_path / "mint.json")
    loaded = read_alias_map(path)
    assert dict(loaded.aliases) == dict(amap.aliases)
    assert (loaded.corpus, loaded.mint_seed) == (CORPUS, MINT_SEED)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == ALIAS_SCHEMA_VERSION
    assert raw["n_aliases"] == len(amap)
    assert list(raw["aliases"]) == sorted(raw["aliases"])
    # Re-minting the same corpus under the same seed rewrites the same bytes.
    again = write_alias_map(
        build_alias_map(_memory_ids(_corpus()), corpus=CORPUS, mint_seed=MINT_SEED),
        path=tmp_path / "again.json",
    )
    assert again.read_bytes() == path.read_bytes()


def test_a_hand_edited_mint_is_refused_rather_than_resolved(tmp_path: Path) -> None:
    # A swapped mapping resolves published ids to the wrong internal ids, and every
    # downstream check agrees with it — so the file is checked against its own seed.
    amap = build_alias_map(["a-one", "b-two"], corpus=CORPUS, mint_seed=MINT_SEED)
    path = write_alias_map(amap, path=tmp_path / "mint.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    first, second = sorted(raw["aliases"])
    raw["aliases"][first], raw["aliases"][second] = (
        raw["aliases"][second],
        raw["aliases"][first],
    )
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="does not reproduce"):
        read_alias_map(path)


def test_a_mint_of_another_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "mint.json"
    path.write_text(
        json.dumps({"schema_version": "public-mint.v0", "corpus": CORPUS, "aliases": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="claims schema"):
        read_alias_map(path)


def test_an_alias_map_that_is_not_a_bijection_is_refused() -> None:
    duplicate = {
        "k-0000000000000000": "a-one",
        "k-1111111111111111": "a-one",
    }
    with pytest.raises(ValueError, match="not a bijection"):
        AliasMap(corpus=CORPUS, mint_seed=MINT_SEED, aliases=duplicate)
    with pytest.raises(ValueError, match="not published ids"):
        AliasMap(corpus=CORPUS, mint_seed=MINT_SEED, aliases={"m-0000000000000000": "a-one"})


def test_published_internal_ids_covers_every_id_a_record_can_carry() -> None:
    sequence = materialize_project_tier(*_world(3), n_tasks=3, seed=3)[-1]
    goal = sequence.steps[-1]
    covered = set(published_internal_ids(sequence))
    (check,) = goal.outcome_checks
    assert set(check.requires_memory) <= covered
    assert set(goal.distractor_memories) <= covered
    assert set(goal.superseded_memory_ids) <= covered
    assert {step.step_id for step in sequence.steps} <= covered
    assert covered == set(published_internal_ids(sequence))


_SECRECY_CLAIM = (
    "de-anonymize every published id",
    "reconstruct the distractor interleave",
    "re-minted with a seed held outside the repository",
)


def test_the_secrecy_boundary_is_written_down_where_it_is_relied_on() -> None:
    """An anonymity scheme whose threat model lives only in a reviewer's head gets
    published by the next person who needs a public benchmark repo. The claim is stated
    in the module that mints the aliases and in the directory that holds the inverse, so
    neither can be read without it. (``public/README.md`` states it to downloaders and is
    pinned by the published-tree tests.)

    This used to pin the sentence "The scheme's secrecy rests on this repository being
    private." Under mem-r6yzk N3 that premise was checked and is false — ``origin`` is
    github.com/sjarmak/mem, which the GitHub API reports as ``"visibility": "public"`` —
    so the mint is no longer tracked and ``fixtures/mint/README.md`` says so instead.
    What is pinned here is the consequence, which holds either way: whoever holds the
    mint can label every published candidate, and a leak means a re-mint."""
    for path in (
        Path(__file__).resolve().parents[1] / "membench" / "public_alias.py",
        MINT_DIR / "README.md",
    ):
        # Both files are hard-wrapped prose, so the claim is matched over collapsed
        # whitespace rather than over line breaks an editor happens to have placed.
        text = " ".join(path.read_text(encoding="utf-8").split())
        for claim in _SECRECY_CLAIM:
            assert claim in text, f"{path.name} does not state: {claim}"


def test_the_mint_directory_is_not_inside_the_published_tree() -> None:
    # The map is the whole inverse of the published id space. If it ever landed under
    # public/ the alias scheme would publish its own answer key.
    published_tree = MINT_DIR.parents[2] / "public"
    assert published_tree not in MINT_DIR.parents
    assert mint_path(CORPUS).parent == MINT_DIR
    assert (MINT_DIR / "README.md").is_file()
