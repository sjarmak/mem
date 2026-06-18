"""Build worlds with NeMo Data Designer and freeze them to fixtures.

Three concerns, split by whether they touch the SDK:

* ``build_config_builder`` / ``generate_world_records`` — the ONLY NeMo-touching
  code. ``data_designer`` is imported inside the functions (the ``nat`` arm's
  pattern), so this module imports without the SDK. These run offline against a
  local NIM / OAuth model and are exercised by a guarded smoke test, not CI.
* ``records_to_world`` — pure parser: flat NeMo rows (one per persona) → a
  coherent ``EnterpriseWorld`` + ``Project``. Enforces world coherence
  (org-level fields constant across rows) and the sampler vocabularies, so a
  malformed run raises loudly instead of producing a Frankenstein world. No SDK.
* ``write_world`` / ``read_world`` — freeze a world to ``fixtures/worlds/<seed>/``
  and read it back. No SDK.

NeMo API note: the SDK surface used here is verified against data-designer 0.6.1
(``SamplerColumnConfig`` + ``CategorySamplerParams``, ``LLMTextColumnConfig``,
``DataDesigner().preview`` returning ``PreviewResults`` with a ``.dataset``
DataFrame). ``build_config_builder`` is covered by the live smoke test (runs
whenever the SDK is installed). ``generate_world_records`` additionally needs a
configured model provider — point ``model_alias`` at a local NIM via
``generators.nemo.model_provider`` — so it is exercised by an operator run, not CI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from membench.generators.nemo.column_spec import (
    CHANNEL_KINDS,
    DEFAULT_MODEL_ALIAS,
    DEFAULT_WORLD_SPEC,
    DOMAINS,
    PERSONA_ROLES,
    REPO_LANGUAGES,
    WorldColumnSpec,
)
from membench.generators.nemo.model_provider import (
    DEFAULT_NIM_ENDPOINT,
    DEFAULT_NIM_MODEL,
    local_nim_model_config,
    local_nim_provider,
)
from membench.schemas.world import (
    WORLD_SCHEMA_VERSION,
    Channel,
    EnterpriseWorld,
    KnowledgeBase,
    Persona,
    Project,
    Repository,
    Team,
)

# Fields that describe the ORG and must be identical across every persona row of a
# single run; if NeMo varies one, the rows do not describe one organization.
_ORG_CONSTANT_FIELDS = ("domain", "org_size", "org_name", "prd_summary")

# Per-row sampler fields validated against their bounded vocabularies.
_VOCAB = {
    "domain": set(DOMAINS),
    "persona_role": set(PERSONA_ROLES),
    "channel_kind": set(CHANNEL_KINDS),
    "repo_language": set(REPO_LANGUAGES),
}


def _slug(text: str) -> str:
    """A filesystem/id-safe slug; collapses non-alphanumerics to single hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "x"


# --- NeMo-touching (offline only) ------------------------------------------------


def build_config_builder(
    spec: WorldColumnSpec = DEFAULT_WORLD_SPEC,
    *,
    model_alias: str | None = None,
    model_configs: list[Any] | None = None,
) -> Any:
    """Assemble a ``DataDesignerConfigBuilder`` from ``spec``. Lazy-imports the SDK;
    ``model_alias`` overrides every text column's alias and ``model_configs`` binds
    those aliases to served models (e.g. a local NIM). Return type is the SDK's
    builder — annotated loosely so this module needs no SDK at import."""
    import data_designer.config as dd  # lazy: SDK only needed for a real run

    builder = dd.DataDesignerConfigBuilder(model_configs=model_configs or [])
    for sampler in spec.samplers:
        builder.add_column(
            dd.SamplerColumnConfig(
                name=sampler.name,
                sampler_type=dd.SamplerType.CATEGORY,
                params=dd.CategorySamplerParams(values=list(sampler.values)),
            )
        )
    for column in spec.text_columns:
        builder.add_column(
            dd.LLMTextColumnConfig(
                name=column.name,
                model_alias=model_alias or column.model_alias,
                prompt=column.prompt,
            )
        )
    return builder


def generate_world_records(
    *,
    num_records: int,
    spec: WorldColumnSpec = DEFAULT_WORLD_SPEC,
    nim_endpoint: str = DEFAULT_NIM_ENDPOINT,
    nim_model: str = DEFAULT_NIM_MODEL,
) -> list[dict[str, Any]]:
    """Run NeMo Data Designer against a LOCAL NIM and return ``num_records`` flat rows
    (one per persona). Lazy-imports the SDK; offline operator use only — needs a NIM
    serving ``nim_model`` at ``nim_endpoint`` (no paid API)."""
    if num_records < 1:
        raise ValueError(f"num_records must be >= 1, got {num_records}")
    from data_designer.interface import DataDesigner  # lazy

    model_config = local_nim_model_config(alias=DEFAULT_MODEL_ALIAS, model=nim_model)
    provider = local_nim_provider(endpoint=nim_endpoint)
    builder = build_config_builder(
        spec, model_alias=DEFAULT_MODEL_ALIAS, model_configs=[model_config]
    )
    designer = DataDesigner(model_providers=[provider])
    preview = designer.preview(config_builder=builder, num_records=num_records)
    if preview.dataset is None:
        raise RuntimeError("NeMo preview returned no dataset (no records generated)")
    # preview.dataset is a pandas DataFrame; one dict per row.
    records: list[dict[str, Any]] = preview.dataset.to_dict(orient="records")
    return records


