"""The PreToolUse interception: does the harness see a native-memory reach as it happens, and can
it answer that reach with the bd verbs instead?

Every test here drives the hook the way the CLI would — a JSON payload on stdin of the script that
was actually installed — rather than calling `hook_decision` in-process. The script body, the
`sys.path` it re-enters this package through, and the settings entry that points the CLI at it are
each part of the mechanism, and a test that skipped them would pass for a hook the CLI could not
run."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from membench.runner.arm_separation import TRANSCRIPT_READ_SPELLINGS, TRANSCRIPT_TOKEN
from membench.runner.native_memory_hook import (
    hook_reaches,
    install_native_memory_hook,
    redirect_reason,
)
from membench.runner.tool_surface import (
    CONFIG_DIR_LEAK_EXEMPT_NAMES,
    MEMORY_COMMAND,
    MEMORY_READ_VERBS,
    MEMORY_WRITE_VERBS,
    NATIVE_MEMORY_HOOK_DENY_REASON,
    NATIVE_MEMORY_HOOK_EVENT,
    NATIVE_MEMORY_HOOK_EXIT_ALLOW,
    NATIVE_MEMORY_HOOK_EXIT_BLOCK,
    NATIVE_MEMORY_HOOK_MODE_DENY,
    NATIVE_MEMORY_HOOK_MODE_OBSERVE,
    NATIVE_MEMORY_HOOK_MODE_REDIRECT,
    NATIVE_MEMORY_HOOK_MODES,
    NATIVE_MEMORY_HOOK_SCRIPT_NAME,
    MemoryToolError,
    config_dir_leak_accesses,
    native_memory_accesses,
    recognizer_policy,
    surface_fingerprint,
)
from membench.schemas.trace import ToolCall


def _fire(config_dir: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    """Run the installed hook script exactly as the CLI runs it: the command out of settings.json,
    the event JSON on stdin."""
    entry = json.loads((config_dir / "settings.json").read_text("utf-8"))
    command = entry["hooks"][NATIVE_MEMORY_HOOK_EVENT][0]["hooks"][0]["command"]
    assert shlex.split(command)[-1].endswith(NATIVE_MEMORY_HOOK_SCRIPT_NAME)
    return subprocess.run(
        command, shell=True, input=json.dumps(payload), capture_output=True, text=True, timeout=60
    )


def _read_of(config_dir: Path) -> dict[str, object]:
    """A pending `Read` of the native memory file inside the pinned config dir — the exact call
    the first paid R4 cycle made 59 times (mem-gj0pc)."""
    return {
        "tool_name": "Read",
        "tool_input": {"file_path": str(config_dir / "memory" / "MEMORY.md")},
        "session_id": "s-1",
    }


def test_observe_mode_records_the_reach_and_lets_the_call_through(tmp_path: Path) -> None:
    """The instrument. The agent is told nothing, so the leg still measures its own disposition,
    and the harness still learns the reach happened."""
    config_dir = tmp_path / "config"
    log = install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_OBSERVE)

    done = _fire(config_dir, _read_of(config_dir))

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_ALLOW
    assert done.stderr == ""
    (reach,) = hook_reaches(log)
    assert reach["tool"] == "Read"
    assert reach["is_write"] is False
    assert reach["session_id"] == "s-1"


@pytest.mark.parametrize("spaced_paths", [False, True])
def test_redirect_mode_blocks_the_reach_and_names_the_bd_verbs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spaced_paths: bool
) -> None:
    """The treatment. Exit 2 is the CLI's block-and-tell-the-model code, so what lands on stderr
    is what the agent reads in place of the file it asked for."""
    if spaced_paths:
        tmp_path = tmp_path / "team member's checkout"
        tmp_path.mkdir()
        interpreter = tmp_path / "python interpreter"
        interpreter.symlink_to(sys.executable)
        monkeypatch.setattr(sys, "executable", str(interpreter))
    config_dir = tmp_path / "config"
    log = install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_REDIRECT)

    done = _fire(config_dir, _read_of(config_dir))

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_BLOCK
    assert done.stderr.strip() == redirect_reason()
    assert f"{MEMORY_COMMAND} remember" in done.stderr
    assert f"{MEMORY_COMMAND} recall" in done.stderr
    assert len(hook_reaches(log)) == 1


def test_a_bash_reach_is_caught_by_the_same_recognizer_the_stream_is_scored_with(
    tmp_path: Path,
) -> None:
    """The file tools are not the only way in. A shell command naming the pinned path is a reach,
    and it is the recognizer in `tool_surface` that says so — here and in the stream scorer."""
    config_dir = tmp_path / "config"
    log = install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_REDIRECT)

    done = _fire(
        config_dir,
        {
            "tool_name": "Bash",
            "tool_input": {"command": f"cat {config_dir}/memory/MEMORY.md"},
        },
    )

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_BLOCK
    (reach,) = hook_reaches(log)
    assert reach["tool"] == "Bash"


def test_a_call_that_is_not_a_memory_reach_is_never_touched(tmp_path: Path) -> None:
    """The hook watches every file tool and Bash, so most of what it sees is ordinary work. A
    redirect on ordinary work would be a coercion the artifact reports as a memory finding."""
    config_dir = tmp_path / "config"
    log = install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_REDIRECT)

    done = _fire(
        config_dir,
        {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "src" / "app.py")}},
    )

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_ALLOW
    assert done.stderr == ""
    assert hook_reaches(log) == []


def test_a_malformed_event_allows_the_call_and_leaves_its_own_fault_in_the_log(
    tmp_path: Path,
) -> None:
    """A hook fault is never a block. Exiting 2 because the input could not be parsed would refuse
    a tool call the agent was entitled to make and publish it as a redirect that never happened."""
    config_dir = tmp_path / "config"
    log = install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_REDIRECT)
    entry = json.loads((config_dir / "settings.json").read_text("utf-8"))
    command = entry["hooks"][NATIVE_MEMORY_HOOK_EVENT][0]["hooks"][0]["command"]

    done = subprocess.run(
        command, shell=True, input="{not json", capture_output=True, text=True, timeout=60
    )

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_ALLOW
    (record,) = hook_reaches(log)
    assert "hook_error" in record


def test_the_install_merges_into_the_rungs_seeded_settings_rather_than_replacing_them(
    tmp_path: Path,
) -> None:
    """R0's whole job is to run with the CLI's own memory system pinned off. The hook is seeded
    into the same file AFTER that pin, and an install that wrote the file fresh would turn the
    native prompt back on for exactly the rung that must not have it."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text('{"autoMemoryEnabled": false}\n', "utf-8")

    install_native_memory_hook(config_dir)

    settings = json.loads((config_dir / "settings.json").read_text("utf-8"))
    assert settings["autoMemoryEnabled"] is False
    assert NATIVE_MEMORY_HOOK_EVENT in settings["hooks"]


