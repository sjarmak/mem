"""Integration test: the `ours` arm against the REAL retrieval-v1 CLI + store.

Proves the wiring honors the actual mem-di8 contract end-to-end (CLI envelope,
D6 boundary, lessons payload) — not just the injected-runner shape. Skips
gracefully when the TS build, node, or the native sqlite module is unavailable,
so the hermetic unit suite still runs everywhere.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from membench.memory_systems.ours_system import OursMemory
from membench.replay import replay_arm
from membench.validity import QueryWork, WorkRef

# memory-bench/tests -> memory-bench -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
MEM_BIN = REPO_ROOT / "bin" / "mem"
DIST = REPO_ROOT / "dist" / "store" / "index.js"
BUILDER = REPO_ROOT / "memory-bench" / "fixtures" / "build_replay_store.mjs"


def _require_cli() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if not DIST.exists():
        pytest.skip("TS build missing (run `npm run build`)")
    if not MEM_BIN.exists():
        pytest.skip("mem CLI bin missing")
    return node


def _build_store(node: str, db_path: Path) -> None:
    proc = subprocess.run(
        [node, str(BUILDER), str(db_path)], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        pytest.skip(f"store builder failed (env issue): {proc.stderr.strip()}")


def _corpus() -> list[WorkRef]:
    # Mirrors build_replay_store.mjs so the harness LOO guard can audit the CLI.
    return [
        WorkRef(work_id="B", rig="rigA", closed="2026-06-11T00:00:00Z"),
        WorkRef(work_id="prior-cross", rig="rigB", closed="2026-06-05T00:00:00Z"),
        WorkRef(work_id="prior-same", rig="rigA", closed="2026-06-05T00:00:00Z"),
        WorkRef(work_id="future", rig="rigB", closed="2026-06-20T00:00:00Z"),
    ]


def _query() -> QueryWork:
    return QueryWork(work_id="B", rig="rigA", started="2026-06-10T00:00:00Z")


def test_ours_cross_rig_against_real_cli(tmp_path):
    node = _require_cli()
    db = tmp_path / "store.db"
    _build_store(node, db)

    arm = OursMemory(store_path=db, mem_bin=str(MEM_BIN))
    # replay_arm runs the arm AND the harness leak audit — a leak would raise.
    result = replay_arm(arm, _query(), _corpus(), scope="cross_rig")

    # Cross-rig: the rigB prior with the shared signature is returned; the query
    # work itself, the same-rig prior, and the future record are all excluded.
    assert "prior-cross" in result.retrieved_ids
    assert "future" not in result.retrieved_ids
    assert "B" not in result.retrieved_ids
    assert "prior-same" not in result.retrieved_ids
    # The consumed lesson made it into the injected payload.
    assert result.injected_context_chars > 0


def test_ours_same_rig_against_real_cli(tmp_path):
    node = _require_cli()
    db = tmp_path / "store.db"
    _build_store(node, db)

    arm = OursMemory(store_path=db, mem_bin=str(MEM_BIN))
    result = replay_arm(arm, _query(), _corpus(), scope="same_rig_temporal")

    # Same-rig track surfaces the same-rig prior; future stays excluded.
    assert "prior-same" in result.retrieved_ids
    assert "future" not in result.retrieved_ids
