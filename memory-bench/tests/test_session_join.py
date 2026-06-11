"""Unit tests for the session<->bead content-scan join mechanism (mem-75t.9 PHASE 1).

All transcripts are synthetic jsonl lines — no real-FS or store dependence. The
mechanism under test is pure id-grammar extraction (ZFC: structural parsing, no
semantic judgment).
"""

import json

import pytest

from membench.session_join import (
    STRONG_SUBCOMMANDS,
    BdMention,
    SessionScan,
    WorkIdLink,
    assignee_sessions,
    classify_gc_command,
    derive_prefixes,
    extract_bd_mentions,
    extract_gc_output_ids,
    parse_assignee,
    scan_transcript_lines,
    session_uuid,
    work_id_pattern,
)

PREFIXES = frozenset({"mem", "gc", "gascity-dashboard", "co"})
PATTERN = work_id_pattern(PREFIXES)


# --- prefix derivation -------------------------------------------------------


def test_derive_prefixes_strips_final_token() -> None:
    ids = ["mem-75t.9", "gascity-dashboard-4lf62", "gc-j551", "live_docs-abc", "co-34c.2"]
    assert derive_prefixes(ids) == frozenset({"mem", "gascity-dashboard", "gc", "live_docs", "co"})


def test_derive_prefixes_skips_hyphenless_ids() -> None:
    assert derive_prefixes(["nohyphen", "mem-1"]) == frozenset({"mem"})


def test_derive_prefixes_empty() -> None:
    assert derive_prefixes([]) == frozenset()


# --- id grammar --------------------------------------------------------------


def test_pattern_matches_plain_and_dotted_ids() -> None:
    assert PATTERN.findall("close mem-75t.9 and gc-j551") == ["mem-75t.9", "gc-j551"]


def test_pattern_prefers_longest_prefix() -> None:
    pat = work_id_pattern(frozenset({"gascity", "gascity-dashboard"}))
    assert pat.findall("see gascity-dashboard-4lf62") == ["gascity-dashboard-4lf62"]


def test_pattern_rejects_embedded_role_prefix() -> None:
    # `mem-worker-gc-351177` is an assignee, not a work id: neither `mem-worker`
    # nor the embedded `gc-351177` session token may surface — ids inside a
    # larger hyphen run are not ids.
    assert PATTERN.findall("--assignee mem-worker-gc-351177") == []
    # A standalone gc session token still matches the gc- grammar (callers
    # filter rows against the store's actual work_ids).
    assert PATTERN.findall("session gc-351177 did it") == ["gc-351177"]


def test_pattern_allows_sentence_final_punctuation() -> None:
    assert PATTERN.findall("closed mem-75t.9.") == ["mem-75t.9"]


def test_pattern_requires_word_boundary() -> None:
    assert PATTERN.findall("system-mem-1 totem-2") == []


# --- bd mention extraction ---------------------------------------------------


def test_claim_is_strong() -> None:
    mentions = extract_bd_mentions("bd claim mem-75t.9", PATTERN)
    assert mentions == (BdMention(subcommand="claim", work_id="mem-75t.9"),)
    assert mentions[0].strength == "strong"


def test_show_is_weak() -> None:
    mentions = extract_bd_mentions("bd show mem-75t 2>&1 | head -30", PATTERN)
    assert mentions == (BdMention(subcommand="show", work_id="mem-75t"),)
    assert mentions[0].strength == "weak"


@pytest.mark.parametrize("sub", sorted(STRONG_SUBCOMMANDS))
def test_strong_subcommands(sub: str) -> None:
    (mention,) = extract_bd_mentions(f"bd {sub} mem-1", PATTERN)
    assert mention.strength == "strong"


def test_compound_command_segments() -> None:
    cmd = 'cd /x && bd update mem-1.2 --notes "progress" ; git status'
    assert extract_bd_mentions(cmd, PATTERN) == (BdMention(subcommand="update", work_id="mem-1.2"),)


def test_rtk_wrapped_bd() -> None:
    assert extract_bd_mentions("rtk bd close gc-j551", PATTERN) == (
        BdMention(subcommand="close", work_id="gc-j551"),
    )


def test_subshell_invocation() -> None:
    assert extract_bd_mentions('echo "$(bd show mem-2)"', PATTERN) == (
        BdMention(subcommand="show", work_id="mem-2"),
    )


def test_multiple_bd_invocations_in_one_command() -> None:
    cmd = "bd show mem-1; bd claim mem-2"
    assert extract_bd_mentions(cmd, PATTERN) == (
        BdMention(subcommand="show", work_id="mem-1"),
        BdMention(subcommand="claim", work_id="mem-2"),
    )


def test_flag_with_value_is_skipped() -> None:
    # `-C <dir>` is bd's chdir flag: the dir must not be parsed as the subcommand.
    assert extract_bd_mentions("bd -C /home/ds/gas-city update mem-1", PATTERN) == (
        BdMention(subcommand="update", work_id="mem-1"),
    )


