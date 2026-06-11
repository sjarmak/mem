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
    effective_work_dir,
    gold_diff,
    infer_work_dir,
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


# --- infer_work_dir / effective_work_dir (mem-75t.7.2 revision 1) -------------------
#
# Shapes mirror docs/mem-75t.7.1-replay-validation.md: record.work_dir is the clone
# root while sessions ran in nested (.claude/worktrees/<name>) or sibling
# (<clone>-wt-<id>) git worktrees; majority-prefix inference over the mutation paths
# recovered 3/8 validation beads from a 0.00 replay rate.


def _mut(path: str) -> MutationCall:
    return MutationCall(tool="Edit", path=path, edits=(EditOp(old_string="a", new_string="b"),))


def test_infer_work_dir_sibling_worktree_majority_beats_memory_writes():
    # 2nbe9/52kv3 shape: edits in a SIBLING worktree of the clone root, plus one
    # auto-memory write under a .claude home -- outvoted by the worktree majority.
    calls = (
        _mut("/home/ds/gascity-dashboard-wt-aqje/backend/src/app.ts"),
        _mut("/home/ds/gascity-dashboard-wt-aqje/frontend/src/ui.tsx"),
        _mut("/home/ds/gascity-dashboard-wt-aqje/shared/types.ts"),
        _mut("/home/ds/.claude-homes/account4/.claude/memory/MEMORY.md"),
    )
    assert infer_work_dir(calls) == "/home/ds/gascity-dashboard-wt-aqje"


def test_infer_work_dir_nested_worktree():
    # usu9f shape: session root NESTED inside the clone (.claude/worktrees/<name>).
    root = "/home/ds/gascity-dashboard/.claude/worktrees/4bol-health"
    calls = (_mut(f"{root}/backend/a.ts"), _mut(f"{root}/frontend/b.tsx"), _mut(f"{root}/c.ts"))
    assert infer_work_dir(calls) == root


def test_infer_work_dir_tie_stops_at_shared_parent():
    # An exact 50/50 split below /repo is NOT a strict majority: descent stops and
    # the shared parent wins (the documented tiebreaker).
    calls = (
        _mut("/repo/a/x.py"),
        _mut("/repo/a/y.py"),
        _mut("/repo/b/x.py"),
        _mut("/repo/b/y.py"),
    )
    assert infer_work_dir(calls) == "/repo"


def test_infer_work_dir_majority_is_over_distinct_paths_not_calls():
    # One hot file edited 5x must not dominate: majority counts DISTINCT paths.
    calls = (
        *(_mut("/homes/acct/memory/MEMORY.md") for _ in range(5)),
        _mut("/work/repo/src/a.py"),
        _mut("/work/repo/b.py"),
    )
    assert infer_work_dir(calls) == "/work/repo"


def test_infer_work_dir_picks_deepest_majority_prefix():
    # The exact rule: 2/3 paths under /work/src outvote the root-level straggler,
    # so the DEEPEST strict-majority prefix wins -- even past the true session root.
    # The over-deepening approximation is caught downstream by the admission
    # threshold (Edit-class drift surfaces as FILE_ABSENT under a wrong prefix).
    calls = (_mut("/work/readme.md"), _mut("/work/src/a.py"), _mut("/work/src/b.py"))
    assert infer_work_dir(calls) == "/work/src"


def test_infer_work_dir_none_when_no_prefix_beyond_filesystem_root():
    calls = (_mut("/etc/passwd"), _mut("/home/x/y.txt"))
    assert infer_work_dir(calls) is None


def test_infer_work_dir_none_without_absolute_paths():
    assert infer_work_dir(()) is None
    assert infer_work_dir((_mut("relative/path.py"),)) is None


def test_effective_work_dir_recovers_sibling_worktree():
    # 2nbe9/52kv3 shape: the record's clone root covers NO mutation path (the
    # validated 0.00 failure mode) -- the chain element at the record's own depth is
    # the sibling worktree the session really ran in.
    calls = (_mut("/home/ds/clone-wt-1/src/a.py"), _mut("/home/ds/clone-wt-1/tests/b.py"))
    assert effective_work_dir("/home/ds/clone", calls) == "/home/ds/clone-wt-1"


def test_effective_work_dir_recovers_nested_worktree():
    # usu9f shape: the record covers every path (the clone root is an ancestor of
    # the nested worktree), but the chain continues through the Claude Code
    # .claude/worktrees/<name> layout -- that nested root wins.
    root = "/home/ds/clone/.claude/worktrees/4bol-health"
    calls = (_mut(f"{root}/src/a.py"), _mut(f"{root}/tests/b.py"))
    assert effective_work_dir("/home/ds/clone", calls) == root


