"""Tests for the ablation grid execution driver (mem-apg.3d).

The driver ties mem-apg.2 (the WorkRecord -> rung task dirs adapter) to mem-apg.3a
(the deterministic scorer): for one held-out WorkRecord it emits a task dir per
rung, injects that rung's memory content, runs the agent, harvests a RunTrace, and
scores it against the held-out trace errors. The whole pipeline runs with a
deterministic StubRunner and an injected extractor -- NO Docker, network, or paid
API. The thin HarborRunner's HARVESTING logic (transcript -> RunTrace) is tested
with a fixture transcript + injected extractor; its real exec is not exercised here.
"""

import pytest

from membench.grading import RunTrace, TraceErrorRef
from membench.harbor.grid import (
    HarborRunner,
    StubRunner,
    harvest_run_trace,
    run_grid,
)
from membench.harbor.memory_inject import MEMORY_FILENAME


def _err(tool="tsc", file="src/a.ts", line=12, error_class="TS2345", signature=None):
    sig = signature if signature is not None else f"{tool}:{file}:{line}:{error_class}"
    return TraceErrorRef(tool=tool, file=file, line=line, error_class=error_class, signature=sig)


def _record(work_id="w1", rig="mem", title="Fix the broken parser", started="2026-01-10T00:00:00Z"):
    return {
        "work_id": work_id,
        "rig": rig,
        "title": title,
        "lifecycle": {
            "created": "2026-01-01T00:00:00Z",
            "started": started,
            "closed": "2026-02-01T00:00:00Z",
            "status": "closed",
        },
    }


# --- StubRunner: resolves the rung from the task.toml the adapter wrote --------


def test_stub_runner_resolves_by_rung(tmp_path):
    from membench.harbor.workrecord_adapter import WorkRecordLadderAdapter

    WorkRecordLadderAdapter(_record(), tmp_path).run()
    none_dir = tmp_path / "w1-none"
    trace = RunTrace(errors=(), files_touched=frozenset({"src/a.ts"}))
    runner = StubRunner({"none": trace})
    assert runner.run(none_dir) == trace


def test_stub_runner_unknown_rung_raises(tmp_path):
    from membench.harbor.workrecord_adapter import WorkRecordLadderAdapter

    WorkRecordLadderAdapter(_record(), tmp_path).run()
    runner = StubRunner({"none": RunTrace(errors=(), files_touched=frozenset())})
    with pytest.raises(KeyError):
        runner.run(tmp_path / "w1-ours")


# --- harvest_run_trace: transcript -> RunTrace via injected extractor ----------


def test_harvest_extracts_errors_and_files():
    transcript = {
        "output": "src/a.ts(12,5): error TS2345: bad arg",
        "files_read": ["src/a.ts"],
        "files_written": ["src/b.ts"],
    }

    def extractor(output):
        # Stub for the canonical TS extractor: returns trace_errors-shaped rows.
        assert "TS2345" in output
        return [
            {
                "tool": "tsc",
                "file": "src/a.ts",
                "line": 12,
                "error_class": "TS2345",
                "signature": "tsc:src/a.ts:12:TS2345",
            }
        ]

    trace = harvest_run_trace(transcript, extractor=extractor)
    assert trace.errors == (_err(),)
    assert trace.files_touched == frozenset({"src/a.ts", "src/b.ts"})


def test_harvest_clean_run_has_no_errors():
    transcript = {"output": "all good", "files_read": ["src/a.ts"], "files_written": []}
    trace = harvest_run_trace(transcript, extractor=lambda _: [])
    assert trace.errors == ()
    assert trace.files_touched == frozenset({"src/a.ts"})


def test_harvest_missing_file_keys_default_empty():
    trace = harvest_run_trace({"output": ""}, extractor=lambda _: [])
    assert trace.files_touched == frozenset()


# --- HarborRunner: harvesting wired through an injected exec + extractor -------


def test_harbor_runner_harvests_via_injected_exec(tmp_path):
    from membench.harbor.workrecord_adapter import WorkRecordLadderAdapter

    WorkRecordLadderAdapter(_record(), tmp_path).run()

    def fake_exec(task_dir):
        # Stands in for `harbor run <task_dir>` -- never invoked in CI for real.
        return {
            "output": "src/a.ts(12,5): error TS2345: bad arg",
            "files_read": ["src/a.ts"],
            "files_written": [],
        }

    runner = HarborRunner(
        exec_task=fake_exec,
        extractor=lambda _: [
            {
                "tool": "tsc",
                "file": "src/a.ts",
                "line": 12,
                "error_class": "TS2345",
                "signature": "tsc:src/a.ts:12:TS2345",
            }
        ],
    )
    trace = runner.run(tmp_path / "w1-none")
    assert trace.errors == (_err(),)
    assert "src/a.ts" in trace.files_touched


def test_harbor_runner_default_exec_is_documented_and_not_callable_in_ci():
    # The default exec shells `harbor run`; constructing without an injected exec
    # is allowed, but calling run() without harbor present must fail loudly, not
    # silently fabricate a trace.
    runner = HarborRunner(extractor=lambda _: [])
    with pytest.raises((RuntimeError, FileNotFoundError, ValueError)):
        runner.run(_record_dir_that_does_not_exist())


