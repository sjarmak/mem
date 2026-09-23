"""The contract the frozen PUBLIC corpus must keep (mem-r6yzk.3).

``fixtures/worlds-public-v1`` is the corpus a third party downloads and runs, so its
guarantees have to be checked by CI rather than by an operator remembering to run a
script. Three things are pinned here:

* SIZE + LABELLING — at least 100 sequences, every one carrying a tier, split across
  the two tiers so neither is an afterthought.
* EVIDENCE AXES — every goal step depends on at least one remembered id and is
  stressed by at least one distractor. A goal with no read is not a memory task; a
  goal with no distractor cannot separate a retriever from a lucky guess.
* DETERMINISM — ``scripts/verify_worlds.py`` over the corpus exits 0. That script was
  operator-only; running it from a test is what puts the determinism manifest in CI,
  so a materialiser change that silently alters frozen task instances fails here.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import random
import re
import string
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from itertools import combinations, permutations
from pathlib import Path
from statistics import mean
from typing import Any, NamedTuple

import pytest

from membench.generators.enterprise_workflow import (
    MIN_LOCAL_FACTS,
    SUPERSESSION_DEPTH,
    fact_subject,
    fact_value,
    materialize_session_tier,
)
from membench.generators.memory_necessity_gate import memory_necessity_gate
from membench.generators.necessity_sweep import NECESSITY_FILE, SEQUENCES_FILE, gate_corpus
from membench.generators.world_manifest import MANIFEST_FILE, build_manifest, write_manifest
from membench.metrics.scorers import states_value
from membench.public_alias import (
    AliasMap,
    build_alias_map,
    load_alias_map,
    published_internal_ids,
)
from membench.public_export import build_records
from membench.report.comparison import EPSILON
from membench.schemas.sequence import BenchmarkSequence, OutcomeCheck
from membench.schemas.world import EnterpriseWorld, Persona, Project, Team
from tests.public_figures import (
    PERMUTATION_DRAWS,
    PERMUTATION_SEED,
    ascending_alias_ranks,
    baseline_driver,
    bucket_merge,
    bucket_merge_first_k,
    necessity_summary,
    ordered_rows,
    published_first_k,
    published_order,
    published_ranks,
    rank_uniformity,
    relabel_coverage,
    released,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "fixtures" / "worlds-public-v1"
MIN_SEQUENCES = 100


def _world_dirs() -> list[Path]:
    return sorted(d for d in CORPUS.iterdir() if (d / MANIFEST_FILE).exists())


def _sequences() -> list[BenchmarkSequence]:
    seqs: list[BenchmarkSequence] = []
    for d in _world_dirs():
        raw = json.loads((d / SEQUENCES_FILE).read_text(encoding="utf-8"))
        seqs.extend(BenchmarkSequence.model_validate(s) for s in raw)
    return seqs


def test_corpus_is_present() -> None:
    # A missing corpus must fail, never skip: the rest of this file would otherwise
    # report green over nothing at all.
    assert CORPUS.is_dir(), f"public corpus missing at {CORPUS}"
    assert _world_dirs(), f"no manifested worlds under {CORPUS}"


def test_corpus_holds_at_least_one_hundred_sequences() -> None:
    seqs = _sequences()
    assert len(seqs) >= MIN_SEQUENCES, f"{len(seqs)} sequences, floor is {MIN_SEQUENCES}"


def test_every_sequence_carries_a_tier_and_both_tiers_are_represented() -> None:
    tiers = Counter(s.tier for s in _sequences())
    assert None not in tiers
    assert set(tiers) == {"session", "project"}, tiers
    assert min(tiers.values()) > 0
    assert all(s.question_type for s in _sequences())


def test_every_goal_step_has_a_read_and_a_distractor() -> None:
    for seq in _sequences():
        goal = seq.steps[-1]
        assert goal.expected_memory_reads, f"{seq.sequence_id}: goal reads nothing"
        assert goal.distractor_memories, f"{seq.sequence_id}: goal has no distractor"


def test_corpus_ids_are_unique() -> None:
    ids = [s.sequence_id for s in _sequences()]
    assert len(ids) == len(set(ids))


def test_verify_worlds_script_exits_zero_over_the_corpus() -> None:
    """The determinism manifest, in CI. Runs the operator script as the operator does.

    The 600s bound is on the subprocess, not on the test: ``pytest-timeout`` is not a
    dependency here, so a ``pytest.mark.timeout`` would have been inert decoration that
    let a wedged re-materialisation hang CI to its own ceiling. ``subprocess.run``
    raises ``TimeoutExpired`` instead, which fails the test with the reason attached.
    """
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run(
        [sys.executable, "scripts/verify_worlds.py", "fixtures/worlds-public-v1"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "worlds reproduce deterministically" in proc.stdout


# --------------------------------------------------------------------------- #
# mem-r6yzk B7 (test vacuity): the publication rule, checked against a corpus that
# actually carries a rejection.
#
# ``public_export.build_records`` skips any sequence the necessity sweep rejected. The
# fixture that guards that rule has to span the accept/reject boundary or its
# "published == admitted" assertion compares a set to itself, and deleting the skip
# leaves the suite green.
#
# The boundary is built in two pieces, because one fixture cannot hold both halves.
# The gate's reject path is exercised DIRECTLY, on an in-memory sequence, so the
# verdict is the gate's own arithmetic and not a number written by hand. The corpus
# then carries a rejected row in its artifact. Deforming a frozen world instead is no
# longer possible: ``build_records`` re-verifies every world against its manifest, and
# a sequence edited after materialisation does not reproduce — the export would refuse
# the world outright, which is a different refusal and would pass this test for the
# wrong reason.
#
# The rejected sequence stays fully publishable in shape, so a lost skip shows up as an
# extra published record rather than as an exporter raise.
# --------------------------------------------------------------------------- #

# Where the boundary comes from. Arm rewards are means over the steps a sequence
# actually GRADES (mem-r6yzk B1 item 4), so a well-formed task scores oracle 1.000 /
# no-memory 0.000 however long it is: length no longer moves a verdict, and two
# generated sequences can no longer straddle any epsilon. The half-answerable sequence
# below grades one thing that needs no memory alongside the one that does, so its
# no-memory arm scores 0.500 — under this epsilon, rejected — while an untouched
# sequence scores 1.000 and clears it.
_GATE_EPSILON = 0.6
_FACTS_PER_TASK = 3
_MINT_SEED = "test-mint-seed-not-the-published-one"


def _synthetic_world(seed: int) -> tuple[EnterpriseWorld, Project]:
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


def _half_answerable(sequence: BenchmarkSequence) -> BenchmarkSequence:
    """Grade one more thing at the goal that no memory is needed for."""
    goal = sequence.steps[-1]
    stateless = OutcomeCheck(
        check_id=f"{goal.step_id}-stateless-check",
        description="a reply is produced at all, which takes no memory",
        requires_memory=[],
    )
    graded = goal.model_copy(update={"outcome_checks": [*goal.outcome_checks, stateless]})
    return sequence.model_copy(update={"steps": [*sequence.steps[:-1], graded]})


def _freeze_world(corpus: Path, seed: int) -> list[BenchmarkSequence]:
    """Freeze one world into ``corpus`` exactly as ``scripts/generate_worlds.py`` does,
    so it re-materialises from its manifest and the export accepts it."""
    world, project = _synthetic_world(seed)
    sequences = materialize_session_tier(world, project, n_tasks=1, facts_per_task=_FACTS_PER_TASK)
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
            n_tasks=1,
            facts_per_task=_FACTS_PER_TASK,
        ),
        world_dir=world_dir,
    )
    return sequences


def _corpus_aliases(corpus: Path) -> AliasMap:
    """A test mint over the fixture corpus. Never the release mint, whose seed and map
    are private; a test that reached for either would couple the suite to a secret."""
    internal: set[str] = set()
    for world_dir in sorted(d for d in corpus.iterdir() if (d / SEQUENCES_FILE).is_file()):
        raw = json.loads((world_dir / SEQUENCES_FILE).read_text(encoding="utf-8"))
        for item in raw:
            internal.update(published_internal_ids(BenchmarkSequence.model_validate(item)))
    return build_alias_map(sorted(internal), corpus=corpus.name, mint_seed=_MINT_SEED)


def _published_ids(corpus: Path) -> set[str]:
    return {record.record_id for record in build_records(corpus, aliases=_corpus_aliases(corpus))}


def test_the_gate_itself_rejects_a_half_answerable_sequence() -> None:
    """The reject path is the gate's own arithmetic, measured here rather than asserted
    by writing a flag. Without this, the rejected row in the fixture below would be a
    number nobody produced."""
    world, project = _synthetic_world(11)
    sequence = materialize_session_tier(world, project, n_tasks=1, facts_per_task=_FACTS_PER_TASK)[
        0
    ]

    clean = memory_necessity_gate(sequence, epsilon=_GATE_EPSILON).verdict
    assert clean.accepted, clean
    assert clean.no_memory_reward == pytest.approx(0.0)

    halved = memory_necessity_gate(_half_answerable(sequence), epsilon=_GATE_EPSILON).verdict
    assert not halved.accepted, halved
    assert halved.no_memory_reward == pytest.approx(0.5)
    assert halved.delta == pytest.approx(0.5)
    assert halved.delta < _GATE_EPSILON <= clean.delta


def _no_memory_answerable(sequence: BenchmarkSequence) -> BenchmarkSequence:
    """Grade the goal on something no memory is needed for, and nothing else.

    The half-answerable sequence above needs an inflated epsilon to be refused, which
    proves the arithmetic but not the SHIPPED threshold. This one is the case the gate
    exists to catch outright: a goal whose every outcome check has ``requires_memory``
    empty is answered identically by the oracle arm and the no-memory arm, so delta is
    0.000 and no epsilon at or above zero can admit it.
    """
    goal = sequence.steps[-1]
    stateless = OutcomeCheck(
        check_id=f"{goal.step_id}-stateless-check",
        description="a reply is produced at all, which takes no memory",
        requires_memory=[],
    )
    graded = goal.model_copy(update={"outcome_checks": [stateless]})
    return sequence.model_copy(update={"steps": [*sequence.steps[:-1], graded]})


def test_the_released_epsilon_rejects_a_sequence_that_needs_no_memory() -> None:
    """The gate's own threshold, not an inflated one, refuses a memoryless task.

    ``rejection_rate`` 0.0000 is the corpus's headline and it is the kind of number
    that reads as a gate working when it can equally mean a gate that cannot fire. The
    test above answers that at ``_GATE_EPSILON`` 0.6, which is twelve times the epsilon
    the release actually shipped with, so it left the shipped threshold untested: a
    gate wired to an epsilon of 1.0 would have passed it and admitted everything.

    So the released epsilon is exercised here, with no argument passed at all, on the
    case it has to refuse -- a goal the scripted agent answers with an empty store. The
    admitted control runs the same way, so the pair shows the same call both accepting
    and rejecting.
    """
    world, project = _synthetic_world(23)
    sequence = materialize_session_tier(world, project, n_tasks=1, facts_per_task=_FACTS_PER_TASK)[
        0
    ]

    admitted = memory_necessity_gate(sequence).verdict
    assert admitted.epsilon == pytest.approx(EPSILON), (
        f"the gate defaulted to epsilon {admitted.epsilon}, not the released {EPSILON}, so "
        "this test is not measuring the shipped threshold"
    )
    assert admitted.accepted, admitted
    assert admitted.delta == pytest.approx(1.0), admitted

    refused = memory_necessity_gate(_no_memory_answerable(sequence)).verdict
    assert refused.epsilon == pytest.approx(EPSILON), refused
    assert not refused.accepted, (
        f"a goal whose only outcome check requires no memory was ADMITTED at the released "
        f"epsilon {EPSILON}: {refused}"
    )
    assert refused.oracle_reward == pytest.approx(1.0), refused
    assert refused.no_memory_reward == pytest.approx(1.0), refused
    assert refused.delta == pytest.approx(0.0), refused
    assert "memory confers no advantage" in refused.reason, (
        f"the rejection does not name its cause, so an operator reading the artifact "
        f"cannot tell this apart from a near miss: {refused.reason}"
    )


def test_the_published_rejection_rate_carries_its_margin() -> None:
    """Pin the delta distribution the headline rate was produced by.

    "200 candidates, 200 admitted, rejection_rate 0.0000" is compatible with two very
    different corpora: one whose candidates cleared a threshold that was deciding close
    calls, and one whose candidates all scored the same thing. This release is the
    second -- every delta is exactly 1.000, oracle 1.000 against no-memory 0.000, so
    ``epsilon`` 0.05 was never consulted on a near miss and the margin is 0.95.

    That is not a defect; a scripted reference agent with the required ids handed to it
    SHOULD separate completely, and manufacturing a spread by weakening the gate or the
    corpus would destroy the signal rather than test it. What it is, is a fact the rate
    hides. Pinning it means a future corpus that drifts off 1.000 shows up here as a
    change rather than as an unchanged rate, and the README's margin sentence -- which
    ``test_published_figures_match_the_corpus`` holds to this same measurement -- has to
    be rewritten when it does.
    """
    stat = necessity_summary()
    print(stat)

    assert stat.n_candidates == 200, stat
    assert stat.n_rejected == 0 and stat.n_accepted == stat.n_candidates, stat
    assert stat.rejection_rate == pytest.approx(0.0), stat
    assert stat.reference_agent == "scripted-ref", stat
    assert stat.epsilon == pytest.approx(
        EPSILON
    ), f"the corpus was gated at epsilon {stat.epsilon}, not the release's {EPSILON}"

    assert stat.delta_min == pytest.approx(1.0) and stat.delta_max == pytest.approx(1.0), (
        f"the corpus deltas have moved off 1.000 ({stat}); the README's margin sentence "
        "is now wrong and the rejection rate means something it did not before"
    )
    assert stat.oracle_min == pytest.approx(1.0) and stat.oracle_max == pytest.approx(1.0), stat
    assert stat.no_memory_min == pytest.approx(0.0) and stat.no_memory_max == pytest.approx(
        0.0
    ), stat
    assert stat.margin == pytest.approx(0.95), stat
    assert stat.is_degenerate, stat


def test_every_published_record_carries_the_verdict_the_sweep_recorded() -> None:
    """The margin above is read off the sweep artifact; the release ships per-record
    copies of the same verdict. If they can disagree, pinning one says nothing about
    the other, and a downloader checking ``necessity`` would be checking a number the
    gate never produced."""
    artifact = json.loads((CORPUS / NECESSITY_FILE).read_text(encoding="utf-8"))
    by_sequence = {row["sequence_id"]: row for row in artifact["per_sequence"]}
    checked = 0
    for record in released():
        sequence_id = record["provenance"]["sequence_id"]
        row = by_sequence.get(sequence_id)
        assert row is not None, (
            f"{record['record_id']} was published from {sequence_id}, which the sweep "
            "artifact never judged"
        )
        necessity = record["necessity"]
        assert necessity["delta"] == pytest.approx(row["delta"]), (record["record_id"], necessity)
        assert necessity["oracle_reward"] == pytest.approx(row["oracle_reward"]), record[
            "record_id"
        ]
        assert necessity["no_memory_reward"] == pytest.approx(row["no_memory_reward"]), record[
            "record_id"
        ]
        assert necessity["epsilon"] == pytest.approx(artifact["epsilon"]), record["record_id"]
        assert row["accepted"], f"{record['record_id']} shipped from a rejected sequence"
        checked += 1
    assert checked == len(released()), "a published record went unchecked"


@pytest.fixture()
def corpus_with_a_rejection(tmp_path: Path) -> Path:
    """A gated two-world corpus holding one admitted and one rejected sequence.

    Both worlds are frozen normally and reproduce their manifests; the rejection is
    stamped onto the artifact row, which is the field the exporter actually reads. The
    test above is what makes that row a verdict the gate can produce rather than a
    convenient constant."""
    corpus = tmp_path / "corpus"
    _freeze_world(corpus, 7)
    rejected = _freeze_world(corpus, 11)[0].sequence_id
    report = gate_corpus(corpus, epsilon=_GATE_EPSILON)
    artifact = report.to_dict()
    for row in artifact["per_sequence"]:
        if row["sequence_id"] == rejected:
            row["accepted"] = False
            row["no_memory_reward"] = 0.5
            row["delta"] = 0.5
            row["reason"] = "half of the graded steps need no memory"
    (corpus / NECESSITY_FILE).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return corpus


def _verdicts(corpus: Path) -> tuple[set[str], set[str]]:
    rows = json.loads((corpus / NECESSITY_FILE).read_text(encoding="utf-8"))["per_sequence"]
    return (
        {row["sequence_id"] for row in rows if row["accepted"]},
        {row["sequence_id"] for row in rows if not row["accepted"]},
    )


def test_the_gated_fixture_spans_the_accept_reject_boundary(corpus_with_a_rejection: Path) -> None:
    """The guard on the guard: if this corpus ever gates to all-accepted, the
    publication test below goes vacuous without failing."""
    admitted, rejected = _verdicts(corpus_with_a_rejection)
    assert admitted, "fixture admits nothing: the publication test would have no records"
    assert rejected, "fixture rejects nothing: the publication test would be vacuous"


def test_only_admitted_sequences_are_published(corpus_with_a_rejection: Path) -> None:
    """A sequence the necessity gate rejected measures nothing about memory, so it must
    not ship even when it is perfectly well-formed."""
    admitted, rejected = _verdicts(corpus_with_a_rejection)
    published = _published_ids(corpus_with_a_rejection)
    assert published == admitted
    assert published.isdisjoint(rejected)


def test_the_rejected_sequence_is_held_back_by_its_verdict_alone(
    corpus_with_a_rejection: Path,
) -> None:
    """Flip the verdict and the same sequence publishes. That pins the cause: it is
    withheld by the gate's decision, not by a shape the exporter refuses anyway."""
    _, rejected = _verdicts(corpus_with_a_rejection)
    path = corpus_with_a_rejection / NECESSITY_FILE
    artifact = json.loads(path.read_text(encoding="utf-8"))
    for row in artifact["per_sequence"]:
        row["accepted"] = True
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    assert rejected <= _published_ids(corpus_with_a_rejection)


