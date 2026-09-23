"""The necessity sweep — the gate run over a whole corpus, with its rate reportable.

These tests pin the two things that make the artifact trustworthy: a sequence a
no-memory arm can answer is REJECTED (the sweep discriminates rather than counting),
and the rejection rate is computed over CANDIDATES, not over the accepted set. They
also pin ``reference_agent`` onto the written artifact, because a ScriptedAgent
verdict read as a real-agent verdict is the failure this field exists to prevent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from statistics import median

import pytest

from membench.cli import main as cli_main
from membench.generators.enterprise_workflow import materialize_project_tier
from membench.generators.memory_necessity_gate import GateCandidate
from membench.generators.necessity_sweep import (
    GATE_VERSION,
    context_for,
    describe_rate,
    gate_corpus,
    load_corpus_sequences,
    sweep_necessity,
    write_necessity_artifact,
)
from membench.generators.synthetic_task import generate_synthetic_sequence
from membench.report.comparison import EPSILON
from membench.runner.agent import ScriptedAgent
from membench.schemas.sequence import BenchmarkSequence, OutcomeCheck, SequenceStep
from membench.schemas.world import Channel, EnterpriseWorld, Persona, Project, Team

# The memory-bench package root, so the gate SCRIPT can be run the way CI runs it.
REPO_ROOT = Path(__file__).resolve().parents[1]


def _candidates(*sequences: BenchmarkSequence) -> list[GateCandidate]:
    """Isolated-scope candidates: no context, so each is piloted alone."""
    return [GateCandidate(sequence=s) for s in sequences]


def _enterprise_world(seed: int = 5) -> tuple[EnterpriseWorld, Project]:
    world = EnterpriseWorld(
        world_id=f"world-seed{seed}",
        domain="cuda-engineering",
        org_name="Acme",
        teams=[Team(team_id="t1", name="Kernels")],
        personas=[
            Persona(persona_id="p1", name="Ada Lovelace", role="staff-engineer", team_id="t1"),
            Persona(persona_id="p2", name="Grace Hopper", role="reliability", team_id="t1"),
        ],
        channels=[Channel(channel_id="c1", name="kernels", kind="chat")],
        seed=seed,
    )
    project = Project(
        project_id=f"world-seed{seed}-project",
        world_id=world.world_id,
        name="Acme initiative",
        goal="Reconcile the launch config.",
    )
    return world, project


def _world_sequences() -> list[BenchmarkSequence]:
    """One project world's three sequences, in the order the corpus freezes them."""
    return materialize_project_tier(*_enterprise_world(), n_tasks=3, seed=5)


def _answerable_without_memory(seq_id: str = "no-memory-dep") -> BenchmarkSequence:
    """A hand-built sequence whose only check requires no memory: it passes
    statelessly in both arms, so oracle == no_memory and it must be rejected."""
    return BenchmarkSequence(
        sequence_id=seq_id,
        title="answerable without memory",
        domain="test",
        goal="answer without recalling anything",
        steps=[
            SequenceStep(
                step_id="s0",
                user_request="answer",
                outcome_checks=[
                    OutcomeCheck(
                        check_id="c0",
                        description="passes without any memory",
                        requires_memory=[],
                    )
                ],
            )
        ],
    )


def test_sequence_answerable_without_memory_is_rejected() -> None:
    result = sweep_necessity(_candidates(_answerable_without_memory()))
    assert result.n_candidates == 1
    assert result.n_accepted == 0
    assert result.n_rejected == 1
    assert result.rejection_rate == 1.0
    (rejected,) = result.rejected
    assert rejected.sequence_id == "no-memory-dep"
    assert rejected.verdict.delta <= EPSILON
    assert "does not discriminate memory benefit" in rejected.verdict.reason


def test_rejection_rate_is_over_candidates_not_accepted() -> None:
    # 3 discriminating + 1 degenerate = 1/4 rejected. Over the ACCEPTED set the same
    # sweep would read 1/3 — the arithmetic this test exists to pin down.
    sequences = [generate_synthetic_sequence(seed=s) for s in (0, 1, 2)]
    sequences.append(_answerable_without_memory())
    result = sweep_necessity(_candidates(*sequences))
    assert (result.n_candidates, result.n_accepted, result.n_rejected) == (4, 3, 1)
    assert result.rejection_rate == pytest.approx(0.25)
    assert result.rejection_rate == result.n_rejected / result.n_candidates


