"""ftp 3-arm grid driver: cross-rig bundle loading (mem-on3f).

`scripts/run_grid_3arm_ftp.py` is not a package module, so it is loaded from its
file path (the test_run_grid_3arm idiom, preloading sibling-script imports).
"""

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


for _sibling in ("run_gate_probe", "run_grid", "run_grid_3arm", "run_grid_3arm_graded"):
    if _sibling not in sys.modules:
        _load_script(_sibling)
ftp_cli = _load_script("run_grid_3arm_ftp")


def _bundle(work_id: str, rig: str) -> TaskBundle:
    return TaskBundle(
        work_id=work_id,
        rig=rig,
        issue_title="t",
        issue_body="",
        trace_ref="/tmp/t.jsonl",
        output=ReplayResult(calls=(), file_diffs=(("src/app.py", "x"),), replay_success_rate=0.0),
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


def test_load_ftp_bundles_single_dir_backward_compatible(tmp_path: Path) -> None:
    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    (bundles_dir / "codeprobe-abc.json").write_text(
        _bundle("codeprobe-abc", "codeprobe").model_dump_json(), encoding="utf-8"
    )

    loaded = ftp_cli.load_ftp_bundles([bundles_dir])

    assert [b.work_id for b in loaded] == ["codeprobe-abc"]


def test_load_ftp_bundles_spans_multiple_rig_dirs(tmp_path: Path) -> None:
    codeprobe_dir = tmp_path / "bundles-codeprobe"
    scix_dir = tmp_path / "bundles-scix_experiments"
    codeprobe_dir.mkdir()
    scix_dir.mkdir()
    (codeprobe_dir / "codeprobe-abc.json").write_text(
        _bundle("codeprobe-abc", "codeprobe").model_dump_json(), encoding="utf-8"
    )
    (scix_dir / "scix_experiments-def.json").write_text(
        _bundle("scix_experiments-def", "scix_experiments").model_dump_json(), encoding="utf-8"
    )

    loaded = ftp_cli.load_ftp_bundles([codeprobe_dir, scix_dir])

    assert [b.work_id for b in loaded] == ["codeprobe-abc", "scix_experiments-def"]


def test_load_ftp_bundles_limit_caps_across_combined_pool(tmp_path: Path) -> None:
    codeprobe_dir = tmp_path / "bundles-codeprobe"
    scix_dir = tmp_path / "bundles-scix_experiments"
    codeprobe_dir.mkdir()
    scix_dir.mkdir()
    (codeprobe_dir / "codeprobe-abc.json").write_text(
        _bundle("codeprobe-abc", "codeprobe").model_dump_json(), encoding="utf-8"
    )
    (scix_dir / "scix_experiments-def.json").write_text(
        _bundle("scix_experiments-def", "scix_experiments").model_dump_json(), encoding="utf-8"
    )

    loaded = ftp_cli.load_ftp_bundles([codeprobe_dir, scix_dir], limit=1)

    assert [b.work_id for b in loaded] == ["codeprobe-abc"]
