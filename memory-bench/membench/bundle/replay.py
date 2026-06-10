"""Trace->diff REPLAY reconstructor -- the bundle's gold-diff `output` leg (mem-75t.7.1).

Plan §9.1 (accepted review revision): the gold diff is produced by REPLAY, not
reconstruction. The caller checks out ``repo@base_commit`` (env_recon's job -- checkout
is explicitly NOT this module's concern), this module replays the transcript's
file-mutation tool calls (``Edit``/``Write``/``MultiEdit``, in transcript order) against
that checkout, and ``git diff`` over the mutated tree IS the gold diff. Two properties
fall out of the replay framing:

- the diff is a real, applyable git diff (near-Option-B exactness for the file-edit
  mutation class), and
- fidelity validation is free: every ``old_string`` that fails to match checkout state
  is a DETECTED drift, classified per call (`ReplayOutcome`) rather than silently
  swallowed. ``replay_success_rate`` = APPLIED / total mutation calls is the bead-level
  fidelity number the P0 acceptance reports.

Replay mirrors Claude Code's own tool semantics: ``Edit`` is exact unique-substring
replacement (ambiguous or missing ``old_string`` fails unless ``replace_all``);
``Write`` is a full-file overwrite that creates the file (and parents); ``MultiEdit``
is a SEQUENTIAL edit list on one file, applied all-or-nothing. Transcript paths are
absolute in the ORIGINAL session's tree, so they are rebased onto the checkout via the
record's ``work_dir`` prefix; a path that does not normalize to inside ``work_dir``
(including ``..`` escapes) is the classified skip `OUTSIDE_WORK_DIR`, never a write
outside the checkout.

Mutation classes the transcript CANNOT carry (inherent fidelity bounds, surfaced as
drift on any later edit that touches their output rather than hidden):

- shell-mediated mutations (``Bash`` ``mv``/``rm``/``sed``/``git apply``/heredocs) --
  no structured path+content argument to replay (same exclusion as harbor_exec's
  ``_FILE_TOOLS`` harvest);
- codegen / build artifacts (files produced by running tools, not by edit calls);
- ``NotebookEdit`` cell operations (harvested as "written" by harbor_exec, but cell
  surgery is not replayable as text substitution -- excluded here);
- file deletions, renames and permission changes (no deleting tool call exists);
- tool calls that ERRORED in the original session: results are not correlated back to
  calls, so an originally-rejected Edit is replayed as-is and surfaces as a classified
  mismatch, slightly deflating the success rate rather than inflating it.

ZFC: pure mechanism -- IO, structural validation, string replacement and arithmetic.
No model calls, no semantic heuristics. The subprocess runner is injectable (the same
``Runner`` pattern as `membench.harbor.env_recon`) so the git-diff leg is testable
against real temp repos without monkeypatching.
"""

import json
import posixpath
import subprocess
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from membench.harbor.harbor_exec import _FILE_TOOLS

# The replayable subset of harbor_exec's file tools: structured path AND structured
# content. (Read carries no mutation; NotebookEdit mutates but not as text substitution.)
MutationTool = Literal["Edit", "Write", "MultiEdit"]
_MUTATION_TOOLS: tuple[MutationTool, ...] = ("Edit", "Write", "MultiEdit")

# A subprocess.run-shaped callable, injectable for testability (env_recon's pattern).
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ReplayOutcome(StrEnum):
    """Per-call replay classification. Never a bare boolean: a non-APPLIED call is a
    *detected* fidelity failure whose class feeds the P0 acceptance report."""

    APPLIED = "applied"
    OLD_STRING_MISSING = "old_string_missing"
    OLD_STRING_AMBIGUOUS = "old_string_ambiguous"
    FILE_ABSENT = "file_absent"
    OUTSIDE_WORK_DIR = "outside_work_dir"


class EditOp(BaseModel):
    """One exact-substring replacement (a single Edit, or one MultiEdit element)."""

    model_config = ConfigDict(frozen=True)

    old_string: str
    new_string: str
    replace_all: bool = False


