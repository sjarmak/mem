"""§Determinism — frozen worlds reproduce their task instances without NeMo (mem-ge51).

The manifest records provenance + hashes; verify_world re-hashes the frozen files and
re-materialises the sequences. These tests prove: a clean freeze verifies, a tampered
world is caught, and the materialiser is deterministic enough that re-derivation
matches the recorded hash. All SDK-free.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from membench.generators import (
    materialize_project_tier,
    materialize_session_tier,
    materialize_world,
)
from membench.generators.enterprise_workflow import GRADED_SUBJECTS_PER_GOAL
from membench.generators.nemo import records_to_world, write_world
from membench.generators.world_manifest import (
    LEGACY_MANIFEST_VERSION,
    MANIFEST_FILE,
    SEQUENCES_FILE,
    WORKFLOW_GENERATOR_VERSION,
    WORLD_MANIFEST_VERSION,
    _hash_sequences,
    build_manifest,
    read_manifest,
    verify_world,
    write_manifest,
)
from membench.schemas.sequence import BenchmarkSequence

_NIM_MODEL = "meta/llama-3.1-8b-instruct"
_N_TASKS = 2
# The goal's whole graded budget. A project-tier world below it is refused outright:
# shared decisions come out of this number, and two local subjects have to survive them.
_FACTS = GRADED_SUBJECTS_PER_GOAL


def _rows() -> list[dict[str, object]]:
    common = {
        "domain": "cuda-engineering",
        "org_size": "scaleup",
        "org_name": "Acme",
        "prd_summary": "Ship X.",
    }
    return [
        {
            **common,
            "persona_role": "staff-engineer",
            "persona_name": "Ada",
            "team_name": "Kernels",
            "channel_kind": "chat",
            "repo_language": "cuda-cpp",
        },
        {
            **common,
            "persona_role": "qa-engineer",
            "persona_name": "Lin",
            "team_name": "QA",
            "channel_kind": "email",
            "repo_language": "python",
        },
    ]


def _write_sequences(world_dir, sequences) -> Path:
    """Freeze the materialised sequences beside the world, exactly as
    ``scripts/generate_worlds.py`` does. Every real freeze writes this file and it is
    the only sequence authority the sweep and the exporter read, so a test freeze that
    omits it cannot exercise the on-disk check."""
    path = Path(world_dir) / SEQUENCES_FILE
    path.write_text(
        json.dumps([seq.model_dump(mode="json") for seq in sequences], indent=2),
        encoding="utf-8",
    )
    return path


def _freeze(tmp_path, *, tool_requiring: bool = False):
    world, project = records_to_world(_rows(), seed=4)
    out = write_world(world, project, base_dir=tmp_path)
    sequences = materialize_world(
        world,
        project,
        n_tasks=_N_TASKS,
        facts_per_task=_FACTS,
        seed=4,
        tool_requiring=tool_requiring,
    )
    _write_sequences(out, sequences)
    manifest = build_manifest(
        world,
        project,
        sequences,
        nim_model=_NIM_MODEL,
        n_tasks=_N_TASKS,
        facts_per_task=_FACTS,
        seed=4,
        tool_requiring=tool_requiring,
    )
    write_manifest(manifest, world_dir=out)
    return out


def test_manifest_roundtrips(tmp_path) -> None:
    out = _freeze(tmp_path)
    m = read_manifest(out)
    assert m.seed == 4
    assert m.nim_model == _NIM_MODEL
    assert m.n_tasks == _N_TASKS and m.facts_per_task == _FACTS
    assert len(m.world_sha256) == 64 and len(m.sequences_sha256) == 64


def test_clean_freeze_verifies(tmp_path) -> None:
    out = _freeze(tmp_path)
    result = verify_world(out)
    assert result.ok, result.mismatches


def test_tool_requiring_freeze_verifies(tmp_path) -> None:
    # H1 (mem-rk41.1): a tool-requiring frozen world must verify. verify_world must
    # re-materialise WITH tool_requiring recorded on the manifest; if it defaults to
    # the text-answer shape the sequence hashes diverge and every tool-requiring world
    # would falsely fail as "materialiser drifted".
    out = _freeze(tmp_path, tool_requiring=True)
    assert read_manifest(out).tool_requiring is True
    result = verify_world(out)
    assert result.ok, result.mismatches


def test_tool_requiring_shape_differs_from_text(tmp_path) -> None:
    # The two shapes must freeze to different sequence hashes — else the flag is inert
    # and the H1 regression above could pass trivially.
    text = read_manifest(_freeze(tmp_path / "text")).sequences_sha256
    tool = read_manifest(_freeze(tmp_path / "tool", tool_requiring=True)).sequences_sha256
    assert text != tool


def test_tampered_world_is_detected(tmp_path) -> None:
    out = _freeze(tmp_path)
    world_file = out / "world.json"
    world_file.write_text(world_file.read_text().replace("Acme", "Evilcorp"), encoding="utf-8")
    result = verify_world(out)
    assert not result.ok
    assert any("world_sha256" in m for m in result.mismatches)


# --------------------------------------------------------------------------- #
# mem-r6yzk B7: the ON-DISK sequences.json is hashed, not just the re-materialised
# sequences. Re-materialisation proves the generator still derives the recorded task
# instances; it never opens the file the sweep and the exporter actually read, so
# editing sequences.json used to clear the gate, the sweep and the export untouched.
# --------------------------------------------------------------------------- #


def _tamper_sequences(world_dir) -> tuple[str, str]:
    """Rewrite one gold value and one question string in the frozen sequences.json.

    Both are exactly what a corpus tamperer would change: the value the goal is
    graded on, and the wording that asks for it. Returns the pair it wrote so the
    test can assert the file really changed."""
    path = Path(world_dir) / SEQUENCES_FILE
    sequences = [
        BenchmarkSequence.model_validate(item)
        for item in json.loads(path.read_text(encoding="utf-8"))
    ]
    establishing = next(
        step for seq in sequences for step in seq.steps if step.expected_memory_writes
    )
    gold_id = next(iter(establishing.expected_memory_writes))
    gold_value = "the deployment window is whatever the tamperer says"
    question = "State any value you like; it will be accepted."
    establishing.expected_memory_writes[gold_id] = gold_value
    sequences[0].steps[-1].user_request = question
    path.write_text(
        json.dumps([seq.model_dump(mode="json") for seq in sequences], indent=2),
        encoding="utf-8",
    )
    return gold_value, question


def test_tampered_sequences_file_is_detected(tmp_path) -> None:
    out = _freeze(tmp_path)
    assert verify_world(out).ok
    gold_value, question = _tamper_sequences(out)
    text = (out / SEQUENCES_FILE).read_text(encoding="utf-8")
    assert gold_value in text and question in text

    result = verify_world(out)
    assert not result.ok
    assert any(SEQUENCES_FILE in m for m in result.mismatches), result.mismatches


def test_tampered_sequences_file_is_detected_under_a_v1_manifest(tmp_path) -> None:
    # The 32 frozen jev32 worlds are world-manifest.v1, and the on-disk hash is taken
    # under the manifest's OWN projection. If the v1 projection were skipped here the
    # legacy corpus would keep the vacuous gate it has today.
    out = tmp_path / "legacy"
    shutil.copytree(_LEGACY_WORLD, out)
    assert read_manifest(out).schema_version == LEGACY_MANIFEST_VERSION
    assert verify_world(out).ok
    _tamper_sequences(out)

    result = verify_world(out)
    assert not result.ok
    assert any(SEQUENCES_FILE in m for m in result.mismatches), result.mismatches


def test_missing_sequences_file_is_detected(tmp_path) -> None:
    # Every freeze writes sequences.json, so its absence is tampering, not a legacy
    # shape that should be waved through.
    out = _freeze(tmp_path)
    (out / SEQUENCES_FILE).unlink()
    result = verify_world(out)
    assert not result.ok
    assert any(SEQUENCES_FILE in m for m in result.mismatches), result.mismatches


def test_malformed_sequences_file_is_a_mismatch_not_a_raise(tmp_path) -> None:
    # A truncated file is a tamper signal the caller must be able to read off
    # VerifyResult; letting json blow up would crash scripts/verify_worlds.py mid-sweep
    # instead of reporting the one bad world.
    out = _freeze(tmp_path)
    (out / SEQUENCES_FILE).write_text("{", encoding="utf-8")
    result = verify_world(out)
    assert not result.ok
    assert any(SEQUENCES_FILE in m for m in result.mismatches), result.mismatches


def test_sequences_file_holding_a_non_list_is_a_mismatch(tmp_path) -> None:
    out = _freeze(tmp_path)
    (out / SEQUENCES_FILE).write_text('{"sequences": []}', encoding="utf-8")
    result = verify_world(out)
    assert not result.ok
    assert any(SEQUENCES_FILE in m for m in result.mismatches), result.mismatches


def test_materializer_is_deterministic_against_manifest(tmp_path) -> None:
    # The sequences hash is reproduced by re-materialising — the core determinism
    # guarantee (no NeMo, byte-identical task instances).
    out = _freeze(tmp_path)
    assert verify_world(out).ok
    # A second independent verify also passes (no hidden state).
    assert verify_world(out).ok


# --------------------------------------------------------------------------- #
# mem-r6yzk.3: the manifest records the TIER, and verify_world dispatches on it
# --------------------------------------------------------------------------- #

# The one v1 manifest in the tree: frozen before BenchmarkSequence grew tier /
# question_type, so it is the real regression case for additive schema growth.
_LEGACY_WORLD = Path(__file__).resolve().parents[1] / "fixtures" / "worlds-tool-jev32" / "0"


def _freeze_tiered(tmp_path, *, tier: str, drop_charter: bool = False):
    world, project = records_to_world(_rows(), seed=4)
    out = write_world(world, project, base_dir=tmp_path)
    materialize = materialize_session_tier if tier == "session" else materialize_project_tier
    kwargs = {"drop_charter": drop_charter} if tier == "project" else {}
    sequences = materialize(
        world, project, n_tasks=_N_TASKS, facts_per_task=_FACTS, seed=4, **kwargs
    )
    _write_sequences(out, sequences)
    manifest = build_manifest(
        world,
        project,
        sequences,
        nim_model=_NIM_MODEL,
        n_tasks=_N_TASKS,
        facts_per_task=_FACTS,
        seed=4,
        tier=tier,
        drop_charter=drop_charter,
    )
    write_manifest(manifest, world_dir=out)
    return out


def test_session_tier_freeze_verifies(tmp_path) -> None:
    out = _freeze_tiered(tmp_path, tier="session")
    assert read_manifest(out).tier == "session"
    result = verify_world(out)
    assert result.ok, result.mismatches


def test_project_tier_freeze_verifies(tmp_path) -> None:
    out = _freeze_tiered(tmp_path, tier="project")
    assert read_manifest(out).tier == "project"
    result = verify_world(out)
    assert result.ok, result.mismatches


def test_project_tier_drop_charter_freeze_verifies(tmp_path) -> None:
    # drop_charter changes the materialised object, so it must ride the manifest too
    # or the Recovery variant would verify against the wrong sequences.
    out = _freeze_tiered(tmp_path, tier="project", drop_charter=True)
    m = read_manifest(out)
    assert m.tier == "project" and m.drop_charter is True
    result = verify_world(out)
    assert result.ok, result.mismatches


def test_tier_shapes_freeze_to_different_hashes(tmp_path) -> None:
    session = read_manifest(_freeze_tiered(tmp_path / "s", tier="session")).sequences_sha256
    project = read_manifest(_freeze_tiered(tmp_path / "p", tier="project")).sequences_sha256
    assert session != project


def test_verify_world_dispatches_on_tier(tmp_path) -> None:
    # The regression this field exists for: a project-tier freeze re-materialised by
    # the session-tier generator is compared against a DIFFERENT object. It must fail
    # loudly rather than silently pass as if it were a session-tier world.
    out = _freeze_tiered(tmp_path, tier="project")
    manifest_file = out / "manifest.json"
    manifest_file.write_text(
        manifest_file.read_text().replace('"tier": "project"', '"tier": "session"'),
        encoding="utf-8",
    )
    assert read_manifest(out).tier == "session"
    result = verify_world(out)
    assert not result.ok
    assert any("sequences_sha256" in m for m in result.mismatches)


def test_legacy_v1_manifest_still_verifies_after_the_schema_grew() -> None:
    # tier / question_type are additive fields on BenchmarkSequence. Hashing them into
    # a v1 manifest's comparison would make all 32 frozen jev32 worlds report
    # "materialiser drifted" on a change that altered no task instance.
    assert read_manifest(_LEGACY_WORLD).schema_version == LEGACY_MANIFEST_VERSION
    result = verify_world(_LEGACY_WORLD)
    assert result.ok, result.mismatches


def test_a_corpus_frozen_under_an_older_generator_says_what_it_did_not_check() -> None:
    """The jev32 worlds were frozen by enterprise-workflow.v3 and this tree no longer
    holds that materialiser, so re-deriving their sequences here compares two different
    task sets. The skip is a reported result, not a silent pass: without the note an
    older corpus would read exactly like one this tree reproduced."""
    manifest = read_manifest(_LEGACY_WORLD)
    assert manifest.workflow_generator_version != WORKFLOW_GENERATOR_VERSION
    result = verify_world(_LEGACY_WORLD)
    assert result.ok, result.mismatches
    assert result.notes, "an unverifiable check reported nothing"
    note = "\n".join(result.notes)
    assert manifest.workflow_generator_version in note and WORKFLOW_GENERATOR_VERSION in note


def test_the_drift_gate_still_fires_for_a_corpus_this_tree_froze(tmp_path) -> None:
    """The version gate must not become a blanket exemption. A freeze that records the
    CURRENT generator is re-materialised, and a manifest claiming sequences the
    materialiser does not produce is a mismatch with no note attached."""
    out = _freeze(tmp_path)
    assert read_manifest(out).workflow_generator_version == WORKFLOW_GENERATOR_VERSION
    assert verify_world(out).ok

    manifest_path = out / MANIFEST_FILE
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["sequences_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    result = verify_world(out)
    assert not result.ok
    assert any("materialiser" in m for m in result.mismatches), result.mismatches
    assert not result.notes, result.notes


def test_legacy_projection_drops_exactly_the_post_v1_fields() -> None:
    world, project = records_to_world(_rows(), seed=4)
    sequences = materialize_session_tier(world, project, n_tasks=1, facts_per_task=_FACTS, seed=4)
    v1 = _hash_sequences(sequences, schema_version=LEGACY_MANIFEST_VERSION)
    v2 = _hash_sequences(sequences, schema_version=WORLD_MANIFEST_VERSION)
    assert v1 != v2
    # A project-tier sequence differs under BOTH projections, so the v1 projection
    # never collapses two distinct task sets onto one hash. Two tasks, because a
    # project needs a second sequence to have anything cross-session to depend on.
    other = materialize_project_tier(world, project, n_tasks=2, facts_per_task=_FACTS, seed=4)
    assert _hash_sequences(other, schema_version=LEGACY_MANIFEST_VERSION) != v1


def test_unknown_manifest_schema_version_is_refused() -> None:
    world, project = records_to_world(_rows(), seed=4)
    sequences = materialize_session_tier(world, project, n_tasks=1, facts_per_task=_FACTS, seed=4)
    with pytest.raises(ValueError, match="unknown world-manifest schema version"):
        _hash_sequences(sequences, schema_version="world-manifest.v99")
