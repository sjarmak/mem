#!/usr/bin/env python3
"""Render saved harness legs as asciinema v2 recordings, optionally as GIF and MP4.

Run from memory-bench:
  python scripts/render_leg_cast.py LEG.json --out leg.cast [--video]
  python scripts/render_leg_cast.py RUN_DIR --out recordings/ [--video] [--audit audit.json]

A saved leg is the transcript of one real ``claude -p`` session: every tool call, every tool
result, and the receipt the bd wrapper wrote for every bd invocation. That stream carries no
per-event clock, so the replay is PACED, not timed: each exchange is shown at a reading cadence
and the CLI-reported duration is printed in the closing line. Nothing is re-run; every command,
result and receipt shown is the recorded one, verbatim after the harness's credential redaction.

The closing block shows the score saved beside the session at run time. With ``--audit``, the
rescored evidence from ``audit_bd_actions`` is shown above it, each block labelled with the
scorer version that produced it, so a recording made after a scorer fix says which is which.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import textwrap
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from membench.runner.headless_agent import tool_calls_from_stream
from membench.schemas.trace import ToolCall

COLS = 120
ROWS = 30
MAX_RESULT_LINES = 12
TOOL_TIMEOUT_S = 600

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
MAGENTA = "\x1b[35m"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
# audit.json's rescored evidence keyed the way a leg's banner identifies it:
# (condition, work_id, variant, repeat, role).
AuditOverlay = dict[tuple[str, str, str, str, str], dict[str, Any]]


@dataclass(frozen=True)
class Frame:
    """One thing the replay prints, how long it lingers, and whether it is typed out."""

    text: str
    hold: float
    typed: bool = False


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _events(stream: str) -> Iterator[dict[str, Any]]:
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _wrap(text: str, *, indent: str = "", width: int = COLS) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        wrapped = textwrap.wrap(paragraph, width=width - len(indent)) or [""]
        lines.extend(indent + line for line in wrapped)
    return lines


def _clip(lines: Sequence[str], limit: int) -> list[str]:
    if len(lines) <= limit:
        return list(lines)
    return [*lines[:limit], f"{DIM}… (+{len(lines) - limit} lines){RESET}"]


def _receipts_by_use(leg: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The finishing receipt of every bd invocation, keyed by the tool call that made it."""
    finished: dict[str, dict[str, Any]] = {}
    for receipt in leg.get("bd_receipts") or ():
        if isinstance(receipt, dict) and receipt.get("event") == "finish":
            use = receipt.get("tool_use_id")
            if isinstance(use, str):
                finished[use] = receipt
    return finished


def _identity(leg: Mapping[str, Any], path: Path | None) -> dict[str, str]:
    """What the banner names. Condition and repeat live in the run layout
    (``pairs/<condition>/<hash>/<repeat>/legs/<leg>.json``), not in the row."""
    parts = path.resolve().parts if path is not None else ()
    laid_out = len(parts) >= 6 and parts[-2] == "legs" and parts[-6] == "pairs"
    return {
        "condition": parts[-5] if laid_out else "",
        "repeat": parts[-3] if laid_out else "",
        "role": str(leg.get("role", "")),
        "leg": str(leg.get("leg", "")),
        "variant": str(leg.get("variant", "")),
        "work_id": str(leg.get("work_id", "")),
        "rung": str(leg.get("rung", "")),
        "status": str(leg.get("status", "")),
    }


def _banner(identity: Mapping[str, str]) -> Frame:
    label = f" {identity['role']} session " if identity["role"] else " session "
    head = f"{BOLD}{YELLOW}{'━' * 3}{label}{'━' * max(0, COLS - 3 - len(label))}{RESET}"
    facts = [
        ("condition", identity["condition"]),
        ("task", identity["work_id"]),
        ("variant", identity["variant"]),
        ("leg", identity["leg"]),
        ("repeat", identity["repeat"]),
        ("guidance rung", identity["rung"]),
    ]
    rows = [f"{DIM}{name:>14}{RESET}  {value}" for name, value in facts if value]
    note = f"{DIM}replay of a recorded claude -p session; paced for reading, nothing re-run{RESET}"
    return Frame("\n".join([head, *rows, note, ""]), hold=3.0)


