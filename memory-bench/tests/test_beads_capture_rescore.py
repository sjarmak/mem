from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from membench.runner import beads_capture_fire
from membench.runner.beads_arm_grid import ArmCell
from membench.runner.beads_arm_plan import ArmGridKey, cell_key, cell_row
from membench.runner.beads_capture_fire import main
from membench.runner.e1_grid import EXIT_OK, EXIT_REFUSED, corpus_fingerprint
from membench.runner.memory_arm import ARM_BEADS
from membench.runner.receipt_engagement import (
    RULE_ACKNOWLEDGED_WRITE,
    RULE_NO_RECEIPTS,
    RULE_NOT_ACKNOWLEDGED,
)
from membench.runner.toolreq_realagent import VARIANT_NECESSARY, ToolReqRealAgentTask
from tests.receipt_helpers import accepted_write, receipt_rows, receipt_text
from tests.test_beads_capture import BD_BUILD, a_capture_cell, corpus

PUBLISHED: dict[str, int] = {
    "beads-capture-confirm-DRAFT/w0s0-f45b249.json": 1,
    "beads-capture-confirm-DRAFT/w1s1-ac57bda.json": 40,
    "beads-capture-models-20260926/claude-opus-5-5-w0s0-f45b249.json": 48,
    "beads-capture-models-20260926/claude-opus-5-5-w1s1-ac57bda.json": 47,
    "beads-capture-models-20260926/claude-haiku-4-5-20251001-w0s0-f45b249.json": 16,
    "beads-capture-models-20260926/claude-haiku-4-5-20251001-w1s1-ac57bda.json": 48,
}
RESULTS_ENV = "MEMBENCH_CAPTURE_RESULTS"
JEV32 = Path("fixtures/worlds-tool-jev32")
CELLS_PER_ARTIFACT = 48
REPEATS = 6


def _stem(key: ArmGridKey) -> str:
    arm_name, variant, work_id, repeat = key
    return f"{arm_name}-{variant}-{work_id}-{repeat}"


def _cells_dir(out: Path) -> Path:
    return out.with_name(f"{out.name}.cells")


def _receipts_path(out: Path, cell: ArmCell) -> Path:
    return _cells_dir(out) / f"{_stem(cell_key(cell))}.establish.receipts.jsonl"


