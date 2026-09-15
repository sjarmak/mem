#!/usr/bin/env python3
"""Stitch every session of a saved run into ONE captioned recording: cast, GIF and MP4.

Run from memory-bench:
  python scripts/render_trial_video.py RUN_DIR --out demo.cast [--video] [--audit audit.json]

``render_leg_cast`` replays one recorded ``claude -p`` session. This composes a whole run into a
single recording a viewer can watch end to end: an opening card naming the run, a chapter card
per trial saying what its condition changed, every session of the trial in plan order with a
label above each tool exchange saying what the agent did, and a closing card per trial with the
verdicts.

Every label is mechanical. It is read off the tool name, the path shape, the bd verb and key on
the receipt's argv, bd's own acknowledgement text, the receipt's exit code, and the manifest's
condition fields. No label is a judgment about the agent. The verdicts are the scorer's
(``trial_outcomes`` over the saved scores), or the audit's rescoring of them with ``--audit``.
"""

from __future__ import annotations

import argparse
import importlib.util
import shlex
import sys
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from membench.runner.e1_grid import guidance_block
from membench.runner.e1_reliability import TRIAL_VERDICTS, BdLegEvidence, trial_outcomes
from membench.runner.leg_plans import GOAL_ROLE
from membench.runner.tool_surface import (
    MEMORY_READ_VERBS,
    MEMORY_WRITE_VERBS,
    NATIVE_MEMORY_HOOK_MODE_REDIRECT,
    NATIVE_MEMORY_SEGMENT,
    NATIVE_MEMORY_TOOL_NAMES,
    NATIVE_MEMORY_WRITE_TOOLS,
    STORE_PREFIX,
    memory_command_is_direct,
    remember_ack_action,
)
from membench.schemas.trace import ToolCall


def _load_sibling(name: str) -> Any:
    """Load a sibling script as a module (the test-suite idiom; scripts are not a package)."""
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cast = _load_sibling("render_leg_cast")

COLS: int = cast.COLS
RESET: str = cast.RESET
BOLD: str = cast.BOLD
DIM: str = cast.DIM
YELLOW: str = cast.YELLOW
LABEL = "\x1b[30;43m"  # black on yellow: the label bar above a tool exchange
TITLE = "\x1b[1;37;44m"  # bold white on blue: a card title
CLEAR = "\x1b[2J\x1b[H"

# What each session of the plan is for, in the words of the step that runs it (``e1_grid``).
ROLE_PURPOSE: dict[str, str] = {
    "establish": (
        "The agent is told the PREVIOUS version of the facts and that a later session will act "
        "on them. Whether it stores them anywhere is up to it."
    ),
    "revise": (
        "A fresh session on the same store. It is told the facts CHANGED and shown both "
        "versions. What it does to the stored value is the observation."
    ),
    "goal": (
        "A fresh session on the same store, given the work and none of the facts. It has to "
        "find the current version and act on it."
    ),
    "stale_writer": (
        "A session that only ever knew the PREVIOUS version, resuming after the revision "
        "landed. What its write does is the observation."
    ),
}

# The question each trial verdict answers (``e1_reliability.trial_outcomes``).
VERDICT_QUESTION: dict[str, str] = {
    "capture_superseded": "session 1 stored the previous version in bd",
    "revision": "what session 2 did to the stored value",
    "revision_captured_current": "session 2 left the current version in bd",
    "retrieval_current_only": "session 3 read the current version and never the previous",
    "retrieval_states_superseded": "session 3 was shown the previous version",
    "goal_action_success": "session 3 did the work with the current values",
    "stale_write": "what session 4 did when it wrote",
    "after_rejection": "what session 4 did after a rejected write",
}


@dataclass(frozen=True)
class Trial:
    """One trial's sessions in plan order, with the run-layout identity they share."""

    condition: str
    work_id: str
    variant: str
    repeat: str
    legs: tuple[tuple[Path, dict[str, Any]], ...]

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.condition, self.work_id, self.variant, self.repeat)