def _call_line(call: ToolCall) -> str:
    if call.name == "Bash":
        return f"{CYAN}${RESET} {call.arguments.get('command', '')}"
    if call.name in ("Write", "Edit", "Read"):
        return f"{CYAN}>{RESET} {call.name} {call.arguments.get('file_path', '')}"
    return f"{CYAN}>{RESET} {call.name} {json.dumps(call.arguments, sort_keys=True)[:COLS - 8]}"


def _call_body(call: ToolCall, limit: int) -> list[str]:
    content = call.arguments.get("content")
    if call.name == "Write" and isinstance(content, str):
        return _clip(_wrap(content, indent="    "), limit)
    return []


def _receipt_line(receipt: Mapping[str, Any] | None) -> str | None:
    if receipt is None:
        return None
    argv = receipt.get("operation_argv")
    code = receipt.get("returncode")
    colour = GREEN if code == 0 else RED
    shown = shlex.join(str(word) for word in argv) if isinstance(argv, list) else ""
    return f"{DIM}  bd receipt:{RESET} {colour}exit {code}{RESET}  {DIM}bd {shown}{RESET}"


def _result_frame(call: ToolCall, receipt: Mapping[str, Any] | None, limit: int) -> Frame:
    if call.result is None:
        lines = [f"{DIM}  (no result recorded: the stream ended before the tool returned){RESET}"]
    else:
        colour = RED if call.is_error else ""
        lines = _clip(_wrap(call.result, indent="  "), limit)
        lines = [f"{colour}{line}{RESET}" if colour else line for line in lines]
    receipt_line = _receipt_line(receipt)
    if receipt_line:
        lines.append(receipt_line)
    return Frame("\n".join([*lines, ""]), hold=0.9 + 0.08 * len(lines))


def _say_frame(text: str) -> Frame:
    lines = _wrap(text.strip())
    words = len(text.split())
    return Frame("\n".join([*lines, ""]), hold=min(4.0, 0.6 + 0.05 * words))


def _cli_summary(event: Mapping[str, Any]) -> str:
    duration = event.get("duration_ms") or event.get("duration_api_ms")
    raw_usage = event.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    parts = []
    if isinstance(duration, (int, float)):
        parts.append(f"{duration / 1000:.0f}s reported by the CLI")
    if isinstance(usage.get("output_tokens"), int):
        parts.append(f"{usage['output_tokens']} output tokens")
    if isinstance(event.get("total_cost_usd"), (int, float)):
        parts.append(f"${event['total_cost_usd']:.3f} estimated")
    return "  ·  ".join(parts)


def _evidence_lines(evidence: Mapping[str, Any]) -> list[str]:
    """One score block: the per-call outcomes when the scorer recorded them, else the leg's
    booleans. This is what cell.json tallies."""
    lines = []
    for call in evidence.get("bd_calls") or ():
        key = f" [{call['key']}]" if call.get("key") else ""
        stated = [
            *(f"current {v}" for v in call.get("current_values", ())),
            *(f"superseded {v}" for v in call.get("superseded_values", ())),
        ]
        detail = f"  {DIM}({', '.join(stated)}){RESET}" if stated else ""
        colour = GREEN if call["outcome"] in ("remembered", "updated", "returned") else MAGENTA
        lines.append(f"  bd {call['verb']}{key} → {colour}{call['outcome']}{RESET}{detail}")
    flags = ("bd_capture_complete", "bd_recall_complete", "goal_action_success")
    shown = [f"{flag}={evidence[flag]}" for flag in flags if evidence.get(flag) is not None]
    if shown:
        lines.append(f"{DIM}  {'  '.join(shown)}{RESET}")
    return lines or [f"{DIM}  no bd calls in this session{RESET}"]


