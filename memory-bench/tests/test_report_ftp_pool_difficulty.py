"""Cross-rig ftp pool difficulty report (mem-on3f)."""

import importlib.util
import sys
import types
from pathlib import Path

from membench.bundle.replay import ReplayResult
from membench.schemas.bundle import BundleEnv, BundleVerification, FtpOracle, TaskBundle

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SIBLINGS = (
    "run_gate_probe",
    "run_grid",
    "run_grid_3arm",
    "run_grid_3arm_graded",
    "run_grid_3arm_ftp",
)
for _sibling in _SIBLINGS:
    if _sibling not in sys.modules:
        _load_script(_sibling)
report_cli = _load_script("report_ftp_pool_difficulty")


def _bundle(work_id: str, rig: str, *, n_files: int) -> TaskBundle:
    file_diffs = tuple((f"src/f{i}.py", "x") for i in range(n_files))
    return TaskBundle(
        work_id=work_id,
        rig=rig,
        issue_title="t",
        issue_body="",
        trace_ref="/tmp/t.jsonl",
        output=ReplayResult(calls=(), file_diffs=file_diffs, replay_success_rate=0.0),
        env=BundleEnv(repo=rig, base_commit="0" * 40, base_image="python:3.12-bookworm"),
        loo_excluded_work_ids=(work_id,),
        verification=BundleVerification(
            ftp_oracle=FtpOracle(
                commit="1" * 40,
                parent="2" * 40,
                ftp_tests=("tests/test_x.py::test_a",),
                behavioral=("tests/test_x.py::test_a",),
                type="behavioral",
            )
        ),
    )


def test_build_report_groups_by_rig_and_band() -> None:
    bundles = [
        _bundle("codeprobe-a", "codeprobe", n_files=1),
        _bundle("codeprobe-b", "codeprobe", n_files=5),
        _bundle("scix_experiments-a", "scix_experiments", n_files=3),
    ]

    report = report_cli.build_report(bundles)

    assert report["n_bundles"] == 3
    assert report["by_rig"] == {"codeprobe": 2, "scix_experiments": 1}
    assert sum(report["by_band"].values()) == 3
    by_id = {a["work_id"]: a for a in report["anchors"]}
    assert by_id["codeprobe-a"]["rig"] == "codeprobe"
    assert by_id["codeprobe-a"]["gold_files"] == 1
    assert by_id["codeprobe-b"]["gold_files"] == 5
