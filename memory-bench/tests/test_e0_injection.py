"""Unit tests for the E0b prime-delivery pass (bead mem-h9pum).

The properties under test are the ones that make a delivery share mean what the
report says it means:

- **Store state decides the verdict.** The two payload fixtures below are the
  literal output of `bd prime` (bd 1.3.0-rc.1) against an EMPTY store and against
  a store holding one memory. The empty one must score carried=False and the
  nonempty one carried=True; that pair is the bead's acceptance criterion, and it
  is the only thing that shows the detector is reading store state rather than
  the boilerplate, which mentions `bd remember` in both.
- **Detection is format-anchored, not keyword matching.** The prime boilerplate
  contains the word "Memory" in every payload ever emitted. Only the section's
  own structural markers count.
- **A truncated payload is undetermined, never not-carried.** Defaulting an
  elided preview to not-carried would understate delivery by exactly the payloads
  large enough to be elided, which are the ones most likely to be large BECAUSE
  they carried memories.
- **The hook surface is the one that matters.** Prime is fired by SessionStart,
  so an extraction that keeps only `tool_use` blocks measures nearly nothing; the
  agent-invoked form must still pair to its `tool_result` by id.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from tests.paths import REPO

E0 = REPO / "results" / "memory-use" / "e0"


def _load(name: str) -> ModuleType:
    if str(E0) not in sys.path:
        sys.path.insert(0, str(E0))
    spec = importlib.util.spec_from_file_location(name, E0 / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


injection = _load("injection")

BANNER = (
    "[bd prime] If this output is truncated by your host, read the full persisted "
    "hook output before continuing; it may contain project memories and session "
    "rules not visible in the preview.\n\n# Beads Workflow Context\n\n"
)

# The boilerplate every payload carries, empty store or not. It names the capture
# verb in prose, which is why the detector may not key on the word.
BOILERPLATE = (
    "# \U0001f6a8 SESSION CLOSE PROTOCOL \U0001f6a8\n\n"
    "## Core Rules\n"
    '- **Memory**: Use `bd remember "insight"` for persistent knowledge across '
    "sessions. Search with `bd memories <keyword>`.\n"
)

EMPTY_STORE = BANNER + BOILERPLATE

NONEMPTY_STORE = (
    BANNER + "## Persistent Memories (1)\n\n"
    "Stored via `bd remember`. Update in place with `bd remember --key <key> "
    '"new content"`.\n\n'
    "### e0b-probe\na durable note about the prime delivery surface\n\n" + BOILERPLATE
)

LEGACY_NONEMPTY = (
    BANNER + "## Memories\n"
    "- **first-key**: a body the shipped prime injects verbatim...\n"
    "- **second-key**: another injected body...\n\n" + BOILERPLATE
)


# --- the acceptance pair ------------------------------------------------------


def test_empty_store_scores_not_carried() -> None:
    verdict = injection.detect(EMPTY_STORE)
    assert verdict.carried is False
    assert verdict.form == injection.FORM_ABSENT
    assert verdict.memory_count == 0


def test_nonempty_store_scores_carried() -> None:
    verdict = injection.detect(NONEMPTY_STORE)
    assert verdict.carried is True
    assert verdict.form == injection.FORM_PERSISTENT
    assert verdict.memory_count == 1


def test_a_zero_count_header_is_not_a_carry() -> None:
    """The header form carries its own count, so a zero there is authoritative."""
    text = NONEMPTY_STORE.replace("Persistent Memories (1)", "Persistent Memories (0)")
    verdict = injection.detect(text)
    assert verdict.carried is False
    assert verdict.memory_count == 0


def test_legacy_uncounted_header_counts_its_bullets() -> None:
    verdict = injection.detect(LEGACY_NONEMPTY)
    assert verdict.carried is True
    assert verdict.form == injection.FORM_LEGACY
    assert verdict.memory_count == 2


def test_legacy_header_with_no_bullets_is_not_a_carry() -> None:
    text = BANNER + "## Memories\n\n" + BOILERPLATE
    assert injection.detect(text).carried is False


# --- format anchoring ---------------------------------------------------------


def test_the_boilerplate_memory_prose_alone_never_scores_a_carry() -> None:
    """Every payload names the verb in prose; only the section is evidence."""
    assert "bd remember" in EMPTY_STORE and "Memory" in EMPTY_STORE
    assert injection.detect(EMPTY_STORE).carried is False


def test_ansi_wrapped_payload_still_matches() -> None:
    """The compaction surface wraps every line in a dim escape pair."""
    wrapped = "\n".join(f"\x1b[2m{ln}\x1b[22m" for ln in NONEMPTY_STORE.splitlines())
    assert injection.detect(wrapped).carried is True


# --- truncation ---------------------------------------------------------------


def _elided(preview: str, target: Path) -> str:
    return (
        f"<persisted-output>\nOutput too large (11.1KB). Full output saved to: {target}\n\n"
        f"Preview (first 2KB):\n{preview}"
    )


def test_elided_payload_resolves_through_the_named_file(tmp_path: Path) -> None:
    target = tmp_path / "hook-stdout.txt"
    target.write_text(NONEMPTY_STORE, encoding="utf-8")
    verdict, resolution = injection.resolve(None, _elided(BANNER, target))
    assert verdict.carried is True
    assert resolution == "persisted_file"


def test_unresolvable_elision_is_undetermined_not_not_carried(tmp_path: Path) -> None:
    missing = tmp_path / "gone.txt"
    verdict, resolution = injection.resolve(None, _elided(BANNER, missing))
    assert verdict.carried is None
    assert verdict.form == injection.FORM_UNDETERMINED
    assert resolution == "elision_preview_truncated"


def test_a_carry_visible_in_the_preview_still_counts(tmp_path: Path) -> None:
    missing = tmp_path / "gone.txt"
    verdict, resolution = injection.resolve(None, _elided(NONEMPTY_STORE, missing))
    assert verdict.carried is True
    assert resolution == "elision_preview"


def test_hook_stdout_beats_the_elided_inline_copy(tmp_path: Path) -> None:
    """The host elides `content` but keeps the complete `stdout` beside it."""
    verdict, resolution = injection.resolve(NONEMPTY_STORE, _elided(BANNER, tmp_path / "x.txt"))
    assert verdict.carried is True
    assert resolution == "hook_stdout"


# --- extraction ---------------------------------------------------------------

LOCK = "2999-01-01T00:00:00.000Z"


def _scan(records: list[dict[str, Any]], tmp_path: Path, lock: str = LOCK) -> Any:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    tally = injection.Tally()
    injection.scan_file(str(path), lock, tally)
    return tally


def _hook_record(stdout: str, ts: str = "2026-08-20T02:15:44.436Z") -> dict[str, Any]:
    return {
        "type": "attachment",
        "sessionId": "s1",
        "timestamp": ts,
        "cwd": "/home/ds/gas-city",
        "attachment": {
            "type": "hook_success",
            "hookName": "SessionStart:startup",
            "hookEvent": "SessionStart",
            "command": "bd prime --mcp",
            "content": "<persisted-output>\nOutput too large (11.1KB). Full output saved to: "
            "/nonexistent/hook-stdout.txt\n\nPreview (first 2KB):\n" + BANNER,
            "stdout": stdout,
        },
    }


def test_the_session_start_hook_payload_is_kept(tmp_path: Path) -> None:
    """The surface E0.3 could not see: no tool_use block is involved at all."""
    tally = _scan([_hook_record(NONEMPTY_STORE), _hook_record(EMPTY_STORE)], tmp_path)
    summary = injection.summarise(tally)
    assert summary["prime_deliveries"] == 2
    assert summary["carried"] == 1
    assert summary["not_carried"] == 1
    assert summary["per_session"]["s1"] == {
        "deliveries": 2,
        "carried": 1,
        "not_carried": 1,
        "undetermined": 0,
    }


def test_an_agent_invoked_prime_pairs_to_its_tool_result(tmp_path: Path) -> None:
    call: dict[str, Any] = {
        "type": "assistant",
        "sessionId": "s2",
        "timestamp": "2026-08-20T03:00:00.000Z",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "cd /home/ds/gas-city && bd prime"},
                }
            ],
        },
    }
    result: dict[str, Any] = {
        "type": "user",
        "sessionId": "s2",
        "timestamp": "2026-08-20T03:00:01.000Z",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": NONEMPTY_STORE}
            ],
        },
    }
    tally = _scan([call, result], tmp_path)
    summary = injection.summarise(tally)
    assert summary["by_origin"] == {"agent_bash": 1}
    assert summary["carried"] == 1


def test_an_unpaired_tool_result_is_not_counted(tmp_path: Path) -> None:
    """A prime payload with no matching call id is not a delivery this pass saw."""
    orphan: dict[str, Any] = {
        "type": "user",
        "sessionId": "s3",
        "timestamp": "2026-08-20T03:00:01.000Z",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_missing", "content": NONEMPTY_STORE}
            ],
        },
    }
    assert injection.summarise(_scan([orphan], tmp_path))["prime_deliveries"] == 0


def test_traffic_at_or_after_the_preregistration_lock_is_excluded(tmp_path: Path) -> None:
    tally = _scan([_hook_record(NONEMPTY_STORE)], tmp_path, lock="2026-01-01T00:00:00.000Z")
    assert injection.summarise(tally)["prime_deliveries"] == 0
    assert tally.excluded_after_prereg_lock == 1


def test_a_non_prime_bd_call_is_not_a_delivery(tmp_path: Path) -> None:
    """`bd prime` is read off the CLI grammar, not off the word appearing in argv."""
    assert injection.is_prime_invocation("bd prime") is True
    assert injection.is_prime_invocation('bd create --title="prime the pump"') is False
    assert injection.is_prime_invocation("bd list --status=open") is False


# --- ZFC gate -----------------------------------------------------------------


def test_no_matcher_in_the_delivery_pass_keys_on_a_cli_verb_token() -> None:
    """The E0a gate's counterpart for this module.

    `injection.py` names the memory verbs in its prose and in the labels it emits,
    so the line-level grep that holds `verbs.py` cannot hold it. What must hold is
    narrower and stronger: no pattern this module MATCHES WITH may contain a verb
    token. Detection keys on the emitted document's headings, never on the CLI
    vocabulary.
    """
    source = (E0 / "injection.py").read_text(encoding="utf-8")
    patterns: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "compile" or not node.args:
            continue
        first = node.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), ast.dump(first)
        patterns.append(first.value)

    assert patterns, "no compiled pattern found; the gate would pass vacuously"
    verb = re.compile(r"remember|recall|memories")
    assert [p for p in patterns if verb.search(p)] == []
