"""Two-rung utilization ladders vary the seeded memory and nothing else."""

from __future__ import annotations

import copy
import hashlib

import pytest

from membench.runner.bd_component_experiment import execute, freeze, validate_manifest
from tests.test_bd_component_experiment import fake_runner, manifest

RUNGS = ("transfer_hint", "directive")


def ladder(tmp_path, rungs=RUNGS):
    original = manifest(tmp_path)
    inputs = tmp_path / "inputs"
    prompt = original["schedule"][0]["prompt"]
    seeds = {}
    for rung in rungs:
        path = inputs / f"seed-{rung}"
        path.write_text(f"memory text for {rung}")
        seeds[rung] = {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    rows = [
        {
            "row_id": f"{rung}-{replicate}",
            "component": "retrieval",
            "intervention": rung,
            "replicate": replicate,
            "condition": "current",
            "initial_store": "seeded",
            "prompt": copy.deepcopy(prompt),
            "sources": [],
            "seed": copy.deepcopy(seeds[rung]),
        }
        for rung in rungs
        for replicate in (1, 2, 3, 4)
    ]
    return {**original, "schema": "bd-ladder-experiment.v1", "schedule": rows}


def test_exact_ladder_eight_no_repurchase(tmp_path):
    m = ladder(tmp_path)
    calls: list[str] = []
    out = tmp_path / "out"
    freeze(out, m)
    first = execute(out, m, session_runner=fake_runner(calls), identity_check=lambda: None)
    second = execute(out, m, session_runner=fake_runner(calls), identity_check=lambda: None)
    assert first["new_sessions"] == 8 and second["new_sessions"] == 0
    assert len(calls) == len(set(calls)) == 8


def test_each_rung_reaches_the_runner_with_its_own_seed(tmp_path):
    m = ladder(tmp_path)
    seen: dict[str, str] = {}

    def runner(row, configuration, out):
        seen[row["row_id"]] = row["seed"]["sha256"]
        return fake_runner([])(row, configuration, out)

    out = tmp_path / "out"
    freeze(out, m)
    execute(out, m, session_runner=runner, identity_check=lambda: None)
    hint = {sha for row, sha in seen.items() if row.startswith("transfer_hint")}
    directive = {sha for row, sha in seen.items() if row.startswith("directive")}
    assert len(hint) == len(directive) == 1 and hint != directive


@pytest.mark.parametrize("replicate", [None, 0, 5, True, "1", 1.0, []])
def test_invalid_ladder_replicate_rejected(tmp_path, replicate):
    m = ladder(tmp_path)
    m["schedule"][0]["replicate"] = replicate
    with pytest.raises(ValueError):
        validate_manifest(m)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate_id",
        "duplicate_slot",
        "adapter",
        "capture_row",
        "empty_store",
        "prompt_contrast",
        "row_source",
        "seed_within_rung",
        "identical_rungs",
        "identical_rung_content",
        "seed_as_prompt",
        "third_rung",
    ],
)
def test_ladder_contrasts_and_leaks_rejected(tmp_path, change):
    m = ladder(tmp_path)
    rows = m["schedule"]
    alternative = {"path": "binary", "sha256": m["pins"][0]["sha256"]}
    if change == "missing":
        rows.pop()
    elif change == "duplicate_id":
        rows[1]["row_id"] = rows[0]["row_id"]
    elif change == "duplicate_slot":
        rows[1]["replicate"] = 1
    elif change == "adapter":
        rows[0]["condition"] = "focused"
    elif change == "capture_row":
        rows[0]["component"] = "capture"
    elif change == "empty_store":
        rows[0]["initial_store"] = "empty"
        rows[0]["seed"] = None
    elif change == "prompt_contrast":
        rows[7]["prompt"] = alternative
    elif change == "row_source":
        rows[0]["sources"] = [{**alternative, "target_path": "source.md"}]
    elif change == "seed_within_rung":
        rows[1]["seed"] = alternative
    elif change == "identical_rungs":
        for row in rows[4:]:
            row["seed"] = copy.deepcopy(rows[0]["seed"])
    elif change == "identical_rung_content":
        (tmp_path / "inputs" / "seed-copy").write_bytes(
            (tmp_path / "inputs" / "seed-transfer_hint").read_bytes()
        )
        for row in rows[4:]:
            row["seed"] = {"path": "seed-copy", "sha256": rows[0]["seed"]["sha256"]}
    elif change == "seed_as_prompt":
        for row in rows:
            row["prompt"] = copy.deepcopy(rows[0]["seed"])
    else:
        rows[7]["intervention"] = "third_rung"
    with pytest.raises(ValueError):
        freeze(tmp_path / "out", m)


def test_rung_names_belong_to_the_experiment(tmp_path):
    """A structure ladder names its rungs prose/fielded, not the old hardcoded pair."""
    m = ladder(tmp_path, rungs=("prose", "fielded"))
    calls: list[str] = []
    out = tmp_path / "out"
    freeze(out, m)
    assert (
        execute(out, m, session_runner=fake_runner(calls), identity_check=lambda: None)[
            "new_sessions"
        ]
        == 8
    )
    assert set(calls) == {f"{rung}-{n}" for rung in ("prose", "fielded") for n in (1, 2, 3, 4)}


def test_single_rung_is_not_a_ladder(tmp_path):
    m = ladder(tmp_path)
    for index, row in enumerate(m["schedule"]):
        row["intervention"] = "prose"
        row["replicate"] = index % 4 + 1
    with pytest.raises(ValueError, match="exactly two named rungs"):
        validate_manifest(m)


def test_three_rungs_is_not_a_ladder(tmp_path):
    m = ladder(tmp_path)
    m["schedule"][7]["intervention"] = "third"
    with pytest.raises(ValueError, match="exactly two named rungs"):
        validate_manifest(m)


@pytest.mark.parametrize("name", ["", "Prose", "_prose", "pro se", "prose-1", None, 7])
def test_unsafe_rung_name_rejected(tmp_path, name):
    m = ladder(tmp_path)
    for row in m["schedule"][:4]:
        row["intervention"] = name
    with pytest.raises(ValueError, match="exactly two named rungs"):
        validate_manifest(m)
