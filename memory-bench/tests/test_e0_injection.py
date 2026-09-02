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


def test_a_help_or_placeholder_prime_line_is_screened_exactly_as_e0a_screens_it() -> None:
    """The reconciliation compares E0b's typed count against E0a's published one.

    E0a drops help screens and placeholder/template lines before it counts, so E0b
    must drop the same ones. Delete the screen in `is_prime_invocation` and a
    `bd prime --help` becomes a typed invocation here and not there, and the
    residual the artifact calls corpus attrition silently becomes a rule
    disagreement.
    """
    assert injection.is_prime_invocation("bd prime") is True
    assert injection.is_prime_invocation("bd prime --help") is False
    assert injection.is_prime_invocation("bd prime <session-id>") is False


def test_the_e0a_count_in_the_reconciliation_is_read_from_e0as_artifact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Not transcribed. A hand-typed number is how two studies drift apart.

    Asserting only against the real artifact is half vacuous: the published value
    IS 47, so a body hardcoded to ``return 47`` satisfies it. The load-bearing
    assertion is behavioural - pointed at an artifact carrying a DIFFERENT
    injection count, the function must follow the artifact.
    """
    published = json.loads((E0 / "analysis.json").read_text(encoding="utf-8"))
    assert injection.e0a_typed_primes() == published["bucket_counts"]["injection"]

    other = dict(published)
    other["bucket_counts"] = dict(published["bucket_counts"])
    other["bucket_counts"]["injection"] = published["bucket_counts"]["injection"] + 101
    fake = tmp_path / "analysis.json"
    fake.write_text(json.dumps(other), encoding="utf-8")
    monkeypatch.setattr(injection, "E0A_ANALYSIS_PATH", fake)
    assert injection.e0a_typed_primes() == published["bucket_counts"]["injection"] + 101

    source = (E0 / "injection.py").read_text(encoding="utf-8")
    assert 'e0a_published_agent_typed_prime_invocations": e0a_typed_primes()' in source


def test_the_off_type_residual_quoted_in_the_module_is_the_one_this_run_produced() -> None:
    """The docstring quotes a population figure; it must come from the artifact.

    An earlier revision quoted 5,839 / 5,839, carried over from the superseded
    run. Re-derived here from `injection.json`: the hook-origin deliveries and the
    off-type residual the module's own guard counts.
    """
    artifact = json.loads((E0 / "injection.json").read_text(encoding="utf-8"))
    hook_origin = sum(
        n for origin, n in artifact["delivery"]["by_origin"].items() if origin.startswith("hook:")
    )
    assert artifact["exclusions"]["attachments_with_banner_but_other_type"] == 0
    doc = injection._attachment_event.__doc__ or ""
    assert f"{hook_origin:,}" in doc


def test_every_e0a_amendment_is_digested_not_just_the_first() -> None:
    """E0a corrects its lock by APPENDING amendments; digesting one pins a stale set."""
    on_disk = sorted(E0.glob("preregistration-amendment-*.json"))
    assert len(on_disk) >= 3
    assert list(injection.AMENDMENT_PATHS) == on_disk


# --- ZFC gate -----------------------------------------------------------------


#: Callables whose string arguments are matched AGAINST text rather than emitted.
MATCHING_CALLS = frozenset(
    {
        "compile",
        "search",
        "match",
        "fullmatch",
        "findall",
        "finditer",
        "sub",
        "subn",
        "split",
        "rsplit",
        "partition",
        "rpartition",
        "startswith",
        "endswith",
        "find",
        "rfind",
        "index",
        "rindex",
        "count",
        "replace",
    }
)


def _bound_strings(tree: ast.Module) -> dict[str, list[str]]:
    """Name -> the string values assigned to it, at ANY scope.

    Without this, the gate is evaded by binding the token to a name and
    comparing against the name; restricting the scan to module level only moves
    that binding one indent to the right. Values are resolved through
    `_string_operands`, so a name bound to a set/list/tuple of tokens, to a
    concatenation, or to another already-bound name resolves too. Two passes,
    because a name may be bound before the name it is built from is seen.
    """
    bound: dict[str, list[str]] = {}
    for _ in range(2):
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if node.value is None:
                continue
            values = _string_operands(node.value, bound)
            if not values:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    bound.setdefault(target.id, [])
                    bound[target.id] = sorted(set(bound[target.id]) | set(values))
    return bound


def _string_operands(node: ast.expr, bound: dict[str, list[str]]) -> list[str]:
    """Every string value this expression can denote, as far as it is decidable.

    Constant folding over ``+`` and over ``"".join([...])`` is load-bearing: a
    single concatenation splits a token across two literals, and a gate that
    trips on the plain literal reads two harmless halves instead.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and node.id in bound:
        return list(bound[node.id])
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return [v for element in node.elts for v in _string_operands(element, bound)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_operands(node.left, bound)
        right = _string_operands(node.right, bound)
        if len(left) == 1 and len(right) == 1:
            return [left[0] + right[0]]
        return left + right
    if isinstance(node, ast.JoinedStr):
        parts = [
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
        return ["".join(parts)] if parts else []
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "join" and node.args:
            sep = _string_operands(func.value, bound)
            pieces = _string_operands(node.args[0], bound)
            if pieces:
                joined = (sep[0] if len(sep) == 1 else "").join(pieces)
                return [joined, *pieces]
        return []
    return []


def _iterable_operands(tree: ast.Module, bound: dict[str, list[str]]) -> list[str]:
    """Strings denoted by the iterable of every comprehension and ``for``.

    A multi-token matcher is most naturally written as a membership test over a
    literal collection (``any(t in text for t in {...})``), and that set literal
    is an ``ast.comprehension.iter`` - a node the comparison/call walk never
    reaches.
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension | ast.For | ast.AsyncFor):
            out.extend(_string_operands(node.iter, bound))
    return out


def _matched_strings(source: str) -> tuple[list[str], list[str]]:
    """(compiled patterns, strings compared or matched against) in ``source``."""
    tree = ast.parse(source)
    bound = _bound_strings(tree)

    patterns: list[str] = []
    operands: list[str] = _iterable_operands(tree, bound)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                operands.extend(_string_operands(side, bound))
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in MATCHING_CALLS:
            continue
        if name == "compile":
            assert node.args, ast.dump(node)
            first = node.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), ast.dump(first)
            patterns.append(first.value)
        for arg in node.args:
            operands.extend(_string_operands(arg, bound))
    return patterns, operands


VERB = re.compile(r"remember|recall|memories")

#: Spellings of a CLI-verb matcher that some revision of this gate could not
#: see. Each is a whole module body; the collector must find the verb in each.
EVADING_SPELLINGS = (
    'if any(t in text for t in {"zzz", "recall-marker"}):\n    pass\n',
    'for token in ["memories-marker"]:\n    if token in text:\n        pass\n',
    'if ("you may re" + "call the following") in text:\n    pass\n',
    'if "".join(["zzz-memo", "ries-marker"]) in text:\n    pass\n',
    'if f"remember" in text:\n    pass\n',
    'if text.startswith("bd remember"):\n    pass\n',
    'PAT = "recall"\nif PAT in text:\n    pass\n',
    'def f(text):\n    tok = "recall"\n    return tok in text\n',
    'TOKENS = {"memories-marker"}\n\n\ndef f(text):\n    return any(t in text for t in TOKENS)\n',
)


def test_the_gate_sees_every_spelling_that_once_evaded_it() -> None:
    """The gate's own coverage, asserted directly.

    Without this, a refactor that quietly makes `_string_operands` return `[]`
    for a construct leaves the gate green on a module that matches on a verb.
    Two of the spellings below (the comprehension iterable and the concatenated
    literal) were demonstrated live against `injection.py` under a full green
    suite.
    """
    for body in EVADING_SPELLINGS:
        _, operands = _matched_strings(body)
        assert [o for o in operands if VERB.search(o)], body


def test_no_matcher_in_the_delivery_pass_keys_on_a_cli_verb_token() -> None:
    """The E0a gate's counterpart for this module.

    `injection.py` names the memory verbs in its prose and in the labels it emits,
    so the line-level grep that holds `verbs.py` cannot hold it. What must hold is
    narrower and stronger: no string this module MATCHES WITH may contain a verb
    token. Detection keys on the emitted document's headings, never on the CLI
    vocabulary.

    "Matches with" is every construct that can decide a branch on text, not only
    `re.compile`: a plain `"you may recall the following memories" in text` is the
    same keyword matcher spelled differently, and an earlier revision of this gate
    read only `re.compile`'s first argument and let it through. Covered here:
    compiled patterns, both sides of every comparison (`==`, `!=`, `in`, `not
    in`), the string arguments of the str/re matching calls above, and the
    iterable of every comprehension and `for` - with module-level constants
    resolved, `+` and `str.join` folded, so the token cannot be hidden behind a
    name, split across two literals, or parked in a set the walk never entered.
    Two of those last three each let a live matcher through a fully green suite.
    """
    source = (E0 / "injection.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    patterns, operands = _matched_strings(source)
    iterables = _iterable_operands(tree, _bound_strings(tree))

    assert patterns, "no compiled pattern found; the gate would pass vacuously"
    assert operands, "no matched-against string found; the gate would pass vacuously"
    assert _matched_strings(EVADING_SPELLINGS[0])[1], "the iterable operand source collects nothing"
    assert [v for v in iterables if VERB.search(v)] == []
    assert [p for p in patterns + operands if VERB.search(p)] == []


# --- what counts as a delivery at all -----------------------------------------


def test_a_hook_attachment_whose_payload_is_not_a_prime_document_is_not_counted(
    tmp_path: Path,
) -> None:
    """The population gate, tested at scan level.

    This is the branch that decides the DENOMINATOR of the headline carry share,
    and it had no test: deleting the `PRIME_BANNER` guard in `_attachment_event`
    left the whole suite green while the delivery count on the first 1500 files of
    the pinned filelist moved 304 -> 837 (re-derived against this tree), with
    `carried` unchanged at 292 and every one of the 533 extra landing in
    `not_carried` (12 -> 453) or `undetermined` (0 -> 92) - i.e. entirely in the
    denominator of the published share and nowhere in its numerator. A hook
    firing that is not `bd prime` at all - here a SessionStart hook that prints an
    unrelated banner, and mentions the word "prime" so the line-level prefilter
    still hands the record to the scanner - is not a prime delivery.
    """
    intruder = _hook_record(EMPTY_STORE)
    other = "Session bootstrap complete. Nothing here is a prime payload.\n"
    intruder["attachment"]["hookName"] = "SessionStart:startup"
    intruder["attachment"]["command"] = "echo 'not prime, but mentions prime'"
    intruder["attachment"]["content"] = other
    intruder["attachment"]["stdout"] = other
    tally = _scan([intruder], tmp_path)
    assert injection.summarise(tally)["prime_deliveries"] == 0

    real = _hook_record(EMPTY_STORE)
    assert injection.summarise(_scan([real], tmp_path))["prime_deliveries"] == 1


def test_a_banner_carrying_attachment_of_another_type_is_not_a_delivery(
    tmp_path: Path,
) -> None:
    """The docstring's stated mechanism, now the implemented one.

    The report and this module say a delivery is read off a record whose
    `attachment.type == "hook_success"`. Every banner-carrying attachment in the
    pinned population satisfies that, so enforcing it moves no published count -
    but an unenforced claim is one a future host can falsify silently, so it is
    enforced, and the off-type residual is published rather than dropped.
    """
    rec = _hook_record(NONEMPTY_STORE)
    rec["attachment"]["type"] = "hook_failure"
    tally = _scan([rec], tmp_path)
    assert injection.summarise(tally)["prime_deliveries"] == 0
    assert tally.attachments_with_banner_but_other_type == 1


def test_an_agent_typed_call_with_no_paired_payload_is_reconciled_not_dropped(
    tmp_path: Path,
) -> None:
    """The 47-vs-30 arithmetic, carried in the artifact instead of in prose."""
    call: dict[str, Any] = {
        "type": "assistant",
        "sessionId": "s4",
        "timestamp": "2026-08-20T03:00:00.000Z",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_9",
                    "name": "Bash",
                    "input": {"command": "bd prime"},
                }
            ],
        },
    }
    result: dict[str, Any] = {
        "type": "user",
        "sessionId": "s4",
        "timestamp": "2026-08-20T03:00:01.000Z",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_9",
                    "content": "bd prime: command failed, no payload",
                }
            ],
        },
    }
    tally = _scan([call, result], tmp_path)
    assert injection.summarise(tally)["prime_deliveries"] == 0
    assert tally.agent_prime_calls == 1
    assert tally.agent_prime_results_without_a_prime_payload == 1
    assert tally.agent_prime_calls_unpaired == 0

    # A result the line-level prefilter never hands to the scanner (it does not
    # mention prime at all) leaves the call unpaired rather than payload-less. The
    # two buckets are named for what was observed, not for what happened on the
    # host, and they sum with the delivered ones to the calls seen.

    tally = _scan([call], tmp_path)
    assert tally.agent_prime_calls == 1
    assert tally.agent_prime_calls_unpaired == 1


def test_the_documented_invocation_returns_a_denominator_and_a_per_session_map(
    tmp_path: Path, capsys: Any
) -> None:
    """The bead's acceptance criterion, run as the CLI the bead names.

    `--filelist ... --json` with no other flag must produce a NONZERO denominator
    and per-session carried / not-carried counts. The map is opt-OUT for size
    (`--no-per-session`), never opt-in, or the documented command would answer
    only half the criterion.
    """
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in (_hook_record(NONEMPTY_STORE), _hook_record(EMPTY_STORE)))
        + "\n",
        encoding="utf-8",
    )
    filelist = tmp_path / "filelist.txt"
    filelist.write_text(str(path) + "\n", encoding="utf-8")

    assert injection.main(["--filelist", str(filelist), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)

    assert out["delivery"]["determined"] == 2
    assert out["delivery"]["prime_deliveries"] > 0
    assert out["delivery"]["per_session"]["s1"] == {
        "deliveries": 2,
        "carried": 1,
        "not_carried": 1,
        "undetermined": 0,
    }

    assert injection.main(["--filelist", str(filelist), "--json", "--no-per-session"]) == 0
    lean = json.loads(capsys.readouterr().out)
    assert "per_session" not in lean["delivery"]
    assert lean["delivery"]["determined"] == 2


def test_the_per_session_map_is_optional_and_the_aggregates_are_not(tmp_path: Path) -> None:
    tally = _scan([_hook_record(NONEMPTY_STORE), _hook_record(EMPTY_STORE)], tmp_path)
    full = injection.summarise(tally)
    lean = injection.summarise(tally, include_per_session=False)
    assert "per_session" in full
    assert "per_session" not in lean
    for key in ("sessions_with_a_prime_delivery", "sessions_with_a_carried_delivery"):
        assert lean[key] == full[key]