def _wrap(text: str, *, indent: str = "  ", width: int = COLS) -> list[str]:
    return [indent + line for line in textwrap.wrap(text, width=width - len(indent)) or [""]]


def label_frame(lines: Sequence[str], *, hold: float = 1.5) -> Any:
    """A yellow bar carrying ``lines``, one wrapped line per row. The bar stays on screen while
    the command beneath it types out, so the hold is only the pause before typing starts."""
    rows = [
        f"{LABEL}  {row.ljust(COLS - 4)}  {RESET}"
        for line in lines
        for row in textwrap.wrap(line, width=COLS - 4) or [""]
    ]
    return cast.Frame("\n".join([*rows, ""]), hold=hold)


def card(title: str, body: Sequence[str], *, hold: float) -> Any:
    """A full-width card: the screen cleared, a titled rule, ``body`` wrapped beneath."""
    rule = f"{BOLD}{YELLOW}{'━' * COLS}{RESET}"
    head = f"{TITLE}  {title.ljust(COLS - 4)}  {RESET}"
    rows = [row for line in body for row in (_wrap(line) if line else [""])]
    return cast.Frame("\n".join([CLEAR + rule, head, rule, "", *rows, ""]), hold=hold)


# --------------------------------------------------------------------------------------
# labels above tool exchanges
# --------------------------------------------------------------------------------------


def is_native_memory_path(path: str) -> bool:
    """A path inside a harness-minted store (``membench-memory-*``) with a ``memory`` segment
    below it: the shape of Claude Code's own memory files, the same two conditions the scorer
    checks against the pinned config dir it has and this replay does not."""
    parts = Path(path).parts
    store = next((i for i, part in enumerate(parts) if part.startswith(STORE_PREFIX)), None)
    return store is not None and NATIVE_MEMORY_SEGMENT in parts[store + 1 :]


def _under(path: str, root: str) -> bool:
    if not root or not path:
        return False
    try:
        Path(path).relative_to(root)
    except ValueError:
        return False
    return True


def _key_of(argv: Sequence[str]) -> str:
    """The key a bd memory invocation named: ``--key K`` on a write, the positional on a read."""
    if "--key" in argv and argv.index("--key") + 1 < len(argv):
        return argv[argv.index("--key") + 1]
    if argv and argv[0] == "recall" and len(argv) > 1 and not argv[1].startswith("-"):
        return argv[1]
    return ""


def _verb_of(receipt: Mapping[str, Any]) -> str:
    argv = receipt.get("operation_argv") or []
    return str(argv[0]) if argv else "?"


def receipt_label(receipt: Mapping[str, Any]) -> str:
    """What one bd invocation did, from its receipt: the verb and key on the argv, the exit
    code, and for a write bd's own acknowledgement on stdout."""
    argv = [str(word) for word in receipt.get("operation_argv") or []]
    verb = _verb_of(receipt)
    key = _key_of(argv)
    code = receipt.get("returncode")
    shown = f"bd {verb} [{key}]" if key else f"bd {verb}"
    if verb == "remember":
        ack = remember_ack_action(str(receipt.get("stdout") or ""))
        if ack == "remembered":
            return f"{shown}: stored under a new key"
        if ack == "updated":
            return f"{shown}: OVERWROTE the value already stored under this key, in place"
        if ack == "recalled":
            return f"{shown}: a bare key, so bd read it back instead of writing"
        if code not in (0, None):
            return f"{shown}: bd refused the write (exit {code})"
        return f"{shown}: no acknowledgement from bd"
    if verb == "recall":
        return f"{shown}: read one exact key" if code == 0 else f"{shown}: exit {code}"
    if verb == "memories":
        return f"{shown}: searched the store" if code == 0 else f"{shown}: exit {code}"
    if verb == "prime":
        return f"{shown}: loaded the store's context into this session"
    return f"{shown}: exit {code}"


