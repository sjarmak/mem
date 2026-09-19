"""The comparison scheduler must never silently re-buy a started pair."""

from pathlib import Path
from typing import Any

import pytest

from membench.runner import bd_experiment as exp
from membench.runner.leg_plans import PAIR_ROLES, TRIAL_ROLES
from membench.runner.toolreq_corpus import load_twin_corpus
from tests.toolreq_helpers import corpus

BD_IDENTITY = {"path": "/fixture/bd", "sha256": "fixture", "version": "0.1"}


def execute(*args: Any, **kwargs: Any) -> dict[str, int]:
    return exp.execute(*args, bd_identity_reader=lambda: BD_IDENTITY, **kwargs)


@pytest.fixture
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("adoption-corpus")
    corpus(root, "w-0", "w-1")
    return root / "corpus"


def setup_plan(corpus_dir: Path) -> tuple[list[Any], dict[str, Any]]:
    _, tasks = load_twin_corpus(corpus_dir)
    tasks = exp.select_tasks(tasks, n_tasks=2)
    manifest = exp.build_manifest(
        tasks,
        model="pinned-model",
        cli_version="1.2.3",
        bd_identity=BD_IDENTITY,
        source_fingerprint="source",
        repeats=1,
        seed=17,
        timeout_s=600,
    )
    return tasks, manifest


class Cell:
    def row(self) -> dict[str, Any]:
        return {"runs": 2, "paid": True}


def test_schedule_balances_and_freezes_treatments(corpus_dir: Path) -> None:
    tasks, manifest = setup_plan(corpus_dir)
    assert len(tasks) == 4
    assert len(manifest["schedule"]) == 24
    assert manifest == setup_plan(corpus_dir)[1]
    # Every block is one task/variant carrying the WHOLE 3x2, so the second axis is balanced
    # against the first inside each block rather than across the run: a run stopped early still
    # has both native-memory states for each arm it bought.
    for offset in range(0, 24, 6):
        block = manifest["schedule"][offset : offset + 6]
        assert len({p["work_id"] for p in block}) == 1
        assert len({p["variant"] for p in block}) == 1
        assert {p["condition"] for p in block} == set(exp.CONDITIONS)
    assert manifest["conditions"]["generic"]["bd_context"] is False


def test_the_frozen_identity_states_the_pin_per_condition(corpus_dir: Path) -> None:
    """The pin is a second axis of the design, so the frozen manifest has to say which arms
    carry it. It is resolved through the same function the runner seeds the config dir with, so
    the manifest cannot describe one pin and the cell mint another."""
    _tasks, manifest = setup_plan(corpus_dir)
    assert manifest["native_memory_settings"] == {
        "generic": {},
        "explicit": {},
        "redirect": {},
        "generic_native_off": {"autoMemoryEnabled": False},
        "explicit_native_off": {"autoMemoryEnabled": False},
        "redirect_native_off": {"autoMemoryEnabled": False},
    }
    assert set(manifest["native_memory_settings"]) == set(manifest["conditions"])
    # A contrast can only be read if both its sides were bought in the same run.
    assert all(
        treatment in exp.CONDITIONS and baseline in exp.CONDITIONS
        for treatment, baseline in exp.CONDITION_CONTRASTS
    )


def test_every_arm_reaches_the_runner_with_its_own_pin(tmp_path: Path, corpus_dir: Path) -> None:
    """`_execute_pair` splats the frozen condition straight into `run_rung_cell`, so the pin
    arrives as a runner keyword rather than as a note about the arm. Six pairs is the whole 3x2
    for one task/variant: each hook mode appears both pinned and unpinned."""
    tasks, manifest = setup_plan(corpus_dir)
    calls: list[dict[str, Any]] = []

    def run(task: Any, **kwargs: Any) -> Cell:
        calls.append(kwargs)
        return Cell()

    execute(tmp_path, manifest, tasks, max_pairs=6, cell_runner=run)
    seen = {(c["bd_context"], c["native_memory_hook_mode"], c["native_memory_off"]) for c in calls}
    assert seen == {
        (False, "observe", False),
        (True, "observe", False),
        (True, "redirect", False),
        (False, "observe", True),
        (True, "observe", True),
        (True, "redirect", True),
    }


def test_resume_and_pair_bound(tmp_path: Path, corpus_dir: Path) -> None:
    tasks, manifest = setup_plan(corpus_dir)
    calls: list[dict[str, Any]] = []

    def run(task: Any, **kwargs: Any) -> Cell:
        calls.append(kwargs)
        return Cell()

    assert execute(tmp_path, manifest, tasks, max_pairs=2, cell_runner=run)["completed"] == 2
    assert len(calls) == 2
    assert execute(tmp_path, manifest, tasks, max_pairs=1, cell_runner=run)["completed"] == 3
    assert len(calls) == 3
    # A resumed run picks up at the schedule position it stopped at, so what was bought is the
    # schedule PREFIX, arm for arm -- not merely the right number of pairs.
    keys = set(exp.CONDITIONS["generic"])
    assert [{k: v for k, v in c.items() if k in keys} for c in calls] == [
        exp.CONDITIONS[p["condition"]] for p in manifest["schedule"][:3]
    ]
    assert all(c["repeats"] == 1 and c["rung"] == "R4" for c in calls)