# --------------------------------------------------------------------------- #
# The RELEASED tree, record by record. Everything below reads ``public/data`` and the
# frozen corpus only, which is what a downloader has plus what this repo keeps private.
# --------------------------------------------------------------------------- #

RELEASE_DIR = REPO_ROOT.parent / "public" / "data"
BASELINE_SCRIPT = REPO_ROOT / "scripts" / "run_public_baseline.py"


def _frozen_by_id() -> dict[str, BenchmarkSequence]:
    return {sequence.sequence_id: sequence for sequence in _sequences()}


def test_every_released_project_record_is_answered_across_sessions() -> None:
    """B1, strictly, for EVERY published project record: at least one gold fact was
    written by a different sequence of the same world.

    Checked against the frozen corpus rather than against the record's own
    ``cross_session_gold_ids`` list, so the exporter is not the witness for its own
    claim. The join is by ``record_id``, which is published in the clear; the release
    mint is read only to turn the frozen internal ids into the aliases the record
    states, never to decide which ids belong in the set.
    """
    frozen = _frozen_by_id()
    aliases = load_alias_map(CORPUS.name)
    project_records = [record for record in released() if record["tier"] == "project"]
    assert project_records, "the release publishes no project record"

    for record in project_records:
        sequence = frozen[record["record_id"]]
        world_id = sequence.sequence_id.rsplit("-", 1)[0]
        siblings = [
            other
            for other in frozen.values()
            if other.sequence_id.rsplit("-", 1)[0] == world_id
            and other.sequence_id != sequence.sequence_id
        ]
        written_elsewhere = {
            memory_id
            for other in siblings
            for step in other.steps
            for memory_id in step.expected_memory_writes
        }
        gold = set(sequence.steps[-1].outcome_checks[0].requires_memory)
        foreign = gold & written_elsewhere
        assert foreign, (
            f"{record['record_id']} is published as tier 'project' but no sibling "
            "sequence wrote any of its gold facts"
        )
        published = record["provenance"]["cross_session_gold_ids"]
        assert set(published) == {aliases.alias(memory_id) for memory_id in foreign}, (
            f"{record['record_id']} names a cross-session gold set the frozen corpus "
            "does not support"
        )
        assert set(published) <= set(record["evidence"]["gold_ids"])


def test_the_gold_set_is_one_width_across_the_whole_release() -> None:
    """B1a. ``len(evidence.gold_ids)`` is the same on every published record, both tiers.

    This is the property that makes the cross-session claim safe to ship. The previous
    release added a goal's shared decisions on top of a fixed local budget, so the gold
    set was wider exactly when more of it came from another session, and
    ``len(gold_ids) - 3`` returned the cross-session count on 160 records of 160. Two
    shipped paragraphs said a downloader could not re-derive that count. A constant width
    is not a measurement that happened to come out flat, it is the generator's budget:
    ``facts_per_task`` is the whole graded count and the shared decisions come out of it.
    """
    records = released()
    assert records
    widths = Counter(len(record["evidence"]["gold_ids"]) for record in records)
    assert len(widths) == 1, f"the release publishes gold sets of several widths: {dict(widths)}"

    by_tier = {
        tier: Counter(
            len(record["evidence"]["gold_ids"]) for record in records if record["tier"] == tier
        )
        for tier in sorted({record["tier"] for record in records})
    }
    assert len(by_tier) == 2, f"expected two published tiers, got {sorted(by_tier)}"
    assert len({next(iter(counts)) for counts in by_tier.values()}) == 1, (
        f"the two tiers publish different gold widths: "
        f"{ {tier: dict(counts) for tier, counts in by_tier.items()} }"
    )


def test_no_affine_function_of_the_gold_width_recovers_the_cross_session_count() -> None:
    """B1b. The defect this release closes, stated as the attack rather than as the fix.

    A downloader holds ``len(evidence.gold_ids)`` and ``tier``. Ask whether either one
    predicts ``len(provenance.cross_session_gold_ids)`` better than the best constant
    guess does. Under the previous release the gold width predicted it exactly; here the
    width is constant, so it carries no information at all, and the tier carries only the
    one bit it announces in the clear (session records have none).

    The assertion is exact, not a threshold: conditioning on the width has to leave the
    project half's cross-session sizes spread over more than one value, which is what
    "the count is not readable off the record" means.
    """
    records = released()
    project = [record for record in records if record["tier"] == "project"]
    assert project, "the release publishes no project record"

    by_width: dict[int, set[int]] = {}
    for record in project:
        width = len(record["evidence"]["gold_ids"])
        by_width.setdefault(width, set()).add(len(record["provenance"]["cross_session_gold_ids"]))

    for width, sizes in sorted(by_width.items()):
        assert len(sizes) > 1, (
            f"every project record with a {width}-id gold set carries exactly "
            f"{sizes.pop()} cross-session gold ids, so the gold width names the count"
        )

    session = [record for record in records if record["tier"] == "session"]
    assert session, "the release publishes no session record"
    assert all(not record["provenance"]["cross_session_gold_ids"] for record in session)


# --------------------------------------------------------------------------- #
# B1c. The join-key channel, measured over EVERY published field rather than a listed
# few.
#
# The guard replaced here conditioned on the candidate-pool width and said, in its own
# docstring, that the pool "is the only width a downloader can condition on". That was
# false, and the counterexample was in the primary key: a record id is
# ``world-seed<N>-task<K>``, and the generator draws a task's shared decisions from the
# ones established before it, so how many cross-session golds a task can even carry is
# monotone in K. The channel is structural rather than a draw of the RNG, it survived a
# regeneration of the corpus unchanged, and it sat one field over from a guard that was
# looking straight at it.
#
# So the family is DERIVED from the published record rather than listed: every leaf
# scalar, the width of every mapping and list, and every integer token inside every
# string, at every depth, minus the label itself. A field nobody thought of is in the
# family because it is in the record.
# --------------------------------------------------------------------------- #

CHANNEL_FWER = 0.01
_CHANNEL_LABEL_PATH = "provenance.cross_session_gold_ids"
_CHANNEL_INT = re.compile(r"\d+")
_TASK_ORDINAL = re.compile(r"-task(\d+)$")


class _ChannelStat(NamedTuple):
    """One published feature, scored against the cross-session count it might name."""

    feature: str
    cells: int
    mutual_information: float
    p_information: float
    accuracy: int
    p_accuracy: float


def _conditioning_features(node: Any, path: str = "") -> dict[str, Any]:
    """Every value a downloader can condition on in one record, keyed by where it sits.

    Three kinds, because a channel can be any of them. The leaf scalars themselves. The
    width of every mapping and list, which is where the previous release kept the count
    outright. And the integers written INSIDE strings, which is where the record id keeps
    the task ordinal and where ``necessity.reason`` restates it in prose - a free-text
    field that reads as narration and is a number in a costume. Each string also gets
    ``#ints``, how many integers it holds, so that a token index present on some records
    and not others is still summarised by a column that every record has.

    ``provenance.cross_session_gold_ids`` is dropped whole, contents and width, because
    it is the label. Everything else in the record is a feature."""
    features: dict[str, Any] = {}
    if isinstance(node, Mapping):
        features[f"len({path or '<root>'})"] = len(node)
        for key in sorted(node):
            here = f"{path}.{key}" if path else str(key)
            if here == _CHANNEL_LABEL_PATH:
                continue
            features.update(_conditioning_features(node[key], here))
    elif isinstance(node, list):
        features[f"len({path})"] = len(node)
    elif isinstance(node, str):
        features[path] = node
        features[f"len({path})"] = len(node)
        tokens = _CHANNEL_INT.findall(node)
        features[f"{path}#ints"] = len(tokens)
        for index, token in enumerate(tokens):
            features[f"{path}#int{index}"] = int(token)
    else:
        features[path] = node
    return features