def _shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def call_labels(
    call: ToolCall, receipts: Sequence[Mapping[str, Any]], *, cwd: str, role: str
) -> list[str]:
    """The label lines for one tool exchange; empty when nothing mechanical can be said."""
    lines: list[str] = []
    memory_calls = [r for r in receipts if _verb_of(r) in MEMORY_READ_VERBS + MEMORY_WRITE_VERBS]
    if memory_calls and not memory_command_is_direct(call):
        lines.append(
            f"one shell command running {len(memory_calls)} bd memory call(s) alongside other "
            "shell work; the scorer leaves a compound command unattributed"
        )
    lines.extend(receipt_label(receipt) for receipt in receipts)
    path = str(call.arguments.get("file_path") or "")
    command = str(call.arguments.get("command") or "")
    if call.name in NATIVE_MEMORY_TOOL_NAMES and path:
        writes = call.name in NATIVE_MEMORY_WRITE_TOOLS
        if is_native_memory_path(path):
            what = "edits, in place," if call.name == "Edit" else ("writes" if writes else "reads")
            lines.append(f"{what} its own Claude Code memory file (native memory), not bd")
        elif _under(path, cwd):
            what = "writes" if writes else "reads"
            goal = (
                " (the work the goal session was asked to do)"
                if writes and role == GOAL_ROLE
                else ""
            )
            lines.append(f"{what} a file in the task sandbox{goal}")
    elif call.name == "Bash" and any(is_native_memory_path(t) for t in _shell_tokens(command)):
        lines.append(
            "a shell command reaching its own Claude Code memory files (native memory), not bd"
        )
    return lines


def make_captioner(leg: Mapping[str, Any]) -> Any:
    cwd = str(leg.get("cwd") or "")
    role = str(leg.get("role") or "")

    def caption(call: ToolCall, receipts: Sequence[Mapping[str, Any]]) -> Any:
        lines = call_labels(call, receipts, cwd=cwd, role=role)
        return label_frame(lines) if lines else None

    return caption


# --------------------------------------------------------------------------------------
# the run, grouped into trials
# --------------------------------------------------------------------------------------


def trials(run_dir: Path, manifest: Mapping[str, Any]) -> list[Trial]:
    """Every saved session under ``run_dir`` grouped by trial, trials in the manifest's condition
    order, sessions in the manifest's plan order."""
    conditions = manifest.get("conditions")
    order = [str(name) for name in conditions] if isinstance(conditions, Mapping) else []
    plan = [str(role) for role in manifest.get("leg_plan") or []]
    groups: dict[tuple[str, str, str, str], list[tuple[Path, dict[str, Any]]]] = {}
    for path in cast.run_legs(run_dir):
        leg = cast.read_object(path)
        identity = cast.leg_identity(leg, path)
        key = tuple(identity[name] for name in ("condition", "work_id", "variant", "repeat"))
        groups.setdefault((key[0], key[1], key[2], key[3]), []).append((path, leg))

    def condition_rank(name: str) -> tuple[int, str]:
        return (order.index(name), name) if name in order else (len(order), name)

    def role_rank(entry: tuple[Path, dict[str, Any]]) -> tuple[int, int]:
        role = str(entry[1].get("role", ""))
        leg = entry[1].get("leg")
        return (plan.index(role) if role in plan else len(plan), leg if isinstance(leg, int) else 0)

    ordered = sorted(
        groups.items(),
        key=lambda item: (condition_rank(item[0][0]), item[0][1], item[0][2], int(item[0][3] or 0)),
    )
    return [Trial(*key, legs=tuple(sorted(legs, key=role_rank))) for key, legs in ordered]