# --- pure parser (SDK-free, CI-tested) ------------------------------------------


def _require_constant(records: list[dict[str, Any]], field: str) -> str:
    values = {str(r[field]) for r in records}
    if len(values) != 1:
        raise ValueError(
            f"org-level field {field!r} is not constant across the run "
            f"(got {sorted(values)}): rows do not describe one organization"
        )
    return values.pop()


def _check_vocab(records: list[dict[str, Any]]) -> None:
    for field, allowed in _VOCAB.items():
        for r in records:
            value = str(r[field])
            if value not in allowed:
                raise ValueError(
                    f"out-of-vocabulary {field!r} value {value!r}; "
                    f"NeMo output drifted from the column spec"
                )


def records_to_world(
    records: list[dict[str, Any]], *, seed: int
) -> tuple[EnterpriseWorld, Project]:
    """Parse flat NeMo rows into a coherent world + project.

    Each row is one persona. Org-level fields must be constant across rows (else the
    run is incoherent and this raises); sampler fields must be in vocabulary. Teams,
    channels and repos are de-duplicated from the per-row values; ids are derived
    deterministically so the same rows always yield the same world."""
    if not records:
        raise ValueError("records_to_world requires at least one record")
    required = set(_ORG_CONSTANT_FIELDS) | set(_VOCAB) | {"team_name", "persona_name"}
    missing = required - set(records[0])
    if missing:
        raise ValueError(f"records missing required columns: {sorted(missing)}")

    _check_vocab(records)
    domain = _require_constant(records, "domain")
    org_name = _require_constant(records, "org_name")
    _require_constant(records, "org_size")
    prd_summary = _require_constant(records, "prd_summary")

    world_id = f"world-seed{seed}"

    # Teams: de-duplicate by name, preserving first-seen order; map name -> id.
    team_id_by_name: dict[str, str] = {}
    teams: list[Team] = []
    for r in records:
        name = str(r["team_name"])
        if name not in team_id_by_name:
            tid = f"{world_id}-team-{_slug(name)}"
            team_id_by_name[name] = tid
            teams.append(Team(team_id=tid, name=name))

    personas = [
        Persona(
            persona_id=f"{world_id}-persona-{i}-{_slug(str(r['persona_name']))}",
            name=str(r["persona_name"]),
            role=str(r["persona_role"]),
            team_id=team_id_by_name[str(r["team_name"])],
        )
        for i, r in enumerate(records)
    ]

    channels = [
        Channel(channel_id=f"{world_id}-channel-{kind}", name=f"{kind} channel", kind=kind)
        for kind in dict.fromkeys(str(r["channel_kind"]) for r in records)
    ]
    repositories = [
        Repository(repo_id=f"{world_id}-repo-{lang}", name=f"{domain}-{lang}", language=lang)
        for lang in dict.fromkeys(str(r["repo_language"]) for r in records)
    ]
    knowledge_bases = [
        KnowledgeBase(
            kb_id=f"{world_id}-kb-{_slug(domain)}",
            name=f"{domain} knowledge base",
            topic=domain,
        )
    ]

    world = EnterpriseWorld(
        world_id=world_id,
        domain=domain,
        org_name=org_name,
        teams=teams,
        personas=personas,
        channels=channels,
        knowledge_bases=knowledge_bases,
        repositories=repositories,
        seed=seed,
        generator_version=WORLD_SCHEMA_VERSION,
    )
    project = Project(
        project_id=f"{world_id}-project",
        world_id=world_id,
        name=f"{org_name} initiative",
        goal=f"Deliver the current {domain} initiative.",
        prd_summary=prd_summary,
    )
    return world, project


# --- fixture IO (SDK-free, CI-tested) -------------------------------------------

_WORLD_FILE = "world.json"
_PROJECT_FILE = "project.json"


def write_world(world: EnterpriseWorld, project: Project, *, base_dir: str | Path) -> Path:
    """Freeze a world + project to ``base_dir/<seed>/``. Creates the seed directory
    (that is this writer's stated job) and returns it. Refuses a mismatched
    world/project pairing rather than writing an inconsistent fixture."""
    if project.world_id != world.world_id:
        raise ValueError(
            f"project.world_id {project.world_id!r} != world.world_id {world.world_id!r}"
        )
    out = Path(base_dir) / str(world.seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / _WORLD_FILE).write_text(world.model_dump_json(indent=2), encoding="utf-8")
    (out / _PROJECT_FILE).write_text(project.model_dump_json(indent=2), encoding="utf-8")
    return out


def read_world(world_dir: str | Path) -> tuple[EnterpriseWorld, Project]:
    """Read a frozen world + project back from ``world_dir`` (a ``base_dir/<seed>/``)."""
    d = Path(world_dir)
    world = EnterpriseWorld.model_validate_json((d / _WORLD_FILE).read_text(encoding="utf-8"))
    project = Project.model_validate_json((d / _PROJECT_FILE).read_text(encoding="utf-8"))
    return world, project
