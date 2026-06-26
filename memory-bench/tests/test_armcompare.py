"""Unit tests for `membench.armcompare` (mem-0ut arm analysis, warm vs cold).

All streams and records are synthetic -- no Docker, no network, no store. Each
of the five metric axes is exercised individually, then end-to-end through
`extract_bead_metrics`, then the unpaired arm summary arithmetic.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from membench.armcompare import (
    ARMS,
    BeadMetrics,
    distractor_read_rate,
    extract_bead_metrics,
    fork_boundary_for,
    iterations_to_green,
    load_arm_assignment,
    load_brain_built_at,
    load_scope_files,
    summarize_arms,
    tool_calls_before_first_edit,
    trim_inherited_events,
    wall_clock_seconds,
)

# --- synthetic stream builders ---------------------------------------------------


def _assistant(blocks: list[dict], *, ts: str | None = None, usage: dict | None = None) -> str:
    event: dict = {"type": "assistant", "message": {"content": blocks}}
    if usage is not None:
        event["message"]["usage"] = usage
    if ts is not None:
        event["timestamp"] = ts
    return json.dumps(event)


def _tool_use(name: str, **args: object) -> dict:
    return {"type": "tool_use", "name": name, "input": args}


def _stream(*lines: str) -> str:
    return "\n".join(lines) + "\n"


READ_A = _tool_use("Read", file_path="/work/repo/src/a.ts")
READ_B = _tool_use("Read", file_path="/work/repo/src/b.ts")
READ_OUT = _tool_use("Read", file_path="/work/repo/docs/notes.md")
BASH = _tool_use("Bash", command="ls")
EDIT_A = _tool_use("Edit", file_path="/work/repo/src/a.ts", old_string="x", new_string="y")
WRITE_C = _tool_use("Write", file_path="/work/repo/src/c.ts", content="z")


# --- tool_calls_before_first_edit -------------------------------------------------


def test_tool_calls_before_first_edit_counts_prior_tool_use() -> None:
    stream = _stream(_assistant([READ_A, BASH]), _assistant([READ_B]), _assistant([EDIT_A, BASH]))
    assert tool_calls_before_first_edit(stream) == 3


def test_tool_calls_before_first_edit_zero_when_edit_first() -> None:
    assert tool_calls_before_first_edit(_stream(_assistant([WRITE_C, READ_A]))) == 0


def test_tool_calls_before_first_edit_none_without_edits() -> None:
    stream = _stream(_assistant([READ_A]), _assistant([BASH]))
    assert tool_calls_before_first_edit(stream) is None


def test_tool_calls_before_first_edit_multiedit_counts_as_edit() -> None:
    multi = _tool_use("MultiEdit", file_path="/work/repo/src/a.ts", edits=[])
    assert tool_calls_before_first_edit(_stream(_assistant([READ_A, multi]))) == 1


def test_tool_calls_before_first_edit_skips_non_json_lines() -> None:
    stream = "not json\n" + _assistant([READ_A]) + "\n" + _assistant([EDIT_A])
    assert tool_calls_before_first_edit(stream) == 1


# --- distractor_read_rate ---------------------------------------------------------


def test_distractor_rate_none_without_scope() -> None:
    assert distractor_read_rate(["/work/repo/src/a.ts"], None) is None


def test_distractor_rate_none_when_no_reads() -> None:
    assert distractor_read_rate([], ("src/a.ts",)) is None


def test_distractor_rate_suffix_matches_scope() -> None:
    reads = ["/work/repo/src/a.ts", "/work/repo/src/b.ts", "/work/repo/docs/notes.md"]
    rate = distractor_read_rate(reads, ("src/a.ts", "src/b.ts"))
    assert rate == pytest.approx(1 / 3)


def test_distractor_rate_all_in_scope_is_zero() -> None:
    assert distractor_read_rate(["/r/src/a.ts"], ("src/a.ts",)) == 0.0


def test_distractor_rate_no_partial_component_match() -> None:
    # "xsrc/a.ts" must NOT suffix-match scope file "src/a.ts".
    assert distractor_read_rate(["/r/xsrc/a.ts"], ("src/a.ts",)) == 1.0


def test_distractor_rate_exact_relative_path_matches() -> None:
    assert distractor_read_rate(["src/a.ts"], ("src/a.ts",)) == 0.0


# --- wall_clock_seconds -----------------------------------------------------------


def test_wall_clock_first_to_last_timestamp() -> None:
    stream = _stream(
        json.dumps({"type": "user", "timestamp": "2026-06-07T02:00:00.000Z"}),
        _assistant([READ_A], ts="2026-06-07T02:00:30.500Z"),
        _assistant([BASH], ts="2026-06-07T02:01:00.000Z"),
    )
    assert wall_clock_seconds(stream) == pytest.approx(60.0)


def test_wall_clock_none_without_timestamps() -> None:
    assert wall_clock_seconds(_stream(_assistant([READ_A]))) is None


def test_wall_clock_single_timestamp_is_zero() -> None:
    stream = _stream(_assistant([READ_A], ts="2026-06-07T02:00:00Z"))
    assert wall_clock_seconds(stream) == 0.0


# --- iterations_to_green ----------------------------------------------------------


def _outcome(runner: str, status: str) -> dict:
    return {"runner": runner, "command": f"{runner} ...", "status": status, "errors": []}


def test_iterations_to_green_counts_fail_to_pass_per_runner() -> None:
    outcomes = [
        _outcome("tsc", "fail"),
        _outcome("tsc", "fail"),
        _outcome("tsc", "pass"),  # tsc: 1
        _outcome("vitest", "pass"),  # never failed: 0
        _outcome("tsc", "fail"),
        _outcome("tsc", "pass"),  # tsc: 2
    ]
    assert iterations_to_green(outcomes) == 2


def test_iterations_to_green_runners_are_independent() -> None:
    outcomes = [
        _outcome("tsc", "fail"),
        _outcome("vitest", "fail"),
        _outcome("tsc", "pass"),
        _outcome("vitest", "pass"),
    ]
    assert iterations_to_green(outcomes) == 2


def test_iterations_to_green_zero_for_empty_or_all_pass() -> None:
    assert iterations_to_green([]) == 0
    assert iterations_to_green([_outcome("go", "pass"), _outcome("go", "pass")]) == 0


def test_iterations_to_green_unresolved_fail_not_counted() -> None:
    assert iterations_to_green([_outcome("go", "fail")]) == 0


def test_iterations_to_green_malformed_entry_raises() -> None:
    with pytest.raises(ValueError, match="tool_outcome"):
        iterations_to_green([{"status": "pass"}])  # no runner
    with pytest.raises(ValueError, match="tool_outcome"):
        iterations_to_green(["not a mapping"])  # type: ignore[list-item]


# --- extract_bead_metrics ----------------------------------------------------------


def _record(work_id: str = "rig-abc", outcomes: list[dict] | None = None) -> dict:
    return {
        "work_id": work_id,
        "trace": {"tool_outcomes": outcomes if outcomes is not None else []},
    }


def test_extract_bead_metrics_end_to_end() -> None:
    stream = _stream(
        _assistant(
            [READ_A, READ_OUT],
            ts="2026-06-07T02:00:00Z",
            usage={"input_tokens": 100, "output_tokens": 20},
        ),
        _assistant(
            [EDIT_A],
            ts="2026-06-07T02:02:00Z",
            usage={"input_tokens": 50, "output_tokens": 30},
        ),
    )
    record = _record(outcomes=[_outcome("tsc", "fail"), _outcome("tsc", "pass")])
    metrics = extract_bead_metrics(record, stream, scope_files=("src/a.ts",))

    assert metrics.work_id == "rig-abc"
    assert metrics.tool_calls_before_first_edit == 2
    assert metrics.distractor_read_rate == pytest.approx(0.5)
    assert metrics.files_read == 2
    assert metrics.total_tokens == 200
    assert metrics.wall_clock_seconds == pytest.approx(120.0)
    assert metrics.iterations_to_green == 1
    assert metrics.turns == 2
    assert metrics.tool_calls == 3


def test_extract_bead_metrics_typed_absences() -> None:
    stream = _stream(_assistant([READ_A]))  # no usage, no timestamps, no edits
    metrics = extract_bead_metrics(_record(), stream, scope_files=None)
    assert metrics.tool_calls_before_first_edit is None
    assert metrics.distractor_read_rate is None
    assert metrics.total_tokens is None
    assert metrics.wall_clock_seconds is None
    assert metrics.iterations_to_green == 0


def test_extract_bead_metrics_total_tokens_none_when_one_side_missing() -> None:
    stream = _stream(_assistant([READ_A], usage={"input_tokens": 10}))
    metrics = extract_bead_metrics(_record(), stream)
    assert metrics.total_tokens is None


def test_extract_bead_metrics_missing_work_id_raises() -> None:
    with pytest.raises(ValueError, match="work_id"):
        extract_bead_metrics({"trace": {}}, _stream(_assistant([READ_A])))


def test_bead_metrics_vector_covers_all_axes() -> None:
    metrics = extract_bead_metrics(_record(), _stream(_assistant([READ_A])))
    assert set(metrics.metrics()) == {
        "tool_calls_before_first_edit",
        "distractor_read_rate",
        "files_read",
        "total_tokens",
        "wall_clock_seconds",
        "iterations_to_green",
        "turns",
        "tool_calls",
    }


# --- load_arm_assignment -----------------------------------------------------------


def test_load_arm_assignment_json_mapping(tmp_path: Path) -> None:
    path = tmp_path / "arms.json"
    path.write_text(json.dumps({"a-1": "warm", "a-2": "cold"}), encoding="utf-8")
    assert load_arm_assignment(path) == {"a-1": "warm", "a-2": "cold"}


def test_load_arm_assignment_json_rows(tmp_path: Path) -> None:
    path = tmp_path / "arms.json"
    rows = [{"work_id": "a-1", "arm": "warm"}, {"work_id": "a-2", "arm": "cold"}]
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert load_arm_assignment(path) == {"a-1": "warm", "a-2": "cold"}


def test_load_arm_assignment_csv(tmp_path: Path) -> None:
    path = tmp_path / "arms.csv"
    path.write_text("work_id,arm\na-1,warm\na-2,cold\n", encoding="utf-8")
    assert load_arm_assignment(path) == {"a-1": "warm", "a-2": "cold"}


def test_load_arm_assignment_rejects_unknown_arm(tmp_path: Path) -> None:
    path = tmp_path / "arms.json"
    path.write_text(json.dumps({"a-1": "lukewarm"}), encoding="utf-8")
    with pytest.raises(ValueError, match="lukewarm"):
        load_arm_assignment(path)


def test_load_arm_assignment_rejects_duplicate_work_id(tmp_path: Path) -> None:
    path = tmp_path / "arms.csv"
    path.write_text("work_id,arm\na-1,warm\na-1,cold\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_arm_assignment(path)


def test_load_arm_assignment_rejects_empty(tmp_path: Path) -> None:
    path = tmp_path / "arms.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_arm_assignment(path)


def test_load_arm_assignment_rejects_unknown_suffix(tmp_path: Path) -> None:
    path = tmp_path / "arms.toml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="suffix"):
        load_arm_assignment(path)


# --- load_scope_files ---------------------------------------------------------------


def test_load_scope_files_brains_manifest_shape(tmp_path: Path) -> None:
    manifest = {
        "v": 1,
        "name": "backend",
        "repo": "/r",
        "scope": ["src/**"],
        "sessionId": "s",
        "model": "m",
        "commit": "c",
        "builtAt": "t",
        "fileHashes": {"src/b.ts": "hash2", "src/a.ts": "hash1"},
        "buildTokens": None,
    }
    path = tmp_path / "backend.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_scope_files(path) == ("src/a.ts", "src/b.ts")


def test_load_scope_files_plain_files_shape(tmp_path: Path) -> None:
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({"files": ["src/b.ts", "src/a.ts"]}), encoding="utf-8")
    assert load_scope_files(path) == ("src/a.ts", "src/b.ts")


def test_load_scope_files_rejects_unknown_shape(tmp_path: Path) -> None:
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({"globs": ["src/**"]}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"files|fileHashes"):
        load_scope_files(path)


# --- summarize_arms ------------------------------------------------------------------


def _metrics(work_id: str, **overrides: object) -> BeadMetrics:
    base: dict = {
        "work_id": work_id,
        "tool_calls_before_first_edit": 4,
        "distractor_read_rate": 0.25,
        "files_read": 4,
        "total_tokens": 1000,
        "wall_clock_seconds": 60.0,
        "iterations_to_green": 1,
        "turns": 10,
        "tool_calls": 8,
    }
    base.update(overrides)
    return BeadMetrics(**base)


def test_summarize_arms_unpaired_stats_and_deltas() -> None:
    summary = summarize_arms(
        {
            "warm": [_metrics("w-1", total_tokens=1000), _metrics("w-2", total_tokens=2000)],
            "cold": [_metrics("c-1", total_tokens=4000)],
        }
    )
    assert summary["design"] == "unpaired"
    assert summary["n_per_arm"] == {"warm": 2, "cold": 1}
    tokens = summary["arms"]["warm"]["total_tokens"]
    assert tokens["mean"] == 1500.0
    assert tokens["median"] == 1500.0
    assert tokens["n"] == 2
    delta = summary["deltas"]["total_tokens"]
    assert delta["mean_delta"] == 1500.0 - 4000.0
    assert delta["median_delta"] == 1500.0 - 4000.0
    assert delta["n_warm"] == 2
    assert delta["n_cold"] == 1


def test_summarize_arms_omits_delta_when_one_side_has_no_values() -> None:
    summary = summarize_arms(
        {
            "warm": [_metrics("w-1", total_tokens=None)],
            "cold": [_metrics("c-1", total_tokens=500)],
        }
    )
    assert "total_tokens" not in summary["deltas"]
    assert summary["arms"]["warm"]["total_tokens"] == {"mean": None, "median": None, "n": 0}


def test_summarize_arms_none_values_excluded_from_stats() -> None:
    summary = summarize_arms(
        {
            "warm": [_metrics("w-1", wall_clock_seconds=None), _metrics("w-2")],
            "cold": [_metrics("c-1")],
        }
    )
    assert summary["arms"]["warm"]["wall_clock_seconds"]["n"] == 1


def test_summarize_arms_tolerates_empty_arm() -> None:
    summary = summarize_arms({"warm": [_metrics("w-1")], "cold": []})
    assert summary["n_per_arm"] == {"warm": 1, "cold": 0}
    assert summary["deltas"] == {}


def test_summarize_arms_rejects_unknown_arm() -> None:
    with pytest.raises(ValueError, match="tepid"):
        summarize_arms({"tepid": [_metrics("x-1")]})


def test_summarize_arms_rejects_no_results() -> None:
    with pytest.raises(ValueError, match="no per-bead results"):
        summarize_arms({"warm": [], "cold": []})


def test_arms_constant() -> None:
    assert ARMS == ("warm", "cold")


# --- fork-aware measurement (mem-0ut.1) -------------------------------------------
#
# A `--fork-session` warm transcript PHYSICALLY contains the brain session's
# inherited build events. Measured raw, the warm arm's headline metric INVERTS
# (the pilot saw tool_calls_before_first_edit 4.0 vs cold 3.5) because the brain
# prefix's reads are counted as the fork's own pre-edit work. The fix trims events
# with ts <= brain.builtAt before computing the stream-derived axes.

BUILT_AT = datetime.fromisoformat("2026-06-26T02:48:24.798+00:00")

# Brain-build prefix: three reads, all stamped at/▽before builtAt (inherited).
_BRAIN_PREFIX = [
    _assistant(
        [_tool_use("Read", file_path="/brain/a.ts")],
        ts="2026-06-26T02:48:00.000Z",
        usage={"input_tokens": 1000, "output_tokens": 400},
    ),
    _assistant(
        [_tool_use("Read", file_path="/brain/b.ts")],
        ts="2026-06-26T02:48:10.000Z",
        usage={"input_tokens": 1000, "output_tokens": 400},
    ),
    _assistant(
        [_tool_use("Read", file_path="/brain/c.ts")],
        ts="2026-06-26T02:48:20.000Z",
        usage={"input_tokens": 1000, "output_tokens": 400},
    ),
]
# Fork's own work: one read, then an edit (all strictly after builtAt).
_FORK_WORK = [
    _assistant(
        [_tool_use("Read", file_path="/fork/x.ts")],
        ts="2026-06-26T02:50:00.000Z",
        usage={"input_tokens": 200, "output_tokens": 100},
    ),
    _assistant(
        [EDIT_A], ts="2026-06-26T02:50:10.000Z", usage={"input_tokens": 200, "output_tokens": 100}
    ),
]


def _warm_stream() -> str:
    return _stream(*_BRAIN_PREFIX, *_FORK_WORK)


def _warm_record(work_id: str = "t1-warm") -> dict:
    return {"work_id": work_id, "trace": {"jsonl_path": "/x.jsonl", "tool_outcomes": []}}


def test_raw_warm_metric_inverts_brain_prefix_counted_as_forks_own() -> None:
    # The bug this bead fixes: untrimmed, the 3 brain reads + the 1 fork read are
    # all counted before the fork's first edit -> 4 (the inverting headline).
    raw = extract_bead_metrics(_warm_record(), _warm_stream())
    assert raw.tool_calls_before_first_edit == 4
    assert raw.files_read == 4


def test_fork_boundary_trims_inherited_prefix_from_all_stream_axes() -> None:
    # Fork-aware: only the fork's own read precedes its edit -> 1 (the true value
    # the pilot recovered). files_read and tokens likewise drop the brain prefix.
    trimmed = extract_bead_metrics(_warm_record(), _warm_stream(), fork_boundary=BUILT_AT)
    assert trimmed.tool_calls_before_first_edit == 1
    assert trimmed.files_read == 1
    # tokens: only the two fork events (2*(200+100)=600), brain prefix excluded.
    assert trimmed.total_tokens == 600


def test_fork_trim_recovers_correct_distractor_rate() -> None:
    # /fork/x.ts is out of a scope listing only brain files -> 1/1 distractor on
    # the fork's own read; raw would dilute with 3 in-scope brain reads (1/4).
    scope = ["fork/y.ts"]
    raw = extract_bead_metrics(_warm_record(), _warm_stream(), scope, fork_boundary=None)
    trimmed = extract_bead_metrics(_warm_record(), _warm_stream(), scope, fork_boundary=BUILT_AT)
    assert raw.distractor_read_rate == pytest.approx(1.0)  # all 4 reads out of scope here
    assert trimmed.distractor_read_rate == pytest.approx(1.0)
    assert raw.files_read == 4 and trimmed.files_read == 1


def test_trim_inherited_events_drops_only_pre_boundary_timestamped_lines() -> None:
    out = trim_inherited_events(_warm_stream(), BUILT_AT)
    kept = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert len(kept) == 2  # only the two fork events survive
    # non-JSON and timestamp-less lines are KEPT (trim only excludes provable inheritance)
    mixed = "garbage\n" + _assistant([READ_A]) + "\n" + _BRAIN_PREFIX[0]
    out2 = trim_inherited_events(mixed, BUILT_AT)
    assert "garbage" in out2  # non-JSON kept
    assert "/brain/a.ts" not in out2  # the pre-boundary brain read dropped


def test_load_brain_built_at_reads_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "brain.json"
    manifest.write_text(
        json.dumps(
            {"name": "b", "builtAt": "2026-06-26T02:48:24.798Z", "fileHashes": {"src/a.ts": "h"}}
        )
    )
    assert load_brain_built_at(manifest) == BUILT_AT
    no_built = tmp_path / "nb.json"
    no_built.write_text(json.dumps({"fileHashes": {"src/a.ts": "h"}}))
    assert load_brain_built_at(no_built) is None


def test_fork_boundary_for_only_trims_warm_with_manifest_or_stamp() -> None:
    rec = _warm_record()
    # cold is never trimmed
    assert fork_boundary_for(rec, "cold", BUILT_AT) is None
    # warm falls back to the manifest builtAt
    assert fork_boundary_for(rec, "warm", BUILT_AT) == BUILT_AT
    # a per-record stamped fork_ts (option 2) takes precedence over the manifest
    stamped = {
        "work_id": "t1-warm",
        "fork": {"parent_sid": "s", "fork_ts": "2026-06-26T03:00:00.000Z"},
    }
    assert fork_boundary_for(stamped, "warm", BUILT_AT) == datetime.fromisoformat(
        "2026-06-26T03:00:00.000+00:00"
    )
    # warm with neither a stamp nor a manifest builtAt -> None (caller must warn)
    assert fork_boundary_for(rec, "warm", None) is None
