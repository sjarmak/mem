"""§1-§2 enterprise world/project schema - referential integrity at construction.

The world is the NeMo-generated surface; these tests pin the invariants the
materialiser later relies on: unique entity ids and persona→team references that
resolve. A malformed world must raise, not pass.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from membench.schemas.world import (
    EnterpriseWorld,
    Persona,
    Project,
    TaskBrief,
    Team,
)


def _world(**overrides: object) -> EnterpriseWorld:
    base: dict[str, object] = {
        "world_id": "w1",
        "domain": "cuda-engineering",
        "org_name": "Acme",
        "teams": [Team(team_id="t1", name="Platform")],
        "personas": [Persona(persona_id="p1", name="Ada", role="staff-engineer", team_id="t1")],
        "seed": 0,
    }
    base.update(overrides)
    return EnterpriseWorld(**base)  # type: ignore[arg-type]


def test_valid_world_constructs() -> None:
    w = _world()
    assert w.world_id == "w1"
    assert w.personas[0].team_id == "t1"


def test_persona_referencing_unknown_team_raises() -> None:
    with pytest.raises(ValidationError, match="unknown team"):
        _world(personas=[Persona(persona_id="p1", name="Ada", team_id="ghost")])


def test_persona_without_team_is_allowed() -> None:
    w = _world(personas=[Persona(persona_id="p1", name="Ada")])
    assert w.personas[0].team_id is None


def test_duplicate_team_id_raises() -> None:
    with pytest.raises(ValidationError, match="duplicate team id"):
        _world(teams=[Team(team_id="t1", name="A"), Team(team_id="t1", name="B")])


def test_project_world_backreference_and_brief_ids() -> None:
    p = Project(
        project_id="proj1",
        world_id="w1",
        name="init",
        task_briefs=[TaskBrief(brief_id="b1", title="x"), TaskBrief(brief_id="b2", title="y")],
    )
    assert p.world_id == "w1"
    assert [b.brief_id for b in p.task_briefs] == ["b1", "b2"]


def test_duplicate_brief_id_raises() -> None:
    with pytest.raises(ValidationError, match="duplicate task_brief id"):
        Project(
            project_id="proj1",
            world_id="w1",
            name="init",
            task_briefs=[TaskBrief(brief_id="b1", title="x"), TaskBrief(brief_id="b1", title="y")],
        )