def _record_dir_that_does_not_exist():
    from pathlib import Path

    return Path("/nonexistent/w1-none")


# --- run_grid: full pipeline with StubRunner + memory injection ----------------


def test_run_grid_scores_every_non_deferred_rung(tmp_path):
    held = [_err(file="src/a.ts", line=12, error_class="TS2345")]
    # A fresh run that touched the held file and did NOT reproduce the error.
    resolved = RunTrace(errors=(), files_touched=frozenset({"src/a.ts"}))
    runner = StubRunner({"none": resolved, "ours": resolved, "oracle": resolved})

    records = run_grid(
        _record(),
        tmp_path,
        held_errors=held,
        runner=runner,
        rungs=("none", "ours", "oracle"),
        ours_payloads={"w-prior": "lesson: guard the boundary"},
        oracle_payload="prior lesson: validate before parse",
    )
    by_rung = {r.rung: r for r in records}
    assert set(by_rung) == {"none", "ours", "oracle"}
    for rec in records:
        assert rec.work_id == "w1"
        assert rec.repeat_idx == 0
        assert rec.components.path_reached is True
        assert rec.components.trace_error_resolved is True


def test_run_grid_injects_per_rung_memory(tmp_path):
    held = [_err()]
    runner = StubRunner(
        {
            "none": RunTrace(errors=(), files_touched=frozenset({"src/a.ts"})),
            "ours": RunTrace(errors=(), files_touched=frozenset({"src/a.ts"})),
        }
    )
    run_grid(
        _record(),
        tmp_path,
        held_errors=held,
        runner=runner,
        rungs=("none", "ours"),
        ours_payloads={"w-prior": "distilled lesson text"},
    )
    # none got no memory; ours got the distilled payload.
    assert not (tmp_path / "w1-none" / MEMORY_FILENAME).exists()
    assert "distilled lesson text" in (tmp_path / "w1-ours" / MEMORY_FILENAME).read_text()


def test_run_grid_records_recurrence_as_not_resolved(tmp_path):
    held = [_err(file="src/a.ts", line=12, error_class="TS2345")]
    # The fresh run reproduced the same failure class at a shifted line.
    recurred = RunTrace(
        errors=(_err(file="src/a.ts", line=99, error_class="TS2345"),),
        files_touched=frozenset({"src/a.ts"}),
    )
    runner = StubRunner({"none": recurred})
    [rec] = run_grid(_record(), tmp_path, held_errors=held, runner=runner, rungs=("none",))
    assert rec.components.path_reached is True
    assert rec.components.trace_error_resolved is False


def test_run_grid_no_op_run_is_not_path_reached(tmp_path):
    held = [_err(file="src/a.ts")]
    noop = RunTrace(errors=(), files_touched=frozenset({"src/unrelated.ts"}))
    runner = StubRunner({"none": noop})
    [rec] = run_grid(_record(), tmp_path, held_errors=held, runner=runner, rungs=("none",))
    assert rec.components.path_reached is False
    assert rec.reward == 0.0


def test_run_grid_skips_deferred_rungs(tmp_path):
    held = [_err()]
    runner = StubRunner({"none": RunTrace(errors=(), files_touched=frozenset({"src/a.ts"}))})
    records = run_grid(
        _record(),
        tmp_path,
        held_errors=held,
        runner=runner,
        rungs=("none", "builtin", "ours+builtin"),
    )
    # builtin / ours+builtin are deferred to a child bead -- not scored, not crashed.
    assert {r.rung for r in records} == {"none"}


def test_run_grid_unknown_rung_fails_before_any_execution(tmp_path):
    held = [_err()]

    class ExplodingRunner:
        def run(self, task_dir):  # pragma: no cover - must never be reached
            raise AssertionError("runner must not be invoked for an invalid ladder")

    with pytest.raises(ValueError, match="unknown ablation rung"):
        run_grid(
            _record(),
            tmp_path,
            held_errors=held,
            runner=ExplodingRunner(),
            rungs=("none", "oarcle"),  # typo'd rung
        )
    # Validation fired before the adapter: no task dirs were emitted.
    assert list(tmp_path.iterdir()) == []


def test_run_grid_repeat_idx_keys_records(tmp_path):
    held = [_err(file="src/a.ts")]
    runner = StubRunner({"none": RunTrace(errors=(), files_touched=frozenset({"src/a.ts"}))})
    records = run_grid(
        _record(), tmp_path, held_errors=held, runner=runner, rungs=("none",), repeat_idx=3
    )
    assert records[0].repeat_idx == 3


def test_run_grid_oracle_self_leak_fails_loud(tmp_path):
    from membench.harbor.memory_inject import OracleSelfLeakError

    held = [_err()]
    runner = StubRunner({"oracle": RunTrace(errors=(), files_touched=frozenset({"src/a.ts"}))})
    with pytest.raises(OracleSelfLeakError):
        run_grid(
            _record(),
            tmp_path,
            held_errors=held,
            runner=runner,
            rungs=("oracle",),
            oracle_payload=f"the answer is {held[0].signature}",
        )


def test_run_grid_empty_held_errors_raises(tmp_path):
    runner = StubRunner({"none": RunTrace(errors=(), files_touched=frozenset())})
    with pytest.raises(ValueError):
        run_grid(_record(), tmp_path, held_errors=[], runner=runner, rungs=("none",))