def test_assignee_value_is_not_a_mention() -> None:
    assert extract_bd_mentions("bd list --assignee mem-worker-gc-351177", PATTERN) == ()


def test_non_bd_command_yields_nothing() -> None:
    assert extract_bd_mentions("echo mem-75t.9", PATTERN) == ()


def test_bd_without_work_id_yields_nothing() -> None:
    assert extract_bd_mentions("bd ready", PATTERN) == ()


# --- transcript scan ---------------------------------------------------------


def _assistant_bd_event(ts: str, command: str, session: str = "sess-1") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "sessionId": session,
            "message": {
                "content": [
                    {"type": "text", "text": "running"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": command}},
                ]
            },
        }
    )


def _user_event(ts: str) -> str:
    return json.dumps(
        {"type": "user", "timestamp": ts, "sessionId": "sess-1", "message": {"content": "hi"}}
    )


def test_scan_collects_links_and_bounds() -> None:
    lines = [
        '{"type":"mode","mode":"normal","sessionId":"sess-1"}',
        _user_event("2026-06-01T10:00:00.000Z"),
        _assistant_bd_event("2026-06-01T10:01:00.000Z", "bd claim mem-75t.9"),
        _assistant_bd_event("2026-06-01T10:05:00.000Z", "bd show mem-75t.9"),
        _assistant_bd_event("2026-06-01T10:06:00.000Z", "bd show gc-j551 | head"),
        _user_event("2026-06-01T10:10:00.000Z"),
    ]
    scan = scan_transcript_lines(lines, PATTERN)
    assert scan == SessionScan(
        session_id="sess-1",
        session_start="2026-06-01T10:00:00.000Z",
        session_end="2026-06-01T10:10:00.000Z",
        links=(
            WorkIdLink(
                work_id="gc-j551",
                strength="weak",
                t_first="2026-06-01T10:06:00.000Z",
                t_last="2026-06-01T10:06:00.000Z",
                n_strong=0,
                n_weak=1,
            ),
            WorkIdLink(
                work_id="mem-75t.9",
                strength="strong",
                t_first="2026-06-01T10:01:00.000Z",
                t_last="2026-06-01T10:05:00.000Z",
                n_strong=1,
                n_weak=1,
            ),
        ),
    )


def test_scan_weak_then_strong_upgrades() -> None:
    lines = [
        _assistant_bd_event("2026-06-01T09:00:00.000Z", "bd show mem-1"),
        _assistant_bd_event("2026-06-01T09:30:00.000Z", "bd close mem-1"),
    ]
    (link,) = scan_transcript_lines(lines, PATTERN).links
    assert link.strength == "strong"
    assert link.t_first == "2026-06-01T09:00:00.000Z"
    assert link.t_last == "2026-06-01T09:30:00.000Z"


def test_scan_skips_garbage_and_empty_lines() -> None:
    lines = ["", "not json at all bd claim mem-1 {", '{"truncated": tool_use bd ']
    scan = scan_transcript_lines(lines, PATTERN)
    assert scan.links == ()
    assert scan.session_id is None
    assert scan.session_start is None


def test_scan_ignores_bd_outside_tool_use() -> None:
    # bd text in prose (a text block) is not a tool invocation.
    line = json.dumps(
        {
            "type": "assistant",
            "timestamp": "2026-06-01T10:00:00.000Z",
            "sessionId": "sess-1",
            "message": {"content": [{"type": "text", "text": "I will bd claim mem-75t.9"}]},
        }
    )
    assert scan_transcript_lines([line], PATTERN).links == ()


# --- gc dispatch channel (mem-75t.4 extension) --------------------------------


def test_classify_gc_prime_and_hook() -> None:
    assert classify_gc_command("gc prime") == "gc-prime"
    assert classify_gc_command("gc hook --claim --drain-ack --json") == "gc-hook-claim"
    assert classify_gc_command('echo "=== gc hook ==="; gc hook 2>&1 | head -40') == "gc-hook"
    assert classify_gc_command("gc mail send mayor -s hi") is None
    assert classify_gc_command("bd show mem-1") is None


def test_classify_gc_strongest_channel_wins() -> None:
    assert classify_gc_command("gc hook; gc hook --claim") == "gc-hook-claim"


def test_gc_output_ids_from_work_bead_header() -> None:
    text = "=== gc hook ===\n=== work bead gc-j551 ===\nTITLE: x\nsee also mem-75t.9\n"
    # only the header id — description mentions of other beads are NOT links
    assert extract_gc_output_ids(text, PATTERN) == ("gc-j551",)


def test_gc_output_ids_from_json_array() -> None:
    text = json.dumps([{"id": "gc-j551", "description": "relates to mem-75t.9"}])
    assert extract_gc_output_ids(text, PATTERN) == ("gc-j551",)


def test_gc_output_ids_from_json_object_bead_id() -> None:
    text = json.dumps({"action": "work", "bead_id": "mem-2"})
    assert extract_gc_output_ids(text, PATTERN) == ("mem-2",)