def test_an_unreadable_settings_file_refuses_rather_than_dropping_the_pin(tmp_path: Path) -> None:
    """The merge cannot be done, so the alternative is discarding whatever the file held. Refusing
    costs a run; discarding silently publishes a leg as pinned when it was not."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{oops", "utf-8")

    with pytest.raises(MemoryToolError, match="cannot be read"):
        install_native_memory_hook(config_dir)


def test_an_unknown_mode_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    """A typo'd mode that fell through to `observe` would run a fire the operator believes is
    redirecting, and publish the resulting call rate as a redirect's effect."""
    config_dir = tmp_path / "config"

    with pytest.raises(MemoryToolError, match="unknown native-memory hook mode"):
        install_native_memory_hook(config_dir, mode="block")
    assert not (config_dir / NATIVE_MEMORY_HOOK_SCRIPT_NAME).exists()


def test_the_hook_mode_is_part_of_the_surface_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leg run under `redirect` received guidance at the moment of its reach; one run under
    `observe` received none. Two artifacts that hashed the same would let a resume serve one for
    the other, which is how an intervention gets published as a disposition."""
    import membench.runner.tool_surface as tool_surface

    before = surface_fingerprint()
    assert "NATIVE_MEMORY_HOOK_MODE_DEFAULT" in recognizer_policy()
    monkeypatch.setattr(
        tool_surface, "NATIVE_MEMORY_HOOK_MODE_DEFAULT", NATIVE_MEMORY_HOOK_MODE_REDIRECT
    )

    assert surface_fingerprint() != before


def test_the_redirect_text_is_rendered_from_the_verbs_the_counter_scores() -> None:
    """A redirect that named a verb the recognizer does not count would produce reaches the
    artifact cannot attribute to the redirect that caused them."""
    import membench.runner.tool_surface as tool_surface

    reason = redirect_reason()
    for verb in (tool_surface.MEMORY_WRITE_VERBS[0], tool_surface.MEMORY_READ_VERBS[0]):
        assert verb in reason


def test_the_matcher_covers_every_tool_a_reach_can_arrive_through(tmp_path: Path) -> None:
    """The CLI decides which pending calls the hook even sees, from the matcher in settings.json.
    A matcher narrower than the recognizer is silent by construction: the shell path — `cat
    $CLAUDE_CONFIG_DIR/memory/MEMORY.md` — is a reach the scorer counts and the hook would never
    be asked about, so the two instruments would disagree with nothing raising."""
    import membench.runner.tool_surface as tool_surface

    config_dir = tmp_path / "config"
    install_native_memory_hook(config_dir)
    entry = json.loads((config_dir / "settings.json").read_text("utf-8"))
    matched = set(entry["hooks"][NATIVE_MEMORY_HOOK_EVENT][0]["matcher"].split("|"))

    assert matched >= {*tool_surface.NATIVE_MEMORY_TOOL_NAMES, tool_surface.NATIVE_MEMORY_BASH_TOOL}


def test_redirect_distinguishes_exact_keys_from_search_queries() -> None:
    reason = redirect_reason()
    assert 'bd remember "<content>"' in reason
    assert "bd recall <key>" in reason
    assert "bd memories <query>" in reason
    assert "bd recall <query>" not in reason


# --- `deny`: the floor arm's enforcement -------------------------------------------------------
#
# `deny` exists because `redirect` cannot be installed on an arm that must not learn bd exists,
# and because the thing it has to keep out is not the memory subtree. The session transcript at
# `projects/<slug>/<session>.jsonl` holds the establish leg's answer verbatim and carries no
# `memory` path segment, so the narrow recognizer the other two modes use cannot see it at all.
#
# The spelling battery below is the point of this block. A source gate that only catches the one
# spelling its author thought of has passed here before and bitten later (mem-e0b): every probe
# is a spelling an agent would plausibly reach for, and each one is asserted to BLOCK.


def _transcript(config_dir: Path) -> Path:
    """The leaked file: a session transcript inside the pin, holding a prior leg's answer."""
    path = config_dir / "projects" / "-home-ds-projects-mem" / "sess-01.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": f"the value is {TRANSCRIPT_TOKEN}"}) + "\n", "utf-8")
    return path