class MutationCall(BaseModel):
    """One file-mutation tool call, in transcript order. ``edits`` carries Edit (one op)
    and MultiEdit (n ops); ``content`` carries Write's full-file payload."""

    model_config = ConfigDict(frozen=True)

    tool: MutationTool
    path: str
    edits: tuple[EditOp, ...] = ()
    content: str | None = None

    @model_validator(mode="after")
    def _payload_matches_tool(self) -> "MutationCall":
        if self.tool == "Write" and self.content is None:
            raise ValueError("Write call requires content")
        if self.tool != "Write" and not self.edits:
            raise ValueError(f"{self.tool} call requires at least one edit op")
        return self


class CallReplay(BaseModel):
    """The replay outcome of one `MutationCall`. ``rebased_path`` is None exactly when
    the outcome is `OUTSIDE_WORK_DIR`; ``detail`` localizes the failure (which MultiEdit
    op, how many ambiguous matches)."""

    model_config = ConfigDict(frozen=True)

    index: int
    tool: str
    path: str
    rebased_path: str | None
    outcome: ReplayOutcome
    detail: str = ""


class ReplayResult(BaseModel):
    """The structured replay product: per-call outcomes, the per-file gold diff (paths
    as git reports them, relative to the checkout root), and the fidelity rate.

    ``replay_success_rate`` for an empty call list is 0.0 by definition: a transcript
    with no file mutations yields no gold diff, and 0.0 marks it non-admittable rather
    than vacuously perfect."""

    model_config = ConfigDict(frozen=True)

    calls: tuple[CallReplay, ...]
    file_diffs: dict[str, str]
    replay_success_rate: float


def _mutation_call_from_block(name: MutationTool, args: Mapping[str, Any]) -> MutationCall:
    """Build the `MutationCall` for one tool_use block, validating structure loudly --
    a mutation call missing its payload is a malformed transcript, never a skip."""
    path_arg = _FILE_TOOLS[name][0]
    try:
        if name == "Write":
            return MutationCall(tool=name, path=args[path_arg], content=args["content"])
        if name == "Edit":
            ops: tuple[Mapping[str, Any], ...] = (args,)
        else:  # MultiEdit
            ops = tuple(args["edits"])
        edits = tuple(
            EditOp(
                old_string=op["old_string"],
                new_string=op["new_string"],
                replace_all=bool(op.get("replace_all", False)),
            )
            for op in ops
        )
        return MutationCall(tool=name, path=args[path_arg], edits=edits)
    except (KeyError, TypeError, ValidationError) as exc:
        raise ValueError(f"malformed {name} tool call (args {dict(args)!r}): {exc}") from exc


def parse_mutation_calls(stream: str) -> tuple[MutationCall, ...]:
    """The transcript's `Edit`/`Write`/`MultiEdit` calls, in order, with full content.

    Same .jsonl event walk as harbor_exec's `project_claude_stream` (``tool_use``
    blocks inside ``message.content``), but ORDERED and content-bearing where that
    harvest only aggregates path sets -- the bundle side needs the payloads to replay.
    Non-JSON lines and non-message events are skipped (matching the documented
    `project_claude_stream` tolerance); a malformed MUTATION call raises."""
    calls: list[MutationCall] = []
    for line in stream.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message") if isinstance(event, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name not in _MUTATION_TOOLS:
                continue
            args = block.get("input")
            if not isinstance(args, Mapping):
                raise ValueError(f"malformed {name} tool call: input is not a mapping")
            calls.append(_mutation_call_from_block(name, args))
    return tuple(calls)


def _rebase_path(path: str, work_dir: str, checkout_dir: Path) -> Path | None:
    """Map an original-session absolute path onto the checkout, or None when it does
    not normalize to inside ``work_dir`` (relative paths and ``..`` escapes included --
    a replay must never write outside the checkout)."""
    norm = PurePosixPath(posixpath.normpath(path))
    work = PurePosixPath(posixpath.normpath(work_dir))
    if not norm.is_absolute() or not norm.is_relative_to(work):
        return None
    return checkout_dir / norm.relative_to(work)