def condition_lines(name: str, fields: Mapping[str, Any]) -> list[str]:
    """What the condition set, read off its manifest fields."""
    lines = [f"condition: {name}", f"guidance rung in the prompt: {fields.get('rung', '?')}"]
    if fields.get("bd_context"):
        lines.append("the context block bd itself injects into a session is in the prompt")
    else:
        lines.append("no bd context in the prompt")
    if fields.get("native_memory_hook_mode") == NATIVE_MEMORY_HOOK_MODE_REDIRECT:
        lines.append(
            "a hook BLOCKS the agent's own Claude Code memory files and tells it to use bd instead"
        )
    else:
        lines.append(
            "a hook OBSERVES the agent's own Claude Code memory files and lets every access through"
        )
    return lines


def opening_card(run_dir: Path, manifest: Mapping[str, Any], shown: Sequence[Trial]) -> Any:
    sessions = sum(len(trial.legs) for trial in shown)
    conditions = manifest.get("conditions")
    names = ", ".join(str(name) for name in conditions) if isinstance(conditions, Mapping) else "?"
    plan = " → ".join(str(role) for role in manifest.get("leg_plan") or []) or "?"
    body = [
        f"{sessions} recorded claude -p sessions: {len(shown)} trial(s) of {plan}, each trial on "
        "one bd store that every session shares.",
        "Nothing is re-run. Every command, result and receipt is the recorded one, paced for "
        "reading.",
        "",
        f"bd: {manifest.get('bd_version', '?')}",
        f"Claude Code CLI: {manifest.get('cli_version', '?')}    "
        f"model: {manifest.get('model', '?')}",
        f"conditions: {names}",
        "",
        "Yellow bars above a tool call say what the agent did, read off the call and bd's receipt.",
        "The closing block of each session is the scorer's evidence; the card after each trial is "
        "its verdicts.",
    ]
    return card(f"bd version-history trial: {run_dir.name}", body, hold=12.0)


def chapter_card(index: int, total: int, trial: Trial, manifest: Mapping[str, Any]) -> Any:
    conditions = manifest.get("conditions")
    fields = conditions.get(trial.condition) if isinstance(conditions, Mapping) else None
    body = condition_lines(trial.condition, fields if isinstance(fields, Mapping) else {})
    body += ["", f"task: {trial.work_id}    variant: {trial.variant}    repeat: {trial.repeat}"]
    rung = str(fields.get("rung", "")) if isinstance(fields, Mapping) else ""
    guidance = guidance_block(rung) if rung else ""
    if guidance:
        body += ["", "the memory guidance in every session's prompt, verbatim:"]
        body += [row for line in guidance.splitlines() for row in _wrap(line, indent="    ")]
    roles = ", ".join(f"{k} {leg.get('role', '?')}" for k, (_, leg) in enumerate(trial.legs, 1))
    body += ["", f"sessions, in order: {roles}"]
    return card(f"Trial {index} of {total}: condition {trial.condition}", body, hold=12.0)


def session_card(index: int, total: int, leg: Mapping[str, Any]) -> Any:
    role = str(leg.get("role", ""))
    purpose = ROLE_PURPOSE.get(role, "")
    return card(f"Session {index} of {total}: {role}", [purpose] if purpose else [], hold=6.0)


def _verdict_text(name: str, verdicts: Mapping[str, Any]) -> str:
    value = verdicts.get(name)
    if name == "after_rejection" and verdicts.get("stale_write") not in (None, "rejected"):
        # ``trial_outcomes`` leaves this None whenever no write was rejected: nothing to ask.
        return "not applicable (no write was rejected)"
    if value is None:
        return "unknown (not observed by this scorer)"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


def saved_verdicts(trial: Trial) -> tuple[str, dict[str, Any]]:
    """The verdicts ``trial_outcomes`` derives from the scores saved beside the sessions."""
    by_role: dict[str, BdLegEvidence] = {}
    versions: set[int] = set()
    for _, leg in trial.legs:
        evidence = leg.get("bd_evidence")
        if isinstance(evidence, Mapping):
            scored = BdLegEvidence.model_validate(evidence)
            by_role[scored.role] = scored
            versions.add(scored.scoring_version)
    version = ", ".join(f"v{v}" for v in sorted(versions)) or "none"
    return f"derived from the scores saved at run time (scorer {version})", trial_outcomes(by_role)