def _bash(command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s-deny"}


def test_deny_blocks_the_memory_file_and_names_no_alternative(tmp_path: Path) -> None:
    """Blocking is the easy half. Saying nothing is the half that matters: one mention of the
    command here and the floor arm has been coached in the subject under test, at the exact
    moment it was reaching for memory."""
    config_dir = tmp_path / "config"
    log = install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_DENY)

    done = _fire(config_dir, _read_of(config_dir))

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_BLOCK
    assert done.stderr.strip() == NATIVE_MEMORY_HOOK_DENY_REASON
    assert MEMORY_COMMAND not in done.stderr
    for verb in (*MEMORY_READ_VERBS, *MEMORY_WRITE_VERBS):
        assert verb not in done.stderr
    assert len(hook_reaches(log)) == 1


def test_the_narrow_recognizer_cannot_see_the_transcript_leak(tmp_path: Path) -> None:
    """The reason `deny` needed its own predicate, asserted rather than asserted-about. If this
    ever starts failing, the wide predicate has stopped being necessary and should be re-argued
    rather than quietly kept."""
    config_dir = tmp_path / "config"
    transcript = _transcript(config_dir)
    call = ToolCall(name="Read", arguments={"file_path": str(transcript)})

    assert native_memory_accesses([call], config_dir=config_dir) == []
    (leak,) = config_dir_leak_accesses([call], config_dir=config_dir)
    assert leak.path == str(transcript)


