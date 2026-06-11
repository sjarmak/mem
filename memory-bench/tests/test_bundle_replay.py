"""Trace->diff replay reconstructor (mem-75t.7.1, plan §9.1).

Replay runs against a REAL temp git repo (not a mocked runner) so the
``git add -N`` / ``git diff`` contract is genuinely exercised; the runner is only
faked where a git failure path is the thing under test. Transcripts are synthetic
Claude Code .jsonl strings covering every outcome class.
"""

import json
import subprocess
from pathlib import Path

import pytest

from membench.bundle import (
    EditOp,
    MutationCall,
    ReplayOutcome,
    ReplayResult,
    gold_diff,
    parse_mutation_calls,
    replay_transcript,
)

# The ORIGINAL session's working directory -- transcript paths are absolute under it.
WORK = "/orig/work"

APP = "def main():\n    return 1\n"
NOTES = "alpha\nbeta\nalpha\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A committed repo standing in for repo@base_commit (env_recon's job, not ours)."""
    repo = tmp_path / "checkout"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(APP, encoding="utf-8")
    (repo / "notes.txt").write_text(NOTES, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _tool_use(name: str, tool_input: dict) -> dict:
    return {"type": "tool_use", "id": "toolu_x", "name": name, "input": tool_input}


def _event(*blocks: dict) -> dict:
    return {"type": "assistant", "message": {"content": list(blocks)}}


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


def _edit(path: str, old: str, new: str, *, replace_all: bool = False) -> dict:
    args: dict = {"file_path": path, "old_string": old, "new_string": new}
    if replace_all:
        args["replace_all"] = True
    return _tool_use("Edit", args)


def _write(path: str, content: str) -> dict:
    return _tool_use("Write", {"file_path": path, "content": content})


def _multi_edit(path: str, edits: list[dict]) -> dict:
    return _tool_use("MultiEdit", {"file_path": path, "edits": edits})


# --- parse_mutation_calls: ordered capture of the mutation tool calls ------------


def test_parse_extracts_ordered_mutation_calls_with_content():
    stream = _stream(
        _event(_tool_use("Read", {"file_path": f"{WORK}/src/app.py"})),
        _event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")),
        _event(_tool_use("Bash", {"command": "mv a b"})),
        _event(_write(f"{WORK}/docs/new.md", "# hi\n")),
        _event(
            _multi_edit(
                f"{WORK}/notes.txt",
                [
                    {"old_string": "beta", "new_string": "gamma"},
                    {"old_string": "alpha", "new_string": "omega", "replace_all": True},
                ],
            )
        ),
    )
    calls = parse_mutation_calls(stream)
    assert [c.tool for c in calls] == ["Edit", "Write", "MultiEdit"]
    assert calls[0] == MutationCall(
        tool="Edit",
        path=f"{WORK}/src/app.py",
        edits=(EditOp(old_string="return 1", new_string="return 2"),),
    )
    assert calls[1].content == "# hi\n"
    assert calls[2].edits[1].replace_all is True


def test_parse_tolerates_non_json_and_blank_lines():
    stream = "not json\n\n" + _stream(_event(_edit(f"{WORK}/a.txt", "x", "y")))
    assert len(parse_mutation_calls(stream)) == 1


def test_parse_malformed_mutation_call_raises_loud():
    # An Edit with no old_string is a malformed transcript, never a silent skip.
    stream = _stream(_event(_tool_use("Edit", {"file_path": f"{WORK}/a.txt", "new_string": "y"})))
    with pytest.raises(ValueError, match="Edit"):
        parse_mutation_calls(stream)


# --- replay: per-call outcomes -----------------------------------------------------


def test_clean_edit_applies(checkout: Path):
    stream = _stream(_event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert [c.outcome for c in result.calls] == [ReplayOutcome.APPLIED]
    assert result.replay_success_rate == 1.0
    assert (checkout / "src" / "app.py").read_text(encoding="utf-8") == APP.replace(
        "return 1", "return 2"
    )
    assert "+    return 2" in result.diff_by_file()["src/app.py"]


def test_write_creates_new_file_and_appears_in_diff(checkout: Path):
    stream = _stream(_event(_write(f"{WORK}/docs/new.md", "# hi\n")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.APPLIED
    assert (checkout / "docs" / "new.md").read_text(encoding="utf-8") == "# hi\n"
    # `git add -N` makes the brand-new file visible to `git diff`.
    assert "+# hi" in result.diff_by_file()["docs/new.md"]


def test_multiedit_applies_sequentially_in_order(checkout: Path):
    # The second op only matches text PRODUCED by the first -- proves ordering.
    stream = _stream(
        _event(
            _multi_edit(
                f"{WORK}/src/app.py",
                [
                    {"old_string": "return 1", "new_string": "return TWO"},
                    {"old_string": "TWO", "new_string": "2"},
                ],
            )
        )
    )
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.APPLIED
    assert "return 2" in (checkout / "src" / "app.py").read_text(encoding="utf-8")


def test_multiedit_is_atomic_on_mid_sequence_failure(checkout: Path):
    stream = _stream(
        _event(
            _multi_edit(
                f"{WORK}/src/app.py",
                [
                    {"old_string": "return 1", "new_string": "return 2"},
                    {"old_string": "NOT PRESENT", "new_string": "x"},
                ],
            )
        )
    )
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.OLD_STRING_MISSING
    assert "edit 1" in result.calls[0].detail
    # All-or-nothing: the first op must NOT have been written.
    assert (checkout / "src" / "app.py").read_text(encoding="utf-8") == APP
    assert result.file_diffs == ()


def test_old_string_drift_is_detected_and_classified(checkout: Path):
    stream = _stream(_event(_edit(f"{WORK}/src/app.py", "return 99", "return 2")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.OLD_STRING_MISSING
    assert result.replay_success_rate == 0.0
    assert (checkout / "src" / "app.py").read_text(encoding="utf-8") == APP


def test_ambiguous_old_string_without_replace_all(checkout: Path):
    stream = _stream(_event(_edit(f"{WORK}/notes.txt", "alpha", "omega")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.OLD_STRING_AMBIGUOUS
    assert "2" in result.calls[0].detail
    assert (checkout / "notes.txt").read_text(encoding="utf-8") == NOTES


def test_replace_all_replaces_every_occurrence(checkout: Path):
    stream = _stream(_event(_edit(f"{WORK}/notes.txt", "alpha", "omega", replace_all=True)))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.APPLIED
    assert (checkout / "notes.txt").read_text(encoding="utf-8") == "omega\nbeta\nomega\n"


def test_edit_on_absent_file_is_classified(checkout: Path):
    stream = _stream(_event(_edit(f"{WORK}/missing.py", "a", "b")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.FILE_ABSENT


def test_path_outside_work_dir_is_classified_skip_not_crash(checkout: Path):
    stream = _stream(_event(_edit("/etc/passwd", "root", "boot")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.OUTSIDE_WORK_DIR
    assert result.calls[0].rebased_path is None


def test_dot_dot_escape_of_work_dir_is_outside(checkout: Path):
    # Lexically under WORK but normalizes outside it -- must never rebase past checkout.
    stream = _stream(_event(_edit(f"{WORK}/../evil.txt", "a", "b")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].outcome is ReplayOutcome.OUTSIDE_WORK_DIR


def test_rebasing_maps_work_dir_prefix_onto_checkout(checkout: Path):
    stream = _stream(_event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.calls[0].rebased_path == str(checkout / "src" / "app.py")


# --- gold diff + success rate -------------------------------------------------------


def test_gold_diff_contains_new_and_modified_files(checkout: Path):
    stream = _stream(
        _event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")),
        _event(_write(f"{WORK}/docs/new.md", "# hi\n")),
    )
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert set(result.diff_by_file()) == {"src/app.py", "docs/new.md"}
    assert all(diff.startswith("diff --git") for _, diff in result.file_diffs)
    assert result.replay_success_rate == 1.0


def test_success_rate_is_applied_over_total(checkout: Path):
    stream = _stream(
        _event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")),  # APPLIED
        _event(_edit(f"{WORK}/src/app.py", "NOPE", "x")),  # OLD_STRING_MISSING
        _event(_edit("/etc/passwd", "a", "b")),  # OUTSIDE_WORK_DIR
        _event(_write(f"{WORK}/new.txt", "n\n")),  # APPLIED
    )
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.replay_success_rate == pytest.approx(0.5)


def test_empty_transcript_yields_zero_rate_and_empty_diff(checkout: Path):
    result = replay_transcript("", checkout_dir=checkout, work_dir=WORK)
    assert result == ReplayResult(calls=(), file_diffs={}, replay_success_rate=0.0)


def test_file_diffs_is_immutable(checkout: Path):
    """Regression (review pass): file_diffs must be a real value, not a mutable dict
    hiding inside a frozen model -- in-place mutation raises, and the mapping view
    returned by diff_by_file() is a detached copy."""
    stream = _stream(_event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    with pytest.raises(TypeError):
        result.file_diffs[0] = ("evil.py", "diff")  # type: ignore[index]
    view = result.diff_by_file()
    view.clear()
    assert result.diff_by_file() != {}
    # A mapping passed at construction is converted to sorted pairs, not aliased.
    source = {"b.py": "diff b", "a.py": "diff a"}
    model = ReplayResult(calls=(), file_diffs=source, replay_success_rate=0.0)
    source["c.py"] = "diff c"
    assert model.file_diffs == (("a.py", "diff a"), ("b.py", "diff b"))


def test_gold_diff_git_failure_raises_loud(tmp_path: Path):
    def failing_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode=128, stdout="", stderr="boom")

    with pytest.raises(RuntimeError, match="boom"):
        gold_diff(tmp_path, runner=failing_runner)