def audit_verdicts(trial: Trial, report: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The verdicts the audit rescored for this trial, or a labelled absence."""
    version = report.get("audit_version")
    for pair in report.get("pairs") or []:
        if not isinstance(pair, Mapping):
            continue
        key = tuple(
            str(pair.get(name, "")) for name in ("condition", "work_id", "variant", "repeat")
        )
        if key == trial.key and isinstance(pair.get("trial_rescored"), Mapping):
            return f"rescored by audit_bd_actions (audit v{version})", dict(pair["trial_rescored"])
    return f"this trial is not in the audit report (audit v{version})", {}


def verdict_card(trial: Trial, source: str, verdicts: Mapping[str, Any]) -> Any:
    body = [
        f"task: {trial.work_id}    variant: {trial.variant}    repeat: {trial.repeat}",
        source,
        "",
    ]
    width = max(len(question) for question in VERDICT_QUESTION.values())
    for name in TRIAL_VERDICTS:
        question = VERDICT_QUESTION.get(name, name)
        body.append(f"{question.ljust(width)}   {_verdict_text(name, verdicts)}")
    return card(f"Verdicts, condition {trial.condition}", body, hold=14.0)


def demo_frames(
    run_dir: Path,
    *,
    manifest: Mapping[str, Any],
    limit: int,
    overlay: Any = None,
    audit_report: Mapping[str, Any] | None = None,
) -> list[Any]:
    shown = trials(run_dir, manifest)
    if not shown:
        raise ValueError(f"No saved legs under {run_dir}")
    out = [opening_card(run_dir, manifest, shown)]
    for index, trial in enumerate(shown, 1):
        out.append(chapter_card(index, len(shown), trial, manifest))
        for k, (path, leg) in enumerate(trial.legs, 1):
            out.append(session_card(k, len(trial.legs), leg))
            rescored = cast._rescored(cast.leg_identity(leg, path), overlay)
            out.extend(
                cast.frames(
                    leg, path=path, limit=limit, rescored=rescored, caption=make_captioner(leg)
                )
            )
        source, verdicts = (
            audit_verdicts(trial, audit_report)
            if audit_report is not None
            else saved_verdicts(trial)
        )
        out.append(verdict_card(trial, source, verdicts))
    return out


def render_demo(
    run_dir: Path,
    out: Path,
    *,
    speed: float,
    limit: int,
    video: bool,
    audit: Path | None = None,
) -> Path:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"No manifest.json under {run_dir}")
    manifest = cast.read_object(manifest_path)
    overlay = cast.load_audit(audit) if audit is not None else None
    report = cast.read_object(audit) if audit is not None else None
    shown = demo_frames(
        run_dir, manifest=manifest, limit=limit, overlay=overlay, audit_report=report
    )
    lines: list[str] = cast.cast_from_frames(
        shown, title=f"bd version-history trial: {run_dir.name}", speed=speed
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    if video:
        # agg caps every pause at its idle limit; the cards hold longer than its default.
        cast.write_video(out, idle_limit=max(frame.hold for frame in shown) / speed)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="a run directory with manifest.json and pairs/")
    parser.add_argument("--out", type=Path, required=True, help="the .cast to write")
    parser.add_argument("--speed", type=float, default=1.0, help="replay pace multiplier")
    parser.add_argument("--max-lines", type=int, default=cast.MAX_RESULT_LINES)
    parser.add_argument("--video", action="store_true", help="also render .gif and .mp4")
    parser.add_argument(
        "--audit", type=Path, help="an audit.json whose rescored evidence and verdicts to show"
    )
    args = parser.parse_args(argv)
    written = render_demo(
        args.run_dir,
        args.out,
        speed=args.speed,
        limit=args.max_lines,
        video=args.video,
        audit=args.audit,
    )
    print(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
