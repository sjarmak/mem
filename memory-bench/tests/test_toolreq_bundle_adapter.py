"""mem-rk41.5 — the synthetic tool-requiring world → bundle/record/lesson adapter and
its offline mem-xe2p gate.

Unit tests are hermetic (no mem CLI): they check the record/bundle/lesson SHAPES, the
shared value-free signature FIELDS, strict temporal ordering, self-only LOO, the
tool-requiring precondition, and the answer-leak firewall. The integration test drives
the real ``mem`` CLI end-to-end (skips like ``test_pipeline_e2e`` when the TS build is
absent): freeze a tiny 2-task tool-requiring world, run the offline gate, and assert
the SAME grid gate fires — signature_overlap > 0 on the held anchor, no LOO id in any
payload, and the injected lesson signatures equal what ``mem retrieve`` reported.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from membench.generators.enterprise_workflow import (
    _APPLY_TOOL,
    _SUBJECTS,
    materialize_world,
)
from membench.generators.toolreq_bundle_adapter import (
    APPLY_TOOL,
    SYNTHETIC_RIG,
    sequence_bundles,
    sequence_lessons,
    sequence_records,
    staleness_error,
)
from membench.metrics.scorers import states_value
from membench.schemas.bundle import TaskBundle
from membench.schemas.sequence import BenchmarkSequence
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team
from tests.paths import DIST_MAIN, MEM_BIN, require_mem_cli

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _world(seed: int = 5) -> EnterpriseWorld:
    return EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Grace Hopper", role="sre", team_id="t1"),
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


def _sequences(n_tasks: int = 2, *, tool_requiring: bool = True) -> list[BenchmarkSequence]:
    return materialize_world(
        _world(), _project(), n_tasks=n_tasks, facts_per_task=3, tool_requiring=tool_requiring
    )


def _authored_values(seq: BenchmarkSequence) -> list[str]:
    goal = next(step for step in reversed(seq.steps) if step.outcome_checks)
    action = next(
        a for check in goal.outcome_checks for a in check.requires_action if a.tool == _APPLY_TOOL
    )
    return [*action.arg_values, *action.forbidden_values]


# --------------------------------------------------------------------------- unit


def test_apply_tool_parity_with_materialiser() -> None:
    """The adapter hard-pins the tool name; this guards against a silent desync if the
    materialiser ever renames ``_APPLY_TOOL``."""
    assert APPLY_TOOL == _APPLY_TOOL


def test_staleness_error_is_shared_value_free_and_subject_agnostic() -> None:
    error = staleness_error()
    assert error["tool"] == _APPLY_TOOL
    assert error["severity"] == "error"
    # Digit-free (so classFallback's digit-mask is a no-op) and subject-agnostic: no
    # authored subject value may appear in the shared signature's message.
    assert not any(ch.isdigit() for ch in str(error["message"]))
    for subject in _SUBJECTS:
        for value in subject.values:
            assert not states_value(str(error["message"]), value)
    # A fresh dict per call — a caller mutation must not bleed across records.
    assert staleness_error() is not error


def test_records_carry_byte_identical_error_and_schema() -> None:
    seqs = _sequences(3)
    records = sequence_records(seqs)
    assert len(records) == 3
    errors = [json.dumps(r["trace"]["errors"], sort_keys=True) for r in records]
    # Byte-identical error across every task => mem derives one shared failure signature.
    assert len(set(errors)) == 1
    for record, seq in zip(records, seqs, strict=True):
        assert record["work_id"] == seq.sequence_id
        assert record["rig"] == SYNTHETIC_RIG
        assert record["lifecycle"]["status"] == "closed"
        # Empty links: independent, non-sibling tasks (no convoy/supersedes exclusion).
        assert record["links"] == {"deps": [], "supersedes": []}
        assert record["trace"]["errors"][0]["tool"] == _APPLY_TOOL


def test_lifecycle_is_strictly_ordered_across_tasks() -> None:
    records = sequence_records(_sequences(3))
    for earlier, later in itertools.pairwise(records):
        # The temporal-LOO precondition: an earlier task closes strictly before the next
        # starts, so it is an admissible retrievable neighbour of the later one.
        assert earlier["lifecycle"]["closed"] < later["lifecycle"]["started"]
        assert earlier["lifecycle"]["started"] < earlier["lifecycle"]["closed"]


def test_bundles_are_valid_with_self_only_loo() -> None:
    seqs = _sequences(2)
    bundles = sequence_bundles(seqs)
    assert [b.work_id for b in bundles] == [s.sequence_id for s in seqs]
    for bundle, seq in zip(bundles, seqs, strict=True):
        assert isinstance(bundle, TaskBundle)
        # LOO preservation: self only — independent tasks add no convoy/supersedes sibling.
        assert bundle.loo_excluded_work_ids == (seq.sequence_id,)
        assert bundle.rig == SYNTHETIC_RIG
        assert bundle.output.replay_success_rate == 0.0
        assert (
            bundle.issue_title
            == next(step for step in reversed(seq.steps) if step.outcome_checks).user_request
        )


def test_lessons_embed_provided_signatures_verbatim() -> None:
    seqs = _sequences(2)
    sigs = {
        # A multi-signature work_id (full + relaxed) so the test catches a bug that drops
        # all-but-one signature — the 1-tuple case would pass such a bug silently.
        seqs[0].sequence_id: (
            "apply_config:config/apply_config.yaml:1:some-class",
            "apply_config:apply_config.yaml:1:some-class",
        ),
        seqs[1].sequence_id: ("apply_config:apply_config.yaml:1:some-class",),
    }
    lessons = sequence_lessons(seqs, sigs)
    assert len(lessons) == 2
    for lesson, seq in zip(lessons, seqs, strict=True):
        assert lesson["work_id"] == seq.sequence_id
        facts = lesson["payload"]["facts"]
        # EVERY canonical signature rides in a fact VERBATIM (no Python recompute), so a
        # later task's signature_overlap scan finds it in the injected payload.
        for signature in sigs[seq.sequence_id]:
            assert any(signature in fact for fact in facts)


def test_lesson_extracted_at_matches_record_closed_time() -> None:
    """The documented provenance-clock coupling: a lesson's ``extracted_at`` must equal
    its record's ``closed`` time so the lesson shares the record's temporal-LOO clock.
    Both are derived independently from ``_lifecycle`` — pin the equality so a future
    refactor that desyncs the two default clocks fails loudly."""
    seqs = _sequences(2)
    records = sequence_records(seqs)
    sigs = {s.sequence_id: ("apply_config:config/apply_config.yaml:1:c",) for s in seqs}
    lessons = sequence_lessons(seqs, sigs)
    for record, lesson in zip(records, lessons, strict=True):
        assert lesson["extracted_at"] == record["lifecycle"]["closed"]


def test_lessons_require_resolved_signatures() -> None:
    seqs = _sequences(2)
    with pytest.raises(ValueError, match="no canonical signatures"):
        sequence_lessons(seqs, {seqs[0].sequence_id: (), seqs[1].sequence_id: ()})


def test_no_authored_value_leaks_into_agent_readable_text() -> None:
    seqs = _sequences(2)
    records = sequence_records(seqs)
    bundles = sequence_bundles(seqs)
    sigs = {s.sequence_id: ("apply_config:config/apply_config.yaml:1:c",) for s in seqs}
    lessons = sequence_lessons(seqs, sigs)
    for record, bundle, lesson, seq in zip(records, bundles, lessons, seqs, strict=True):
        values = _authored_values(seq)
        assert values  # the sequence really has a current + stale value to protect
        surfaces = [
            record["title"],
            str(record["trace"]["errors"][0]["message"]),
            bundle.issue_title,
            *lesson["payload"]["facts"],
        ]
        for surface in surfaces:
            for value in values:
                assert not states_value(surface, value), (value, surface)


def test_rejects_non_tool_requiring_world() -> None:
    text_seqs = _sequences(2, tool_requiring=False)
    with pytest.raises(ValueError, match="tool-requiring"):
        sequence_records(text_seqs)
    with pytest.raises(ValueError, match="tool-requiring"):
        sequence_bundles(text_seqs)


def test_leak_guard_raises_on_a_planted_answer() -> None:
    seqs = _sequences(1)
    current = _authored_values(seqs[0])[0]
    leaking = seqs[0].model_copy(update={"title": f"the answer is {current} for the record"})
    with pytest.raises(ValueError, match="leaks authored answer value"):
        sequence_records([leaking])


def test_leak_guard_covers_bundle_issue_title() -> None:
    """The firewall must fire on ``sequence_bundles``' issue_title, not only records —
    plant the answer into the goal step's user_request and assert it is caught."""
    seq = _sequences(1)[0]
    current = _authored_values(seq)[0]
    goal_idx = max(i for i, step in enumerate(seq.steps) if step.outcome_checks)
    steps = list(seq.steps)
    steps[goal_idx] = steps[goal_idx].model_copy(update={"user_request": f"set it to {current}"})
    leaking = seq.model_copy(update={"steps": steps})
    with pytest.raises(ValueError, match="leaks authored answer value"):
        sequence_bundles([leaking])