def test_effective_work_dir_keeps_consistent_record_over_deeper_inference():
    # mem-us6j shape: the record work_dir covers a majority of the paths, so it is
    # consistent with the trace; the deeper inferred prefix (/repo/src here) is
    # subtree concentration, not a different session root. A blind
    # prefer-the-inferred rule would destroy a perfect 1.00 replay.
    calls = (_mut("/repo/src/a.py"), _mut("/repo/src/b.py"), _mut("/repo/tests/c.py"))
    assert infer_work_dir(calls) == "/repo/src"
    assert effective_work_dir("/repo", calls) == "/repo"


def test_effective_work_dir_uses_deepest_prefix_when_no_same_depth_element():
    # Inconsistent record deeper than the whole chain: nothing at its depth to pick,
    # so the deepest majority prefix is the best mechanical guess.
    calls = (_mut("/wt/src/a.py"), _mut("/wt/tests/b.py"))
    assert effective_work_dir("/home/ds/clone", calls) == "/wt"


def test_effective_work_dir_falls_back_to_record_when_inference_fails():
    assert effective_work_dir("/home/ds/clone", ()) == "/home/ds/clone"


# --- adjusted success rate (mem-75t.7.2 revision 2) ----------------------------------


def test_adjusted_rate_excludes_outside_work_dir_from_denominator(checkout: Path):
    # The auto-memory write is OUTSIDE_WORK_DIR: out-of-repo by construction, so it
    # can never affect the gold diff and must not deflate fidelity.
    stream = _stream(
        _event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")),  # APPLIED
        _event(_edit(f"{WORK}/src/app.py", "NOPE", "x")),  # OLD_STRING_MISSING
        _event(_write("/home/ds/.claude-homes/account4/memory/MEMORY.md", "m\n")),  # OWD
    )
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.replay_success_rate == pytest.approx(1 / 3)
    assert result.adjusted_replay_success_rate == pytest.approx(0.5)


def test_adjusted_rate_zero_when_every_call_is_outside(checkout: Path):
    stream = _stream(_event(_write("/home/ds/.claude-homes/account4/memory/m.md", "x\n")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.adjusted_replay_success_rate == 0.0


def test_adjusted_rate_zero_for_empty_transcript(checkout: Path):
    result = replay_transcript("", checkout_dir=checkout, work_dir=WORK)
    assert result.adjusted_replay_success_rate == 0.0


def test_derived_fields_serialize_with_the_result(checkout: Path):
    stream = _stream(_event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")))
    dumped = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK).model_dump()
    assert dumped["adjusted_replay_success_rate"] == 1.0
    assert dumped["base_predates_tree"] is False


# --- base-predates-tree detection (mem-75t.7.2 revision 5) ---------------------------


def test_first_edit_on_absent_file_flags_base_predates_tree(checkout: Path):
    # zg4da/041jz shape: the bead's FIRST mutation of a file is an Edit and the file
    # is absent at the checkout -- the timestamp-approximate base_commit predates
    # the session tree.
    stream = _stream(
        _event(_edit(f"{WORK}/backend/src/routes/allowlist.ts", "a", "b")),
        _event(_edit(f"{WORK}/src/app.py", "return 1", "return 2")),
    )
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.base_predates_tree is True
    assert result.first_edit_absent_paths() == (f"{WORK}/backend/src/routes/allowlist.ts",)


def test_repeat_edits_of_one_absent_file_flag_it_once(checkout: Path):
    stream = _stream(
        _event(_edit(f"{WORK}/missing.py", "a", "b")),
        _event(_multi_edit(f"{WORK}/missing.py", [{"old_string": "a", "new_string": "b"}])),
    )
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.first_edit_absent_paths() == (f"{WORK}/missing.py",)


def test_write_then_edit_of_new_file_does_not_flag_base_predates_tree(checkout: Path):
    # The first mutation being a Write means the session CREATED the file; a later
    # Edit of it is not evidence about the base tree.
    stream = _stream(
        _event(_write(f"{WORK}/docs/new.md", "# hi\n")),
        _event(_edit(f"{WORK}/docs/new.md", "hi", "yo")),
    )
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.base_predates_tree is False


def test_old_string_drift_does_not_flag_base_predates_tree(checkout: Path):
    stream = _stream(_event(_edit(f"{WORK}/src/app.py", "return 99", "x")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.base_predates_tree is False


def test_outside_work_dir_calls_do_not_flag_base_predates_tree(checkout: Path):
    stream = _stream(_event(_edit("/etc/passwd", "root", "boot")))
    result = replay_transcript(stream, checkout_dir=checkout, work_dir=WORK)
    assert result.base_predates_tree is False


def test_gold_diff_git_failure_raises_loud(tmp_path: Path):
    def failing_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode=128, stdout="", stderr="boom")

    with pytest.raises(RuntimeError, match="boom"):
        gold_diff(tmp_path, runner=failing_runner)
