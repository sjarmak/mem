"""When a run has earned the right to replace the committed fixture.

The rule is not about extraction quality. It was learned by watching a run
destroy the fixture it was supposed to replace. The re-send policy that used to
be tested here moved to `test_fidelity_local_extractor.py` along with the
recorder, which the probe runner now shares.

The script is loaded by path because `scripts/` is not an installed package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _builder() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_capture_fidelity_fixture.py"
    spec = importlib.util.spec_from_file_location("_fixture_builder", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _builder()


def _staged(tmp_path: Path, names: list[str]) -> tuple[Path, Path]:
    staging, final = tmp_path / "staging", tmp_path / "final"
    staging.mkdir()
    final.mkdir()
    (final / "previous.json").write_text("{}\n")
    for name in names:
        (staging / f"{name}.json").write_text("{}\n")
    return staging, final


def test_a_complete_run_replaces_the_committed_fixture(tmp_path: Path) -> None:
    staging, final = _staged(tmp_path, ["a", "b"])
    BUILDER._swap_in(staging, final, expected=2)
    assert sorted(p.name for p in final.glob("*.json")) == ["a.json", "b.json"]
    assert not staging.exists()


def test_a_run_missing_a_record_leaves_the_previous_fixture_alone(tmp_path: Path) -> None:
    """The loss this exists to prevent, in the shape it actually happened: a run
    that answered for some records and not others. Mixing them would commit a
    fixture no single run produced; clearing first, as an earlier version did,
    destroyed a good one and left nothing."""
    staging, final = _staged(tmp_path, ["a"])
    BUILDER._swap_in(staging, final, expected=2)
    assert [p.name for p in final.glob("*.json")] == ["previous.json"]
    assert (staging / "a.json").exists()


def test_a_run_that_recorded_nothing_leaves_the_previous_fixture_alone(tmp_path: Path) -> None:
    """The degenerate case of the same rule, and the one run 9 hit: every record
    failed, so the staging directory was empty and swapping it in would have
    published an empty corpus as a measurement."""
    staging, final = _staged(tmp_path, [])
    BUILDER._swap_in(staging, final, expected=2)
    assert [p.name for p in final.glob("*.json")] == ["previous.json"]
