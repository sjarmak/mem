"""§11 shape well-formedness gate (mem-rk41.2) — a corpus VALIDITY gate.

The gate flags MALFORMED generated tool-requiring tasks (construction bugs). It is a
peer of the memory-necessity gate, NOT an ours-vs-builtin verdict: on a well-formed
shape the underlying discrimination is ~100% by construction, so a FAILURE flags
malformation. All offline via the reference ``ScriptedAgent`` — no model, no Docker.

Covered:
* a well-formed tool-requiring seq is admitted (wellformed True), and the underlying
  discrimination result is carried through;
* the M1 ``facts_per_task > top_k`` truncation case short-circuits to malformed
  WITHOUT running the arms (a monkeypatched discrimination gate must never fire);
* a low ``top_k`` forces the same structural short-circuit.
"""

from __future__ import annotations

import importlib

from membench.generators.enterprise_workflow import materialize_world
from membench.generators.shape_wellformedness_gate import shape_wellformedness_gate
from membench.memory_systems.lexical_system import DEFAULT_TOP_K
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team

# The submodule name collides with the function the package re-exports, so grab the
# module object explicitly to monkeypatch its ``retrieval_discrimination_gate`` symbol.
gate_mod = importlib.import_module("membench.generators.shape_wellformedness_gate")


def _world(seed: int = 7) -> EnterpriseWorld:
    return EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(
                persona_id="p2", name="Grace Hopper", role="site-reliability-engineer", team_id="t1"
            ),
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


def _tool_requiring_seq(seed: int = 7, facts_per_task: int = 3) -> BenchmarkSequence:
    return materialize_world(
        _world(seed), _project(seed), n_tasks=1, facts_per_task=facts_per_task, tool_requiring=True
    )[0]


def test_wellformed_seq_is_admitted() -> None:
    # A well-formed tool-requiring shape: quality (id-exact) cleanly beats naive
    # (token top-k) at the goal, so the gate admits it and carries the underlying
    # discrimination result through for provenance.
    seq = _tool_requiring_seq(facts_per_task=3)
    result = shape_wellformedness_gate(seq, facts_per_task=3, top_k=DEFAULT_TOP_K)
    assert result.wellformed, result.reason
    assert result.sequence_id == seq.sequence_id
    assert result.discrimination is not None
    assert result.discrimination.accepted
    assert result.discrimination.quality_reward > result.discrimination.naive_reward


def test_wellformed_holds_across_seeds() -> None:
    for seed in (7, 11, 23, 42):
        seq = _tool_requiring_seq(seed, facts_per_task=3)
        result = shape_wellformedness_gate(seq, facts_per_task=3, top_k=DEFAULT_TOP_K)
        assert result.wellformed, f"seed {seed}: {result.reason}"


def test_m1_truncation_short_circuits_without_running_arms(monkeypatch) -> None:
    # facts_per_task > top_k confounds the naive arm (truncation, not staleness), so
    # the gate must declare malformed WITHOUT running the arms. Monkeypatch the
    # discrimination gate to blow up if it is ever called — the short-circuit means
    # it must not be.
    def _must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("arms must not run for an M1-malformed task")

    monkeypatch.setattr(gate_mod, "retrieval_discrimination_gate", _must_not_run)

    seq = _tool_requiring_seq(facts_per_task=3)
    result = shape_wellformedness_gate(seq, facts_per_task=DEFAULT_TOP_K + 1, top_k=DEFAULT_TOP_K)
    assert not result.wellformed
    assert result.discrimination is None
    assert "M1 truncation" in result.reason
    assert result.facts_per_task == DEFAULT_TOP_K + 1
    assert result.top_k == DEFAULT_TOP_K


def test_low_top_k_forces_malformed() -> None:
    # A retriever bounded below the task's fact count is the same structural bug: a
    # top_k under facts_per_task short-circuits to malformed, arms not run.
    seq = _tool_requiring_seq(facts_per_task=3)
    result = shape_wellformedness_gate(seq, facts_per_task=3, top_k=2)
    assert not result.wellformed
    assert result.discrimination is None
    assert "M1 truncation" in result.reason