def _complete_columns(records: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    """The derived features every record carries, as one column each.

    A feature NAME present on only some records is a per-record key rather than a
    published field: ``evidence.distractors`` is a mapping keyed by alias, so descending
    into it names about 4,900 columns, each one separating a single record from the other
    79. Keeping them would not make the guard stronger, it would make it silent - Holm at
    ``CHANNEL_FWER`` over 4,900 features cannot reject anything at a permutation floor of
    ``1 / (PERMUTATION_DRAWS + 1)``, so the family would be too wide to fire. What those
    maps carry that is shared IS in the family, as ``len(evidence.distractors)`` and as
    the per-string token counts; and a column that isolates one record is the per-record
    lookup an open-key release grants outright."""
    rows = [_conditioning_features(record) for record in records]
    shared = set(rows[0]).intersection(*rows[1:])
    return {name: [row[name] for row in rows] for name in sorted(shared)}


def _cells(values: Sequence[Any]) -> tuple[tuple[int, ...], ...]:
    """The partition of the records that conditioning on one feature induces."""
    grouped: dict[Any, list[int]] = {}
    for index, value in enumerate(values):
        grouped.setdefault(value, []).append(index)
    return tuple(tuple(members) for members in grouped.values())


def _entropy(counts: Sequence[int], total: int) -> float:
    return -sum((count / total) * math.log2(count / total) for count in counts if count)


def _observed(
    cells: Sequence[Sequence[int]], labels: Sequence[int], entropy: float
) -> tuple[float, int]:
    """(mutual information in bits, best-conditional-accuracy) for one partition.

    The two statistics answer different questions and this guard needs both. Mutual
    information asks whether the feature carries anything about the count at all. The
    accuracy asks whether what it carries is worth a record to somebody guessing. A
    high-cardinality field such as ``provenance.source_sha256`` maximises the first by
    memorising and buys nothing over the best constant guess on a record it has not seen,
    which is exactly why neither statistic is read without the permutation null under
    it."""
    total = len(labels)
    conditional = 0.0
    hits = 0
    for cell in cells:
        inside = Counter(labels[index] for index in cell)
        hits += max(inside.values())
        conditional += (len(cell) / total) * _entropy(list(inside.values()), len(cell))
    return entropy - conditional, hits


@cache
def _channel_draws(labels: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    """The shared permutation draws, at this file's seed and draw count."""
    rng = random.Random(PERMUTATION_SEED)
    shuffled = list(labels)
    draws: list[tuple[int, ...]] = []
    for _ in range(PERMUTATION_DRAWS):
        rng.shuffle(shuffled)
        draws.append(tuple(shuffled))
    return tuple(draws)


@cache
def _channel_null(
    sizes: tuple[int, ...], labels: tuple[int, ...]
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """The null for both statistics, for every feature sharing one cell-size profile.

    Under a random relabelling the distribution of either statistic depends only on the
    cell SIZES and the label multiset, not on which record sits in which cell, so the
    cells can be read off as consecutive slices of a shuffled labelling and one null
    serves every feature with that profile. That is what makes a family this wide
    affordable at this file's draw count, rather than a reason to shrink the family."""
    total = len(labels)
    values = sorted(set(labels))
    entropy = _entropy(list(Counter(labels).values()), total)
    information: list[float] = []
    accuracy: list[int] = []
    for draw in _channel_draws(labels):
        conditional = 0.0
        hits = 0
        offset = 0
        for size in sizes:
            segment = draw[offset : offset + size]
            offset += size
            counts = [segment.count(value) for value in values]
            hits += max(counts)
            conditional += (size / total) * _entropy(counts, size)
        information.append(entropy - conditional)
        accuracy.append(hits)
    return tuple(information), tuple(accuracy)


def _channel_family(records: Sequence[Mapping[str, Any]]) -> tuple[_ChannelStat, ...]:
    """Every varying published feature of these records, permutation-calibrated."""
    labels = tuple(len(record["provenance"]["cross_session_gold_ids"]) for record in records)
    entropy = _entropy(list(Counter(labels).values()), len(labels))
    varying = {
        name: values for name, values in _complete_columns(records).items() if len(set(values)) > 1
    }

    by_profile: dict[tuple[int, ...], list[tuple[str, tuple[tuple[int, ...], ...]]]] = {}
    for name, values in sorted(varying.items()):
        cells = _cells(values)
        by_profile.setdefault(tuple(sorted(len(cell) for cell in cells)), []).append((name, cells))

    family: list[_ChannelStat] = []
    for sizes, members in by_profile.items():
        null_information, null_accuracy = _channel_null(sizes, labels)
        for name, cells in members:
            information, accuracy = _observed(cells, labels, entropy)
            family.append(
                _ChannelStat(
                    feature=name,
                    cells=len(cells),
                    mutual_information=information,
                    p_information=(
                        sum(1 for value in null_information if value >= information - 1e-12) + 1
                    )
                    / (PERMUTATION_DRAWS + 1),
                    accuracy=accuracy,
                    p_accuracy=(sum(1 for value in null_accuracy if value >= accuracy) + 1)
                    / (PERMUTATION_DRAWS + 1),
                )
            )
    return tuple(family)


def _channel_rejections(
    family: Sequence[_ChannelStat], statistic: Callable[[_ChannelStat], float]
) -> list[_ChannelStat]:
    """Holm-Bonferroni over the channel family, at ``CHANNEL_FWER``.

    Holm rather than a raw per-feature bound because the family is wide and its members
    are dependent by construction - the record id, the sequence id and the necessity
    prose all restate the same ordinal - so a raw 0.01 over 41 features would red about
    one regenerated corpus in three with nothing wrong with it."""
    ordered = sorted(family, key=statistic)
    rejected: list[_ChannelStat] = []
    for index, stat in enumerate(ordered):
        if statistic(stat) >= CHANNEL_FWER / (len(ordered) - index):
            break
        rejected.append(stat)
    return rejected


def _sequence_ordinal_cells(
    records: Sequence[Mapping[str, Any]],
) -> tuple[tuple[int, ...], ...]:
    """The partition the published record ids induce through their task ordinal."""
    ordinals: list[int] = []
    for record in records:
        found = _TASK_ORDINAL.search(str(record["record_id"]))
        assert found is not None, f"{record['record_id']} does not end in a task ordinal"
        ordinals.append(int(found.group(1)))
    return _cells(ordinals)


def test_no_published_field_recovers_the_cross_session_count() -> None:
    """B1c. The count is not readable off ANY published field, measured over all of them.

    Two layers, and the first is the one the previous guard got wrong. Its exact width
    checks are kept below, because they are exact: the gold and superseded sets are one
    width across the release, every pool width that more than one project record shares
    holds more than one cross-session size, and conditioning on the pool width beats the
    best constant guess by at most one record. What it then claimed was that the pool
    width was the last thing left to condition on, and the record id falsifies that in
    the clear.

    So the second layer ranges over the whole record: every leaf, every container width,
    every integer inside every string, calibrated against two statistics, with Holm at
    ``CHANNEL_FWER`` across the family. Only the session tier is asserted outright - its
    cross-session set is empty on every record, so there is nothing there to recover -
    and the project tier is where the whole measurement lives.

    What this MEASURES on the release, as distinct from what it asserts: 41 of the 130
    features every project record carries vary, over 20 distinct cell-size profiles.
    Exactly four clear a raw 0.01 on mutual information - ``record_id#int1``,
    ``provenance.sequence_id#int1`` and the two trailing integers of ``necessity.reason``
    - all four at 0.1516 bits and p = 1.0e-4, and all four induce the SAME two-cell
    partition, the task ordinal. It buys a downloader nothing: conditioning on the ordinal
    predicts the count on 57 project records of 80, which is exactly what the best
    constant guess gets, so p on the accuracy statistic is 1.0000 and Holm rejects nothing
    there. Its one operational use is a hard exclusion - task 1 splits 36/4/0 over counts
    1/2/3 and task 2 splits 21/14/5, so a task-1 record never carries three - and several
    published widths have that shape too.

    The assertions are directional on purpose. A generator that stopped writing the
    ordinal into the record id keeps this green; a generator that let any published field
    start naming the count does not."""
    records = released()
    project = [record for record in records if record["tier"] == "project"]
    session = [record for record in records if record["tier"] == "session"]
    assert project and session, "the release does not publish both tiers"
    assert {len(record["provenance"]["cross_session_gold_ids"]) for record in session} == {
        0
    }, "a session-tier record carries a cross-session gold id"

    for field, widths in (
        ("evidence.gold_ids", {len(record["evidence"]["gold_ids"]) for record in records}),
        (
            "evidence.superseded_ids",
            {len(record["evidence"]["superseded_ids"]) for record in records},
        ),
    ):
        assert len(widths) == 1, f"{field} varies across the release: {sorted(widths)}"

    by_width: dict[int, Counter[int]] = {}
    for record in project:
        width = len(record["candidate_pool"]["ids"])
        by_width.setdefault(width, Counter())[
            len(record["provenance"]["cross_session_gold_ids"])
        ] += 1
    assert len(by_width) > 1, "the candidate pool is one width across the whole release"

    for width, sizes in sorted(by_width.items()):
        if sum(sizes.values()) > 1:
            assert len(sizes) > 1, (
                f"every project record with a {width}-id pool carries exactly "
                f"{next(iter(sizes))} cross-session gold ids, so the pool width names "
                "the count"
            )

    counts = [len(record["provenance"]["cross_session_gold_ids"]) for record in project]
    constant = Counter(counts).most_common(1)[0][1]
    from_width = sum(max(sizes.values()) for sizes in by_width.values())
    assert from_width - constant <= 1, (
        f"the pool width predicts the cross-session count on {from_width} of "
        f"{len(project)} project records, against {constant} for the best constant guess"
    )

    # The derivation has to REACH the record, or a family that quietly stopped
    # enumerating would pass this whole test by looking at nothing. Checked against the
    # columns rather than the family, because a column that turns out to be CONSTANT
    # across the release - ``necessity.reason#ints`` is, every reason holds the same
    # count of integers - is correctly derived and then correctly dropped.
    columns = _complete_columns(project)
    for required in (
        "record_id",
        "record_id#int1",
        "necessity.reason",
        "necessity.reason#ints",
        "necessity.reason#int2",
        "provenance.sequence_id#int1",
        "len(candidate_pool.ids)",
        "len(evidence.distractors)",
    ):
        assert required in columns, f"the derivation does not reach {required}"

    family = _channel_family(project)
    names = {stat.feature for stat in family}
    assert {"record_id#int1", "provenance.sequence_id#int1", "necessity.reason#int2"} <= names, (
        "the features known to restate the task ordinal are not in the scored family: "
        f"{sorted(names)}"
    )
    assert CHANNEL_FWER / len(family) > 1 / (PERMUTATION_DRAWS + 1), (
        f"a family of {len(family)} features cannot reject anything at Holm's smallest "
        f"threshold {CHANNEL_FWER / len(family):.2e} against a permutation floor of "
        f"{1 / (PERMUTATION_DRAWS + 1):.2e}; the guard would be unable to fire"
    )

    exploitable = _channel_rejections(family, lambda stat: stat.p_accuracy)
    assert exploitable == [], (
        "a published field predicts the cross-session count better than the best constant "
        f"guess of {constant} of {len(project)}: "
        f"{[(stat.feature, stat.accuracy, stat.p_accuracy) for stat in exploitable]}"
    )

    ordinal = frozenset(frozenset(cell) for cell in _sequence_ordinal_cells(project))
    for stat in _channel_rejections(family, lambda stat: stat.p_information):
        partition = frozenset(frozenset(cell) for cell in _cells(columns[stat.feature]))
        assert partition == ordinal, (
            f"{stat.feature} carries information about the cross-session count "
            f"({stat.mutual_information:.4f} bits, p = {stat.p_information:.2e}) and does "
            "not induce the task-ordinal partition; it is a channel nothing in this "
            "release accounts for"
        )

    entropy = _entropy(list(Counter(counts).values()), len(counts))
    _, ordinal_accuracy = _observed(_sequence_ordinal_cells(project), counts, entropy)
    assert ordinal_accuracy - constant <= 1, (
        f"the task ordinal predicts the cross-session count on {ordinal_accuracy} of "
        f"{len(project)} project records, against {constant} for the best constant guess"
    )


def test_the_channel_family_catches_a_field_that_does_name_the_count() -> None:
    """B1c, positive control. A calibrated guard has to be shown to bite.

    Every assertion above is a null result, and a null result from a measurement that has
    stopped working looks exactly like a null result from a clean corpus. So publish the
    count: give every record a field whose value IS the width of its cross-session set,
    and a second that is that width on three records in four and a per-record constant on
    the fourth. Both have to be rejected by Holm on both statistics. The noisy one is
    there because the exact one would also be caught by a much weaker test, and a real
    channel is never exact."""
    project = [record for record in released() if record["tier"] == "project"]
    leaked = [
        {
            **record,
            "question": {
                **record["question"],
                "planted_exact": len(record["provenance"]["cross_session_gold_ids"]),
                "planted_noisy": (
                    len(record["provenance"]["cross_session_gold_ids"]) if index % 4 else -index
                ),
            },
        }
        for index, record in enumerate(project)
    ]

    family = _channel_family(leaked)
    by_name = {stat.feature: stat for stat in family}
    informative = {
        stat.feature for stat in _channel_rejections(family, lambda stat: stat.p_information)
    }
    exploitable = {
        stat.feature for stat in _channel_rejections(family, lambda stat: stat.p_accuracy)
    }
    for planted in ("question.planted_exact", "question.planted_noisy"):
        assert planted in by_name, f"the derived family does not reach {planted}"
        assert planted in informative, (
            f"{planted} publishes the cross-session count and the information layer did "
            f"not reject it: {by_name[planted]}"
        )
        assert planted in exploitable, (
            f"{planted} publishes the cross-session count and the accuracy layer did not "
            f"reject it: {by_name[planted]}"
        )


def _moved_claim_traces(
    record: Mapping[str, Any],
    siblings: Sequence[Mapping[str, Any]],
    memory_id: str,
    direction: str,
) -> set[str]:
    """Every published thing that contradicts moving ``memory_id`` across the boundary.

    Computed from the released fields only, and from the corpus's own construction
    rules rather than from the validator's code, so this is a second opinion on the
    validator and not a paraphrase of it:

    * ``alias-repeats`` - the id is published by another record of the world, and a
      memory one sequence wrote for itself is minted at sequence scope and appears
      nowhere else;
    * ``sibling-grades-it-local`` / ``sibling-grades-it-cross`` - another record of the
      world grades that subject on the other side of the boundary, and a world's shared
      subjects come OUT of the pool its sequences draw local subjects from;
    * ``chain-subject`` - the record publishes superseded versions of that subject, and
      a chain is authored inside one sequence;
    * ``local-floor`` / ``empty-cross-on-project`` - the move breaks a count the record
      states about itself.

    A move with none of these is one the release does not contradict anywhere. Whether
    the validator agrees is the assertion, not the definition.
    """
    cross = set(record["provenance"]["cross_session_gold_ids"])
    local = [gold for gold in record["evidence"]["gold_ids"] if gold not in cross]
    subject = fact_subject(record["evidence"]["gold"][memory_id])
    found: set[str] = set()

    if any(memory_id in bucket_merge(other) for other in siblings):
        found.add("alias-repeats")
    if subject in {fact_subject(text) for text in record["evidence"]["superseded"].values()}:
        found.add("chain-subject")

    def sibling_subjects(cross_side: bool) -> set[str]:
        subjects: set[str] = set()
        for other in siblings:
            other_cross = set(other["provenance"]["cross_session_gold_ids"])
            wanted = [
                gold
                for gold in other["evidence"]["gold_ids"]
                if (gold in other_cross) is cross_side
            ]
            subjects |= {fact_subject(other["evidence"]["gold"][gold]) for gold in wanted}
        return subjects

    if direction == "promotion":
        if len(local) - 1 < MIN_LOCAL_FACTS:
            found.add("local-floor")
        if subject in sibling_subjects(cross_side=False):
            found.add("sibling-grades-it-local")
    else:
        if len(cross) == 1:
            found.add("empty-cross-on-project")
        if subject in sibling_subjects(cross_side=True):
            found.add("sibling-grades-it-cross")
    return found


def test_a_moved_cross_session_claim_is_caught_exactly_when_it_leaves_a_trace() -> None:
    """B1c. The one published field that cannot be checked completely, measured.

    ``provenance.cross_session_gold_ids`` is answer-key material, so a rule that decided
    an arbitrary id would be the derivation the harness seam exists to prevent. What the
    validator has instead are the exact consequences the corpus fixes. The round-4 audit
    found the previous release's scalar version of this claim could be inflated inside
    the gold set with nothing noticing, so the honest thing to ship with the id set is
    how far the checking reaches.

    The sweep moves ONE id across the boundary on one project record, leaves the rest of
    the release alone, and asks the shipped validator. Both directions, every id, 400
    moves. The assertion is not the rate: it is that the validator's verdict and the
    published trace are the SAME set, so every move the release contradicts is caught
    and every miss is a move the release does not contradict anywhere. A rule deleted
    from the validator breaks the equality on the left, and a corpus re-drawn so that a
    trace stops existing breaks it on the right.

    What is left over is not closed by any rule, and ``public/data/SHA256SUMS`` is what
    closes it, exactly as it closes a value rewritten consistently through a record.
    """
    records = released()
    by_scope: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        by_scope.setdefault(str(record["question"]["scope_id"]), []).append(record)

    for direction in ("promotion", "demotion"):
        stat = relabel_coverage()[direction]
        missed = set(stat.misses)
        assert stat.n_tried, f"the release offers no {direction} to sweep"
        traceless = 0
        for record in records:
            if record["tier"] != "project":
                continue
            cross = set(record["provenance"]["cross_session_gold_ids"])
            moved = (
                [gold for gold in record["evidence"]["gold_ids"] if gold not in cross]
                if direction == "promotion"
                else sorted(cross)
            )
            siblings = [
                other
                for other in by_scope[str(record["question"]["scope_id"])]
                if other["record_id"] != record["record_id"]
            ]
            for memory_id in moved:
                traces = _moved_claim_traces(record, siblings, memory_id, direction)
                caught = (str(record["record_id"]), memory_id) not in missed
                traceless += int(not traces)
                evidence = (
                    "contradicts it via " + ", ".join(sorted(traces))
                    if traces
                    else "says nothing about it"
                )
                assert caught is bool(traces), (
                    f"{direction} of {memory_id} in {record['record_id']}: the validator "
                    f"{'caught' if caught else 'cleared'} it while the release {evidence}"
                )
        assert len(missed) == traceless == stat.n_tried - stat.n_caught


def test_the_released_timeline_does_not_hand_over_the_gold_set() -> None:
    """B3. The strongest structural attack the timeline still admits - take the k
    latest-closing candidates, k being the gold size the record states - reconstructs
    the gold set at chance, not at the 1.000 the v1 release scored.

    v1 published each candidate's ``supersedes`` list. Subtracting the union of those
    lists from the pool returned the gold set exactly, on every record. That field is
    gone, and this test measures what is left.
    """
    records = released()
    assert records
    assert all(
        "supersedes" not in candidate
        for record in records
        for candidate in record["loo"]["candidates"]
    ), "a candidate still publishes its supersession edges, which IS the answer key"

    exact = 0
    precision_sum = 0.0
    chance_exact = 0.0
    chance_precision = 0.0
    for record in records:
        pool = set(record["candidate_pool"]["ids"])
        gold = set(record["evidence"]["gold_ids"])
        assert gold <= pool
        closed = {
            str(c["id"]): str(c["closed"])
            for c in record["loo"]["candidates"]
            if c["closed"] is not None and str(c["id"]) in pool
        }
        k = len(gold)
        picked = set(sorted(closed, key=lambda mid: closed[mid], reverse=True)[:k])
        exact += int(picked == gold)
        precision_sum += len(picked & gold) / k
        # A uniform k-subset of the pool: the rate an attacker gets with no structure
        # to read at all.
        chance_exact += 1.0 / math.comb(len(pool), k)
        chance_precision += k / len(pool)

    n = len(records)
    measured = exact / n
    chance = chance_exact / n
    detail = (
        f"exact {exact}/{n} = {measured:.4f} vs chance {chance_exact:.4f}/{n} = {chance:.4f}; "
        f"mean precision {precision_sum / n:.4f} vs chance {chance_precision / n:.4f}"
    )
    # Three times the closed-form rate, which on this corpus is 0.0038 against a v1
    # reading of 1.0000. The bound is loose on purpose: it is here to catch a
    # RECONSTRUCTION, not to pin a rate that a corpus reshape would move.
    print(detail)
    assert measured <= 3.0 * chance, detail
    assert measured < 0.05, detail


def test_the_released_pool_zeroes_an_answer_that_states_everything() -> None:
    """B4. The pool carries the superseded values, so the policy that needs no
    retrieval at all - paste every candidate and let the grader find the answer - states
    a forbidden value on every record and scores zero.

    The control is the same policy restricted to the gold: it still passes, which is
    what makes the zero above a trap firing rather than a broken grader.
    """
    driver = baseline_driver()
    records = driver.load_release(RELEASE_DIR)
    assert records

    whole_pool = sum(
        1 for record in records if driver.grade("\n".join(record.candidates.values()), record)[1]
    )
    gold_only = sum(
        1 for record in records if driver.grade("\n".join(record.gold_payloads.values()), record)[1]
    )
    n = len(records)
    assert (
        whole_pool == 0
    ), f"copy-paste-the-whole-pool passes {whole_pool}/{n}; the stale bucket is not trapping"
    assert (
        gold_only == n
    ), f"copy-paste-the-gold passes only {gold_only}/{n}; the grader is broken, not the trap"
    assert all(
        record.superseded_ids for record in records
    ), "a released record carries no stale candidate, so no answer policy can fail on it"


# --------------------------------------------------------------------------- #
# The three validity residuals the second adversarial pass left inside rows it had
# marked fixed (mem-r6yzk R1/R2/R3). Each one is a policy that scores well above
# chance WITHOUT retrieving anything, so each is measured here over the whole released
# tree rather than over a hand-built fixture: a per-fixture check would pass on a
# corpus whose aggregate still hands the answer over.
# --------------------------------------------------------------------------- #

# R1. Ten subjects can carry a project's cross-session decision and the draw is
# uniform over them, so the expected share of any one is 0.100. The bound sits well
# above that because the released draw is one finite sample of 40 worlds, and well
# below the 1.000 v1 scored, where the cross-session fact was the charter every time.
# This release reads 0.130 on "the data retention window" (14 of 108 cross-session
# facts, tied there with the checkout_v2 feature flag state), and every one of the ten
# carries at least 7.
#
# What this share does NOT answer is the OTHER direction of the same table, and a third
# pass found the leak sitting in it: P(cross-session | subject) was 1.000 on the charter
# while the charter's share of all cross-session facts was 0.118, comfortably inside
# this bound. Share asks "does one subject carry most of the cross-session facts"; the
# exploitable question is "is some subject ALWAYS the cross-session one", and it is
# asked by ``test_no_subject_is_always_the_cross_session_one`` below.
MAX_SHARED_SUBJECT_SHARE = 0.40

# R2. "Whichever candidate closed last". The published order is a uniform permutation
# of the pool since mem-r6yzk R3b, so the expected edge over the base rate is exactly
# zero and what is left is the finite-sample draw. v1 read 0.700 session / 0.725 project
# against base rates of 0.375 and 0.400; the linear-extension release that followed read
# 0.2875 against 0.2143 and 0.2250 against 0.2240. This release reads 0.2750 against a
# 0.2005 project base rate and 0.1750 against a 0.2028 session one, and its recall edges
# are 0.0005 project, 0.0047 session, 0.0021 corpus.
#
# One constant used to bound both statistics, which was slack, because the two have very
# different nulls. Both are exact on this corpus rather than guessed: the hit is
# Bernoulli(k/N) per record, the recall is Hypergeometric(N, k, k)/k, and each convolves
# to the distribution of its own mean.
#
#     statistic   cut       null sd   1-in-100   1-in-1000
#     hit         a tier     0.0448     0.113      0.148
#     hit         corpus     0.0317     0.080      0.105
#     recall      a tier     0.0183     0.047      0.060
#     recall      corpus     0.0129     0.033      0.042
#
# So 0.08 was 1.8 null standard deviations for the hit and 4.4 for the recall. The hit
# bound STAYS at 0.08 and cannot honestly go lower: it already sits under the 1-in-100
# point of its own null, and a bound inside a statistic's own spread rejects honest
# corpora rather than leaky ones. The recall bound drops to 0.055, three null standard
# deviations on a tier and four on the corpus. Both are now asserted on the corpus as
# well as on each tier, which is the cut where the null is tightest and where a leak
# planted across both tiers shows up first. A re-draw that exceeds either is a corpus to
# reject rather than a bound to widen.
#
# These are coarse acceptance criteria for a frozen corpus, not the tests with the
# power. ``test_the_published_clock_says_nothing_about_which_value_is_current`` scores
# the same channel against an exact Poisson-binomial null at every cut a policy reads
# for free, and reads p = 0.4898 on this release for the whole-pool half of it.
MAX_RECENCY_HIT_EDGE = 0.08
MAX_RECENCY_RECALL_EDGE = 0.055

# R3. Ten authored values per subject put the expected modal share at 0.10. The bound
# holds the finite-sample maximum, which over roughly 40 project draws per subject sits
# near 0.25. v1 read 0.513 on "the approved rollback command" with four values. This
# release reads 0.256 on "the paging severity floor", 11 of the 43 project records that
# ask about it answering "any customer-visible regression".
MAX_CONSTANT_GUESS_SHARE = 0.35

# Below this many observations a per-cell share is noise rather than a policy anyone
# could exploit: one lucky value out of 8 draws is 0.125 of that cell and nothing at
# all of the corpus. Cells at or above it are asserted; the pooled figure covers the
# rest.
#
# The floor is sound for THIS statistic, where a constant answer has to beat the value
# bank, and unsound for a rate that can be degenerate. A cell holding 13 observations at
# a rate of 1.000 is a rule, not noise, however small the cell is, which is why the
# cross-session guard below carries no floor at all.
MIN_CELL = 20


def _internal_contents() -> dict[str, str]:
    """Every memory the frozen corpus writes, by its INTERNAL id.

    The release publishes keyed-HMAC aliases and the inverse map is private, so a test
    that needed alias -> content would have to read the mint. Joining on ``record_id``
    and reading the frozen side instead keeps the suite clear of the secret.
    """
    contents: dict[str, str] = {}
    for sequence in _sequences():
        for step in sequence.steps:
            contents.update(step.expected_memory_writes)
    return contents


def _cross_session_gold(
    sequence: BenchmarkSequence, frozen: dict[str, BenchmarkSequence]
) -> set[str]:
    """The gold ids of ``sequence`` that some OTHER sequence of the same world wrote."""
    world_id = sequence.sequence_id.rsplit("-", 1)[0]
    written_elsewhere = {
        memory_id
        for other in frozen.values()
        if other.sequence_id.rsplit("-", 1)[0] == world_id
        and other.sequence_id != sequence.sequence_id
        for step in other.steps
        for memory_id in step.expected_memory_writes
    }
    return set(sequence.steps[-1].outcome_checks[0].requires_memory) & written_elsewhere


def _project_cross_session() -> list[tuple[str, frozenset[str], tuple[str, ...]]]:
    """``(world_id, cross-session gold ids, their subjects)`` per published project record."""
    frozen = _frozen_by_id()
    contents = _internal_contents()
    rows: list[tuple[str, frozenset[str], tuple[str, ...]]] = []
    for record in released():
        if record["tier"] != "project":
            continue
        sequence = frozen[record["record_id"]]
        gold = _cross_session_gold(sequence, frozen)
        assert gold, f"{record['record_id']}: no cross-session gold"
        subjects = tuple(sorted(fact_subject(contents[memory_id]) for memory_id in gold))
        rows.append((sequence.sequence_id.rsplit("-", 1)[0], frozenset(gold), subjects))
    assert rows, "the release publishes no project record"
    return rows


def test_the_cross_session_fact_is_not_one_fixed_lookup() -> None:
    """R1. In v1 every project record's single cross-session gold was the charter, and
    both records of a world carried the SAME charter memory. One fetch, learned once,
    answered the cross-session half of the entire project tier.

    Three things have to hold for that policy to be worthless: the subject varies, the
    count varies, and two records of one world do not resolve to the same fetch.
    """
    rows = _project_cross_session()

    subjects = Counter(subject for _, _, subject_tuple in rows for subject in subject_tuple)
    assert len(subjects) > 1, f"one subject carries every cross-session fact: {subjects}"
    total = sum(subjects.values())
    top_subject, top_count = subjects.most_common(1)[0]
    share = top_count / total
    assert share <= MAX_SHARED_SUBJECT_SHARE, (
        f"{top_subject!r} carries {top_count}/{total} = {share:.3f} of all cross-session "
        f"facts, over the {MAX_SHARED_SUBJECT_SHARE} bound: {subjects}"
    )

    counts = Counter(len(gold) for _, gold, _ in rows)
    assert len(counts) > 1, (
        f"every project record needs exactly {next(iter(counts))} cross-session fact(s), "
        "so the number itself is a constant an arm can assume"
    )

    by_world: dict[str, list[frozenset[str]]] = {}
    for world_id, gold, _ in rows:
        by_world.setdefault(world_id, []).append(gold)
    shared = {world_id: sets for world_id, sets in by_world.items() if len(sets) != len(set(sets))}
    assert (
        not shared
    ), f"{len(shared)} world(s) answer two records with one identical fetch: {shared}"


def test_the_close_order_does_not_point_at_the_gold() -> None:
    """R2. Rank the pool by close time and take the latest. In v1 that landed on a gold
    fact 0.700 of the time in the session tier and 0.725 in the project tier, against
    base rates of 0.375 and 0.400: a recency prior stood in for retrieval and scored
    most of the way to a real retriever.

    The clock is a uniform permutation of the pool since R3b, so "closed last" encodes
    nothing at all and the expected edge over the base rate is zero. The coarse bounds
    here are a corpus acceptance criterion; the power against this channel is in
    ``test_the_published_clock_says_nothing_about_which_value_is_current``, which scores
    the same counts against their exact null.

    Three cuts, not two. Each tier is reported separately because the release reports
    them separately, and the corpus is scored as well because that is where the null is
    tightest: a leak planted across both tiers is diluted by neither.
    """
    records = released()
    assert records

    per_tier: dict[str, list[tuple[float, float, float]]] = {}
    for record in records:
        pool = set(record["candidate_pool"]["ids"])
        gold = set(record["evidence"]["gold_ids"])
        closed = {
            str(c["id"]): str(c["closed"])
            for c in record["loo"]["candidates"]
            if c["closed"] is not None and str(c["id"]) in pool
        }
        assert set(closed) == pool, (
            f"{record['record_id']}: {len(pool - set(closed))} pool candidate(s) publish "
            "no close time, so this attack cannot even be measured against them"
        )
        ranked = sorted(closed, key=lambda mid: closed[mid], reverse=True)
        assert closed[ranked[0]] != closed[ranked[1]], (
            f"{record['record_id']}: two candidates tie for latest, so 'the most recent' "
            "is not well defined"
        )
        k = len(gold)
        hit = float(ranked[0] in gold)
        recall = len(set(ranked[:k]) & gold) / k
        base = k / len(pool)
        per_tier.setdefault(str(record["tier"]), []).append((hit, recall, base))

    assert set(per_tier) == {"session", "project"}, sorted(per_tier)
    cuts = dict(per_tier)
    cuts["corpus"] = [row for rows in per_tier.values() for row in rows]
    for cut, rows in sorted(cuts.items()):
        n = len(rows)
        p_most_recent = sum(hit for hit, _, _ in rows) / n
        recall = sum(r for _, r, _ in rows) / n
        base = sum(b for _, _, b in rows) / n
        detail = (
            f"{cut}: n={n} P(gold | closed last)={p_most_recent:.4f} "
            f"(edge {abs(p_most_recent - base):.4f}, bound {MAX_RECENCY_HIT_EDGE}) "
            f"rank-by-recency recall={recall:.4f} "
            f"(edge {abs(recall - base):.4f}, bound {MAX_RECENCY_RECALL_EDGE}) "
            f"base rate={base:.4f}"
        )
        print(detail)
        assert abs(p_most_recent - base) <= MAX_RECENCY_HIT_EDGE, detail
        assert abs(recall - base) <= MAX_RECENCY_RECALL_EDGE, detail


def test_the_published_cut_is_still_checkable_from_the_release_alone() -> None:
    """The R2 repair reorders close times, which is exactly the field the temporal
    leave-one-out cut is read off. A downloader must still be able to verify the cut
    without this repo: every candidate offered closed strictly before the question was
    asked, the record's own memory is withheld, and the boundary is the ask time.
    """
    for record in released():
        boundary = str(record["loo"]["boundary"])
        assert str(record["question"]["asked_at"]) == boundary, record["record_id"]

        pool = set(record["candidate_pool"]["ids"])
        by_id = {str(c["id"]): c for c in record["loo"]["candidates"]}
        assert pool <= set(by_id), record["record_id"]

        for memory_id in sorted(pool):
            closed = by_id[memory_id]["closed"]
            assert closed is not None, f"{record['record_id']}/{memory_id}: no close time"
            assert str(closed) < boundary, (
                f"{record['record_id']}/{memory_id}: closed {closed} is not before the "
                f"{boundary} cut"
            )

        excluded = set(map(str, record["loo"]["excluded_ids"]))
        assert excluded == set(by_id) - pool, record["record_id"]
        assert str(record["loo"]["query"]["id"]) in excluded, record["record_id"]
        assert "temporal" in record["loo"]["exclusion_axes"], record["record_id"]


def test_no_constant_answer_carries_a_subject() -> None:
    """R3. Pick one value per subject WITH the answers in hand and answer every
    question about that subject with it, retrieving nothing. In v1 that scored 0.513 on
    project "the approved rollback command" against a 0.231 mean over the other
    subjects: one subject was answerable by memorising a single string.

    The count below is an upper bound on the policy, not the policy's score. A constant
    that happens to equal a superseded value on some record states a forbidden value
    there and scores zero on it. Bounding the upper bound is the conservative side.
    """
    per_cell: dict[tuple[str, str], Counter[str]] = {}
    pooled: dict[str, Counter[str]] = {}
    for record in released():
        tier = str(record["tier"])
        for payload in record["evidence"]["gold"].values():
            subject = fact_subject(payload)
            value = fact_value(payload)
            per_cell.setdefault((tier, subject), Counter())[value] += 1
            pooled.setdefault(subject, Counter())[value] += 1
    assert pooled, "the release carries no gold payload to attack"

    worst: list[tuple[float, str]] = []
    for (tier, subject), values in per_cell.items():
        n = sum(values.values())
        if n < MIN_CELL:
            continue
        value, count = values.most_common(1)[0]
        worst.append((count / n, f"{tier} {subject!r} -> {value!r} {count}/{n}"))
    assert worst, f"no subject reaches {MIN_CELL} observations; the measurement is vacuous"

    for subject, counted in pooled.items():
        n = sum(counted.values())
        value, count = counted.most_common(1)[0]
        worst.append((count / n, f"corpus-wide {subject!r} -> {value!r} {count}/{n}"))

    worst.sort(reverse=True)
    share, detail = worst[0]
    print(f"the best constant answer scores {share:.3f}: {detail}")
    assert share <= MAX_CONSTANT_GUESS_SHARE, (
        f"a constant answer scores {share:.3f} with no retrieval, over the "
        f"{MAX_CONSTANT_GUESS_SHARE} bound: {detail}"
    )


# --------------------------------------------------------------------------- #
# The pool ORDER. A third adversarial pass found two answer channels riding in it
# (mem-r6yzk leak 4 and leak 5), and both are aggregate: no per-record check can see a
# bias that only exists as a rate over 160 records. Both are therefore measured here
# over the released tree, against the frozen corpus and the shipped validator.
# --------------------------------------------------------------------------- #

VALIDATOR_PATH = REPO_ROOT.parent / "public" / "validator" / "membench_validate.py"

# Below this a pool order is rejected as informative about role. The published order
# reads p = 0.0546 on the lead statistic corpus-wide and 0.6152 on the mean rank, so the
# bound is not close to it; an order that promotes gold on one record in ten reads 0.0006,
# so the bound catches a bias well inside what a truncating runner would exploit. The
# tiers are half the sample and carry half the power, which is why the power witnesses
# below are asserted on the corpus-wide figure and the tier figures ride the same bound.
MIN_RANK_UNIFORMITY_P = 0.01

# Leak 5. A first-k policy over `candidate_pool.ids` recalls what chance recalls, k over
# the pool size, which is 0.242 pooled on this release against a measured 0.245, and
# passes 0 of 160 records against a chance 0.02. The slack is roughly three standard
# deviations of either rate over 160 records and leaves the 1.000 / 1.000 the bucket
# merge scores far outside it.
#
# This is an acceptance criterion for a frozen release, not a hypothesis test. A future
# re-draw that exceeds it is a release to re-order rather than a bound to widen.
MAX_FIRST_K_EDGE = 0.05


# How often the leading witness below promotes gold: every ``1 / LEAD_WITNESS_SHARE``-th
# record, taken in published record order, so the choice of records is blind to where the
# gold already sits. One in ten is a deliberately small thumb on the scale -- it moves the
# corpus first-is-gold rate by about nine points -- because a witness that promotes gold
# everywhere would only show that the bound rejects certainty.
LEAD_WITNESS_SHARE = 10


def _gold_led_order(record: Mapping[str, Any]) -> list[str]:
    """The published order with one gold moved to the front of every tenth record.

    Which records are touched is decided by position in the release, not by the record's
    contents, so the witness adds a lead bias and nothing else.
    """
    order = published_order(record)
    index = [str(row["record_id"]) for row in released()].index(str(record["record_id"]))
    if index % LEAD_WITNESS_SHARE:
        return order
    gold = {str(alias) for alias in record["evidence"]["gold_ids"]}
    first = next(alias for alias in order if alias in gold)
    return [first, *(alias for alias in order if alias != first)]


def _gold_nudged_order(record: Mapping[str, Any]) -> list[str]:
    """The published order with every gold swapped one place earlier.

    The bias is spread over every gold entry rather than concentrated at position zero,
    which is the shape the mean-rank statistic exists to catch: a pool that pulls gold
    forward without ever putting it first would clear a lead bound outright.
    """
    order = published_order(record)
    gold = {str(alias) for alias in record["evidence"]["gold_ids"]}
    nudged = list(order)
    for position in range(1, len(nudged)):
        if nudged[position] in gold and nudged[position - 1] not in gold:
            nudged[position - 1], nudged[position] = nudged[position], nudged[position - 1]
    return nudged


def test_the_published_pool_order_ranks_gold_uniformly() -> None:
    """Leak 4. `candidate_pool.ids` is the order an arm is seeded in, so it is an input
    to every arm, and any correlation between a position and a role is an answer no arm
    had to retrieve.

    The order this release replaced was ascending by alias. That looks neutral and is
    not. An alias is one keyed hash per memory, so it is one fixed pseudorandom draw that
    returns in every record carrying that memory, and a low-sorting memory that happens to
    be gold is counted once per record it appears in. Keying the draw by `record_id` gives
    a memory an independent position in each record it appears in, so no one memory's
    accident accumulates.

    That accident is why the replaced order is no longer what the bound is tested
    against. It leaked by luck, not by construction: on the release it first failed this
    bound it led with gold on 47 records of 160, and when the corpus was re-drawn the same
    rule led with gold on 35 and cleared the bound at p = 0.3191. A power witness whose
    bite depends on the draw is the defect this file exists to remove, one level up. So
    the assertions below run against two orders that leak by construction -- one that
    promotes gold to the front of every tenth record, one that moves every gold a single
    place earlier -- and the replaced order is printed as history rather than relied on.

    The two witnesses are not interchangeable, and the measurement says so. Promoting on
    a tenth of records leads with gold on 51 records of 160 and fails the lead bound at
    p = 0.0006 while leaving the mean rank at 0.4926, p = 0.4463 -- it clears the rank
    bound outright. The one-place nudge fails both. So the lead bound catches something
    the rank bound does not, which is the reason to state both, and each is asserted
    against a witness that reaches it.
    """
    published = published_ranks()
    for stat in published.values():
        print(stat)

    for stat in published.values():
        assert (
            stat.p_first_upper >= MIN_RANK_UNIFORMITY_P
        ), f"the published pool order leads with gold more often than chance -- {stat}"
        assert (
            stat.p_rank_two >= MIN_RANK_UNIFORMITY_P
        ), f"the published pool order does not place gold uniformly -- {stat}"

    for stat in ascending_alias_ranks().values():
        print(f"ascending-alias order, replaced -- {stat}")

    led = rank_uniformity(ordered_rows(_gold_led_order))["corpus"]
    print(f"gold-led witness -- {led}")
    assert led.p_first_upper < MIN_RANK_UNIFORMITY_P, (
        f"promoting gold to the front of one record in {LEAD_WITNESS_SHARE} does not read "
        f"as leading with gold -- {led}. The lead bound is evidence of something only "
        "while an order that leads with gold fails it"
    )

    nudged = rank_uniformity(ordered_rows(_gold_nudged_order))["corpus"]
    print(f"gold-nudged witness -- {nudged}")
    assert nudged.p_rank_two < MIN_RANK_UNIFORMITY_P, (
        f"moving every gold one place earlier does not read as non-uniform -- {nudged}. "
        "The rank bound is evidence of something only while an order that pulls gold "
        "forward fails it"
    )


def _validator() -> Any:
    """The shipped standalone validator, imported by path the way a downloader runs it."""
    spec = importlib.util.spec_from_file_location("membench_validate_contract", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_bucket_merge_no_longer_answers_the_benchmark() -> None:
    """Leak 5. The published contract used to pin no pool order at all. It said the
    candidate texts are "merged into one flat id-to-text mapping", and a third party
    doing that literally writes `{**gold, **distractors, **superseded}`, whose leading
    entries are the gold set of every record. A first-k policy over that mapping answers
    every question in the benchmark while retrieving nothing. Our own driver happened to
    sort before seeding; the contract never required it, so the result was a benchmark
    that scored a perfect ceiling for anyone who followed its own instructions.

    Three things have to hold at once for that to be closed, so all three are measured
    here over the released tree:

    * the bucket merge is still the exploit it was. Without this the other two numbers
      would be reporting a broken grader rather than a repaired contract;
    * the order the README now instructs, `candidate_pool.ids`, scores the same policy
      at chance, on recall and on pass rate both;
    * a record published in bucket-merge order is REJECTED by the shipped validator, so
      the instruction is enforced rather than merely written down.
    """
    merged = bucket_merge_first_k()
    published = published_first_k()
    k = published.k
    n = published.n_records
    print(merged)
    print(published)

    assert merged.recall == 1.0 and merged.passes == n, (
        f"the bucket merge recalls {merged.recall:.3f} and passes {merged.passes}/{n}, so "
        "it is no longer the exploit the figures below are measured against, and those "
        "figures now compare to nothing"
    )

    recall_edge = published.recall - published.chance_recall
    assert recall_edge <= MAX_FIRST_K_EDGE, (
        f"a first-{k} policy over candidate_pool.ids recalls {published.recall:.3f} "
        f"against a chance {published.chance_recall:.3f}, an edge of {recall_edge:.3f} "
        f"over the {MAX_FIRST_K_EDGE} bound: the published order is still ranking gold early"
    )
    pass_edge = (published.passes - published.chance_passes) / n
    assert pass_edge <= MAX_FIRST_K_EDGE, (
        f"a first-{k} policy over candidate_pool.ids passes {published.passes}/{n} "
        f"against a chance {published.chance_passes:.2f}/{n}, an edge of "
        f"{pass_edge:.3f} over the {MAX_FIRST_K_EDGE} bound"
    )

    released_records = released()
    validator = _validator()
    normative = [str(alias) for alias in released_records[0]["candidate_pool"]["ids"]]
    bucket = list(bucket_merge(released_records[0]))
    assert bucket != normative, (
        f"{released_records[0]['record_id']}: the bucket merge already produces this record's "
        "normative order, so the reordering below changes nothing and the rejection it "
        "asserts would be vacuous"
    )
    reordered = dict(released_records[0])
    reordered["candidate_pool"] = {"ids": bucket}
    problems = validator.validate_record(reordered)
    assert any("normative order" in problem for problem in problems), (
        "the validator accepts a record whose pool is published in bucket-merge order, "
        f"so the README's instruction is unenforced: {problems}"
    )
    assert not validator.validate_record(released_records[0]), "the untampered record must pass"


# --------------------------------------------------------------------------- #
# The third adversarial pass (mem-r6yzk leaks 1-3). None of the three is visible in a
# single record: each is a RATE over the release, produced by the generator's draw or
# by the exporter's clock. Each is scored against an exact null - the Poisson-binomial
# distribution of the count under "the published order says nothing", or a permutation
# of the labels - so a p-value here is a property of the release and not of a machine
# or a seed. Each carries a witness: the construction that was replaced has to FAIL the
# same bound, or the bound is asserting something nothing in play could violate.
# --------------------------------------------------------------------------- #

# Leak 1. Group size was a function of role: every subject published three wrong values,
# so the superseded subject published 3 + 3 = 6 candidates and every other 1 + 3 = 4.
# Grouping the merged pool by subject named the chain on 160 of 160 records with no
# timestamp, no id and no bucket label read. Sizes are drawn per subject now
# (``enterprise_workflow.PUBLISHED_GROUP_SIZES``) and the total variation distance
# between the two conditional size distributions reads 0.0828, p = 0.1202. The replaced
# construction reads 1.0000 at p < 0.0001.
#
# The bound is applied to each TIER as well as to the pooled corpus, because pooling
# hides a tier. A record is asked about three subjects in the session tier and four to
# six in the project tier, so the two tiers contribute different numbers of groups and a
# residual confined to one of them is averaged against the other's noise. The project
# tier alone reads 0.0875 at p = 0.3156 and session 0.0781 at p = 0.4098, neither of
# which the pooled 0.0828 contains or would have reported. The residual this per-tier
# bound was added for was on the draw before this one, where project read 0.1532 at
# p = 0.0293 while the pooled figure read 0.0943: inside the bound, invisible to it.
MIN_GROUP_SIZE_INDEPENDENCE_P = 0.01

# What that residual is WORTH, which a distance does not say. The project tier's
# P(chain | size) runs 0.2441 / 0.1631 / 0.1970 over sizes 4 / 5 / 6 against a 0.2000
# base, and "clears p = 0.01" is not an answer to "how many more records does an arm win
# by reading it". So the release is also scored on the outcome: fit the best size-reading
# rule to the published labels, with the answers in hand, and count the records where it
# names the group holding the chain.
#
# Fitting a rule to the sample it is scored on wins by itself, so the comparison is
# against the SAME procedure run on labels drawn at random rather than against uniform
# guessing. The release reads 0.1750 on the project tier against 0.2309 for chance
# fitting (-0.0559, p = 0.9355), 0.1875 against 0.2333 on session and 0.1750 against
# 0.2224 pooled -- the size rule loses to chance fitting on every stratum.
#
# Both a p-value and an absolute edge are asserted because they fail at different sample
# sizes: a p-value is weak at n = 80 (one null standard deviation is 0.042 of accuracy
# there), and an absolute edge is weak on a large corpus where a small real bias is
# significant but worthless. The edge bound is 0.10, about 2.4 null standard deviations
# on a tier, so a re-draw does not trip it by accident; the replaced constant-count
# construction reads 1.0000 against 0.2581 and fails both.
MIN_SIZE_RULE_EXPLOITABILITY_P = 0.01
MAX_SIZE_RULE_EDGE = 0.10

# Leak 2. The published close order was a linear extension of the supersession partial
# order, which dated every replaced value before the value that replaced it. That was an
# answer key at both ends: P(stale | earliest) read 64/160 against an expected 19.7
# (p < 0.0001) and P(gold | latest) inside a chain's own group read 45/160 against 26.7
# (p = 0.0003). The order is a uniform permutation now and both read at chance.
MIN_CLOCK_NEUTRALITY_P = 0.01

# Leak 2, the other half. A trap nobody can trip is not a trap. Answering with each
# subject's latest-closing candidate scored 0.263 / 0.267 and was zeroed on 0 of 160
# records, because a superseded version could never close last. Under a uniform
# permutation the latest-closing member of a chain's group is stale with probability
# stale/size, and the observed firing rate has to be consistent with that: 54 of 160
# against an expected 65.5, p = 0.0619. A dead trap (0 of 160) fails this at p < 0.0001,
# which is the direction that matters.
MIN_TRAP_LIVE_P = 0.01

# Leak 3. P(cross-session | subject) was 1.000 on the charter, over 13 observations. The
# charter was reachable only as a shared decision and a shared decision is established by
# an earlier task, so it was cross-session by construction. It is an ordinary subject
# now: the worst per-subject deviation from the pooled rate reads 0.1119, p = 0.3841,
# against 0.6857 at p < 0.0001 before.
MIN_CROSS_SESSION_SUBJECT_P = 0.01


def _poisson_binomial(probabilities: Sequence[float]) -> list[float]:
    """The exact distribution of the number of successes over independent trials.

    Convolved rather than sampled: the counts below run over 160 records, and a
    Monte-Carlo p-value would put the release's verdict downstream of a draw seed."""
    pmf = [1.0]
    for p in probabilities:
        assert 0.0 <= p <= 1.0, p
        nxt = [0.0] * (len(pmf) + 1)
        for successes, mass in enumerate(pmf):
            nxt[successes] += mass * (1.0 - p)
            nxt[successes + 1] += mass * p
        pmf = nxt
    return pmf


def _small_p(pmf: Sequence[float], observed: int) -> float:
    """P(a count at least as unlikely as ``observed``) under an exact discrete null.

    The method of small p-values: sum the mass of every outcome no more likely than the
    one seen. Two-sided on purpose - a channel that suppresses a count is as readable as
    one that inflates it, and the dead staleness trap was a suppression."""
    assert 0 <= observed < len(pmf), f"{observed} outcomes over {len(pmf) - 1} trials"
    here = pmf[observed]
    return min(1.0, sum(mass for mass in pmf if mass <= here + 1e-12))


def _two_sided_p(probabilities: Sequence[float], observed: int) -> float:
    """``_small_p`` against the Poisson-binomial null of independent unequal trials."""
    return _small_p(_poisson_binomial(probabilities), observed)


def _subject_groups(record: Mapping[str, Any]) -> dict[str, list[str]]:
    """The record's whole published pool, grouped by the subject each candidate speaks to.

    This is the view an arm builds for free: every candidate's text names its subject,
    so the merged evidence falls into one group per graded fact with no key."""
    evidence = record["evidence"]
    texts = {**evidence["gold"], **evidence["distractors"], **evidence["superseded"]}
    groups: dict[str, list[str]] = {}
    for alias, payload in texts.items():
        groups.setdefault(fact_subject(payload), []).append(str(alias))
    return groups


class _SizeStat(NamedTuple):
    """How far apart the two conditional group-size distributions sit, and how odd that is."""

    label: str
    n_groups: int
    n_chain: int
    chain_sizes: Counter[int]
    other_sizes: Counter[int]
    distance: float
    p_value: float

    def __str__(self) -> str:
        return (
            f"{self.label}: {self.n_chain} chain-holding groups of {self.n_groups}, sizes "
            f"{dict(sorted(self.chain_sizes.items()))} against "
            f"{dict(sorted(self.other_sizes.items()))}, total variation distance "
            f"{self.distance:.4f}, p = {self.p_value:.4f}"
        )


def _size_distance(sizes: Sequence[int], holds_chain: Sequence[bool]) -> float:
    chain = Counter(size for size, chain in zip(sizes, holds_chain, strict=True) if chain)
    other = Counter(size for size, chain in zip(sizes, holds_chain, strict=True) if not chain)
    n_chain, n_other = sum(chain.values()), sum(other.values())
    assert n_chain and n_other, "one side of the comparison is empty"
    return 0.5 * sum(
        abs(chain[size] / n_chain - other[size] / n_other) for size in set(chain) | set(other)
    )


def _size_independence(
    label: str,
    sizes: Sequence[int],
    holds_chain: Sequence[bool],
    *,
    draws: int = PERMUTATION_DRAWS,
    seed: int = PERMUTATION_SEED,
) -> _SizeStat:
    """Permutation test: is a group's size independent of whether it holds the chain?

    The chain/not-chain labels are reshuffled with their count held fixed, so the null
    keeps the corpus's group shapes and varies only which groups hold a chain."""
    observed = _size_distance(sizes, holds_chain)
    labels = list(holds_chain)
    rng = random.Random(seed)
    at_least = sum(
        1
        for _ in range(draws)
        if _size_distance(sizes, rng.sample(labels, len(labels))) >= observed - 1e-12
    )
    return _SizeStat(
        label=label,
        n_groups=len(sizes),
        n_chain=sum(holds_chain),
        chain_sizes=Counter(s for s, c in zip(sizes, holds_chain, strict=True) if c),
        other_sizes=Counter(s for s, c in zip(sizes, holds_chain, strict=True) if not c),
        distance=observed,
        p_value=at_least / draws,
    )


def _group_size_rows() -> dict[str, tuple[list[int], list[bool]]]:
    """(group size, does it hold the chain) for every published subject group, filed
    under the pooled corpus AND under the tier it came from.

    Both views are needed. The pooled one is the larger sample; the per-tier ones are the
    only place a residual confined to one tier is visible, and the two tiers publish
    different numbers of groups per record, so pooling is an average over a mixture."""
    strata: dict[str, tuple[list[int], list[bool]]] = {}
    for record in released():
        stale = set(map(str, record["evidence"]["superseded_ids"]))
        assert stale, f"{record['record_id']}: no stale candidate, so no group holds a chain"
        for aliases in _subject_groups(record).values():
            size = len(aliases)
            chain = bool(set(aliases) & stale)
            for label in ("corpus", str(record["tier"])):
                sizes, flags = strata.setdefault(label, ([], []))
                sizes.append(size)
                flags.append(chain)
    return strata


def test_the_group_a_subject_publishes_does_not_name_the_superseded_one() -> None:
    """Leak 1. Every published candidate names its own subject, so an arm can group the
    merged pool for free and read off how many candidates each subject carries. While
    that count was ``role + a constant``, the group holding the supersession chain was
    the biggest one in the record, every time, and the arm needed nothing else: no
    timestamp, no id, no bucket label, no retrieval.

    Measured per TIER as well as pooled. The pooled statistic is an average over a
    mixture: a session record publishes three subject groups and a project record four to
    six, so a residual inside one tier is diluted by the other's noise and the pooled
    figure is not a bound on either. On this release the pooled distance is 0.0828 and
    the tiers are 0.0875 and 0.0781, none of which the others contain; on the draw before
    it the pooled distance was 0.0943 while the project tier sat at 0.1532, which is the
    residual this per-tier form was added to make visible.

    Both constructions are measured on every stratum. The published draw has to clear the
    bound everywhere and the constant-count rule it replaced has to fail it everywhere --
    a witness that only fails pooled would leave the per-tier bounds asserting nothing.
    """
    strata = _group_size_rows()
    assert sorted(strata) == ["corpus", "project", "session"], sorted(strata)

    for label, (sizes, holds_chain) in sorted(strata.items()):
        assert len(set(sizes)) > 1, f"{label}: every group is the same size: {Counter(sizes)}"
        published = _size_independence(label, sizes, holds_chain)
        print(published)
        assert published.p_value >= MIN_GROUP_SIZE_INDEPENDENCE_P, (
            "a subject's published group size still says whether it holds the supersession "
            f"chain -- {published}"
        )

        # The rule this release replaced, re-derived per group from the role it would
        # have read: SUPERSESSION_DEPTH versions or one gold value, plus three
        # distractors either way. Nothing about the corpus changes except the counts.
        replaced = _size_independence(
            f"{label} constant-count witness",
            [(SUPERSESSION_DEPTH if chain else 1) + 3 for chain in holds_chain],
            holds_chain,
        )
        print(replaced)
        assert replaced.p_value < MIN_GROUP_SIZE_INDEPENDENCE_P, (
            "the constant distractor count this release replaced now clears the same bound "
            f"-- {replaced}. The bound is evidence of something only while a known-leaking "
            "construction fails it."
        )


# What the README says about the figures above, matched on the numbers rather than on the
# sentences, so re-wording the prose does not break the test and dropping a figure does.
# A bare `[0-9.]+` would also catch a version or a section number, so each pattern carries
# enough of its own sentence to be unambiguous. The value maps to (stratum, attribute).
_NUMBER = r"([0-9]*\.?[0-9]+)"
_README_SIZE_FIGURES = {
    re.compile(rf"sit {_NUMBER} apart in total variation"): ("corpus", "distance"),
    re.compile(rf"permutation of the labels produces at p = {_NUMBER}"): ("corpus", "p_value"),
    re.compile(rf"reads {_NUMBER} in the project half"): ("project", "distance"),
    re.compile(rf"and {_NUMBER} in\s+the session half"): ("session", "distance"),
}


def test_the_readme_states_the_group_size_distances_this_corpus_has() -> None:
    """The figures above are published as prose, and prose does not re-derive itself.

    Every other measured figure the release states is rendered by
    ``public_figures.claims()`` and matched verbatim against each file that carries it, so
    a re-draw reds a test naming the exact replacement string. These are not in that set --
    they are written out by hand in one paragraph of the README -- which made the pooled
    distance the one published measurement a re-draw could falsify in silence. It did: the
    release was re-drawn and the paragraph kept the previous draw's 0.094 at p = 0.084.

    Matched on the numbers rather than on the sentences, and to whatever precision the
    README chooses, so the prose stays free to change and the figures do not.
    """
    readme = (REPO_ROOT.parent / "public" / "README.md").read_text(encoding="utf-8")
    strata = _group_size_rows()
    for pattern, (stratum, attribute) in _README_SIZE_FIGURES.items():
        found = pattern.search(readme)
        assert found is not None, (
            f"public/README.md no longer states the {stratum} group-size {attribute}, which "
            f"/{pattern.pattern}/ reads. The guard above measures it and the release "
            "publishes it, so move the pattern with the paragraph rather than dropping it"
        )
        sizes, holds_chain = strata[stratum]
        measured = _size_independence(stratum, sizes, holds_chain)
        value = float(getattr(measured, attribute))
        written = found.group(1)
        places = len(written.partition(".")[2])
        assert written == f"{value:.{places}f}", (
            f"public/README.md states a {stratum} group-size {attribute} of {written}; this "
            f"corpus reads {value:.{places}f} ({measured}). Rewrite the prose, never the "
            "measurement"
        )


class _GroupShape(NamedTuple):
    """One record's subject groups in published pool order: how big each one is, and
    which of them holds the supersession chain."""

    record_id: str
    tier: str
    sizes: tuple[int, ...]
    chain_index: int


def _group_shapes() -> tuple[_GroupShape, ...]:
    """Every record's group shape. Exactly one group per record holds the chain, which
    is what makes "name the stale group" a well-posed single-answer question."""
    shapes: list[_GroupShape] = []
    for record in released():
        stale = set(map(str, record["evidence"]["superseded_ids"]))
        position = {
            str(alias): index for index, alias in enumerate(record["candidate_pool"]["ids"])
        }
        groups = sorted(
            (min(position[alias] for alias in aliases), len(aliases), bool(set(aliases) & stale))
            for aliases in _subject_groups(record).values()
        )
        chain = [index for index, (_, _, holds) in enumerate(groups) if holds]
        assert len(chain) == 1, (
            f"{record['record_id']}: {len(chain)} of its {len(groups)} subject groups hold a "
            "supersession chain, so 'which group is the stale one' has no single answer"
        )
        shapes.append(
            _GroupShape(
                record_id=str(record["record_id"]),
                tier=str(record["tier"]),
                sizes=tuple(size for _, size, _ in groups),
                chain_index=chain[0],
            )
        )
    return tuple(shapes)


class _EdgeStat(NamedTuple):
    """What reading the group sizes buys, against what fitting the same rule to noise
    buys, over one stratum."""

    label: str
    n_records: int
    fitted: float
    uniform: float
    null_mean: float
    edge: float
    p_value: float

    def __str__(self) -> str:
        return (
            f"{self.label} n={self.n_records}: the best size-reading rule names the stale "
            f"group on {self.fitted:.4f} of records against {self.null_mean:.4f} for the "
            f"same rule fitted to random labels (edge {self.edge:+.4f}, p = "
            f"{self.p_value:.4f}) and {self.uniform:.4f} for guessing uniformly"
        )


def _size_rule_edge(
    label: str,
    shapes: Sequence[_GroupShape],
    *,
    draws: int = PERMUTATION_DRAWS,
    seed: int = PERMUTATION_SEED,
) -> _EdgeStat:
    """Score the best size-reading rule an adversary could fit, against a null that
    fits the same rule to labels that carry nothing.

    The rule: score each group size by P(holds the chain | that size) over the stratum,
    then per record name the group whose size scores highest, ties going to whichever of
    them the published pool order puts first. It is fitted WITH the answers in hand, so
    it is an upper bound on what a downloader could learn, not a retrieval result.

    An upper bound fitted in-sample beats guessing even on labels that say nothing, so
    the null re-fits it on a chain group drawn uniformly inside each record. Sizes,
    record shapes and the one-chain-per-record structure are all held fixed; only which
    group is stale varies.

    Which group the rule names depends only on how the sizes RANK against one another,
    and there are ``len(sizes)!`` rankings, so every record's answer under every possible
    fit is precomputed once rather than per draw."""
    assert shapes, f"{label}: nothing to score"
    sizes = sorted({size for shape in shapes for size in shape.sizes})
    assert len(sizes) > 1, f"{label}: every group is size {sizes}, so there is no rule to fit"
    published_per_size = Counter(size for shape in shapes for size in shape.sizes)

    def picks_under(preference: tuple[int, ...]) -> list[int]:
        rank = {size: index for index, size in enumerate(preference)}
        return [
            min(range(len(shape.sizes)), key=lambda index: (rank[shape.sizes[index]], index))
            for shape in shapes
        ]

    picks = {preference: picks_under(preference) for preference in permutations(sizes)}

    def named(chain: Sequence[int]) -> float:
        holders = Counter(shape.sizes[j] for shape, j in zip(shapes, chain, strict=True))
        preference = tuple(
            sorted(sizes, key=lambda size: (-holders[size] / published_per_size[size], size))
        )
        hits = sum(int(p == c) for p, c in zip(picks[preference], chain, strict=True))
        return hits / len(shapes)

    fitted = named([shape.chain_index for shape in shapes])
    rng = random.Random(seed)
    null = [named([rng.randrange(len(shape.sizes)) for shape in shapes]) for _ in range(draws)]
    null_mean = mean(null)
    return _EdgeStat(
        label=label,
        n_records=len(shapes),
        fitted=fitted,
        uniform=mean(1.0 / len(shape.sizes) for shape in shapes),
        null_mean=null_mean,
        edge=fitted - null_mean,
        p_value=sum(1 for value in null if value >= fitted - 1e-12) / draws,
    )


def test_reading_the_group_sizes_does_not_help_an_arm_name_the_stale_subject() -> None:
    """Leak 1, scored in the units the leak is spent in.

    The distance above says how far apart two distributions sit; it does not say how many
    records an arm wins by reading them: the draw before this one put the project tier at
    0.1532, inside the bound and still looking like it might be worth several points on a
    0.2000 base. This settles that directly: an
    adversary who has already seen the answers fits the best possible size-reading rule
    and is scored on how often it names the group holding the supersession chain.

    Fitting to the scored sample wins on its own, so the comparison is against the same
    procedure re-fitted to labels drawn at random. Both bounds are asserted because they
    fail at different sample sizes -- a p-value is weak at n = 80, an absolute edge is
    weak on a big corpus -- and the constant-count construction this release replaced has
    to fail both on every stratum.
    """
    shapes = _group_shapes()
    strata: dict[str, list[_GroupShape]] = {"corpus": list(shapes)}
    for shape in shapes:
        strata.setdefault(shape.tier, []).append(shape)
    assert sorted(strata) == ["corpus", "project", "session"], sorted(strata)

    for label, rows in sorted(strata.items()):
        published = _size_rule_edge(label, rows)
        print(published)
        assert published.p_value >= MIN_SIZE_RULE_EXPLOITABILITY_P, (
            f"reading the published group sizes names the stale subject more often than "
            f"fitting the same rule to noise -- {published}"
        )
        assert published.edge <= MAX_SIZE_RULE_EDGE, (
            f"reading the published group sizes is worth {published.edge:+.4f} of accuracy "
            f"over chance fitting, past the {MAX_SIZE_RULE_EDGE} this release accepts -- "
            f"{published}. That is a corpus to re-draw, not a bound to widen."
        )

        # The construction this release replaced: the chain's group published
        # SUPERSESSION_DEPTH versions plus three distractors and every other group one
        # value plus three, so the biggest group was the stale one on every record.
        witness = _size_rule_edge(
            f"{label} constant-count witness",
            [
                shape._replace(
                    sizes=tuple(
                        (SUPERSESSION_DEPTH if index == shape.chain_index else 1) + 3
                        for index in range(len(shape.sizes))
                    )
                )
                for shape in rows
            ],
        )
        print(witness)
        assert witness.p_value < MIN_SIZE_RULE_EXPLOITABILITY_P, (
            f"the constant distractor count this release replaced clears the same bound "
            f"-- {witness}, so the bound asserts nothing"
        )
        assert witness.edge > MAX_SIZE_RULE_EDGE, (
            f"the constant distractor count bought no measurable accuracy -- {witness}, so "
            "the edge bound is not being read"
        )


def _clock(record: Mapping[str, Any]) -> list[str]:
    """The record's pool, earliest close first. Raises rather than guessing on a tie."""
    pool = set(map(str, record["candidate_pool"]["ids"]))
    closed = {
        str(c["id"]): str(c["closed"])
        for c in record["loo"]["candidates"]
        if c["closed"] is not None and str(c["id"]) in pool
    }
    assert set(closed) == pool, f"{record['record_id']}: a pool candidate publishes no close time"
    assert len(set(closed.values())) == len(closed), f"{record['record_id']}: two share an instant"
    return sorted(closed, key=lambda alias: closed[alias])


class _ClockCut(NamedTuple):
    """One readable slice of the published clock, and how odd its count is."""

    statistic: str
    scope: str
    observed: int
    probabilities: tuple[float, ...]

    @property
    def p_value(self) -> float:
        return _two_sided_p(self.probabilities, self.observed)

    def __str__(self) -> str:
        return (
            f"{self.statistic} [{self.scope}]: {self.observed}/{len(self.probabilities)} "
            f"against an expected {sum(self.probabilities):.2f} if the clock carried "
            f"nothing, p = {self.p_value:.4f}"
        )


class _GroupSlot(NamedTuple):
    """Which slot of its own subject group's clock that group's gold closes on."""

    tier: str
    subject: str
    size: int
    chain: bool
    rank: int


@cache
def _group_slots() -> tuple[_GroupSlot, ...]:
    """Every published subject group, with the slot its gold closes on.

    One row per GROUP rather than per record. The residual that survived R5 lived inside
    the groups and averaged away across them: the record-level counts read at chance
    while a group-level rule scored 0.2625 against 0.2049."""
    rows: list[_GroupSlot] = []
    for record in released():
        order = _clock(record)
        gold = set(map(str, record["evidence"]["gold_ids"]))
        stale = set(map(str, record["evidence"]["superseded_ids"]))
        for subject, aliases in _subject_groups(record).items():
            ranked = sorted(aliases, key=order.index)
            golds = [alias for alias in ranked if alias in gold]
            assert len(golds) == 1, (
                f"{record['record_id']}/{subject}: {len(golds)} gold values in one subject "
                "group, so the group has no single slot for a rank rule to read"
            )
            rows.append(
                _GroupSlot(
                    tier=str(record["tier"]),
                    subject=subject,
                    size=len(ranked),
                    chain=bool(set(aliases) & stale),
                    rank=ranked.index(golds[0]),
                )
            )
    assert rows, "no record publishes a subject group, so nothing was measured"
    return tuple(rows)


# The cuts of the group population a published record hands over for free. Tier, group
# size, whether the group carries the supersession chain and the subject it speaks to are
# all readable without retrieving anything, so each names a slice a zero-retrieval policy
# can be fitted on offline against the frozen release and then run question-blind and
# key-blind at eval time.
_GROUP_SLICERS: tuple[Callable[[_GroupSlot], str], ...] = (
    lambda slot: "",
    lambda slot: f"size {slot.size}",
    lambda slot: f"{'chain' if slot.chain else 'non-chain'} size {slot.size}",
    lambda slot: slot.subject,
)


def _scoped(scope: str) -> list[_GroupSlot]:
    return [slot for slot in _group_slots() if scope in (slot.tier, "corpus")]


def _group_rank_cuts(statistic: str, holds: Callable[[_GroupSlot], bool]) -> list[_ClockCut]:
    """``holds`` counted over every free cut, against the 1/size a uniform clock implies."""
    cuts: list[_ClockCut] = []
    for scope in ("corpus", "project", "session"):
        for slicer in _GROUP_SLICERS:
            buckets: dict[str, list[_GroupSlot]] = {}
            for slot in _scoped(scope):
                buckets.setdefault(slicer(slot), []).append(slot)
            cuts.extend(
                _ClockCut(
                    statistic=statistic,
                    scope=", ".join(part for part in (scope, key) if part),
                    observed=sum(holds(slot) for slot in bucket),
                    probabilities=tuple(1 / slot.size for slot in bucket),
                )
                for key, bucket in sorted(buckets.items())
            )
    return cuts


def _assert_clock_cuts(cuts: Sequence[_ClockCut]) -> None:
    """Every cut at ``MIN_CLOCK_NEUTRALITY_P``, with no multiplicity correction.

    That is affordable here, and only here, because the group slot is DEALT rather than
    drawn: ``membench.public_clock`` balances it inside each (tier, size, stale count,
    subject) cell, so every one of these counts equals its target up to the cell's
    divisibility remainder. A cut that reaches 0.01 means the deal broke, not that the
    corpus drew unluckily, which is what a correction would otherwise be protecting."""
    assert cuts
    for cut in cuts:
        print(cut)
    worst = min(cuts, key=lambda cut: cut.p_value)
    print(f"worst cut: {worst}")
    assert worst.p_value >= MIN_CLOCK_NEUTRALITY_P, f"the close order is readable -- {worst}"


def test_the_published_clock_says_nothing_about_which_value_is_current() -> None:
    """Leak 2. The published close order used to be a linear extension of the
    supersession partial order. That is a total answer key in miniature: a replaced value
    was dated before the value replacing it, so the stale bucket was pushed toward the
    earliest slot and locked out of the latest one, and neither fact needed retrieving.

    Two whole-pool counts and one per-group count, each against the exact distribution it
    would have if the order carried nothing.

    The per-group count used to be restricted to the one group holding the supersession
    chain, on the reasoning that the chain is where the trap lives. That restriction was
    the R5 leak: it scored 80 of the 800 published groups and read p = 0.5 while the
    other 720 carried a rule worth 0.2625 against 0.2049, concentrated in non-chain
    groups of four at 0.3854 against 0.2500, p = 0.0031. All five groups of every record
    are scored now, at every cut a record hands over for free.
    """
    pool_rows: list[tuple[str, int, float, int, float]] = []
    for record in released():
        order = _clock(record)
        gold = set(map(str, record["evidence"]["gold_ids"]))
        stale = set(map(str, record["evidence"]["superseded_ids"]))
        pool_rows.append(
            (
                str(record["tier"]),
                int(order[0] in stale),
                len(stale) / len(order),
                int(order[-1] in gold),
                len(gold) / len(order),
            )
        )
    assert pool_rows

    cuts: list[_ClockCut] = []
    for scope in ("corpus", "project", "session"):
        rows = [row for row in pool_rows if scope in (row[0], "corpus")]
        cuts.append(
            _ClockCut(
                "the earliest candidate is stale",
                scope,
                sum(r[1] for r in rows),
                tuple(r[2] for r in rows),
            )
        )
        cuts.append(
            _ClockCut(
                "the latest candidate is gold",
                scope,
                sum(r[3] for r in rows),
                tuple(r[4] for r in rows),
            )
        )
    cuts.extend(
        _group_rank_cuts(
            "a subject group's latest candidate is its gold",
            lambda slot: slot.rank == slot.size - 1,
        )
    )
    _assert_clock_cuts(cuts)


def test_no_slot_of_a_subject_group_is_the_one_its_gold_prefers() -> None:
    """Leak 2, generalised past the slot the R5 audit happened to probe.

    "Answer with each subject group's LATEST candidate" is one member of a family.
    "Answer with each group's Nth" is exactly as zero-retrieval, needs no question and no
    key, and a repair that flattens the last slot by pushing its mass one slot inwards
    has moved the leak rather than removed it. This epic has moved a leak on each of its
    five previous rounds, so every slot of every group size is scored here against the
    exact Binomial(n, 1/size) a uniform clock implies, not just the two ends.
    """
    cuts: list[_ClockCut] = []
    for scope in ("corpus", "project", "session"):
        slots = _scoped(scope)
        for size in sorted({slot.size for slot in slots}):
            sized = [slot for slot in slots if slot.size == size]
            cuts.extend(
                _ClockCut(
                    statistic=f"the gold of a group of {size} closes on slot {rank}",
                    scope=scope,
                    observed=sum(slot.rank == rank for slot in sized),
                    probabilities=tuple(1 / size for _ in sized),
                )
                for rank in range(size)
            )
    _assert_clock_cuts(cuts)


@cache
def _retrieval_only_scores(arm_name: str) -> dict[str, tuple[float, float]]:
    """``{cut: (recall, reward)}`` for one real arm over the release, retrieval only.

    The arm picks and the driver grades exactly what it picked, so this is the published
    table minus the answering agent: no model is called, nothing is paid for, and it runs
    in CI. Only arms that need no weights, no store and no network can be built here,
    which is `lexical` and the `grouped` system wrapped around it. That is enough to
    bound a zero-retrieval policy, because the bound only has to name the BEST real arm
    and `nemo-embed` has never led this corpus.
    """
    driver = baseline_driver()
    factory = driver.default_arm_factory()
    rows: dict[str, list[tuple[float, float]]] = {}
    for record in driver.load_release(RELEASE_DIR):
        arm = factory(arm_name)
        ctx = driver.StepContext(
            trial_id=f"{arm_name}-{record.record_id}-contract",
            session_id=record.record_id,
            step_id="goal",
            clock=driver.IdClock(),
        )
        payloads = driver.retrieve_for(arm, arm_name, record, ctx)
        scored = (
            len(set(payloads) & set(record.gold_ids)) / len(record.gold_ids),
            driver.grade("\n".join(payloads.values()), record)[0],
        )
        for cut in ("corpus", str(record.tier)):
            rows.setdefault(cut, []).append(scored)
    assert set(rows) == {"corpus", "project", "session"}, sorted(rows)
    return {
        cut: (mean(recall for recall, _ in scored), mean(reward for _, reward in scored))
        for cut, scored in rows.items()
    }


def test_the_staleness_trap_fires_on_a_time_aware_policy() -> None:
    """Leak 2, the half a neutral clock does not fix by itself. The corpus buries two
    superseded values per record and grades an answer that states one at zero, so a
    policy that reaches for the wrong version is supposed to lose everything. Under the
    linear-extension order it could not: a superseded value could never close last, so
    answering with each subject's LATEST-closing candidate scored 0.263 / 0.267 and was
    zeroed on 0 of 160 records. The trap was live in the data and dead in practice.

    Three things are asserted, and the last two are the R5 repair. The trap's firing rate
    against the rate a uniform clock implies, so a trap nobody can trip fails and so does
    one that fires more often than the construction allows. And then the policy's own
    RECALL and REWARD against the best real arm, because the firing rate was never the
    claim anyone cares about: R5 shipped a release where this policy fired 29 times, read
    p = 0.06 here, and still beat every retrieval arm on the project tier while injecting
    20% fewer characters. A benchmark whose best arm is beaten by a question-blind,
    key-blind, zero-retrieval rule is measuring the rule, and the count alone could not
    see that.

    The margin is not large, and that is a property of the corpus rather than of this
    guard: the real arms sit close to chance on it (`lexical` recalls 0.2413 against a
    0.2017 base rate), so the smallest gap this asserts is 0.0150, on session-tier
    reward. It is a floor under the benchmark's premise, not a claim that retrieval is
    strong here.
    """
    driver = baseline_driver()
    by_id = {record.record_id: record for record in driver.load_release(RELEASE_DIR)}
    assert by_id

    tripped = 0
    expected: list[float] = []
    policy: dict[str, list[tuple[float, float]]] = {}
    for payload in released():
        record = by_id[str(payload["record_id"])]
        order = _clock(payload)
        latest = [max(aliases, key=order.index) for aliases in _subject_groups(payload).values()]
        answer = "\n".join(record.candidates[alias] for alias in latest)
        score, passed = driver.grade(answer, record)
        for cut in ("corpus", str(record.tier)):
            policy.setdefault(cut, []).append(
                (len(set(latest) & set(record.gold_ids)) / len(record.gold_ids), score)
            )
        stale = set(map(str, payload["evidence"]["superseded_ids"]))
        chains = [aliases for aliases in _subject_groups(payload).values() if set(aliases) & stale]
        assert len(chains) == 1, f"{record.record_id}: {len(chains)} groups hold a chain"
        expected.append(len(set(chains[0]) & stale) / len(chains[0]))

        # Firing is stating a superseded value, which is the rule the README publishes and
        # the driver grades on. Scoring zero is not the same event: an answer that states
        # no expected value scores zero too, and counting those would report the policy's
        # ACCURACY as though it were the trap's reach.
        fired = any(states_value(answer, value) for value in record.forbidden_values)
        tripped += fired
        if fired:
            assert (score, passed) == (0.0, False), (
                f"{record.record_id}: a stale value reached the answer and it still scored "
                f"{score:.3f}; the trap fires without costing anything"
            )

    n = len(by_id)
    p_value = _two_sided_p(expected, tripped)
    detail = (
        f"answering with each subject's latest-closing candidate scores "
        f"{mean(reward for _, reward in policy['corpus']):.4f} and states a superseded "
        f"value on {tripped}/{n} records, against the {sum(expected):.2f} a uniform clock "
        f"implies, p = {p_value:.4f}"
    )
    print(detail)
    assert tripped > 0, f"the staleness trap never fires on a time-aware policy -- {detail}"
    assert (
        p_value >= MIN_TRAP_LIVE_P
    ), f"the trap does not fire at the rate the pool sets -- {detail}"

    real = {name: _retrieval_only_scores(name) for name in ("lexical", "grouped")}
    for cut in ("corpus", "project", "session"):
        for index, statistic in enumerate(("recall", "reward")):
            best_arm = max(real, key=lambda name: real[name][cut][index])
            best = real[best_arm][cut][index]
            got = mean(scored[index] for scored in policy[cut])
            line = (
                f"{cut}: the zero-retrieval policy's {statistic} is {got:.4f} against "
                f"{best:.4f} for {best_arm}, the best real arm on that cut"
            )
            print(line)
            assert got < best, (
                f"a question-blind, key-blind policy that retrieves nothing beats the "
                f"best real arm -- {line}"
            )


class _SubjectRateStat(NamedTuple):
    """Per-subject P(cross-session), and whether the spread is more than a draw."""

    label: str
    counts: dict[str, tuple[int, int]]
    pooled: float
    worst_deviation: float
    degenerate: tuple[str, ...]
    p_value: float

    def __str__(self) -> str:
        rates = ", ".join(
            f"{subject!r} {cross}/{total} = {cross / total:.3f}"
            for subject, (cross, total) in sorted(
                self.counts.items(), key=lambda item: -item[1][0] / item[1][1]
            )
        )
        return (
            f"{self.label}: pooled {self.pooled:.4f}, worst deviation "
            f"{self.worst_deviation:.4f}, p = {self.p_value:.4f}; {rates}"
        )


def _project_gold_rows() -> list[tuple[str, bool]]:
    """``(subject, was it written by another session)`` for every project-tier gold fact."""
    frozen = _frozen_by_id()
    contents = _internal_contents()
    rows: list[tuple[str, bool]] = []
    for record in released():
        if record["tier"] != "project":
            continue
        sequence = frozen[str(record["record_id"])]
        cross = _cross_session_gold(sequence, frozen)
        for memory_id in sequence.steps[-1].outcome_checks[0].requires_memory:
            rows.append((fact_subject(contents[memory_id]), memory_id in cross))
    assert rows, "the release publishes no project gold fact"
    return rows


def _subject_rates(
    label: str,
    rows: Sequence[tuple[str, bool]],
    *,
    draws: int = PERMUTATION_DRAWS,
    seed: int = PERMUTATION_SEED,
) -> _SubjectRateStat:
    """Per-subject cross-session rates, their worst deviation, and a permutation p-value.

    The cross-session flags are reshuffled across the subjects with their total held
    fixed, so the null keeps how often the corpus is cross-session at all and varies only
    which subject carries it."""

    def tally(flags: Sequence[bool]) -> dict[str, tuple[int, int]]:
        counts: dict[str, list[int]] = {}
        for (subject, _), flag in zip(rows, flags, strict=True):
            cell = counts.setdefault(subject, [0, 0])
            cell[0] += flag
            cell[1] += 1
        return {subject: (cross, total) for subject, (cross, total) in counts.items()}

    def worst(counts: Mapping[str, tuple[int, int]], pooled: float) -> float:
        return max(abs(cross / total - pooled) for cross, total in counts.values())

    flags = [flag for _, flag in rows]
    pooled = sum(flags) / len(flags)
    counts = tally(flags)
    observed = worst(counts, pooled)
    rng = random.Random(seed)
    at_least = sum(
        1
        for _ in range(draws)
        if worst(tally(rng.sample(flags, len(flags))), pooled) >= observed - 1e-12
    )
    return _SubjectRateStat(
        label=label,
        counts=counts,
        pooled=pooled,
        worst_deviation=observed,
        degenerate=tuple(
            sorted(
                subject
                for subject, (cross, total) in counts.items()
                if cross == total or cross == 0
            )
        ),
        p_value=at_least / draws,
    )


def test_no_subject_is_always_the_cross_session_one() -> None:
    """Leak 3. "The project charter decision" was cross-session on 13 observations out
    of 13. An arm that answered "whatever the charter clause asks for came from an
    earlier session" was right every time it came up, having retrieved nothing, and the
    share guard above waved it through because 13 facts is 0.118 of the cross-session
    total.

    Two checks, and the first one carries no floor on purpose. A subject whose rate is
    1.000 (or 0.000) is a RULE about that subject, and a rule is not less exploitable for
    being rare - it is the cheapest thing in the corpus to learn. The second scores the
    spread of the rest against a permutation of the same flags.
    """
    rows = _project_gold_rows()
    published = _subject_rates("published", rows)
    print(published)

    assert not published.degenerate, (
        f"{list(published.degenerate)} answer 'is this fact cross-session?' with no "
        f"exception anywhere in the release, so the answer is a rule -- {published}"
    )
    assert (
        published.p_value >= MIN_CROSS_SESSION_SUBJECT_P
    ), f"which subject carries the cross-session fact is not a draw -- {published}"

    # The corpus this replaced, rebuilt from the released flags: every cross-session fact
    # is the charter's and the charter carries nothing else, which is what a charter
    # reachable only as a shared decision produced. The guard has to go red on it.
    charter = "the project charter decision"
    others = sorted({subject for subject, _ in rows} - {charter})
    assert others, "the corpus has one subject, so the collapse cannot be expressed"
    collapsed = [
        (charter, True) if cross else (others[index % len(others)], False)
        for index, (_, cross) in enumerate(rows)
    ]
    mutant = _subject_rates("collapsed-onto-one-subject witness", collapsed)
    print(mutant)
    assert charter in mutant.degenerate, (
        "the mutant that collapses every cross-session fact onto one subject is not read "
        f"as degenerate, so the guard above asserts nothing -- {mutant}"
    )
    assert (
        mutant.p_value < MIN_CROSS_SESSION_SUBJECT_P
    ), f"the collapsed corpus clears the spread bound -- {mutant}"


# --------------------------------------------------------------------------- #
# The SHAPE of the published text (mem-r6yzk leak 6). Every guard above reads WHERE a
# candidate sits -- its pool position, its close time, the size of its subject group --
# and none reads what it LOOKS like. A candidate's text is the one thing every arm is
# handed in full, so if a gold value is systematically shorter, or plainer, or carries
# fewer digits than the distractors beside it, then sorting the pool by that and taking
# the head is a policy that never reads the question and still beats chance.
#
# A single such rule is not the risk. The risk is the FAMILY: an author who checks a
# handful of surface rules one at a time, at p < 0.05 each, finds one that fires on an
# innocent corpus about as often as not. So the whole family is enumerated up front --
# every statistic against every scope, role, tier and window -- and the family-wise error
# rate is controlled across all of it. A rule that survives that is a finding; a rule
# that only clears 0.05 alone is the multiple-comparison artifact the correction absorbs.
# --------------------------------------------------------------------------- #

# Family-wise, over every rule enumerated below, under Holm-Bonferroni. Holm is valid
# under arbitrary dependence between the rules, which matters here because the rules are
# dependent by construction: character count and token count over the same text rank a
# pool almost identically, and the corpus and tier statistics are sums over overlapping
# records. A method that assumed independence would be anti-conservative on this family.
#
# 0.01 rather than 0.05 to match the other acceptance bounds in this file, and because a
# frozen release is re-checked on every CI run: a 0.05 family would red the build on one
# corpus in twenty with nothing wrong.
SURFACE_SHAPE_FWER = 0.01

# The pooled corpus and both tiers. Pooled alone is not enough -- the tiers publish
# different pool sizes and different numbers of subjects per record, so a tell confined
# to one of them is diluted by the other's noise -- and the tiers alone are not enough,
# because each is half the sample and carries half the power.
_SURFACE_STRATA = ("corpus", "project", "session")

_SURFACE_TOKEN_RE = re.compile(r"\w+")
_PUNCTUATION = frozenset(string.punctuation)


def _mean_token_length(text: str) -> float:
    tokens = _SURFACE_TOKEN_RE.findall(text)
    return mean(len(token) for token in tokens) if tokens else 0.0


# What a reader can measure about a candidate while knowing nothing about the task.
# Length in two units, because a padded value and a wordy one are different tells;
# distinct characters, because a value drawn from a smaller alphabet reads as simpler
# without being shorter; digits and punctuation, because an identifier-shaped value and
# a prose-shaped one are trivially separable; upper-case count, because casing survives
# every other normalisation; and the token-length pair, because a mean and a max
# disagree exactly when one long token is doing the work.
SURFACE_STATISTICS: dict[str, Callable[[str], float]] = {
    "chars": lambda text: float(len(text)),
    "tokens": lambda text: float(len(_SURFACE_TOKEN_RE.findall(text))),
    "distinct_chars": lambda text: float(len(set(text))),
    "digits": lambda text: float(sum(character.isdigit() for character in text)),
    "punctuation": lambda text: float(sum(character in _PUNCTUATION for character in text)),
    "uppercase": lambda text: float(sum(character.isupper() for character in text)),
    "mean_token_len": _mean_token_length,
    "max_token_len": lambda text: float(
        max((len(token) for token in _SURFACE_TOKEN_RE.findall(text)), default=0)
    ),
}

# The whole published string, and the answer-bearing part of it alone. Both are needed:
# a published fact wraps its value in a persona, a role and a channel, so a tell in the
# value can be swamped by the wrapper's length, and a tell in the wrapper is invisible to
# a reader who parses first. ``fact_value`` is the generator's own parser, so "the value"
# here means what the grader means by it.
SURFACE_SCOPES: dict[str, Callable[[str], str]] = {
    "text": lambda text: text,
    "value": fact_value,
}

# Three ways to spend a sorted pool, each a policy someone would actually run: take the
# single top candidate, take the retrieval width the published baseline runs every arm
# at, or split the pool down the middle. The first two are tested one-sided in each
# direction separately -- a rule that ranks gold first and a rule that ranks it last are
# both readable, and they are different rules -- while the half split is one two-sided
# test, because "marked in the front half" and "marked in the back half" are the same
# statistic read from either end.
_SURFACE_WINDOWS: dict[str, Callable[[int], int]] = {
    "first": lambda pool_size: 1,
    "topk": lambda pool_size: int(baseline_driver().PUBLIC_TOP_K),
    "half": lambda pool_size: pool_size // 2,
}


class _SurfaceStat(NamedTuple):
    """One surface rule: how many candidates of the role it was looking for landed in the
    head of the order it imposes, against the exact null of a rule that says nothing."""

    rule: str
    window_total: int
    observed: int
    expected: float
    p_value: float

    def __str__(self) -> str:
        return (
            f"{self.rule}: {self.observed} of the role's candidates inside a total window "
            f"of {self.window_total}, against {self.expected:.1f} expected, "
            f"p = {self.p_value:.6f}"
        )


def _convolve(pmfs: Sequence[Sequence[float]]) -> list[float]:
    """The exact distribution of a sum of independent counts."""
    total = [1.0]
    for pmf in pmfs:
        nxt = [0.0] * (len(total) + len(pmf) - 1)
        for index, mass in enumerate(total):
            if mass == 0.0:
                continue
            for offset, share in enumerate(pmf):
                nxt[index + offset] += mass * share
        total = nxt
    return total


def _upper_p(pmf: Sequence[float], observed: int) -> float:
    assert 0 <= observed < len(pmf), f"{observed} outcomes over {len(pmf) - 1} trials"
    return min(1.0, sum(pmf[observed:]))


@cache
def _instance_null(window_counts: tuple[int, ...], marked: int) -> tuple[float, ...]:
    """The exact null of "how much ONE subject group contributed to the count".

    ``window_counts`` holds one entry per member of the group: the number of the stratum's
    records in which that member fell inside the window. A group published by a single
    record contributes 0 or 1; a cross-session decision copied into two of them
    contributes 0, 1 or 2. Under the generator's randomisation the role's members are a
    uniformly random ``marked``-subset of the group -- gold is a designated member drawn
    uniformly, the stale pair is a uniform pair inside the chain group -- so the
    contribution is uniform over the subsets' totals. Enumerated, never sampled.

    Two conditionings make this the right null rather than a looser or a tighter one:

    * on the GROUP. Drawing the marked set from the whole pool is a corpus the generator
      never produces, and permissively so: at the published width it puts sd 11.04 on the
      corpus gold count where this puts 8.16, reporting a deviation as markedly less
      surprising than it is.
    * on the INSTANCE. A shared decision is authored once per world (``_shared_cast`` is
      deterministic in ``(world_id, label)``) and copied into every task that requires it,
      so its records are not independent draws -- they are one draw, read several times.
      Convolving per record instead inflates the evidence: on the release it turns the
      project tier's sd 5.57 into 5.41 and reports the widest deviation there at
      p = 1.744e-05 instead of 2.912e-05, which is the difference between rejecting this
      family at a family-wise 0.01 and not.
    """
    assert 0 <= marked <= len(window_counts)
    pmf = [0.0] * (sum(sorted(window_counts)[len(window_counts) - marked :]) + 1)
    share = 1.0 / math.comb(len(window_counts), marked)
    for subset in combinations(window_counts, marked):
        pmf[sum(subset)] += share
    return tuple(pmf)


def _surface_null(instances: Sequence[tuple[tuple[int, ...], int]]) -> list[float]:
    """The same count over a whole stratum. Distinct subject groups are independent draws,
    so the stratum null is the convolution of the per-instance ones."""
    return _convolve([_instance_null(counts, marked) for counts, marked in instances])


def _published_texts(record: Mapping[str, Any]) -> dict[str, str]:
    """Every candidate's text exactly as the record publishes it."""
    evidence = record["evidence"]
    return {
        str(alias): str(text)
        for bucket in ("gold", "distractors", "superseded")
        for alias, text in evidence[bucket].items()
    }


def _restate(text: str, value: str) -> str:
    """``text`` with its value replaced, rebuilt through the generator's own parse rather
    than by editing the string: prompt and value come from ``fact_subject`` /
    ``fact_value``, and the attribution is whatever the template put after them."""
    prompt, current = fact_subject(text), fact_value(text)
    prefix = f"{prompt} is {current} — by "
    assert text.startswith(prefix), f"not a fact-shaped content: {text!r}"
    return f"{prompt} is {value} — by {text[len(prefix):]}"


def _gold_is_shortest_in_its_group(record: Mapping[str, Any]) -> dict[str, str]:
    """The power witness: a release in which each subject group's gold carries that
    group's shortest value. Nothing else moves -- the same values, the same personas,
    roles and channels, the same pool, the same order -- only which candidate in a group
    states which value, reassigned so gold takes the shortest and the rest follow in
    ascending length.

    This is the leak the family has to be able to see, and it is not hypothetical: values
    come from a per-subject bank, and a bank whose entries differed in length by role --
    an id-shaped current value against prose-shaped wrong ones, say -- would produce
    exactly this with nobody intending it.
    """
    texts = _published_texts(record)
    gold_ids = {str(alias) for alias in record["evidence"]["gold_ids"]}
    groups: dict[str, list[str]] = {}
    for alias, text in texts.items():
        groups.setdefault(fact_subject(text), []).append(alias)

    shortened = dict(texts)
    for members in groups.values():
        gold = sorted(alias for alias in members if alias in gold_ids)
        if not gold:
            continue
        rest = sorted(alias for alias in members if alias not in gold_ids)
        values = sorted(
            (fact_value(texts[alias]) for alias in members), key=lambda value: (len(value), value)
        )
        for alias, value in zip(gold + rest, values, strict=True):
            shortened[alias] = _restate(texts[alias], value)
    return shortened


def _published_pool_order(record: Mapping[str, Any]) -> list[str]:
    """The candidate order the release ships, which is the one a downloader reads."""
    return [str(alias) for alias in record["candidate_pool"]["ids"]]


def _role_ordered(record: Mapping[str, Any]) -> list[str]:
    """Gold first, then distractors, then superseded: the order an auditor gets for free
    by walking the evidence buckets, and the one thing a tie-break must never be."""
    return list(_published_texts(record))


def _surface_family(
    texts_of: Callable[[Mapping[str, Any]], dict[str, str]],
    order_of: Callable[[Mapping[str, Any]], list[str]] = _published_pool_order,
) -> list[_SurfaceStat]:
    """Every surface rule, scored against its exact null: one entry per
    (role, scope, statistic, stratum, window, direction).

    Ties are broken by ``order_of``, defaulting to the record's published pool order.
    That default is load-bearing, not incidental: these statistics tie constantly (see
    the tie-break test below), so for most records the tie-break, not the statistic, is
    what names the top candidate. It has to be an order chosen without reference to role,
    or the count measures the enumeration instead of the corpus. The published order
    qualifies, and it composes this family with the pool-order guard above: a statistic
    constant across a pool degenerates to the published order, so the test that
    establishes that order is neutral stays the one carrying that claim.
    """
    records = released()
    assert records, "no released record to measure"
    strata: dict[str, list[Mapping[str, Any]]] = {"corpus": list(records)}
    for record in records:
        strata.setdefault(str(record["tier"]), []).append(record)
    assert sorted(strata) == sorted(_SURFACE_STRATA), sorted(strata)

    pools = {str(record["record_id"]): order_of(record) for record in records}
    texts = {str(record["record_id"]): texts_of(record) for record in records}
    for record_id, pool in pools.items():
        assert set(pool) == set(texts[record_id]), (
            f"{record_id}: the pool order and the candidate texts name different ids, so a "
            "rule over one cannot be scored against the other"
        )

    family: list[_SurfaceStat] = []
    for role in ("gold_ids", "superseded_ids"):
        marked = {
            str(record["record_id"]): {str(alias) for alias in record["evidence"][role]}
            for record in records
        }
        # The groups the null conditions on, with the role's share of each. Every subject
        # group publishes exactly one current value, and the stale pair sits inside a
        # single group; both are asserted rather than assumed, because the null is only
        # the generator's randomisation while they hold.
        grouped: dict[str, tuple[tuple[tuple[str, ...], int], ...]] = {}
        for record in records:
            record_id = str(record["record_id"])
            rows_for_record = tuple(
                (tuple(aliases), len(set(aliases) & marked[record_id]))
                for aliases in _subject_groups(record).values()
            )
            counts = [count for _, count in rows_for_record]
            if role == "gold_ids":
                assert set(counts) == {1}, (
                    f"{record_id}: subject groups hold {sorted(counts)} current values, so "
                    "gold is not one designated member per group and the null below is not "
                    "the draw the generator makes"
                )
            else:
                assert sum(1 for count in counts if count) == 1, (
                    f"{record_id}: the stale versions span {sum(1 for c in counts if c)} "
                    "subject groups, so there is no single chain group to condition on"
                )
            grouped[record_id] = rows_for_record

        for scope_name, scope in SURFACE_SCOPES.items():
            for statistic_name, statistic in SURFACE_STATISTICS.items():
                orders: dict[str, tuple[list[str], list[str]]] = {}
                for record_id, pool in pools.items():
                    position = {alias: index for index, alias in enumerate(pool)}
                    keyed = {alias: statistic(scope(texts[record_id][alias])) for alias in pool}
                    ascending = sorted(pool, key=lambda alias: (keyed[alias], position[alias]))
                    orders[record_id] = (ascending, list(reversed(ascending)))
                for index, direction in ((0, "asc"), (1, "desc")):
                    for window, width_of in _SURFACE_WINDOWS.items():
                        if window == "half" and direction == "desc":
                            continue
                        # One pass per record: the window and what it caught. Both strata a
                        # record belongs to read the same pass.
                        seen: dict[str, tuple[int, int, set[str]]] = {}
                        for record_id, both in orders.items():
                            order = both[index]
                            width = width_of(len(order))
                            head = set(order[:width])
                            seen[record_id] = (width, len(head & marked[record_id]), head)
                        for stratum, rows in strata.items():
                            ids = [str(record["record_id"]) for record in rows]
                            # One subject group is ONE draw however many of the stratum's
                            # records publish it, so the instance -- keyed by its alias set,
                            # which a shared decision carries unchanged into every task that
                            # requires it -- is what the null convolves over.
                            caught: dict[frozenset[str], Counter[str]] = {}
                            sizes: dict[frozenset[str], int] = {}
                            for record_id in ids:
                                head = seen[record_id][2]
                                for aliases, count in grouped[record_id]:
                                    if not count:
                                        continue
                                    key = frozenset(aliases)
                                    assert sizes.setdefault(key, count) == count, (
                                        f"subject group {sorted(key)} holds {count} of the "
                                        f"role in {record_id} and {sizes[key]} elsewhere, so "
                                        "its copies are not one draw and cannot share a null"
                                    )
                                    counter = caught.setdefault(key, Counter())
                                    for alias in aliases:
                                        counter[alias] += alias in head
                            pmf = _surface_null(
                                [
                                    (tuple(sorted(counter.values())), sizes[key])
                                    for key, counter in caught.items()
                                ]
                            )
                            observed = sum(seen[record_id][1] for record_id in ids)
                            suffix = "" if window == "half" else f"-{direction}"
                            family.append(
                                _SurfaceStat(
                                    rule=(
                                        f"{role.removesuffix('_ids')}/{scope_name}."
                                        f"{statistic_name}/{stratum}/{window}{suffix}"
                                    ),
                                    window_total=sum(seen[record_id][0] for record_id in ids),
                                    observed=observed,
                                    expected=sum(i * mass for i, mass in enumerate(pmf)),
                                    p_value=(
                                        _small_p(pmf, observed)
                                        if window == "half"
                                        else _upper_p(pmf, observed)
                                    ),
                                )
                            )
    return family


def _holm_rejections(family: Sequence[_SurfaceStat], alpha: float) -> list[_SurfaceStat]:
    """Holm-Bonferroni: sort ascending, reject while p < alpha / (m - i), stop at the
    first rule that fails. Valid under arbitrary dependence between the rules."""
    ordered = sorted(family, key=lambda stat: stat.p_value)
    rejected: list[_SurfaceStat] = []
    for index, stat in enumerate(ordered):
        if stat.p_value >= alpha / (len(ordered) - index):
            break
        rejected.append(stat)
    return rejected


def _surface_family_size() -> int:
    """The enumeration this family claims to be, computed from its own axes: two roles,
    every scope, every statistic, every stratum, and both directions of every window
    except the half split, which is one two-sided test."""
    directional = 2 * len(_SURFACE_WINDOWS) - 1
    return 2 * len(SURFACE_SCOPES) * len(SURFACE_STATISTICS) * len(_SURFACE_STRATA) * directional


def test_no_surface_rule_over_the_published_text_beats_chance_at_finding_a_role() -> None:
    """Leak 6. Nothing in this repo asserted that a candidate's text SHAPE is independent
    of its role, so a corpus whose gold values were shorter (or plainer, or less
    punctuated) than the distractors around them would have shipped with every other
    guard green. Sorting a pool by a surface statistic and taking the head is a policy
    that reads no question and retrieves nothing, and it would have scored above chance.

    The family is enumerated rather than sampled: eight statistics over two scopes and
    two roles, each read over the pooled corpus and each tier, at three window widths and
    both sort directions. Every rule is scored against an exact enumeration over the
    generator's own randomisation, one draw per subject-group instance, so no p-value here
    depends on a seed or a draw count; Holm-Bonferroni controls the family-wise error over
    the whole enumeration, and it is valid under arbitrary dependence, which these rules
    have in quantity -- characters and tokens rank almost identically, and each tier's
    records are also inside the pooled stratum.

    The release clears it, but not by much, and the margin is the interesting part. Holm
    rejects nothing and 4 of 480 rules sit under an uncorrected 0.01 where 4.8 are
    expected, so the family as a whole is indistinguishable from noise. One cell is not:
    sorting a project-tier pool by total text length and taking the published width of six
    catches gold 117 times against 93.9 expected, p = 0.000029 against a Holm floor of
    0.0000208. It points at the ATTRIBUTION rather than the value -- project gold values
    average slightly LONGER than their group-mates, while the persona ROLE names gold is
    attributed to run shorter, 14.605 characters against a within-group null of 15.010
    (sd 0.167, z = -2.29, P = 0.0106 uncorrected, and 0.158 on the session tier). One
    uncorrected tail at one tier, in a family of 480 where it does not clear the
    correction, is what a real but small non-uniformity and a lucky draw look like alike.
    Recorded here rather than acted on, because the next re-draw settles it: a channel
    recurs and a draw does not.

    The generator says it should not recur. Gold and distractor values come from one
    per-subject bank drawn without replacement with no length term, and every persona is
    drawn uniformly per fact, so inside a subject group gold is exchangeable with its
    distractors by construction.

    The round-4 audit's top-1 counts (gold picked on 28 of 80 project records by fewest
    distinct characters) are weaker than noise: they are not a property of the corpus.
    Fewest-distinct-characters ties 3.75 of the 25 project candidates on average, so what
    the rule names is decided by its tie-break, and the answer moves from 20 of 80 under
    the published order to 60 of 80 under the evidence-bucket order. The tie-break test
    below pins that. Scored against chance rather than against the README-literal order,
    the same sort is unremarkable: at the published width it recalls 0.244 where guessing
    recalls 0.242, and it passes 0 of 160 records, which is what the README-literal order
    scores too (0.245, 0 of 160).
    """
    family = _surface_family(_published_texts)
    assert len(family) == _surface_family_size(), (
        f"{len(family)} rules enumerated, {_surface_family_size()} expected: the family "
        "the correction is applied over is not the family that was described"
    )

    ordered = sorted(family, key=lambda stat: stat.p_value)
    floor = SURFACE_SHAPE_FWER / len(family)
    print(f"surface family m={len(family)}, Holm floor {floor:.2e}")
    for stat in ordered[:5]:
        print(f"  {stat}")

    rejected = _holm_rejections(family, SURFACE_SHAPE_FWER)
    assert not rejected, (
        "a surface rule over the published text finds a role more often than chance at "
        f"family-wise {SURFACE_SHAPE_FWER} over {len(family)} rules: "
        + "; ".join(str(stat) for stat in rejected[:5])
        + ". A candidate's value is drawn independently of its role, so this is a "
        "generator to fix and a corpus to re-draw, not a bound to widen."
    )


def test_a_corpus_that_shortens_the_gold_fails_the_surface_family() -> None:
    """The power witness. A bound nothing in play can fail asserts nothing, and a family
    test is the easiest kind to make vacuous: enumerate enough rules and the correction
    alone will swallow a real signal.

    So the same family runs over a mutant of the release in which each subject group's
    gold carries that group's shortest value. Everything else is identical -- the same
    values, the same wrappers, the same pool order, the same marked sets -- and Holm has
    to reject, at a p-value orders of magnitude under its own floor, or the passing run
    above is measuring nothing. It rejects 59 rules, the smallest at 6.35e-43 against a
    floor of 2.08e-05.
    """
    family = _surface_family(_gold_is_shortest_in_its_group)
    rejected = _holm_rejections(family, SURFACE_SHAPE_FWER)
    ordered = sorted(family, key=lambda stat: stat.p_value)
    print(
        f"witness family m={len(family)}, {len(rejected)} Holm rejection(s), "
        f"smallest p = {ordered[0].p_value:.3e}"
    )
    for stat in ordered[:5]:
        print(f"  {stat}")

    assert rejected, (
        "a corpus whose gold value is the shortest in every subject group is caught by "
        f"none of the {len(family)} surface rules, so the passing family above is not "
        f"evidence of anything. Smallest p = {ordered[0].p_value:.6f} against a Holm "
        f"floor of {SURFACE_SHAPE_FWER / len(family):.2e}"
    )
    assert any(".chars/" in stat.rule for stat in rejected), (
        "the shortened-gold witness is caught, but by no character-length rule, so the "
        "family is firing on something other than the leak that was planted: "
        + "; ".join(stat.rule for stat in rejected[:5])
    )


def test_the_surface_rules_tie_so_often_that_the_tie_break_must_be_role_blind() -> None:
    """Why the family orders ties by the published pool and not by anything else.

    These statistics are coarse: values are short, drawn from one bank per subject, and
    measured in whole characters, so the minimum is routinely shared. On the project tier
    the fewest-distinct-characters minimum over the value is a tie on 3.75 of 25
    candidates on average, which means a top-1 rule spends most of its decisions in the
    tie-break rather than in the statistic.

    So the tie-break is part of the rule, and an order that knows the answer turns any
    statistic into an oracle. Walking the evidence buckets -- gold, then distractors,
    then superseded -- is exactly that order, and it is the one an auditor gets for free
    from the record's own structure: it lifts fewest-distinct-characters from 20 of 80
    project records to 60 of 80 and makes the family reject outright. Reading that as a
    text-shape leak would have been a measurement artifact, which is the disposition of
    the round-4 top-1 counts.

    The published pool order is role-blind by construction (HMAC over the alias, keyed by
    the record id) and is separately measured neutral above, so it is the tie-break a
    downloader can reproduce and the only one whose count means anything.
    """
    project = [record for record in released() if str(record["tier"]) == "project"]
    assert project, "no project-tier record to measure"
    distinct_chars = SURFACE_STATISTICS["distinct_chars"]
    ties = [
        [distinct_chars(fact_value(text)) for text in _published_texts(record).values()]
        for record in project
    ]
    tied_at_minimum = mean(values.count(min(values)) for values in ties)
    assert tied_at_minimum > 1.0, (
        f"the fewest-distinct-characters minimum is unique on the project tier "
        f"({tied_at_minimum:.2f} candidates tied on average), so the tie-break does not "
        "decide these rules and this test is measuring nothing"
    )

    def _top_one_gold(order_of: Callable[[Mapping[str, Any]], list[str]]) -> int:
        found = 0
        for record in project:
            texts = _published_texts(record)
            order = order_of(record)
            position = {alias: index for index, alias in enumerate(order)}
            keyed = {alias: distinct_chars(fact_value(texts[alias])) for alias in order}
            best = min(order, key=lambda alias: (keyed[alias], position[alias]))
            found += best in {str(alias) for alias in record["evidence"]["gold_ids"]}
        return found

    published, role_ordered = _top_one_gold(_published_pool_order), _top_one_gold(_role_ordered)
    print(
        f"project n={len(project)}: fewest distinct characters names gold on {published} "
        f"under the published order and {role_ordered} under the evidence-bucket order, "
        f"with {tied_at_minimum:.2f} of {mean(len(values) for values in ties):.1f} "
        "candidates tied at the minimum"
    )
    assert role_ordered > published, (
        "reading the evidence buckets in order does not favour gold, so this corpus "
        "cannot demonstrate the artifact the published tie-break exists to avoid"
    )

    rejected = _holm_rejections(
        _surface_family(_published_texts, _role_ordered), SURFACE_SHAPE_FWER
    )
    assert rejected, (
        "breaking ties by an order that lists gold first does not make the surface family "
        "reject, so the family is blind to its own tie-break and the published order it "
        "uses cannot be defended as the reason it passes"
    )
