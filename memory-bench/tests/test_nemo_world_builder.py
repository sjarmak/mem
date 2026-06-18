"""NeMo world builder — SDK-free parser + IO, plus a guarded live smoke test.

The parser (records → world) and the fixture IO are the CI-tested core; they run
without ``data_designer``. The live builder is only smoke-tested when the SDK is
installed (it is skipped otherwise), so CI never needs NeMo or a model.
"""

from __future__ import annotations

import pytest

from membench.generators.nemo import DEFAULT_WORLD_SPEC, read_world, records_to_world, write_world


def _rows() -> list[dict[str, object]]:
    """Two coherent persona rows (same org, two teams, two channel kinds)."""
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
            "persona_name": "Ada Lovelace",
            "team_name": "Kernels",
            "channel_kind": "chat",
            "repo_language": "cuda-cpp",
        },
        {
            **common,
            "persona_role": "site-reliability-engineer",
            "persona_name": "Grace Hopper",
            "team_name": "Platform",
            "channel_kind": "email",
            "repo_language": "python",
        },
    ]


def test_records_to_world_builds_coherent_world() -> None:
    world, project = records_to_world(_rows(), seed=3)
    assert world.seed == 3
    assert world.domain == "cuda-engineering"
    assert world.org_name == "Acme"
    assert len(world.personas) == 2
    assert {t.name for t in world.teams} == {"Kernels", "Platform"}
    assert {c.kind for c in world.channels} == {"chat", "email"}
    assert {r.language for r in world.repositories} == {"cuda-cpp", "python"}
    # every persona resolves to a real team (the schema validator would have raised)
    team_ids = {t.team_id for t in world.teams}
    assert all(p.team_id in team_ids for p in world.personas)
    assert project.world_id == world.world_id
    assert project.prd_summary == "Ship X."


def test_records_to_world_is_deterministic() -> None:
    a, _ = records_to_world(_rows(), seed=3)
    b, _ = records_to_world(_rows(), seed=3)
    assert a.model_dump_json() == b.model_dump_json()


def test_incoherent_org_field_raises() -> None:
    rows = _rows()
    rows[1]["domain"] = "legal"  # two domains in one run -> not one organization
    with pytest.raises(ValueError, match="not constant"):
        records_to_world(rows, seed=1)


def test_out_of_vocabulary_value_raises() -> None:
    rows = _rows()
    rows[0]["channel_kind"] = "carrier-pigeon"
    with pytest.raises(ValueError, match="out-of-vocabulary"):
        records_to_world(rows, seed=1)


def test_empty_records_raises() -> None:
    with pytest.raises(ValueError, match="at least one record"):
        records_to_world([], seed=1)


def test_write_then_read_roundtrips(tmp_path) -> None:
    world, project = records_to_world(_rows(), seed=7)
    out = write_world(world, project, base_dir=tmp_path)
    assert out == tmp_path / "7"
    assert (out / "world.json").exists() and (out / "project.json").exists()
    rt_world, rt_project = read_world(out)
    assert rt_world.model_dump_json() == world.model_dump_json()
    assert rt_project.model_dump_json() == project.model_dump_json()


def test_write_rejects_mismatched_pair(tmp_path) -> None:
    world, _ = records_to_world(_rows(), seed=7)
    other_project = records_to_world(_rows(), seed=8)[1]
    with pytest.raises(ValueError, match="world_id"):
        write_world(world, other_project, base_dir=tmp_path)


def test_default_spec_column_names_are_unique() -> None:
    names = DEFAULT_WORLD_SPEC.column_names()
    assert len(names) == len(set(names))
    # domain/org_size are injected per-world (constant), not in the per-row spec.
    assert "persona_role" in names and "org_name" in names
    assert "domain" not in names


def test_live_builder_smoke() -> None:
    # Only runs where the SDK is installed; CI skips. Verifies the documented API
    # path assembles a builder without raising.
    pytest.importorskip("data_designer")
    from membench.generators.nemo.world_builder import build_config_builder

    builder = build_config_builder()
    assert builder is not None


def test_live_local_nim_provider_and_config() -> None:
    # Local NIM wiring: the provider points at an OpenAI-compatible endpoint and the
    # model config binds the spec's alias to it. SDK-gated; CI skips.
    pytest.importorskip("data_designer")
    from membench.generators.nemo.column_spec import DEFAULT_MODEL_ALIAS
    from membench.generators.nemo.model_provider import (
        DEFAULT_NIM_ENDPOINT,
        local_nim_model_config,
        local_nim_provider,
    )
    from membench.generators.nemo.world_builder import build_config_builder

    provider = local_nim_provider()
    assert provider.endpoint == DEFAULT_NIM_ENDPOINT
    assert provider.provider_type == "openai"
    model_config = local_nim_model_config()
    assert model_config.alias == DEFAULT_MODEL_ALIAS
    # The builder must accept the model config that resolves the text columns' alias.
    builder = build_config_builder(model_alias=DEFAULT_MODEL_ALIAS, model_configs=[model_config])
    assert builder is not None
