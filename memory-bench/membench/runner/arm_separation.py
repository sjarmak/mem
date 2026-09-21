"""The pre-registration's §5 artifact: what the three arms differ in, proved before any spend.

Every claim §5 makes is a property of a MINT, so this module buys nothing. It mints each arm,
reads the separation off the minted surface, fires the deny hook against the transcript-read
battery, and writes the result as one JSON document for the pre-registration to cite.

Written rather than asserted-only because the paid run has to be able to say WHICH separation it
ran under. A gate that only ever turned green leaves the reader nothing to compare a later run
against; a document with the settings bytes, the PATH entries, the planted context and the
spellings blocked can be diffed against the next one.

The spelling battery lives HERE and the hook test imports it, one definition: a report that
proved a narrower set than the suite checks would be a report claiming a gate it never ran.

ZFC: filesystem reads, a PATH lookup, a subprocess exit code. No model call, no judgment.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from membench.runner.arm_context import capability_of, context_words, scaffold_of
from membench.runner.beads_arm_grid import (
    COMMAND_NOT_FOUND,
    SHARED_SYSTEM_PATH,
    arm_cell_store,
    child_path_of,
)
from membench.runner.headless_agent import MemoryChannel
from membench.runner.memory_arm import ARM_NAMES, SHARED_PROTOCOL, arm, arm_settings_fingerprint
from membench.runner.native_memory_hook import (
    SETTINGS_NAME,
    hook_reaches,
    install_native_memory_hook,
)
from membench.runner.resume_cache import digest
from membench.runner.tool_surface import (
    BD_CONTEXT_FILES,
    MEMORY_COMMAND,
    NATIVE_MEMORY_HOOK_EVENT,
    NATIVE_MEMORY_HOOK_EXIT_BLOCK,
    NATIVE_MEMORY_HOOK_MODE_DENY,
    NATIVE_MEMORY_HOOK_SCRIPT_NAME,
)

# The token planted in the fake transcript. Distinctive so a grep spelling has something to find
# and a stray match elsewhere is visible for what it is.
TRANSCRIPT_TOKEN = "zt-7Q4W-KEEPOUT"

# Every idiomatic way an agent reaches a file, as a `{path}` / `{parent}` / `{config_dir}`
# template. The backtick row is not decoration: it is the spelling that got past the recognizer
# until `tool_surface` made the backtick a segment break, and it is here so a regression shows up
# in the artifact and not only in the suite.
TRANSCRIPT_READ_SPELLINGS: tuple[tuple[str, str], ...] = (
    ("plain cat", "cat {path}"),
    ("command substitution", "echo $(cat {path})"),
    ("backticks", "echo `cat {path}`"),
    ("sed range", "sed -n 1,5p {path}"),
    ("env wrapper", "env cat {path}"),
    ("head through a pipe", "cat {path} | head -1"),
    ("recursive grep over the pin", "grep -r " + TRANSCRIPT_TOKEN + " {config_dir}"),
    ("cd then a relative read", "cd {parent} && cat sess-01.jsonl"),
    (
        "the pinned variable",
        'cat "$CLAUDE_CONFIG_DIR/projects/-home-ds-projects-mem/sess-01.jsonl"',
    ),
)


class ArmSeparationError(RuntimeError):
    """A separation the mint could not demonstrate. Raised rather than reported: a report that
    recorded its own failure as a field would be cited as evidence of the separation it denies."""


def _plant_transcript(config_dir: Path) -> Path:
    path = config_dir / "projects" / "-home-ds-projects-mem" / "sess-01.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": f"the value is {TRANSCRIPT_TOKEN}"}) + "\n", "utf-8")
    return path


def fire_hook(config_dir: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    """Run the installed hook exactly as the CLI runs it: its own command line out of
    `settings.json`, the event JSON on stdin."""
    settings = json.loads((config_dir / SETTINGS_NAME).read_text("utf-8"))
    command = settings["hooks"][NATIVE_MEMORY_HOOK_EVENT][0]["hooks"][0]["command"]
    if not shlex.split(command)[-1].endswith(NATIVE_MEMORY_HOOK_SCRIPT_NAME):
        raise ArmSeparationError(f"the settings name a hook this module did not install: {command}")
    return subprocess.run(
        command, shell=True, input=json.dumps(payload), capture_output=True, text=True, timeout=60
    )


def deny_battery(root: Path) -> list[dict[str, Any]]:
    """Fire every spelling at a freshly installed `deny` hook and record what it did.

    A fresh config dir per spelling, so the log a row reports is that row's own; a shared one
    would let an earlier block stand in for a later allow."""
    rows: list[dict[str, Any]] = []
    for index, (label, template) in enumerate(TRANSCRIPT_READ_SPELLINGS):
        config_dir = root / f"deny-{index}"
        config_dir.mkdir(parents=True, exist_ok=True)
        transcript = _plant_transcript(config_dir)
        log = install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_DENY)
        command = template.format(path=transcript, parent=transcript.parent, config_dir=config_dir)
        done = fire_hook(
            config_dir,
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s-deny"},
        )
        blocked = done.returncode == NATIVE_MEMORY_HOOK_EXIT_BLOCK
        recorded = bool(hook_reaches(log))
        if not (blocked and recorded):
            raise ArmSeparationError(
                f"the deny hook did not stop {label!r} ({command}): exit {done.returncode}, "
                f"{'no' if not recorded else 'a'} reach recorded. The floor arm's transcript is "
                "readable, so the leak this gate exists to close is open."
            )
        rows.append(
            {
                "label": label,
                "command": command,
                "exit_code": done.returncode,
                "reach_recorded": recorded,
                # The refusal text, verbatim: the floor arm must not be coached, so what the hook
                # says is part of the separation and not an implementation detail.
                "stderr": done.stderr.strip(),
            }
        )
    return rows


def _path_shape(env: Mapping[str, str], store: Any) -> list[str]:
    """One arm's child PATH with its per-cell directories replaced by their role.

    The bd arm and the floor arms mint different temp roots, so their PATHs can never be equal
    byte for byte even when they are the same PATH. What must be equal is the SHAPE: same number
    of entries, same roles in the same order, same system directories -- and, from the separate
    `shared_toolchain` listing, the same tools reachable through them. A difference here is a
    difference in what tooling the arm HAS, which is the confound this shape exists to catch."""
    roles = {str(store.surface.bin_dir): "<cell>/bin", str(store.toolchain): "<cell>/toolchain"}
    return [roles.get(entry, entry) for entry in child_path_of(env)]


def arm_rows(task: Any, *, model: str, channel: MemoryChannel) -> list[dict[str, Any]]:
    """One row per arm, each read off that arm's own minted surface."""
    from membench.runner.beads_arm_grid import arm_cell_calls

    rows: list[dict[str, Any]] = []
    for name in ARM_NAMES:
        with arm_cell_store(name, label="separation") as store:
            settings_text = (store.config_dir / SETTINGS_NAME).read_text(encoding="utf-8")
            context = (store.sandbox / BD_CONTEXT_FILES[0]).read_text(encoding="utf-8")
            env = store.env()
            resolved = shutil.which(MEMORY_COMMAND, path=env.get("PATH", ""))
            stub_exit = None
            if resolved is not None and not store.arm.provisions_bd:
                stub_exit = subprocess.run(
                    [resolved], capture_output=True, text=True, timeout=60
                ).returncode
                if stub_exit != COMMAND_NOT_FOUND:
                    raise ArmSeparationError(
                        f"{name}: the planted {MEMORY_COMMAND} exited {stub_exit}, not "
                        f"{COMMAND_NOT_FOUND}, so this arm's error surface is not a missing "
                        "command's and the agent can tell the arms apart by how they fail."
                    )
            rows.append(
                {
                    "arm": name,
                    "hook_mode": store.arm.hook_mode,
                    "provisions_bd": store.arm.provisions_bd,
                    "pinned_off": store.pinned_off,
                    "settings": json.loads(settings_text),
                    "settings_digest": digest(settings_text),
                    "child_path": child_path_of(env),
                    # The per-cell directories are named by ROLE, not by their temp path, so the
                    # three arms' PATHs can be compared as the shapes they are. Raw entries are
                    # kept above for the reader; the gate below runs on this.
                    "child_path_shape": _path_shape(env, store),
                    "shared_toolchain": sorted(entry.name for entry in store.toolchain.iterdir()),
                    "memory_command_resolves_to": resolved,
                    "memory_command_exit": stub_exit,
                    "establish_tools": list(store.arm.establish_tools),
                    "context_files": list(store.context_files),
                    "context_digest": digest(context),
                    "scaffold_digest": digest(scaffold_of(context)),
                    "capability_words": context_words(capability_of(context)),
                    "goal_call": arm_cell_calls(task, name, channel, model=model).calls[-1],
                }
            )
    return rows


