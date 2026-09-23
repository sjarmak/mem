"""The other upward walk: a `.beads` workspace above a paid sandbox (mem-pkglb).

`assert_neutral_ancestry` guards auto-loaded agent CONTEXT. A bead store is neither
`CLAUDE.md` nor `AGENTS.md`, so it walks straight past one -- and both `bd init` and `bd -C`
resolve upward the same way. The guard under test is the pre-spend refusal.

The tests pass `ceiling=tmp_path` wherever they assert a chain is CLEAN, because they cannot
assert anything about the real directories above pytest's temp root: this box carries
`.beads` at `/workspace`, `/tmp` and `/workspace/projects`, which is the same contamination the
guard exists to catch. The refusal cases need no ceiling -- they fire on the fixture's own
ancestor, below any real one.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from membench.runner.sandbox import (
    SandboxContaminationError,
    assert_no_bead_store_above,
    paid_sandbox,
)


def test_a_clean_chain_is_admitted(tmp_path: Path) -> None:
    room = tmp_path / "run-root"
    room.mkdir()
    assert_no_bead_store_above(room, ceiling=tmp_path)


def test_a_bead_store_in_a_parent_refuses_the_run(tmp_path: Path) -> None:
    (tmp_path / ".beads").mkdir()
    room = tmp_path / "run-root"
    room.mkdir()

    with pytest.raises(SandboxContaminationError) as exc:
        assert_no_bead_store_above(room, ceiling=tmp_path)

    message = str(exc.value)
    assert str(tmp_path / ".beads") in message
    # The message must say why the RECORDS would be wrong, not merely that a path exists:
    # the reader's next move is deciding whether a session's result can be trusted.
    assert "would not be this session's" in message


def test_a_bead_store_two_levels_up_still_refuses(tmp_path: Path) -> None:
    (tmp_path / ".beads").mkdir()
    room = tmp_path / "outer" / "inner"
    room.mkdir(parents=True)

    with pytest.raises(SandboxContaminationError):
        assert_no_bead_store_above(room, ceiling=tmp_path)


def test_a_bead_store_in_the_directory_itself_refuses(tmp_path: Path) -> None:
    room = tmp_path / "run-root"
    room.mkdir()
    (room / ".beads").mkdir()

    with pytest.raises(SandboxContaminationError):
        assert_no_bead_store_above(room, ceiling=tmp_path)


def test_a_bead_store_in_a_sibling_is_none_of_the_guards_business(tmp_path: Path) -> None:
    """bd resolves upward, never sideways. A guard that refused a sibling would be
    unsatisfiable on any shared temp root and would train callers to bypass it."""
    sibling = tmp_path / "somebody-elses-run"
    sibling.mkdir()
    (sibling / ".beads").mkdir()
    room = tmp_path / "run-root"
    room.mkdir()

    assert_no_bead_store_above(room, ceiling=tmp_path)


def test_a_bead_store_file_refuses_as_readily_as_a_directory(tmp_path: Path) -> None:
    """Telling an initialized workspace from a stray marker means running bd. Refusing on
    the name is the conservative call, and the docstring says so."""
    (tmp_path / ".beads").write_text("not a directory\n")
    room = tmp_path / "run-root"
    room.mkdir()

    with pytest.raises(SandboxContaminationError):
        assert_no_bead_store_above(room, ceiling=tmp_path)


def test_the_ceiling_bounds_the_walk_and_nothing_more(tmp_path: Path) -> None:
    """A `.beads` ABOVE the ceiling is out of scope by construction; the same one below it
    still refuses. Without this, `ceiling` could silently be a no-op and every clean-chain
    assertion above would be vacuous."""
    (tmp_path / ".beads").mkdir()
    vouched = tmp_path / "vouched"
    vouched.mkdir()
    room = vouched / "run-root"
    room.mkdir()

    assert_no_bead_store_above(room, ceiling=vouched)

    (vouched / ".beads").mkdir()
    with pytest.raises(SandboxContaminationError):
        assert_no_bead_store_above(room, ceiling=vouched)


def test_without_a_ceiling_the_walk_reaches_the_real_ancestors(tmp_path: Path) -> None:
    """The production call passes no ceiling. Pinning that the walk does NOT stop at the
    fixture keeps a future `ceiling` default from quietly disarming every paid run."""
    room = tmp_path / "run-root"
    room.mkdir()
    contaminated = [parent for parent in room.resolve().parents if (parent / ".beads").exists()]
    if not contaminated:
        pytest.skip("no real ancestor of the temp root carries .beads on this machine")

    with pytest.raises(SandboxContaminationError):
        assert_no_bead_store_above(room)


def test_paid_sandbox_mints_under_the_parent_it_is_given(tmp_path: Path) -> None:
    room = tmp_path / "run-root"
    room.mkdir()

    with paid_sandbox("guard-test-", parent=room) as sandbox:
        assert sandbox.parent == room.resolve()
        assert sandbox.name.startswith("guard-test-")
        assert sandbox.is_dir()
        held = sandbox

    assert not held.exists()


def test_paid_sandbox_without_a_parent_still_honors_the_ambient_temp_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override is opt-in. Existing callers, whose temp root is known clean, keep the
    behaviour they were written against.

    `tempfile.tempdir` is patched rather than `TMPDIR`, because tempfile reads the env var
    once per process and caches it; setting the variable inside a test that has already
    created a temp directory changes nothing."""
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(ambient))

    with paid_sandbox("guard-test-") as sandbox:
        assert sandbox.parent == ambient.resolve()


def test_paid_sandbox_still_refuses_a_context_contaminated_parent(tmp_path: Path) -> None:
    """`parent` chooses WHERE, never WHETHER: the context guard runs on the minted path
    regardless of who picked the root."""
    room = tmp_path / "run-root"
    room.mkdir()
    (tmp_path / "CLAUDE.md").write_text("# ambient project context\n")

    with pytest.raises(SandboxContaminationError), paid_sandbox("guard-test-", parent=room):
        pytest.fail("a sandbox under an auto-loaded CLAUDE.md must never be handed out")
