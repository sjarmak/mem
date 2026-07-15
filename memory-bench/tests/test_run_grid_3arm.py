"""3-arm pilot CLI (mem-p3w): row assembly, reuse semantics, the held-signature
covariate, and the mechanism gate. Payload resolution moved to the ``ours`` arm with
``resolve_payloads`` (mem-rsmq7) — its tests live in ``test_ours_system``.

`scripts/run_grid_3arm.py` is not a package module, so it is loaded from its file
path (the test_run_gate_probe idiom, preloading its sibling-script imports). No
Docker, no network: retrieval goes through an injected runner; row assembly reads
fixture result JSONs written to a temp grid dir.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from membench.bundle.replay import ReplayResult
from membench.grading.probe_direct import ProbeEfficiency
from membench.harbor.bundle_grid import GridConditionResult
from membench.memory_systems.ours_system import OursQuery
from membench.schemas.bundle import BundleEnv, TaskBundle

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


for _sibling in ("run_gate_probe", "run_grid"):
    if _sibling not in sys.modules:
        _load_script(_sibling)
three_arm_cli = _load_script("run_grid_3arm")


def _bundle(work_id: str) -> TaskBundle:
    return TaskBundle(
        work_id=work_id,
        rig="demo",
        issue_title="t",
        issue_body="",
        trace_ref="/tmp/t.jsonl",
        output=ReplayResult(calls=(), file_diffs=(("src/app.ts", "x"),), replay_success_rate=0.0),
        env=BundleEnv(repo="demo", base_commit="0" * 40, base_image="node:22-bookworm"),
        loo_excluded_work_ids=(work_id,),
    )


def _result(work_id: str, condition: str, *, turns: int = 10) -> GridConditionResult:
    return GridConditionResult(
        work_id=work_id,
        condition=condition,
        score_direct=0.0,
        score_artifact=0.5,
        direct_mode="test_repro",
        repro_passed=False,
        repro_error=None,
        efficiency=ProbeEfficiency(turns=turns, tool_calls=5, input_tokens=10, output_tokens=100),
        candidate_files=("src/app.ts",),
    )


def _write(grid_dir: Path, result: GridConditionResult) -> None:
    grid_dir.mkdir(parents=True, exist_ok=True)
    path = grid_dir / f"{result.work_id}.{result.condition}.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def test_assemble_rows_reuses_none_clean_for_empty_retrieval(tmp_path: Path) -> None:
    grid_dir = tmp_path / "grid"
    # demo-a has a payload-bearing ours run; demo-b's retrieval was empty.
    for work_id, turns in (("demo-a", 20), ("demo-b", 10)):
        _write(grid_dir, _result(work_id, "none-clean", turns=turns))
        _write(grid_dir, _result(work_id, "none", turns=turns + 5))  # cached -> builtin
    _write(grid_dir, _result("demo-a", "ours", turns=12))

    rows = three_arm_cli.assemble_rows(
        [_bundle("demo-a"), _bundle("demo-b")],
        {"demo-a": {"prior-1": "payload"}, "demo-b": {}},
        grid_dir,
    )

    by_id = {row.work_id: row for row in rows}
    assert by_id["demo-a"].ours_retrieval_empty is False
    assert dict(by_id["demo-a"].deltas_ours)["turns"] == -8.0
    # builtin relabeled from the cached `none` scoring.
    assert by_id["demo-a"].builtin.condition == "builtin"
    assert dict(by_id["demo-a"].deltas_builtin)["turns"] == 5.0
    # Empty retrieval: ours IS the none-clean run, all deltas exactly 0.
    assert by_id["demo-b"].ours_retrieval_empty is True
    assert by_id["demo-b"].ours.condition == "ours"
    assert all(delta == 0.0 for _, delta in by_id["demo-b"].deltas_ours)


def test_assemble_rows_raises_on_missing_leg(tmp_path: Path) -> None:
    grid_dir = tmp_path / "grid"
    _write(grid_dir, _result("demo-a", "none-clean"))
    # No cached `none` (builtin) leg on disk.
    with pytest.raises(FileNotFoundError, match=r"demo-a \[none\]"):
        three_arm_cli.assemble_rows([_bundle("demo-a")], {"demo-a": {}}, grid_dir)


def test_scrub_unfinished_jobs_removes_only_resultless_pilot_dirs(tmp_path: Path) -> None:
    """The ghost-trap guard: a job dir without its probe result file is a corpse
    from a died run -- remove it so resume re-executes instead of re-harvesting
    the stale transcript. Finished pairs and cached none/oracle jobs survive."""
    probe_dir = tmp_path / "probe"
    jobs = probe_dir / "jobs"
    for name in ("demo-a.none-clean", "demo-a.ours", "demo-a.none"):
        (jobs / name).mkdir(parents=True)
        (jobs / f"{name}.job.json").write_text("{}", encoding="utf-8")
    # demo-a.none-clean finished (result exists); demo-a.ours died mid-run.
    (probe_dir / "demo-a.none-clean.json").write_text("{}", encoding="utf-8")

    three_arm_cli.scrub_unfinished_jobs(
        [_bundle("demo-a")], ("none-clean", "ours"), probe_dir=probe_dir
    )

    assert (jobs / "demo-a.none-clean").is_dir()  # finished: kept
    assert not (jobs / "demo-a.ours").exists()  # corpse: scrubbed
    assert not (jobs / "demo-a.ours.job.json").exists()
    assert (jobs / "demo-a.none").is_dir()  # cached gate job: never touched


# --- mem-tnyo: held-signature covariate input -----------------------------------------


def test_resolve_held_signatures_reads_the_retrieval_envelope() -> None:
    """The held record's canonical full+relaxed signatures come from the TS
    retrieval result's query_signatures fields -- never recomputed in Python."""

    def runner(query: OursQuery) -> dict:
        assert query.no_trace_query is False  # the trace-triggered call carries them
        return {
            "items": [],
            "query_signatures": ["tsc:src/a.ts:12:TS2345"],
            "query_signatures_relaxed": ["tsc:a.ts:TS2345"],
        }

    signatures = three_arm_cli.resolve_held_signatures(
        [_bundle("demo-a")], store_path=Path("/tmp/s.db"), runner=runner
    )
    assert signatures == {"demo-a": ("tsc:src/a.ts:12:TS2345", "tsc:a.ts:TS2345")}


