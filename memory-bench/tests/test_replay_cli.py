"""Integration test: `membench replay` end-to-end over a real built store.

Builds the error-carrying fixture store (so `ours` actually retrieves through the
real retrieval-v1 CLI), runs the replay subcommand in-process, and checks the
emitted 5-axis report + OTel spans. Skips when node / the TS build is absent.
"""

import json
import subprocess
from pathlib import Path

import pytest

from membench import cli
from tests.paths import DIST_STORE, MEM_BIN, REPO, require_mem_cli

BUILDER = REPO / "fixtures" / "build_replay_store.mjs"


def _build_store(tmp_path: Path) -> Path:
    node = require_mem_cli(DIST_STORE)
    db = tmp_path / "store.db"
    proc = subprocess.run(
        [node, str(BUILDER), str(db)], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        pytest.skip(f"store builder failed (env issue): {proc.stderr.strip()}")
    return db


def test_replay_cli_end_to_end(tmp_path):
    db = _build_store(tmp_path)
    out = tmp_path / "reports"

    rc = cli.main(
        [
            "replay",
            "--store",
            str(db),
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
    # prior-cross + prior-same are closed before B.started; future + B are not.
    assert report["eligible_count"] == 2

    arms = {(a["arm"], a["scope"]): a for a in report["arms"]}
    # ours under cross-rig retrieves the rigB prior through the real CLI; the LOO
    # guard would have raised on any leak, so reaching here proves it held.
    assert arms[("ours", "cross_rig")]["retrieved"] >= 1
    assert arms[("none", None)]["retrieved"] == 0

    spans = json.loads((out / "replay_spans.json").read_text())
    assert any(s["name"] == "memory_eval.replay" for s in spans)


def test_replay_cli_unknown_arm_surfaces_pointer(tmp_path):
    db = _build_store(tmp_path)
    # `builtin` must fail loudly with its mem-whi pointer, not silently skip.
    with pytest.raises(ValueError, match="mem-whi"):
        cli.main(
            [
                "replay",
                "--store",
                str(db),
                "--work-id",
                "B",
                "--arms",
                "builtin",
                "--mem-bin",
                str(MEM_BIN),
                "--out",
                str(tmp_path / "r"),
            ]
        )