def _apply_edits(text: str, edits: tuple[EditOp, ...]) -> tuple[str, ReplayOutcome, str]:
    """Apply a sequential edit list to ``text`` (Claude Code Edit/MultiEdit semantics).

    Returns the resulting text only on full success -- the caller writes all-or-nothing,
    so a mid-sequence failure leaves the file untouched (MultiEdit atomicity)."""
    for i, op in enumerate(edits):
        occurrences = text.count(op.old_string)
        if occurrences == 0:
            return text, ReplayOutcome.OLD_STRING_MISSING, f"edit {i}: old_string not found"
        if occurrences > 1 and not op.replace_all:
            return (
                text,
                ReplayOutcome.OLD_STRING_AMBIGUOUS,
                f"edit {i}: old_string matches {occurrences} times without replace_all",
            )
        count = -1 if op.replace_all else 1
        text = text.replace(op.old_string, op.new_string, count)
    return text, ReplayOutcome.APPLIED, ""


def replay_call(call: MutationCall, index: int, *, checkout_dir: Path, work_dir: str) -> CallReplay:
    """Replay one mutation call against the checkout and classify the outcome."""
    rebased = _rebase_path(call.path, work_dir, checkout_dir)
    if rebased is None:
        return CallReplay(
            index=index,
            tool=call.tool,
            path=call.path,
            rebased_path=None,
            outcome=ReplayOutcome.OUTSIDE_WORK_DIR,
            detail=f"path does not normalize to inside work_dir {work_dir!r}",
        )
    if call.tool == "Write":
        assert call.content is not None  # guaranteed by MutationCall._payload_matches_tool
        rebased.parent.mkdir(parents=True, exist_ok=True)
        rebased.write_text(call.content, encoding="utf-8")
        outcome, detail = ReplayOutcome.APPLIED, ""
    elif not rebased.is_file():
        outcome, detail = ReplayOutcome.FILE_ABSENT, "file absent in checkout"
    else:
        new_text, outcome, detail = _apply_edits(rebased.read_text(encoding="utf-8"), call.edits)
        if outcome is ReplayOutcome.APPLIED:
            rebased.write_text(new_text, encoding="utf-8")
    return CallReplay(
        index=index,
        tool=call.tool,
        path=call.path,
        rebased_path=str(rebased),
        outcome=outcome,
        detail=detail,
    )


def _run_git(args: list[str], checkout_dir: Path, runner: Runner) -> str:
    completed = runner(
        ["git", "-C", str(checkout_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {args[0]} in {checkout_dir} failed (exit {completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def gold_diff(checkout_dir: Path, *, runner: Runner = subprocess.run) -> dict[str, str]:
    """The per-file gold diff of the replayed checkout vs its base commit.

    ``git add -N`` (intent-to-add) first, so files the replay CREATED appear in
    ``git diff`` alongside modifications. File names come from ``--name-only -z``
    (NUL-delimited -- robust to spaces), then one ``git diff -- <path>`` per file keeps
    the per-file split unambiguous instead of parsing combined-diff headers."""
    _run_git(["add", "-N", "."], checkout_dir, runner)
    names = _run_git(["diff", "--no-color", "--name-only", "-z"], checkout_dir, runner)
    return {
        name: _run_git(["diff", "--no-color", "--", name], checkout_dir, runner)
        for name in names.split("\0")
        if name
    }


def replay_transcript(
    stream: str,
    *,
    checkout_dir: Path,
    work_dir: str,
    runner: Runner = subprocess.run,
) -> ReplayResult:
    """Parse, replay in order, then diff: the P0 entry point.

    ``stream`` is the resolved transcript text (.jsonl); ``checkout_dir`` is the repo
    already checked out at ``base_commit``; ``work_dir`` is the original record's
    working directory (the rebase prefix)."""
    calls = parse_mutation_calls(stream)
    replays = tuple(
        replay_call(call, index, checkout_dir=checkout_dir, work_dir=work_dir)
        for index, call in enumerate(calls)
    )
    applied = sum(1 for r in replays if r.outcome is ReplayOutcome.APPLIED)
    return ReplayResult(
        calls=replays,
        file_diffs=gold_diff(checkout_dir, runner=runner),
        replay_success_rate=applied / len(replays) if replays else 0.0,
    )