# --- mem-xe2p: pre-run mechanism-fires gate at the grid entrypoint --------------------


def test_tier1_mechanism_gate_passes_when_signature_overlap_fires() -> None:
    payloads = {
        "demo-a": {"prior-1": "recurrence of tsc:src/a.ts:12:TS2345 fixed by pinning"},
        "demo-b": {},
    }
    held = {"demo-a": ("tsc:src/a.ts:12:TS2345",), "demo-b": ()}

    gate = three_arm_cli.tier1_mechanism_gate(payloads, held)

    assert gate.fired is True
    assert gate.mechanism == "tier1_exact_signature_retrieval"
    assert gate.covariate == "signature_overlap"
    assert gate.fired_anchor_ids == ("demo-a",)


def test_tier1_mechanism_gate_refuses_the_grid_when_the_mechanism_never_fires() -> None:
    """The mem-lvp.24 shape: payloads resolved, but no anchor's payload shares a
    held signature — the mechanism under test never engages, so the grid must
    refuse before a single leg burns."""
    from membench.grading.mechanism_gate import MechanismNeverFiredError

    payloads = {"demo-a": {"prior-1": "unrelated lesson"}, "demo-b": {}}
    held = {"demo-a": ("tsc:src/a.ts:12:TS2345",), "demo-b": ()}

    with pytest.raises(MechanismNeverFiredError) as exc:
        three_arm_cli.tier1_mechanism_gate(payloads, held)
    message = str(exc.value)
    for token in ("tier1_exact_signature_retrieval", "signature_overlap", "demo-a", "demo-b"):
        assert token in message


def test_tier1_mechanism_gate_override_is_recorded_never_silent() -> None:
    gate = three_arm_cli.tier1_mechanism_gate(
        {"demo-a": {}}, {"demo-a": ()}, override_reason="diagnosing an empty substrate"
    )
    assert gate.fired is False
    assert gate.overridden is True
    assert gate.override_reason == "diagnosing an empty substrate"


def test_issue_trigger_summary_pairs_against_none_clean(tmp_path: Path) -> None:
    grid_dir = tmp_path / "grid"
    _write(grid_dir, _result("demo-a", "none-clean", turns=20))
    _write(grid_dir, _result("demo-a", "ours-issue-trigger", turns=14))
    _write(grid_dir, _result("demo-b", "none-clean", turns=10))

    block = three_arm_cli.issue_trigger_summary(
        [_bundle("demo-a"), _bundle("demo-b")],
        {"demo-a": {"prior-1": "payload"}, "demo-b": {}},
        grid_dir,
    )

    assert block["trigger"] == "issue-text"
    assert block["n_attempted"] == 1
    assert block["not_attempted_empty_retrieval"] == ["demo-b"]
    assert block["per_bundle"]["demo-a"]["deltas_vs_none_clean"]["turns"] == -6.0
