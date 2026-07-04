"""Real external anchor: adaptation determinism, manifest integrity, induction honesty.

mem-mmuu repair: the prior checked-in anchor was 2 hand-written rows whose
latent_rule appeared verbatim in every episode — recovering the rule required
zero induction. These tests pin the replacement contract:

* the anchor is adapted from a REAL external corpus (BIG-bench ``list_functions``)
  frozen in-repo at a pinned upstream commit, at meaningful N;
* the adaptation is deterministic and manifest-verified (raw tamper, fixture
  tamper, and adapter drift all fail closed);
* the latent rule NEVER appears verbatim (whitespace-normalized) in any episode
  or the probe — the loader rejects such rows AND ``verify_anchor`` re-checks the
  invariant, so the toy-anchor failure mode cannot be reintroduced.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from membench.generators.anchor_adaptation import (
    MIN_ANCHOR_TASKS,
    adapt_raw_dir,
    adapt_raw_task,
    build_anchor_manifest,
    read_anchor_manifest,
    render_anchor_jsonl,
    verify_anchor,
    write_anchor_manifest,
)
from membench.generators.external_anchor import load_external_schema_sequences

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ANCHOR_DIR = FIXTURES / "external_anchor"
ANCHOR = FIXTURES / "sequences" / "list_functions_schema_anchor.jsonl"


def _anchor_rows() -> list[dict]:
    return [json.loads(line) for line in ANCHOR.read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------- #
# Frozen fixture: meaningful N, real source, induction-bearing rows
# --------------------------------------------------------------------------- #
def test_anchor_has_meaningful_n():
    rows = _anchor_rows()
    assert len(rows) >= MIN_ANCHOR_TASKS >= 24, "the anchor must not be a toy sample"
    for row in rows:
        assert len(row["episodes"]) >= 3
        assert row["latent_rule"].strip()


def test_latent_rule_never_appears_verbatim_in_any_episode():
    for row in _anchor_rows():
        rule = row["latent_rule"].strip().lower()
        for episode in row["episodes"]:
            assert rule not in episode.lower(), (
                f"task {row['task_id']}: latent_rule leaks verbatim into an episode — "
                "recovering it would require no induction"
            )


def test_anchor_loads_as_sequences_with_the_rule_as_oracle():
    seqs = load_external_schema_sequences(ANCHOR)
    assert len(seqs) >= MIN_ANCHOR_TASKS
    for s in seqs:
        assert s.latent_rule
        writes = [m for st in s.steps for m in st.expected_memory_writes]
        assert set(s.steps[-1].expected_memory_reads) == set(writes)


# --------------------------------------------------------------------------- #
# Loader fail-closed: the toy-anchor failure mode cannot come back
# --------------------------------------------------------------------------- #
def test_loader_rejects_a_row_whose_rule_leaks_into_an_episode(tmp_path):
    leaky = {
        "task_id": "leaky",
        "source": "test",
        "latent_rule": "responses wrap data in an envelope",
        "episodes": [
            "the users endpoint: responses wrap data in an envelope with a status field",
            "the orders endpoint: responses wrap data in an envelope with a status field",
        ],
        "probe": "what convention governs the endpoints above?",
    }
    path = tmp_path / "leaky.jsonl"
    path.write_text(json.dumps(leaky) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="verbatim"):
        load_external_schema_sequences(path)


def test_loader_rejects_a_rule_leaking_into_the_probe(tmp_path):
    leaky = {
        "task_id": "leaky-probe",
        "source": "test",
        "latent_rule": "responses wrap data in an envelope",
        "episodes": [
            "the users endpoint returns a status field",
            "the orders endpoint returns a status field",
        ],
        "probe": "do responses wrap data in an envelope across the endpoints above?",
    }
    path = tmp_path / "leaky-probe.jsonl"
    path.write_text(json.dumps(leaky) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="verbatim"):
        load_external_schema_sequences(path)


def test_loader_rejects_a_whitespace_variant_verbatim_leak(tmp_path):
    leaky = {
        "task_id": "leaky-ws",
        "source": "test",
        "latent_rule": "responses wrap data in an envelope",
        "episodes": [
            "the users endpoint: responses wrap  data in an\nenvelope with a status field",
            "the orders endpoint returns a status field",
        ],
        "probe": "what convention governs the endpoints above?",
    }
    path = tmp_path / "leaky-ws.jsonl"
    path.write_text(json.dumps(leaky) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="verbatim"):
        load_external_schema_sequences(path)


def test_loader_rejects_a_blank_latent_rule(tmp_path):
    # A blank rule makes the leak-check needle empty, so the verbatim check would
    # silently short-circuit and admit a probe that hands the reader the answer —
    # the exact recall-not-induction failure mode the check exists to prevent.
    leaky = {
        "task_id": "blank-rule",
        "source": "test",
        "latent_rule": "   ",
        "episodes": [
            "the users endpoint returns a status field",
            "the orders endpoint returns a status field",
        ],
        "probe": "the answer is: responses wrap data in an envelope",
    }
    path = tmp_path / "blank-rule.jsonl"
    path.write_text(json.dumps(leaky) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="blank"):
        load_external_schema_sequences(path)


# --------------------------------------------------------------------------- #
# Adaptation: deterministic, mechanical rule extraction
# --------------------------------------------------------------------------- #
def _raw_task(
    description: str = 'Infer stuff. The target function is "reverse the list".',
    name: str = "c999",
) -> dict:
    return {
        "canary": "canary GUID test-guid",
        "name": name,
        "description": description,
        "examples": [
            {"input": "[1, 2, 3]", "target": "[3, 2, 1]"},
            {"input": "[4, 5]", "target": "[5, 4]"},
            {"input": "[6]", "target": "[6]"},
        ],
    }


def test_adapt_raw_task_extracts_the_rule_and_formats_episodes():
    row = adapt_raw_task(_raw_task())
    assert row["task_id"] == "list-functions-c999"
    assert row["latent_rule"] == "reverse the list"
    assert row["episodes"][0] == "example 0: input [1, 2, 3] -> output [3, 2, 1]"
    assert len(row["episodes"]) == 3
    assert "function" in row["probe"]


def test_adapt_raw_task_fails_closed_without_a_rule():
    with pytest.raises(ValueError, match="target function"):
        adapt_raw_task(_raw_task(description="No rule sentence here."))


def test_adapt_raw_task_captures_only_the_first_quoted_phrase():
    row = adapt_raw_task(
        _raw_task(description='The target function is "reverse the list" not "sort the list".')
    )
    assert row["latent_rule"] == "reverse the list", (
        "a greedy match would over-capture to the last quote and silently freeze a "
        "wrong latent_rule into the fixture"
    )


def test_adapt_raw_task_fails_closed_on_an_empty_rule():
    with pytest.raises(ValueError, match="empty"):
        adapt_raw_task(_raw_task(description='The target function is "".'))


def test_adaptation_is_deterministic_and_matches_the_manifest():
    first = render_anchor_jsonl(adapt_raw_dir(ANCHOR_DIR / "raw"))
    second = render_anchor_jsonl(adapt_raw_dir(ANCHOR_DIR / "raw"))
    assert first == second
    assert first == ANCHOR.read_text(encoding="utf-8"), (
        "re-running the adaptation over the frozen raw corpus must reproduce the "
        "checked-in fixture byte-identically"
    )


# --------------------------------------------------------------------------- #
# Manifest: provenance recorded, tampering fails closed
# --------------------------------------------------------------------------- #
def test_manifest_verifies_the_frozen_anchor():
    result = verify_anchor(ANCHOR_DIR, ANCHOR)
    assert result.ok, result.mismatches


def test_manifest_records_provenance():
    manifest = read_anchor_manifest(ANCHOR_DIR)
    assert manifest.source == "bigbench-list-functions"
    assert len(manifest.source_commit) == 40
    assert manifest.canary_guid
    assert manifest.n_tasks == len(manifest.raw_sha256) == len(_anchor_rows())


def _copy_fixture(tmp_path: Path) -> tuple[Path, Path]:
    anchor_dir = tmp_path / "external_anchor"
    shutil.copytree(ANCHOR_DIR, anchor_dir)
    anchor = tmp_path / ANCHOR.name
    shutil.copy(ANCHOR, anchor)
    return anchor_dir, anchor


def test_verify_detects_a_tampered_raw_file(tmp_path):
    anchor_dir, anchor = _copy_fixture(tmp_path)
    victim = sorted((anchor_dir / "raw").glob("*.json"))[0]
    raw = json.loads(victim.read_text(encoding="utf-8"))
    raw["examples"][0]["target"] = "[999]"
    victim.write_text(json.dumps(raw), encoding="utf-8")
    result = verify_anchor(anchor_dir, anchor)
    assert not result.ok
    assert any(victim.name in m for m in result.mismatches)


def test_verify_detects_an_edited_anchor_fixture(tmp_path):
    anchor_dir, anchor = _copy_fixture(tmp_path)
    with anchor.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"task_id": "extra"}) + "\n")
    result = verify_anchor(anchor_dir, anchor)
    assert not result.ok
    assert any("anchor" in m for m in result.mismatches)


def _build_tmp_anchor(tmp_path: Path, raw_tasks: list[dict]) -> tuple[Path, Path]:
    """Freeze a corpus of raw tasks exactly the way the adapt CLI does."""
    anchor_dir = tmp_path / "external_anchor"
    (anchor_dir / "raw").mkdir(parents=True)
    for task in raw_tasks:
        (anchor_dir / "raw" / f"{task['name']}.json").write_text(json.dumps(task), encoding="utf-8")
    anchor = tmp_path / "anchor.jsonl"
    anchor.write_text(render_anchor_jsonl(adapt_raw_dir(anchor_dir / "raw")), encoding="utf-8")
    write_anchor_manifest(
        build_anchor_manifest(anchor_dir, anchor, source_commit="0" * 40), anchor_dir
    )
    return anchor_dir, anchor


def test_verify_detects_a_leaky_rendered_anchor(tmp_path):
    # The rule text reproduces an episode line verbatim, so the rendered anchor
    # would measure recall — verify_anchor itself must fail, not just the loader.
    leaky_raw = _raw_task(
        description='The target function is "input [1, 2, 3] -> output [3, 2, 1]".'
    )
    anchor_dir, anchor = _build_tmp_anchor(tmp_path, [leaky_raw])
    result = verify_anchor(anchor_dir, anchor)
    assert not result.ok
    assert any("induction-honesty" in m for m in result.mismatches)


def test_verify_detects_a_toy_sized_corpus(tmp_path):
    tasks = [_raw_task(name=f"c{i:03d}") for i in range(2)]
    anchor_dir, anchor = _build_tmp_anchor(tmp_path, tasks)
    result = verify_anchor(anchor_dir, anchor)
    assert not result.ok
    assert any("floor" in m for m in result.mismatches)


def test_verify_detects_manifest_metadata_drift(tmp_path):
    anchor_dir, anchor = _copy_fixture(tmp_path)
    manifest_path = anchor_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["n_tasks"] = manifest["n_tasks"] - 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = verify_anchor(anchor_dir, anchor)
    assert not result.ok
    assert any("n_tasks" in m for m in result.mismatches)