def test_sweep_records_the_reference_agent_and_epsilon() -> None:
    result = sweep_necessity(
        _candidates(generate_synthetic_sequence(seed=0)), agent=ScriptedAgent("probe-ref")
    )
    assert result.reference_agent == "probe-ref"
    assert result.epsilon == EPSILON
    assert result.gate_version == GATE_VERSION


def test_sweep_over_no_candidates_raises() -> None:
    # Never 0.0: a rate over an empty corpus would report a clean sweep of nothing.
    with pytest.raises(ValueError, match="empty candidate set"):
        sweep_necessity([])


def _write_corpus(base: Path) -> None:
    for world, sequences in (
        ("0", [generate_synthetic_sequence(seed=0), generate_synthetic_sequence(seed=1)]),
        ("1", [_answerable_without_memory()]),
    ):
        d = base / world
        d.mkdir(parents=True)
        (d / "sequences.json").write_text(
            json.dumps([s.model_dump() for s in sequences]), encoding="utf-8"
        )


def test_gate_corpus_reads_every_world_and_keeps_its_origin(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    candidates = load_corpus_sequences(tmp_path)
    assert [c.world for c in candidates] == ["0", "0", "1"]

    report = gate_corpus(tmp_path)
    assert report.sweep.n_candidates == 3
    assert report.sweep.n_rejected == 1
    assert report.worlds["no-memory-dep"] == "1"


def test_artifact_carries_the_agent_the_rate_was_measured_under(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    path = write_necessity_artifact(gate_corpus(tmp_path, agent=ScriptedAgent("probe-ref")))

    assert path == tmp_path / "necessity.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["reference_agent"] == "probe-ref"
    assert artifact["gate_version"] == GATE_VERSION
    assert (artifact["n_candidates"], artifact["n_accepted"], artifact["n_rejected"]) == (3, 2, 1)
    assert artifact["rejection_rate"] == pytest.approx(1 / 3)
    rejected = [row for row in artifact["per_sequence"] if not row["accepted"]]
    assert [row["sequence_id"] for row in rejected] == ["no-memory-dep"]
    assert rejected[0]["world"] == "1"
    assert rejected[0]["oracle_reward"] == rejected[0]["no_memory_reward"]


def test_missing_corpus_dir_raises_rather_than_reporting_an_empty_sweep(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        gate_corpus(tmp_path / "absent")
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match=r"no sequences\.json"):
        gate_corpus(tmp_path / "empty")


def test_context_for_gives_a_project_sequence_the_siblings_before_it() -> None:
    world = _world_sequences()
    assert context_for(world, 0) == ()
    assert context_for(world, 1) == (world[0],)
    assert context_for(world, 2) == (world[0], world[1])


def test_context_for_leaves_a_session_sequence_isolated() -> None:
    # A session-tier task is answerable inside its own scope, so replaying siblings
    # would hand its pilot a store the benchmark never gives the arm under test.
    session = [
        generate_synthetic_sequence(seed=0),
        generate_synthetic_sequence(seed=1),
    ]
    assert all(s.tier == "session" for s in session)
    assert context_for(session, 1) == ()


def test_context_for_refuses_an_index_outside_its_world() -> None:
    with pytest.raises(IndexError):
        context_for(_world_sequences(), 3)


def test_a_project_tier_corpus_is_admitted_in_full(tmp_path: Path) -> None:
    """mem-r6yzk B1 — the corpus half of the gate half.

    Every continuation task of a project world reads a charter its first sequence
    wrote. Swept one-by-one in isolated stores they were all rejected; swept at the
    scope the corpus gives them they all discriminate.
    """
    sequences = _world_sequences()
    world_dir = tmp_path / "0"
    world_dir.mkdir(parents=True)
    (world_dir / "sequences.json").write_text(
        json.dumps([s.model_dump() for s in sequences]), encoding="utf-8"
    )

    report = gate_corpus(tmp_path)
    assert report.sweep.n_candidates == 3
    assert report.sweep.n_rejected == 0, [r.verdict.reason for r in report.sweep.rejected]
    # Isolated, the continuation tasks cannot pass — which is what the sweep used to do.
    assert sweep_necessity(_candidates(sequences[1])).n_accepted == 0


def test_the_artifact_records_the_scope_each_delta_was_measured_over(tmp_path: Path) -> None:
    # A delta is unreadable without its denominator: the same task reads as
    # discriminating or not depending on which trials were averaged.
    _write_corpus(tmp_path)
    path = write_necessity_artifact(gate_corpus(tmp_path))
    rows = json.loads(path.read_text(encoding="utf-8"))["per_sequence"]
    assert rows
    for row in rows:
        assert row["scope"] == f"the 1 graded step(s) of {row['sequence_id']}"
        assert row["scope"] in row["reason"]


def test_cli_gate_corpus_prints_the_margin_and_what_the_rate_is_evidence_of(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rate alone overstates itself, so the deltas travel with it (mem-r6yzk R4).

    "200/200 admitted, rejection_rate 0.0000" reads as a threshold that examined and
    passed 200 candidates. On the published corpus every delta was exactly 1.000
    against an epsilon of 0.05, so the threshold decided nothing -- a fact that lives
    in the deltas and is invisible in the rate. An operator reads this line and nothing
    else, so the line has to carry the margin and say which of the two readings the
    rate supports.
    """
    _write_corpus(tmp_path)
    out = tmp_path / "necessity.json"
    assert cli_main(["gate-corpus", str(tmp_path), "--out", str(out), "--min-accepted", "1"]) == 0
    printed = capsys.readouterr().out

    report = gate_corpus(tmp_path)
    sweep = report.sweep
    deltas = sorted(r.verdict.delta for r in (*sweep.accepted, *sweep.rejected))
    expected = (
        f"delta = oracle - no_memory over {len(deltas)} candidate(s): min {deltas[0]:.4f}, "
        f"median {median(deltas):.4f}, max {deltas[-1]:.4f}, against epsilon "
        f"{sweep.epsilon:.4f}; the closest candidate sits {deltas[0] - sweep.epsilon:+.4f} "
        "from the threshold."
    )
    assert expected in printed, printed

    assert f"What {sweep.rejection_rate:.4f} is evidence of:" in printed, printed
    assert "the corpus separates" in printed, printed
    assert "What it is NOT evidence of:" in printed, printed
    assert "that epsilon is doing work" in printed, printed
    assert "No model ran." in printed, printed

    # The margin has to be the sweep's own arithmetic, not a constant that happens to
    # look right on one fixture: this corpus spans the boundary, so min and max differ
    # and a line printing either one twice would fail here.
    assert deltas[0] != deltas[-1], "fixture no longer spans the boundary; the line is untested"

    # ``scripts/gate_public_corpus.py`` is the surface CI and pre-commit call, and it is
    # the one whose output gets pasted into a report. It prints the same three lines,
    # from the same renderer, or the reading of the rate depends on which entry point
    # the operator happened to use.
    script_out = tmp_path / "script-necessity.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "gate_public_corpus.py"),
            str(tmp_path),
            "--out",
            str(script_out),
            "--min-accepted",
            "1",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    for line in describe_rate(sweep):
        assert line in completed.stdout, (line, completed.stdout)


def test_cli_gate_corpus_fails_when_too_few_sequences_are_admitted(tmp_path: Path) -> None:
    # The floor must fail LOUDLY: a corpus that stops discriminating memory benefit
    # exits non-zero rather than writing a quiet artifact nobody reads.
    _write_corpus(tmp_path)
    out = tmp_path / "necessity.json"
    assert cli_main(["gate-corpus", str(tmp_path), "--out", str(out), "--min-accepted", "3"]) == 1
    assert cli_main(["gate-corpus", str(tmp_path), "--out", str(out), "--min-accepted", "2"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["n_accepted"] == 2
