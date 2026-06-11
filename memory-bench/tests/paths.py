"""Shared, cwd-independent paths + skip guards for tests."""

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE = str(REPO / "fixtures" / "sequences" / "gascity_backend_conventions.json")

# memory-bench -> repo root (the TS half: bin/mem + dist/).
REPO_ROOT = REPO.parent
MEM_BIN = REPO_ROOT / "bin" / "mem"
DIST_MAIN = REPO_ROOT / "dist" / "main.js"
DIST_STORE = REPO_ROOT / "dist" / "store" / "index.js"


def require_mem_cli(*dist_artifacts: Path) -> str:
    """Skip unless node, the built TS artifacts, and the `mem` bin are present.

    `dist_artifacts` are the dist/ entry points the test actually loads (e.g.
    DIST_MAIN for CLI-driving tests, DIST_STORE for store-builder scripts).
    Returns the node executable path. node_modules is checked here too, so a
    runtime "Cannot find module" is always a real packaging regression (hard
    failure), never an env gap to skip over."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    for artifact in dist_artifacts:
        if not artifact.exists():
            pytest.skip("TS build missing (run `npm run build`)")
    if not MEM_BIN.exists():
        pytest.skip("mem CLI bin missing")
    if not (REPO_ROOT / "node_modules").exists():
        pytest.skip("TS runtime deps missing (run `npm install`)")
    return node