def test_gc_output_ids_skips_exit_code_prefix() -> None:
    text = "Exit code 5\n" + json.dumps([{"id": "gc-j551"}])
    assert extract_gc_output_ids(text, PATTERN) == ("gc-j551",)


def test_gc_output_ids_free_text_is_never_scanned() -> None:
    assert extract_gc_output_ids("# Mayor\nwork mem-75t.9 today", PATTERN) == ()


def _gc_use_event(ts: str, command: str, tool_id: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "sessionId": "sess-1",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "Bash",
                        "input": {"command": command},
                    },
                ]
            },
        }
    )


def _gc_result_event(ts: str, tool_id: str, text: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "timestamp": ts,
            "sessionId": "sess-1",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": [{"type": "text", "text": text}],
                    },
                ]
            },
        }
    )


def test_scan_links_bead_from_gc_claim_output() -> None:
    # The polecat never types its bead id — it arrives in the claim output.
    lines = [
        _gc_use_event("2026-06-01T10:00:00.000Z", "gc hook --claim 2>&1", "toolu_1"),
        _gc_result_event(
            "2026-06-01T10:00:05.000Z", "toolu_1", "=== work bead gc-j551 ===\nTITLE: x"
        ),
    ]
    (link,) = scan_transcript_lines(lines, PATTERN).links
    assert link == WorkIdLink(
        work_id="gc-j551",
        strength="strong",
        t_first="2026-06-01T10:00:05.000Z",
        t_last="2026-06-01T10:00:05.000Z",
        n_strong=1,
        n_weak=0,
        n_gc=1,
    )


def test_scan_bare_hook_listing_is_weak() -> None:
    lines = [
        _gc_use_event("2026-06-01T10:00:00.000Z", "gc hook 2>&1 | head -40", "toolu_1"),
        _gc_result_event("2026-06-01T10:00:05.000Z", "toolu_1", json.dumps([{"id": "mem-2"}])),
    ]
    (link,) = scan_transcript_lines(lines, PATTERN).links
    assert link.strength == "weak"
    assert link.n_gc == 1


def test_scan_raw_string_tool_result_content() -> None:
    line_use = _gc_use_event("2026-06-01T10:00:00.000Z", "gc prime", "toolu_9")
    line_result = json.dumps(
        {
            "type": "user",
            "timestamp": "2026-06-01T10:00:02.000Z",
            "sessionId": "sess-1",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_9",
                        "content": "=== work bead mem-2 ===",
                    },
                ]
            },
        }
    )
    (link,) = scan_transcript_lines([line_use, line_result], PATTERN).links
    assert link.work_id == "mem-2"
    assert link.strength == "strong"


def test_scan_unrelated_tool_result_is_ignored() -> None:
    lines = [
        _gc_result_event("2026-06-01T10:00:05.000Z", "toolu_404", "=== work bead mem-2 ==="),
    ]
    assert scan_transcript_lines(lines, PATTERN).links == ()


# --- dolt-history assignee parsing (source b) --------------------------------


def test_parse_assignee_role_and_session() -> None:
    assert parse_assignee("mem-worker-gc-351177") == ("mem-worker", "gc-351177")
    assert parse_assignee("polecat-gc-335825") == ("polecat", "gc-335825")
    assert parse_assignee("gc-335825") == (None, "gc-335825")


def test_parse_assignee_plain_actor() -> None:
    assert parse_assignee("sjarmak") == (None, "sjarmak")
    assert parse_assignee("  ") is None


def test_assignee_sessions_counts_distinct_session_agents() -> None:
    rows = [
        {"id": "mem-1", "assignee": "mem-worker-gc-100", "first_seen": "a", "last_seen": "b"},
        {"id": "mem-1", "assignee": "polecat-gc-200", "first_seen": "c", "last_seen": "d"},
        {"id": "mem-1", "assignee": "sjarmak", "first_seen": "e", "last_seen": "f"},
        {"id": "mem-2", "assignee": "gc-100", "first_seen": "g", "last_seen": "h"},
        {"id": "mem-3", "assignee": None, "first_seen": "i", "last_seen": "j"},
    ]
    sessions = assignee_sessions(rows)
    assert sessions == {"mem-1": ("gc-100", "gc-200"), "mem-2": ("gc-100",)}


# --- session_uuid identity (mem-75t.10) --------------------------------------


def test_session_uuid_is_namespace_independent() -> None:
    homes = "/home/ds/.claude-homes/acct/.claude/projects/p/abc-123.jsonl"
    bare = "/home/ds/.claude/projects/p/abc-123.jsonl"
    assert session_uuid(homes) == "abc-123"
    assert session_uuid(bare) == "abc-123"
    assert session_uuid(homes) == session_uuid(bare)


def test_session_uuid_strips_known_suffixes() -> None:
    assert session_uuid("/x/abc-123.jsonl.gz") == "abc-123"
    assert session_uuid("/x/abc-123.gz") == "abc-123"
    assert session_uuid("abc-123") == "abc-123"


def test_session_uuid_empty_is_none() -> None:
    assert session_uuid(None) is None
    assert session_uuid("") is None