def render_arm_separation(out: Path, task: Any, *, root: Path, model: str = "sonnet") -> Path:
    """Write the §5 artifact and return its path. `root` holds the per-spelling config dirs."""
    channel = MemoryChannel.TRUSTED
    rows = arm_rows(task, model=model, channel=channel)

    scaffolds = {row["scaffold_digest"] for row in rows}
    if len(scaffolds) != 1:
        raise ArmSeparationError(
            f"the arms were planted with {len(scaffolds)} different scaffolds; the presentation "
            "is supposed to differ in the capability paragraph and nowhere else."
        )
    goal_calls = {json.dumps(row["goal_call"], sort_keys=True) for row in rows}
    if len(goal_calls) != 1:
        raise ArmSeparationError(
            "the goal leg's command line differs across the arms, so they differ in what they "
            "were ASKED as well as in what they could remember."
        )
    shapes = {json.dumps(row["child_path_shape"]) for row in rows}
    if len(shapes) != 1:
        raise ArmSeparationError(
            "the arms' child PATHs differ in shape, so they differ in what tooling they can "
            f"reach and not only in the memory they have: {sorted(shapes)}"
        )
    toolchains = {json.dumps(row["shared_toolchain"]) for row in rows}
    if len(toolchains) != 1:
        raise ArmSeparationError(f"the arms reach different shared tools: {sorted(toolchains)}")
    leaked = sorted(
        entry
        for row in rows
        for entry in row["child_path_shape"]
        if not entry.startswith("<cell>/") and entry not in SHARED_SYSTEM_PATH
    )
    if leaked:
        raise ArmSeparationError(
            "an arm's child PATH carries a directory that is neither its own nor a shared "
            f"system directory, so the operator's own toolchain is reachable from it: {leaked}"
        )
    settings = {row["settings_digest"] for row in rows}
    if len(settings) != len(rows):
        raise ArmSeparationError("two arms seeded identical settings, so they are one arm.")

    document = {
        "shared_protocol": {
            field: getattr(SHARED_PROTOCOL, field)
            for field in ("corpus", "goal_tools", "scorer", "model", "cli_version")
        }
        | {
            "goal_tools": list(SHARED_PROTOCOL.goal_tools),
            "timeout_s": SHARED_PROTOCOL.timeout_s,
            "sandbox_policy": SHARED_PROTOCOL.sandbox_policy,
        },
        "arms": rows,
        "goal_call_is_identical_across_arms": True,
        "child_path_is_identical_across_arms": True,
        "shared_system_path": list(SHARED_SYSTEM_PATH),
        "scaffold_is_identical_across_arms": True,
        "deny_battery": deny_battery(root),
        "verbs_counted": list(arm("beads").verbs),
        "registry_fingerprint": arm_settings_fingerprint(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
