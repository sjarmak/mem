"""Shared, cwd-independent paths + skip guards for tests."""

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE = str(REPO / "fixtures" / "sequences" / "gascity_backend_conventions.json")

# memory-bench -> repo root (the TS half: bin/mem + dist/).
REPO_ROOT = REPO.parent
SOURCE_ROOT = REPO_ROOT / "src"
MEM_BIN = REPO_ROOT / "bin" / "mem"
DIST_MAIN = REPO_ROOT / "dist" / "main.js"
DIST_STORE = REPO_ROOT / "dist" / "store" / "index.js"


def require_mem_cli(*dist_artifacts: Path) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if not (REPO_ROOT / "node_modules").exists():
        pytest.skip("TS runtime deps missing (run `npm install`)")
    if not MEM_BIN.is_file():
        pytest.fail("mem CLI bin missing")
    sources = tuple(SOURCE_ROOT.rglob("*.ts"))
    if not sources:
        pytest.fail("TS sources missing")
    latest_source_mtime = max(source.stat().st_mtime_ns for source in sources)
    for artifact in dist_artifacts:
        if not artifact.is_file():
            pytest.fail(f"TS build missing at {artifact} (run `npm run build`)")
        if artifact.stat().st_mtime_ns < latest_source_mtime:
            pytest.fail(f"TS build stale at {artifact} (run `npm run build`)")
    return node