@pytest.mark.parametrize(
    ("label", "spelling"),
    TRANSCRIPT_READ_SPELLINGS,
    ids=[label for label, _ in TRANSCRIPT_READ_SPELLINGS],
)
def test_deny_blocks_every_idiomatic_spelling_of_the_transcript_read(
    tmp_path: Path, label: str, spelling: str
) -> None:
    config_dir = tmp_path / "config"
    transcript = _transcript(config_dir)
    log = install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_DENY)

    command = spelling.format(path=transcript, parent=transcript.parent, config_dir=config_dir)
    done = _fire(config_dir, _bash(command))

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_BLOCK, f"{label} was allowed: {command}"
    assert done.stderr.strip() == NATIVE_MEMORY_HOOK_DENY_REASON
    assert hook_reaches(log), label


def test_deny_blocks_a_direct_read_of_the_transcript(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    transcript = _transcript(config_dir)
    install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_DENY)

    done = _fire(
        config_dir,
        {"tool_name": "Read", "tool_input": {"file_path": str(transcript)}, "session_id": "s"},
    )

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_BLOCK


def test_deny_lets_the_arms_own_machinery_through(tmp_path: Path) -> None:
    """The exemption, and its limit. `settings.json` at the top of the pin is the harness's own
    file and carries nothing from any leg; the same name nested under `projects/` is not the same
    file and is not exempt."""
    config_dir = tmp_path / "config"
    install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_DENY)
    nested = config_dir / "projects" / "slug" / "settings.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("{}", "utf-8")

    for name in sorted(CONFIG_DIR_LEAK_EXEMPT_NAMES):
        done = _fire(
            config_dir,
            {"tool_name": "Read", "tool_input": {"file_path": str(config_dir / name)}},
        )
        assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_ALLOW, name

    done = _fire(config_dir, {"tool_name": "Read", "tool_input": {"file_path": str(nested)}})
    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_BLOCK


def test_a_path_outside_the_pin_is_not_a_leak(tmp_path: Path) -> None:
    """The predicate is containment-anchored, not name-anchored. The operator's own memory, and
    the repository the agent is supposed to be working in, stay reachable."""
    config_dir = tmp_path / "config"
    install_native_memory_hook(config_dir, mode=NATIVE_MEMORY_HOOK_MODE_DENY)
    elsewhere = tmp_path / "repo" / "projects" / "slug" / "sess-01.jsonl"
    elsewhere.parent.mkdir(parents=True, exist_ok=True)
    elsewhere.write_text("{}", "utf-8")

    done = _fire(config_dir, {"tool_name": "Read", "tool_input": {"file_path": str(elsewhere)}})

    assert done.returncode == NATIVE_MEMORY_HOOK_EXIT_ALLOW


def test_deny_is_a_distinct_treatment_in_the_resume_identity(tmp_path: Path) -> None:
    """A leg run under `deny` did not run under `observe`. If the fingerprint could not tell them
    apart, a resume would serve a blocked leg's result for an unblocked leg's cell."""
    assert NATIVE_MEMORY_HOOK_MODE_DENY in NATIVE_MEMORY_HOOK_MODES
    policy = recognizer_policy()
    assert NATIVE_MEMORY_HOOK_DENY_REASON in policy.values()
    assert policy["NATIVE_MEMORY_HOOK_MODES"] == list(NATIVE_MEMORY_HOOK_MODES)
    assert sorted(policy["CONFIG_DIR_LEAK_EXEMPT_NAMES"]) == sorted(CONFIG_DIR_LEAK_EXEMPT_NAMES)


def test_an_unknown_mode_is_refused_at_install_time(tmp_path: Path) -> None:
    with pytest.raises(MemoryToolError, match="unknown native-memory hook mode"):
        install_native_memory_hook(tmp_path / "config", mode="silence")
