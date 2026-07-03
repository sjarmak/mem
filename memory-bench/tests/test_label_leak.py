"""mem-z3gi — no agent-visible string may separate memory classes.

The real agent sees exactly three string surfaces: each memory's id and content
(``build_agent_prompt`` renders "[{mid}] {content}") and each step's
``user_request``. If any of them systematically differs between the truth /
stale / distractor classes — labeled id suffixes ("-distractor", "-v1"), verb
tense ("was" vs "is now"), attribution shape — a real-agent Confusion/Staleness
run measures label-reading, not memory. These tests pin the opacity contract for
both interference-bearing generators:

* every agent-visible memory id is an opaque content-keyed hash;
* truth, stale and distractor contents normalise to the SAME template once the
  value (and the drawn attribution) is masked;
* establishing ``user_request``s do not mark the superseded subject.
"""

from __future__ import annotations

import re

from membench.generators.enterprise_workflow import materialize_world
from membench.generators.factorial_dag import FactorCell, generate_cell
from membench.generators.opaque_ids import OPAQUE_ID_PATTERN
from membench.runner.headless_agent import build_agent_prompt
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team

# Class-label tokens that must never reach the agent-visible surface.
_LEAK_TOKENS = ("distractor", "stale", "superseded", "-v1", "-v2", " was ", " is now ")


def _world(seed: int = 5) -> EnterpriseWorld:
    return EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Lin", role="qa-engineer", team_id="t1"),
        ],
        channels=[Channel(channel_id="c1", name="kernels", kind="chat")],
        seed=seed,
    )


def _project(seed: int = 5) -> Project:
    return Project(
        project_id=f"world-seed{seed}-project",
        world_id=f"world-seed{seed}",
        name="Acme initiative",
        goal="Reconcile the launch config.",
    )


def _classes(seq: BenchmarkSequence) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """(current, stale, distractor) id→content maps, keyed off HARNESS-side fields."""
    pool = {mid: c for st in seq.steps for mid, c in st.expected_memory_writes.items()}
    goal = seq.steps[-1]
    current = {mid: pool[mid] for mid in goal.outcome_checks[0].requires_memory if mid in pool}
    stale = {mid: pool[mid] for mid in goal.superseded_memory_ids}
    return current, stale, dict(goal.distractor_memories)


# --------------------------------------------------------------------------- #
# enterprise_workflow
# --------------------------------------------------------------------------- #
_ENTERPRISE_TAIL = re.compile(r" is .+ — by .+$")


def _enterprise_shape(content: str) -> str:
    masked = _ENTERPRISE_TAIL.sub(" is <VALUE> — by <WHO>", content)
    assert masked != content, f"content does not follow the shared template: {content!r}"
    return masked


def test_enterprise_agent_visible_ids_are_opaque() -> None:
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        _current, stale, distractor = _classes(seq)
        pool = {mid for st in seq.steps for mid in st.expected_memory_writes}
        for mid in [*pool, *distractor]:
            assert OPAQUE_ID_PATTERN.match(mid), f"labeled id leaks to the agent: {mid!r}"
        assert stale, "expected a superseded chain"


def test_enterprise_content_template_is_identical_across_classes() -> None:
    # Once the value + attribution are masked, a stale or distractor content is
    # indistinguishable from some current content — no verb-tense/attribution tell.
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        current, stale, distractor = _classes(seq)
        current_shapes = {_enterprise_shape(c) for c in current.values()}
        assert {_enterprise_shape(c) for c in stale.values()} <= current_shapes
        assert {_enterprise_shape(c) for c in distractor.values()} <= current_shapes


def test_enterprise_goal_prompt_carries_no_class_marker() -> None:
    # Render the worst case a memory arm can surface at the goal: truth + stale +
    # distractor together. The rendered prompt must carry no class token.
    seq = materialize_world(_world(), _project(), n_tasks=1)[0]
    current, stale, distractor = _classes(seq)
    prompt = build_agent_prompt(seq.steps[-1], {**current, **stale, **distractor})
    for token in _LEAK_TOKENS:
        assert token not in prompt, f"class marker {token!r} leaks into the agent prompt"


def test_enterprise_user_requests_do_not_mark_the_superseded_subject() -> None:
    # A superseding step's request must read exactly like any other establishing
    # step's ("initial"/"corrected" wording would label the chain to the agent).
    template = re.compile(r"^Record the current value of .+\.$")
    for seq in materialize_world(_world(), _project(), n_tasks=3):
        for step in seq.steps[:-1]:
            assert template.match(step.user_request), step.user_request


# --------------------------------------------------------------------------- #
# factorial_dag
# --------------------------------------------------------------------------- #
_FACTORIAL_TAIL = re.compile(r" is \d{3}$")
_ALL_ON = FactorCell(interference=True, supersession=True, consolidation=True)


def _factorial_shape(content: str) -> str:
    masked = _FACTORIAL_TAIL.sub(" is <VALUE>", content)
    assert masked != content, f"content does not follow the shared template: {content!r}"
    return masked


def test_factorial_agent_visible_ids_are_opaque() -> None:
    seq = generate_cell(seed=3, width=4, cell=_ALL_ON)
    _current, stale, distractor = _classes(seq)
    pool = {mid for st in seq.steps for mid in st.expected_memory_writes}
    for mid in [*pool, *distractor]:
        assert OPAQUE_ID_PATTERN.match(mid), f"labeled id leaks to the agent: {mid!r}"
    assert stale and distractor


def test_factorial_content_template_is_identical_across_classes() -> None:
    seq = generate_cell(seed=3, width=4, cell=_ALL_ON)
    current, stale, distractor = _classes(seq)
    current_shapes = {_factorial_shape(c) for c in current.values()}
    assert {_factorial_shape(c) for c in stale.values()} <= current_shapes
    assert {_factorial_shape(c) for c in distractor.values()} <= current_shapes


def test_factorial_goal_prompt_carries_no_class_marker() -> None:
    seq = generate_cell(seed=3, width=4, cell=_ALL_ON)
    current, stale, distractor = _classes(seq)
    prompt = build_agent_prompt(seq.steps[-1], {**current, **stale, **distractor})
    for token in _LEAK_TOKENS:
        assert token not in prompt, f"class marker {token!r} leaks into the agent prompt"