def _scored_block(label: str, evidence: Mapping[str, Any] | None) -> list[str]:
    if evidence is None:
        return [f"  {label} {DIM}(no saved score on this row){RESET}"]
    version = evidence.get("scoring_version", 0)
    return [f"  {label} {DIM}(scorer v{version}){RESET}", *_evidence_lines(evidence)]


def _closing(
    leg: Mapping[str, Any],
    identity: Mapping[str, str],
    summary: str,
    rescored: Mapping[str, Any] | None,
) -> Frame:
    status = identity["status"]
    colour = GREEN if status == "ok" else RED
    head = f"{BOLD}{YELLOW}{'━' * 3} scored {'━' * (COLS - 10)}{RESET}"
    saved = leg.get("bd_evidence")
    saved = saved if isinstance(saved, Mapping) else None
    lines = [head, f"  session status: {colour}{status}{RESET}"]
    if rescored is not None:
        lines += _scored_block(f"{BOLD}rescored by audit{RESET}", rescored)
        lines += _scored_block("saved at run time", saved)
    else:
        lines += _scored_block("saved score", saved)
    if summary:
        lines.append(f"{DIM}  {summary}{RESET}")
    return Frame("\n".join([*lines, ""]), hold=4.0)


def frames(
    leg: Mapping[str, Any],
    *,
    path: Path | None = None,
    limit: int,
    rescored: Mapping[str, Any] | None = None,
) -> list[Frame]:
    """The replay, in wire order: banner, each assistant turn and tool exchange, the score."""
    stream = str(leg.get("stream", ""))
    calls = {c.tool_use_id: c for c in tool_calls_from_stream(stream) if c.tool_use_id}
    receipts = _receipts_by_use(leg)
    identity = _identity(leg, path)
    out = [_banner(identity)]
    summary = ""
    for event in _events(stream):
        if event.get("type") == "result":
            summary = _cli_summary(event)
        message = event.get("message") if event.get("type") == "assistant" else None
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text", "")).strip():
                out.append(_say_frame(str(block["text"])))
            elif block.get("type") == "tool_use" and block.get("id") in calls:
                call = calls[str(block["id"])]
                body = _call_body(call, limit)
                out.append(Frame(_call_line(call), hold=0.4, typed=True))
                if body:
                    out.append(Frame("\n".join(body), hold=0.3))
                out.append(_result_frame(call, receipts.get(call.tool_use_id or ""), limit))
    out.append(_closing(leg, identity, summary, rescored))
    return out


def cast_lines(
    leg: Mapping[str, Any],
    *,
    path: Path | None = None,
    speed: float = 1.0,
    limit: int,
    rescored: Mapping[str, Any] | None = None,
) -> list[str]:
    """An asciinema v2 recording: a header line, then ``[time, "o", text]`` events."""
    if speed <= 0:
        raise ValueError("speed must be positive")
    identity = _identity(leg, path)
    title = " ".join(
        part
        for part in (
            identity["condition"],
            identity["role"],
            identity["work_id"],
            identity["variant"],
        )
        if part
    )
    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "title": title,
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
    }
    lines = [json.dumps(header)]
    clock = 0.0

    def emit(text: str, hold: float) -> None:
        nonlocal clock
        lines.append(json.dumps([round(clock, 3), "o", text.replace("\n", "\r\n")]))
        clock += hold / speed

    for frame in frames(leg, path=path, limit=limit, rescored=rescored):
        if frame.typed:
            step = min(0.03, 1.5 / max(1, len(frame.text)))
            for char in frame.text:
                emit(char, step)
            emit("\r\n", frame.hold)
        else:
            emit(frame.text + "\r\n", frame.hold)
    return lines


def _checked(run: Runner, argv: Sequence[str]) -> None:
    completed = run(list(argv), capture_output=True, text=True, timeout=TOOL_TIMEOUT_S)
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError(f"{argv[0]} exited {completed.returncode}: {' | '.join(tail)}")