def _write_artifact(
    out: Path,
    tasks: Sequence[ToolReqRealAgentTask],
    *,
    engaged_count: int,
    fingerprint: str | None = None,
) -> list[ArmCell]:
    cells_dir = _cells_dir(out)
    cells_dir.mkdir(parents=True)
    work_ids = sorted({task.work_id for task in tasks})[: CELLS_PER_ARTIFACT // REPEATS]
    cells: list[ArmCell] = []
    for work_id in work_ids:
        for repeat in range(REPEATS):
            cell = a_capture_cell(
                ARM_BEADS, work_id, repeat=repeat, engaged=len(cells) < engaged_count
            )
            key = cell_key(cell)
            (cells_dir / f"{_stem(key)}.json").write_text(
                json.dumps({"key": list(key)} | cell_row(cell), indent=2), encoding="utf-8"
            )
            cells.append(cell)
    out.write_text(
        json.dumps(
            {
                "corpus_fingerprint": fingerprint or corpus_fingerprint(tasks),
                "cells": [cell_row(cell) for cell in cells],
            }
        ),
        encoding="utf-8",
    )
    return cells


def _token_of(tasks: Sequence[ToolReqRealAgentTask], work_id: str) -> str:
    task = next(one for one in tasks if one.work_id == work_id and one.variant == VARIANT_NECESSARY)
    return task.current_opaque_values[0]


def _rescore(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main(["rescore", *argv]) == EXIT_OK
    report: dict[str, Any] = json.loads(capsys.readouterr().out)
    return report


def test_rescore_reproduces_the_recorded_counts_when_no_receipts_were_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = corpus(8)
    monkeypatch.setattr(beads_capture_fire, "load_twin_corpus", lambda _dir: (None, tasks))
    outs: list[Path] = []
    for index, count in enumerate(PUBLISHED.values()):
        out = tmp_path / f"turn-{index}.json"
        _write_artifact(out, tasks, engaged_count=count)
        outs.append(out)

    report = _rescore(["--out", *map(str, outs)], capsys)

    assert [one["out"] for one in report["artifacts"]] == [str(out) for out in outs]
    assert [
        (one["cells"], one["engaged_as_recorded"], one["engaged_rescored"])
        for one in report["artifacts"]
    ] == [(CELLS_PER_ARTIFACT, count, count) for count in PUBLISHED.values()]
    assert all(one["with_receipts"] == 0 and one["changed"] == 0 for one in report["artifacts"])
    first = report["artifacts"][0]["per_cell"][0]
    assert first["receipts"] is False
    assert first["engaged_by_receipts"] == "unknown"
    assert first["rule"] is None


def test_rescore_applies_the_receipt_rule_where_receipts_exist_and_reports_the_delta(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = corpus(8)
    monkeypatch.setattr(beads_capture_fire, "load_twin_corpus", lambda _dir: (None, tasks))
    out = tmp_path / "turn.json"
    cells = _write_artifact(out, tasks, engaged_count=2)
    recorded_true_argv_only, recorded_true_unknown, recorded_false_written, silent = cells[:4]

    token = _token_of(tasks, recorded_true_argv_only.work_id)
    _receipts_path(out, recorded_true_argv_only).write_text(
        receipt_text(receipt_rows(["remember", f"the current value is {token}"])),
        encoding="utf-8",
    )
    token = _token_of(tasks, recorded_false_written.work_id)
    _receipts_path(out, recorded_false_written).write_text(
        receipt_text(accepted_write(f"the current value is {token}")), encoding="utf-8"
    )
    _receipts_path(out, silent).write_text("", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in _cells_dir(out).iterdir()}

    report = _rescore(["--out", str(out)], capsys)

    artifact = report["artifacts"][0]
    assert artifact["engaged_as_recorded"] == 2
    assert artifact["with_receipts"] == 3
    assert artifact["engaged_rescored"] == 2
    assert artifact["changed"] == 2
    by_key = {tuple(one["key"]): one for one in artifact["per_cell"]}
    assert by_key[cell_key(recorded_true_argv_only)] == {
        "key": list(cell_key(recorded_true_argv_only)),
        "engaged_as_recorded": True,
        "receipts": True,
        "engaged_by_receipts": False,
        "rule": RULE_NOT_ACKNOWLEDGED,
        "row_index": 1,
        "engaged": False,
        "changed": True,
    }
    assert by_key[cell_key(recorded_true_unknown)]["engaged_by_receipts"] == "unknown"
    assert by_key[cell_key(recorded_true_unknown)]["engaged"] is True
    assert by_key[cell_key(recorded_true_unknown)]["changed"] is False
    written = by_key[cell_key(recorded_false_written)]
    assert (written["engaged"], written["rule"], written["row_index"], written["changed"]) == (
        True,
        RULE_ACKNOWLEDGED_WRITE,
        1,
        True,
    )
    assert by_key[cell_key(silent)]["rule"] == RULE_NO_RECEIPTS
    assert by_key[cell_key(silent)]["engaged"] is False
    assert {path.name: path.read_bytes() for path in _cells_dir(out).iterdir()} == before


def test_rescore_refuses_an_artifact_bought_on_another_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = corpus(8)
    monkeypatch.setattr(beads_capture_fire, "load_twin_corpus", lambda _dir: (None, tasks))
    out = tmp_path / "turn.json"
    cells = _write_artifact(out, tasks, engaged_count=1, fingerprint="0" * 16)
    _receipts_path(out, cells[0]).write_text(
        receipt_text(accepted_write("the current value is ZZZ-CUR")), encoding="utf-8"
    )

    assert main(["rescore", "--out", str(out)]) == EXIT_REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "corpus" in captured.err


def test_rescore_refuses_a_cell_the_corpus_does_not_hold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = corpus(8)
    monkeypatch.setattr(beads_capture_fire, "load_twin_corpus", lambda _dir: (None, tasks))
    out = tmp_path / "turn.json"
    _write_artifact(out, tasks, engaged_count=1)
    stray = a_capture_cell(ARM_BEADS, "w-t99")
    key = cell_key(stray)
    (_cells_dir(out) / f"{_stem(key)}.json").write_text(
        json.dumps({"key": list(key)} | cell_row(stray)), encoding="utf-8"
    )

    assert main(["rescore", "--out", str(out)]) == EXIT_REFUSED
    assert "w-t99" in capsys.readouterr().err


def test_rescore_refuses_a_duplicated_cell_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = corpus(8)
    monkeypatch.setattr(beads_capture_fire, "load_twin_corpus", lambda _dir: (None, tasks))
    out = tmp_path / "turn.json"
    cells = _write_artifact(out, tasks, engaged_count=1)
    first = _cells_dir(out) / f"{_stem(cell_key(cells[0]))}.json"
    first.with_name(f"{first.stem}.attempt1.json").write_bytes(first.read_bytes())

    assert main(["rescore", "--out", str(out)]) == EXIT_REFUSED
    assert "attempt1" in capsys.readouterr().err


def test_the_fire_keeps_the_receipts_beside_the_cell_and_never_over_a_prior_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = corpus(12)
    monkeypatch.setattr(beads_capture_fire, "load_twin_corpus", lambda _dir: (None, tasks))
    monkeypatch.setattr(beads_capture_fire, "resolve_cli_version", lambda: "2.1.278")
    monkeypatch.setattr(beads_capture_fire, "_bd_build_of", lambda _args, runner: BD_BUILD)
    text = receipt_text(accepted_write("the current value is ZZZ-CUR"))
    written = a_capture_cell(ARM_BEADS, "w-t0", engaged=True)
    silent = a_capture_cell(ARM_BEADS, "w-t0", repeat=1)

    def launch(_tasks: object, _keys: object, **kwargs: Any) -> list[ArmCell]:
        kwargs["on_receipts"](cell_key(written), "establish", text)
        kwargs["on_receipts"](cell_key(written), "establish", text)
        kwargs["on_cell"](written)
        kwargs["on_receipts"](cell_key(silent), "establish", "")
        kwargs["on_cell"](silent)
        return [written, silent]

    monkeypatch.setattr(beads_capture_fire, "run_grid", launch)
    out = tmp_path / "capture.json"
    assert (
        main(
            [
                "--dry-run",
                "--out",
                str(out),
            ]
        )
        == EXIT_OK
    )
    cells_dir = _cells_dir(out)
    assert (cells_dir / "beads-necessary-w-t0-0.establish.receipts.jsonl").read_text() == text
    assert (
        cells_dir / "beads-necessary-w-t0-0.establish.receipts.attempt1.jsonl"
    ).read_text() == text
    assert (cells_dir / "beads-necessary-w-t0-1.establish.receipts.jsonl").read_bytes() == b""


@pytest.mark.skipif(not JEV32.exists(), reason="the registered corpus is not in this checkout")
def test_rescore_over_the_published_result_sets_reproduces_the_recorded_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = os.environ.get(RESULTS_ENV)
    if not root or not all((Path(root) / relative).exists() for relative in PUBLISHED):
        pytest.skip(f"set {RESULTS_ENV} to a results root holding both capture result sets")
    outs = [str(Path(root) / relative) for relative in PUBLISHED]

    report = _rescore(["--corpus-dir", str(JEV32), "--out", *outs], capsys)

    assert [
        (one["cells"], one["engaged_as_recorded"], one["engaged_rescored"], one["with_receipts"])
        for one in report["artifacts"]
    ] == [(CELLS_PER_ARTIFACT, count, count, 0) for count in PUBLISHED.values()]
