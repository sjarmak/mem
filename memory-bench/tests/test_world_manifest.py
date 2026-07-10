"""§Determinism — frozen worlds reproduce their task instances without NeMo (mem-ge51).

The manifest records provenance + hashes; verify_world re-hashes the frozen files and
re-materialises the sequences. These tests prove: a clean freeze verifies, a tampered
world is caught, and the materialiser is deterministic enough that re-derivation
matches the recorded hash. All SDK-free.
"""

from __future__ import annotations

from membench.generators import materialize_world
from membench.generators.nemo import records_to_world, write_world
from membench.generators.world_manifest import (
    build_manifest,
    read_manifest,
    verify_world,
    write_manifest,
)

_NIM_MODEL = "meta/llama-3.1-8b-instruct"
_N_TASKS = 2
_FACTS = 3


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


def test_materializer_is_deterministic_against_manifest(tmp_path) -> None:
    # The sequences hash is reproduced by re-materialising — the core determinism
    # guarantee (no NeMo, byte-identical task instances).
    out = _freeze(tmp_path)
    assert verify_world(out).ok
    # A second independent verify also passes (no hidden state).
    assert verify_world(out).ok