def test_mismatch_refuses_before_spend(tmp_path: Path, corpus_dir: Path) -> None:
    tasks, manifest = setup_plan(corpus_dir)
    execute(tmp_path, manifest, tasks, max_pairs=1, cell_runner=lambda *a, **k: Cell())
    with pytest.raises(exp.ResumeMismatchError):
        execute(
            tmp_path,
            {**manifest, "model": "changed"},
            tasks,
            max_pairs=1,
            cell_runner=lambda *a, **k: pytest.fail("spent"),
        )


def test_interruption_preserves_raw_and_refuses_repurchase(
    tmp_path: Path, corpus_dir: Path
) -> None:
    tasks, manifest = setup_plan(corpus_dir)

    class Leg:
        filename = "leg.json"

        def row(self) -> dict[str, Any]:
            return {"stream": "raw evidence"}

    def interrupt(task: Any, **kwargs: Any) -> Cell:
        kwargs["on_leg"](Leg())
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        execute(tmp_path, manifest, tasks, max_pairs=1, cell_runner=interrupt)
    assert next(tmp_path.glob("pairs/**/leg.json")).read_text().find("raw evidence") >= 0
    with pytest.raises(exp.IncompletePairError):
        execute(
            tmp_path,
            manifest,
            tasks,
            max_pairs=1,
            cell_runner=lambda *a, **k: pytest.fail("re-bought"),
        )


def test_invalid_selection_and_bounds(tmp_path: Path, corpus_dir: Path) -> None:
    tasks, manifest = setup_plan(corpus_dir)
    with pytest.raises(ValueError):
        exp.select_tasks(tasks[:1], n_tasks=1)
    with pytest.raises(ValueError):
        execute(tmp_path, manifest, tasks, max_pairs=0)


def test_cli_freezes_plan_and_bounds_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corpus_dir: Path
) -> None:
    import subprocess

    monkeypatch.setattr(exp, "resolve_cli_version", lambda: "1.2.3")
    monkeypatch.setattr(exp, "resolve_bd_identity", lambda: BD_IDENTITY)
    monkeypatch.setattr(exp, "_refusal", lambda **kwargs: None)
    monkeypatch.setattr(
        exp.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "bd 0.1")
    )
    args = [
        "--out",
        str(tmp_path),
        "--model",
        "pinned-model",
        "--expect-cli-version",
        "1.2.3",
        "--corpus-dir",
        str(corpus_dir),
    ]
    assert exp.main(args) == 0
    assert (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "pairs").exists()
    purchases = []

    def execute(*a: Any, **k: Any) -> dict[str, int]:
        purchases.append(k)
        return {"completed": 1}

    monkeypatch.setattr(exp, "execute", execute)
    assert exp.main([*args, "--fire", "--max-pairs", "2"]) == 0
    assert purchases[0]["max_pairs"] == 2
    assert exp.main([*args, "--expect-cli-version", "wrong"]) == 2
    monkeypatch.setattr(exp, "_refusal", lambda **kwargs: "credentials missing")
    assert exp.main([*args, "--fire"]) == 2


def test_unowned_directory_and_task_change_refuse(tmp_path: Path, corpus_dir: Path) -> None:
    tasks, manifest = setup_plan(corpus_dir)
    (tmp_path / "unexpected").touch()
    with pytest.raises(exp.ResumeMismatchError):
        execute(tmp_path, manifest, tasks, max_pairs=1)
    with pytest.raises(exp.ResumeMismatchError):
        execute(tmp_path, manifest, tasks[:1], max_pairs=1)
    with pytest.raises(ValueError):
        exp.select_tasks(tasks, n_tasks=0)
    with pytest.raises(ValueError):
        exp.select_tasks(tasks, n_tasks=3)
    with pytest.raises(ValueError):
        exp.build_manifest(
            tasks,
            model="",
            cli_version="1",
            bd_identity=BD_IDENTITY,
            source_fingerprint="3",
            repeats=1,
            seed=1,
            timeout_s=1,
        )


def test_bd_identity_uses_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    binary = tmp_path / "bd-override"
    binary.write_text("#!/bin/sh\nprintf 'override-version\\n'\n")
    binary.chmod(0o700)
    monkeypatch.setenv("MEMBENCH_BD_BINARY", str(binary))
    identity = exp.resolve_bd_identity()
    assert identity == {
        "path": str(binary.resolve()),
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "version": "override-version",
    }