def write_video(cast: Path, *, run: Runner = subprocess.run) -> tuple[Path, Path]:
    """Render ``cast`` to a GIF beside it with agg, then to an H.264 MP4 with ffmpeg."""
    for tool in ("agg", "ffmpeg"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} is not installed; cannot render video")
    gif = cast.with_suffix(".gif")
    mp4 = cast.with_suffix(".mp4")
    _checked(
        run,
        [
            "agg",
            *("--cols", str(COLS), "--rows", str(ROWS)),
            *("--font-size", "24", "--theme", "monokai"),
            *("--idle-time-limit", "2", "--last-frame-duration", "2"),
            str(cast),
            str(gif),
        ],
    )
    _checked(
        run,
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(gif),
            *("-movflags", "faststart", "-pix_fmt", "yuv420p"),
            *("-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"),
            str(mp4),
        ],
    )
    return gif, mp4


def load_audit(path: Path) -> AuditOverlay:
    """The rescored evidence an ``audit_bd_actions`` report carries per pair and role."""
    report = read_object(path)
    pairs = report.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError(f"No pairs in audit report: {path}")
    overlay: AuditOverlay = {}
    for pair in pairs:
        legs = pair.get("rescored_legs")
        if not isinstance(legs, dict):
            raise ValueError(
                f"Audit report predates leg rescoring "
                f"(audit_version {report.get('audit_version')}): {path}"
            )
        for role, evidence in legs.items():
            if not isinstance(evidence, dict):
                raise ValueError(f"Rescored evidence for {role} is not an object: {path}")
            key = tuple(str(pair[name]) for name in ("condition", "work_id", "variant", "repeat"))
            overlay[(*key, str(role))] = evidence
    return overlay


def _rescored(identity: Mapping[str, str], audit: AuditOverlay | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    names = ("condition", "work_id", "variant", "repeat", "role")
    return audit.get(tuple(identity[name] for name in names))


def render_leg(
    source: Path,
    out: Path,
    *,
    speed: float,
    limit: int,
    video: bool,
    audit: AuditOverlay | None = None,
) -> Path:
    leg = read_object(source)
    rescored = _rescored(_identity(leg, source), audit)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = cast_lines(leg, path=source, speed=speed, limit=limit, rescored=rescored)
    out.write_text("\n".join(lines) + "\n")
    if video:
        write_video(out)
    return out


def run_legs(run_dir: Path) -> list[Path]:
    return sorted(run_dir.glob("pairs/*/*/*/legs/*.json"))


def _cast_name(leg_path: Path) -> str:
    identity = _identity(read_object(leg_path), leg_path)
    return (
        "__".join(
            [
                identity["condition"],
                identity["work_id"],
                identity["variant"],
                f"r{identity['repeat']}",
                f"{identity['leg']}-{identity['role']}",
            ]
        )
        + ".cast"
    )


def render_run(
    run_dir: Path,
    out_dir: Path,
    *,
    speed: float,
    limit: int,
    video: bool,
    audit: AuditOverlay | None = None,
) -> list[Path]:
    legs = run_legs(run_dir)
    if not legs:
        raise ValueError(f"No saved legs under {run_dir}")
    return [
        render_leg(
            leg, out_dir / _cast_name(leg), speed=speed, limit=limit, video=video, audit=audit
        )
        for leg in legs
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="a saved leg .json, or a run directory")
    parser.add_argument("--out", type=Path, required=True, help="a .cast path, or a directory")
    parser.add_argument("--speed", type=float, default=1.0, help="replay pace multiplier")
    parser.add_argument("--max-lines", type=int, default=MAX_RESULT_LINES)
    parser.add_argument("--video", action="store_true", help="also render .gif and .mp4")
    parser.add_argument(
        "--audit", type=Path, help="an audit.json whose rescored evidence to show beside the saved"
    )
    args = parser.parse_args(argv)
    audit = load_audit(args.audit) if args.audit else None
    if args.source.is_dir():
        written = render_run(
            args.source,
            args.out,
            speed=args.speed,
            limit=args.max_lines,
            video=args.video,
            audit=audit,
        )
    else:
        written = [
            render_leg(
                args.source,
                args.out,
                speed=args.speed,
                limit=args.max_lines,
                video=args.video,
                audit=audit,
            )
        ]
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