def test_leak_guard_covers_lesson_facts() -> None:
    """The firewall must fire on ``sequence_lessons``' facts — a caller-supplied signature
    string carrying the answer value is caught before it reaches the retrievable lesson."""
    seq = _sequences(1)[0]
    current = _authored_values(seq)[0]
    with pytest.raises(ValueError, match="leaks authored answer value"):
        sequence_lessons(
            [seq], {seq.sequence_id: (f"apply_config:config/apply_config.yaml:1:{current}",)}
        )


# -------------------------------------------------------------------- integration


def _load_script(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _driver() -> types.ModuleType:
    for sibling in ("run_gate_probe", "run_grid", "run_grid_3arm"):
        if sibling not in sys.modules:
            _load_script(sibling)
    return _load_script("gate_toolreq_offline")


def test_offline_gate_fires_end_to_end(tmp_path: Path) -> None:
    """The bead's acceptance, offline: a frozen tool-requiring world loads as TaskBundles
    and PASSES the mem-xe2p mechanism-fires gate — signature_overlap > 0 on the held
    anchor, no LOO id injected, and the lesson signatures equal ``mem retrieve``'s."""
    require_mem_cli(DIST_MAIN)
    driver = _driver()

    seqs = _sequences(2)
    outcome = driver.run_offline_gate(
        seqs,
        store_path=tmp_path / "store.db",
        out_dir=tmp_path / "gate",
        mem_bin=str(MEM_BIN),
    )

    assert outcome.gate.fired is True
    assert outcome.gate.overridden is False
    held = outcome.held_anchor
    assert held == seqs[-1].sequence_id
    # The mechanism fired on the intended anchor, not merely somewhere.
    assert outcome.observations[held] > 0

    # No payload injected a LOO-excluded id (own work / siblings).
    for bundle in outcome.bundles:
        assert not (set(outcome.payloads[bundle.work_id]) & set(bundle.loo_excluded_work_ids))

    # Parity: the signatures the lessons injected are exactly what mem computed for the
    # held record — read the relaxed set straight from the retrieval envelope and assert
    # every one appears in the injected payload (no Python recompute anywhere).
    envelope = json.loads(
        subprocess.run(
            [
                str(MEM_BIN),
                "retrieve",
                held,
                "--scope",
                "same-rig",
                "--store",
                str(tmp_path / "store.db"),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    relaxed = envelope["data"]["query_signatures_relaxed"]
    assert relaxed
    injected = "".join(outcome.payloads[held].values()).lower()
    for signature in relaxed:
        assert signature.lower() in injected


def test_dry_run_main_is_green_and_persists_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--dry-run` runs the identical FREE gate in an ephemeral workspace and exits 0
    without writing under the requested --out."""
    require_mem_cli(DIST_MAIN)
    driver = _driver()

    # Freeze a tiny tool-requiring world dir the driver's main() can read.
    from membench.generators.nemo.world_builder import write_world
    from membench.generators.world_manifest import build_manifest, write_manifest

    world, project = _world(), _project()
    seqs = _sequences(2)
    # write_world creates and returns the base_dir/<seed>/ dir — use the returned
    # seed dir for the manifest and the driver (base_dir alone lacks world.json).
    world_dir = write_world(world, project, base_dir=tmp_path / "world")
    manifest = build_manifest(
        world, project, seqs, nim_model="none", n_tasks=2, facts_per_task=3, tool_requiring=True
    )
    write_manifest(manifest, world_dir=world_dir)

    persisted = tmp_path / "should-not-appear"
    rc = driver.main(
        [
            "--world-dir",
            str(world_dir),
            "--mem-bin",
            str(MEM_BIN),
            "--out",
            str(persisted),
            "--dry-run",
        ]
    )
    assert rc == 0
    assert not persisted.exists()
    assert "mechanism" in capsys.readouterr().out.lower()