def test_binary_drift_refuses_before_next_pair(tmp_path: Path, corpus_dir: Path) -> None:
    tasks, manifest = setup_plan(corpus_dir)
    identities = iter(
        [manifest["bd_identity"], {"path": "/different", "sha256": "different", "version": "0.1"}]
    )
    calls = []

    def run(*a: Any, **k: Any) -> Cell:
        calls.append(k)
        return Cell()

    with pytest.raises(exp.ResumeMismatchError, match="bd executable"):
        exp.execute(
            tmp_path,
            manifest,
            tasks,
            max_pairs=2,
            cell_runner=run,
            bd_identity_reader=lambda: next(identities),
        )
    assert len(calls) == 1
    assert len(list(tmp_path.glob("pairs/**/started.json"))) == 1


# --------------------------------------------------------------------------------------
# the four-leg trial plan (mem-605zn)
# --------------------------------------------------------------------------------------


def _manifest(corpus_dir: Path, **overrides: Any) -> tuple[list[Any], dict[str, Any]]:
    _, tasks = load_twin_corpus(corpus_dir)
    tasks = exp.select_tasks(tasks, n_tasks=2)
    kwargs: dict[str, Any] = {
        "model": "pinned-model",
        "cli_version": "1.2.3",
        "bd_identity": BD_IDENTITY,
        "source_fingerprint": "source",
        "repeats": 1,
        "seed": 17,
        "timeout_s": 600,
        **overrides,
    }
    return tasks, exp.build_manifest(tasks, **kwargs)


def test_a_trial_manifest_plans_four_calls_per_schedule_entry(corpus_dir: Path) -> None:
    """The number a human authorizes money against counts every leg of the plan, and the plan is
    frozen into the manifest so a resume runs the legs the fire was priced for."""
    _, pair = _manifest(corpus_dir)
    assert pair["leg_plan"] == list(PAIR_ROLES)
    assert pair["planned_calls"] == 2 * len(pair["schedule"])
    _, trial = _manifest(corpus_dir, leg_plan=TRIAL_ROLES)
    assert trial["leg_plan"] == list(TRIAL_ROLES)
    assert trial["planned_calls"] == 4 * len(trial["schedule"])
    assert trial["schema_version"] == pair["schema_version"] == 5
    assert trial["schedule"] == pair["schedule"], "the plan must not reshuffle the schedule"
    with pytest.raises(ValueError, match="plan"):
        _manifest(corpus_dir, leg_plan=("establish", "bogus"))


def test_execute_hands_the_frozen_plan_to_every_cell(tmp_path: Path, corpus_dir: Path) -> None:
    tasks, manifest = _manifest(corpus_dir, leg_plan=TRIAL_ROLES)
    calls: list[dict[str, Any]] = []

    def run(task: Any, **kwargs: Any) -> Cell:
        calls.append(kwargs)
        return Cell()

    execute(tmp_path, manifest, tasks, max_pairs=2, cell_runner=run)
    assert [c["leg_plan"] for c in calls] == [TRIAL_ROLES, TRIAL_ROLES]


def test_a_legacy_manifest_without_a_plan_executes_pairs(tmp_path: Path, corpus_dir: Path) -> None:
    """Frozen artifacts predate the plan field; they were priced and fired as pairs."""
    tasks, manifest = _manifest(corpus_dir)
    legacy = {key: value for key, value in manifest.items() if key != "leg_plan"}
    calls: list[dict[str, Any]] = []

    def run(task: Any, **kwargs: Any) -> Cell:
        calls.append(kwargs)
        return Cell()

    execute(tmp_path, legacy, tasks, max_pairs=1, cell_runner=run)
    assert calls[0]["leg_plan"] == PAIR_ROLES


def test_cli_legs_flag_selects_the_trial_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corpus_dir: Path
) -> None:
    import json
    import subprocess

    monkeypatch.setattr(exp, "resolve_cli_version", lambda: "1.2.3")
    monkeypatch.setattr(exp, "resolve_bd_identity", lambda: BD_IDENTITY)
    monkeypatch.setattr(exp, "_refusal", lambda **kwargs: None)
    monkeypatch.setattr(
        exp.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "bd 0.1")
    )
    args = [
        "--out",
        str(tmp_path / "trial"),
        "--model",
        "pinned-model",
        "--expect-cli-version",
        "1.2.3",
        "--corpus-dir",
        str(corpus_dir),
        "--legs",
        "trial",
    ]
    assert exp.main(args) == 0
    frozen = json.loads((tmp_path / "trial" / "manifest.json").read_text())
    assert frozen["leg_plan"] == list(TRIAL_ROLES)
    assert frozen["planned_calls"] == 4 * len(frozen["schedule"])
    with pytest.raises(SystemExit):
        exp.main([*args[:-1], "bogus"])
