"""End-to-end golden fixture test: dolt-shaped spine -> `mem build-store
--with-traces` -> membench replay -> ablation grid.

Both halves are well-tested in isolation; this is the one test that drives the
full cross-language path through the REAL `mem` CLI binary:

1. committed dolt-shaped fixture rows + fixture transcript JSONL
   (fixtures/pipeline/), served to the literal `mem build-store --with-traces
   --json` invocation by stub `dolt` / `gc` executables on PATH — so the actual
   ingest chain runs: doltRunner -> readAllRigs -> attachTraceRefs
   (gcSessionResolver) -> parseRecordTrace -> writeRecords -> JSON envelope;
2. `membench replay --arms none,ours` over the resulting store — the `ours` arm
   fires on the planted failure signature through `mem retrieve`, the
   harness-owned LOO guard bounds the corpus (and re-audits the arm's output),
   and the 5-axis report renders;
3. `run_grid` with a StubRunner, scored against the held-out errors the store
   round-tripped — the deterministic avoid axis separates a recurring failure
   (none rung) from a resolved one (ours rung).

The seam this protects: the two halves agreeing on failure signatures
(normalizePath/errorClass/failureSignature), the CLI JSON envelope contract, and
the D6 boundary fields (started/closed/external_ref) surviving the store
round-trip. convoy/supersedes are NOT plantable through this path — the dolt
ingest does not populate links yet (see store/schema.ts).

Skips when node or the TS build is unavailable, like test_ours_integration, so
the hermetic unit suite still runs everywhere.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from membench import cli
from membench.grading import RunTrace
from membench.grading.trace_score import deterministic_term
from membench.harbor.base_rate_spike import (
    load_held_errors_from_store,
    load_record_from_store,
)
from membench.harbor.grid import StubRunner, run_grid
from membench.mem_cli import run_mem_json
from tests.paths import DIST_MAIN, MEM_BIN, REPO, require_mem_cli

FIXTURES = REPO / "fixtures" / "pipeline"
STUB_BIN = FIXTURES / "bin"

# The one failure signature every fixture transcript plants, in the canonical
# TS form `tool:normalizePath(file):line:errorClass` (parse/recurrence.ts).
# Pinning the exact string here is the cross-language parity check: the Python
# side never recomputes it, it must read back byte-identical from the store.
# The same error is pinned in tests/fixtures/extract-errors/polyglot.expected.json
# (TS golden) and fixtures/build_replay_store.mjs — a signature-format change
# touches all three.
PLANTED_SIGNATURE = "tsc:src/a.ts:12:TS2345"
PLANTED_FILE = "src/a.ts"

# The fixture's five works (dolt_rows.json). B is the query work; the other
# four probe one D6 exclusion each. All five share PLANTED_SIGNATURE, so any
# record that is *retrievable* would match — exclusion is the only filter.
ALL_WORK_IDS = {"B", "prior-cross", "prior-same", "future", "sibling"}


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the store once for the module by invoking the real CLI with the
    stub `dolt`/`gc` executables on PATH. cwd is a temp dir so no real
    `.beads/dolt-server.port` is picked up."""
    node = require_mem_cli(DIST_MAIN)
    # Hard assert, not skip: if the stubs are missing or lost their exec bit
    # (core.fileMode=false checkouts), PATH falls through to any REAL `dolt`/
    # `gc` on the machine and build-store ingests the live city server
    # (minutes, thousands of records). Fail before that can start.
    for stub in ("dolt", "gc"):
        stub_path = STUB_BIN / stub
        assert stub_path.exists(), f"fixture stub missing: {stub_path}"
        assert os.access(stub_path, os.X_OK), f"fixture stub not executable: {stub_path}"

    tmp_path = tmp_path_factory.mktemp("pipeline-e2e")
    db = tmp_path / "store.db"
    env = {**os.environ, "PATH": f"{STUB_BIN}{os.pathsep}{os.environ.get('PATH', '')}"}
    proc = subprocess.run(
        [node, str(MEM_BIN), "build-store", "--with-traces", "--json", "--store", str(db)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f"mem build-store failed: {proc.stderr.strip()}"

    # The CLI JSON envelope contract (the shape mem_cli.run_mem_json unwraps).
    envelope = json.loads(proc.stdout)
    assert envelope["ok"] is True
    assert envelope["cmd"] == "build-store"
    data = envelope["data"]
    assert data["count"] == len(ALL_WORK_IDS)
    # Every fixture transcript carries the planted error, so --with-traces must
    # land a D8 signature on every record — 0 here is the silent-resolve bug.
    assert data["records_with_errors"] == len(ALL_WORK_IDS)
    return db


def test_d6_boundary_fields_survive_store_round_trip(store: Path) -> None:
    """started/closed/external_ref written from dolt-shaped rows read back
    through `mem query --json` exactly — the fields the LOO guard keys on."""
    data = run_mem_json([str(MEM_BIN), "query", "--store", str(store)])
    records = {r["work_id"]: r for r in data["records"]}
    assert set(records) == ALL_WORK_IDS

    b = records["B"]
    assert b["lifecycle"]["started"] == "2026-06-10T00:00:00Z"
    assert b["lifecycle"]["closed"] == "2026-06-11T00:00:00Z"
    assert b["external_ref"] == "branch/feat-x"
    assert b["labels"] == ["golden"]  # the labels join (groupLabels) round-trips
    assert records["sibling"]["external_ref"] == "branch/feat-x"
    assert "external_ref" not in records["prior-cross"]

    # The trace parse projection is exposed by `mem query` and the planted error
    # round-trips structurally (the all-records signature claim is
    # test_failure_signature_parity's).
    errors = b["trace"]["errors"]
    assert len(errors) == 1
    assert errors[0]["file"] == PLANTED_FILE
    assert errors[0]["line"] == 12


def test_failure_signature_parity(store: Path) -> None:
    """The persisted trace_errors signature is the canonical TS form — the
    byte-identical key both halves must agree on (D8 retrieval, D17 scoring)."""
    con = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT work_id, signature FROM trace_errors").fetchall()
    finally:
        con.close()
    assert {wid for wid, _ in rows} == ALL_WORK_IDS
    assert {sig for _, sig in rows} == {PLANTED_SIGNATURE}


def test_replay_cli_fires_ours_under_loo_guard(store: Path, tmp_path: Path) -> None:
    """`membench replay --arms none,ours` over the CLI-built store: the LOO
    guard bounds the corpus, `ours` fires on the planted signature, and the
    5-axis report renders. `assert_no_leak` re-audits every arm's output inside
    replay_arm, so reaching the assertions proves the TS exclusions and the
    Python guard agree."""
    out = tmp_path / "reports"
    rc = cli.main(
        [
            "replay",
            "--store",
            str(store),
            "--work-id",
            "B",
            "--arms",
            "none,ours",
            "--mem-bin",
            str(MEM_BIN),
            "--out",
            str(out),
        ]
    )
    assert rc == 0

    report = json.loads((out / "replay_report.json").read_text())
    assert report["work_id"] == "B"
    # LOO bounds the corpus: of 5 records, only the two priors closed strictly
    # before B.started survive (B itself, future, and the external_ref sibling
    # are excluded).
    assert report["eligible_count"] == 2

    arms = {(a["arm"], a["scope"]): a for a in report["arms"]}
    assert arms[("none", None)]["retrieved"] == 0
    assert arms[("ours", "cross_rig")]["retrieved"] == 1
    assert arms[("ours", "same_rig_temporal")]["retrieved"] == 1
    # The injected payload is non-empty — the D10 token_budget axis measured it.
    assert arms[("ours", "cross_rig")]["token_budget_chars"] > 0

    md = (out / "replay_report.md").read_text()
    assert md.startswith("# Replay 5-axis (raw) — B")


@pytest.fixture(scope="module")
def cross_items(store: Path) -> list[dict[str, Any]]:
    """The cross-rig retrieval result for B, shared by the id-level D6 test and
    the grid's ours-payload construction (read-only against an immutable store)."""
    data = run_mem_json(
        [str(MEM_BIN), "retrieve", "B", "--scope", "cross-rig", "--store", str(store)]
    )
    items: list[dict[str, Any]] = data["items"]
    return items


def test_retrieve_returns_exactly_the_planted_priors(
    store: Path, cross_items: list[dict[str, Any]]
) -> None:
    """Id-level D6 check straight through `mem retrieve`: each track returns
    exactly its planted prior; future (temporal), B (self), and sibling
    (shared external_ref) never appear despite sharing the signature."""
    assert [item["work_id"] for item in cross_items] == ["prior-cross"]

    same = run_mem_json(
        [str(MEM_BIN), "retrieve", "B", "--scope", "same-rig", "--store", str(store)]
    )
    assert [item["work_id"] for item in same["items"]] == ["prior-same"]


def test_grid_scores_stub_runs_against_held_errors(
    store: Path, cross_items: list[dict[str, Any]], tmp_path: Path
) -> None:
    """run_grid over the held-out record the CLI built: the held errors come
    from the store round-trip, the ours payload from the real retrieval, and
    the deterministic axis separates a recurring failure from a resolved one."""
    record = load_record_from_store(store, "B")
    held = load_held_errors_from_store(store, "B")
    assert [e.signature for e in held] == [PLANTED_SIGNATURE]

    # The ours rung injects what retrieval-v1 actually returned for B.
    ours_payloads = {
        item["work_id"]: json.dumps(
            {"citation": item["citation"], "lessons": item["lessons"]}, sort_keys=True
        )
        for item in cross_items
    }
    assert ours_payloads  # the planted prior must be present to inject

    runner = StubRunner(
        {
            # none rung: the fresh run reaches the held file and the planted
            # failure class recurs -> deterministic term 0.0.
            "none": RunTrace(errors=tuple(held), files_touched=frozenset({PLANTED_FILE})),
            # ours rung: same path engaged, failure absent -> 1.0.
            "ours": RunTrace(errors=(), files_touched=frozenset({PLANTED_FILE})),
        }
    )
    rewards = run_grid(
        record,
        tmp_path / "grid",
        held_errors=held,
        runner=runner,
        rungs=("none", "ours"),
        ours_payloads=ours_payloads,
    )

    by_rung = {r.rung: r for r in rewards}
    assert set(by_rung) == {"none", "ours"}
    assert deterministic_term(by_rung["none"].components) == 0.0
    assert deterministic_term(by_rung["ours"].components) == 1.0
