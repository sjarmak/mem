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
* a low ``top_k`` forces the same structural short-circuit;
* an invalid ``facts_per_task`` (<1) short-circuits to malformed without raising,
  independently of the M1 check (mem-03acq).
"""

from __future__ import annotations

import importlib

from membench.generators.enterprise_workflow import materialize_world
from membench.generators.shape_wellformedness_gate import shape_wellformedness_gate
from membench.memory_systems.lexical_system import DEFAULT_TOP_K
from membench.runner import conditions as conditions_mod
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team

# The submodule name collides with the function the package re-exports, so grab the
# module object explicitly to monkeypatch its ``retrieval_discrimination_gate`` symbol.
gate_mod = importlib.import_module("membench.generators.shape_wellformedness_gate")


def _forbid_arms(monkeypatch) -> None:
    """Make ``retrieval_discrimination_gate`` blow up if called — for a short-circuit
    test, that proves the arms were never run rather than merely asserting the
    returned verdict."""

    def _must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("arms must not run for a malformed-shape short-circuit")

    monkeypatch.setattr(gate_mod, "retrieval_discrimination_gate", _must_not_run)


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
    # the gate must declare malformed WITHOUT running the arms.
    _forbid_arms(monkeypatch)

    seq = _tool_requiring_seq(facts_per_task=3)
    result = shape_wellformedness_gate(seq, facts_per_task=DEFAULT_TOP_K + 1, top_k=DEFAULT_TOP_K)
    assert not result.wellformed
    assert result.discrimination is None
    assert "M1 truncation" in result.reason
    # the reason carries the confounding counts inline (no redundant result fields)
    assert f"facts_per_task {DEFAULT_TOP_K + 1} > top_k {DEFAULT_TOP_K}" in result.reason


def test_low_top_k_forces_malformed() -> None:
    # A retriever bounded below the task's fact count is the same structural bug: a
    # top_k under facts_per_task short-circuits to malformed, arms not run.
    seq = _tool_requiring_seq(facts_per_task=3)
    result = shape_wellformedness_gate(seq, facts_per_task=3, top_k=2)
    assert not result.wellformed
    assert result.discrimination is None
    assert "M1 truncation" in result.reason


def test_invalid_facts_per_task_returns_malformed_without_raising(monkeypatch) -> None:
    # facts_per_task <= 0 evades the M1 check (facts_per_task > top_k is false when
    # facts_per_task is 0 and top_k is DEFAULT_TOP_K), so it needs its own guard.
    # The gate's contract is "never raises for an invalid boundary" (mem-03acq) — the
    # arms must never run on a structurally invalid boundary either.
    #
    # Note top_k < 1 needs no equivalent test here: for any facts_per_task >= 1 it is
    # already caught by test_low_top_k_forces_malformed's M1 path (facts_per_task >
    # top_k trivially holds once top_k <= 0), so a dedicated top_k-only case would
    # just re-test the same M1 branch under a different name.
    _forbid_arms(monkeypatch)

    seq = _tool_requiring_seq(facts_per_task=3)
    result = shape_wellformedness_gate(seq, facts_per_task=0, top_k=DEFAULT_TOP_K)
    assert not result.wellformed
    assert result.discrimination is None
    assert "invalid boundary" in result.reason
    assert "facts_per_task=0" in result.reason


def test_nondefault_top_k_reaches_the_lexical_arm(monkeypatch) -> None:
    # facts_per_task <= top_k here, so this exercises the discrimination path (not
    # the M1 short-circuit) — the gate's top_k must actually construct the naive
    # ("lexical") arm, not silently fall back to DEFAULT_TOP_K.
    assert DEFAULT_TOP_K != 5
    captured: dict[str, object] = {}
    real_build = conditions_mod.build_memory_system

    def _spy(name: str, **kwargs: object) -> object:
        if name == "lexical":
            captured.update(kwargs)
        return real_build(name, **kwargs)

    monkeypatch.setattr(conditions_mod, "build_memory_system", _spy)

    seq = _tool_requiring_seq(facts_per_task=3)
    shape_wellformedness_gate(seq, facts_per_task=3, top_k=5)

    # the trailing wellformed/discrimination shape is already covered by
    # test_wellformed_seq_is_admitted; this test's only claim is that top_k reaches
    # the lexical arm's construction rather than falling back to DEFAULT_TOP_K.
    assert captured.get("top_k") == 5
