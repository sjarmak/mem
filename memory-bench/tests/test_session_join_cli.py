"""Tests for the pure helpers of the mem-75t.9 driver scripts (loaded from file
path, the arm_analysis test idiom). The full-corpus walk and the dolt client
call are real-infra plumbing exercised by the production run, not here; the
agreement / population-selection arithmetic is what these pin."""

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_session_join = _load("build_session_join")
compute_cross_session = _load("compute_cross_session")


def _row(
    session_id: str,
    work_id: str,
    transcript_path: str,
    strength: str = "strong",
    in_store: bool = True,
    n_strong: int = 1,
) -> dict:
    return {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "work_id": work_id,
        "strength": strength,
        "t_first": None,
        "t_last": None,
        "session_start": None,
        "session_end": None,
        "n_strong": n_strong,
        "n_weak": 0,
        "in_store": in_store,
    }


# --- calibrate ----------------------------------------------------------------


def test_calibrate_counts_agreement(tmp_path: Path) -> None:
    t1 = tmp_path / "s1.jsonl"
    t2 = tmp_path / "s2.jsonl"
    t1.write_text("{}", encoding="utf-8")
    t2.write_text("{}", encoding="utf-8")
    rows = [
        _row("s1", "mem-1", str(t1), strength="strong"),
        _row("s2", "mem-2", str(t2), strength="weak"),
    ]
    store_links = [
        ("mem-1", str(t1)),  # found, strong
        ("mem-2", str(t2)),  # found, weak
        ("mem-3", str(t2)),  # scannable but not found
        ("mem-4", str(tmp_path / "gone.jsonl")),  # transcript missing on disk
    ]
    cal = build_session_join.calibrate(rows, store_links)
    assert cal["store_links"] == 4
    assert cal["transcript_missing_on_disk"] == 1
    assert cal["scannable"] == 3
    assert cal["found_any"] == 2
    assert cal["found_strong"] == 1
    assert cal["agreement_any"] == 2 / 3
    assert cal["misses_sample"] == [{"work_id": "mem-3", "trace_path": str(t2)}]


# --- cross_validate -------------------------------------------------------------


def test_cross_validate_counts() -> None:
    rows = [
        _row("s1", "mem-1", "/t/s1.jsonl"),
        _row("s2", "mem-1", "/t/s2.jsonl"),
        _row("s3", "mem-2", "/t/s3.jsonl"),
        _row("s4", "mem-9", "/t/s4.jsonl", in_store=False),  # filtered out
        _row("s5", "mem-3", "/t/s5.jsonl", strength="weak"),  # weak: filtered out
    ]
    dolt = {"mem-1": ["gc-100", "gc-200"], "mem-2": ["gc-100", "gc-300"], "mem-7": ["gc-1"]}
    val = build_session_join.cross_validate(rows, dolt)
    assert val["beads_in_both"] == 2  # mem-1, mem-2
    assert val["beads_content_only"] == 0
    assert val["beads_dolt_only"] == 1  # mem-7
    assert val["exact_count_match"] == 1  # mem-1: 2 == 2
    assert val["content_ge_dolt_rate"] == 0.5
    assert val["mean_count_diff_content_minus_dolt"] == -0.5  # (0 + -1) / 2


# --- select_population ----------------------------------------------------------


def test_select_population_strong_in_store_min_sessions() -> None:
    rows = [
        _row("s1", "mem-1", "/t/s1.jsonl"),
        _row("s2", "mem-1", "/t/s2.jsonl"),
        _row("s3", "mem-2", "/t/s3.jsonl"),  # single session: excluded
        _row("s4", "mem-3", "/t/s4.jsonl"),
        _row("s5", "mem-3", "/t/s5.jsonl", strength="weak"),  # weak: not counted
        _row("s6", "mem-4", "/t/s6.jsonl", in_store=False),
        _row("s7", "mem-4", "/t/s7.jsonl", in_store=False),
    ]
    population = compute_cross_session.select_population(rows, min_sessions=2)
    assert set(population) == {"mem-1"}
    assert sorted(r["session_id"] for r in population["mem-1"]) == ["s1", "s2"]


def test_select_population_dedupes_session_ids_by_strong_mentions() -> None:
    rows = [
        _row("s1", "mem-1", "/t/a.jsonl", n_strong=1),
        _row("s1", "mem-1", "/t/b.jsonl", n_strong=5),
        _row("s2", "mem-1", "/t/c.jsonl"),
    ]
    population = compute_cross_session.select_population(rows, min_sessions=2)
    (s1_row,) = [r for r in population["mem-1"] if r["session_id"] == "s1"]
    assert s1_row["transcript_path"] == "/t/b.jsonl"
