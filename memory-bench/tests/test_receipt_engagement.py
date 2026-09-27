from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields

import pytest
from hypothesis import given
from hypothesis import strategies as st

from membench.runner.bd_receipts import CALLER_HARNESS, CALLER_HOOK
from membench.runner.receipt_engagement import (
    RULE_ACKNOWLEDGED_WRITE,
    RULE_NO_RECEIPTS,
    RULE_NO_TOKENS,
    RULE_NONZERO_RETURNCODE,
    RULE_NOT_ACKNOWLEDGED,
    RULE_NOT_AGENT,
    RULE_NOT_FINISH,
    RULE_NOT_REMEMBER,
    RULE_RECALL,
    RULE_TOKEN_ABSENT,
    EngagementVerdict,
    engaged_by_receipts,
)
from tests.receipt_helpers import accepted_write, receipt_rows

TOKEN = "ZZZ-CUR"
VALUE = f"the retention window is {TOKEN}"
REFUSAL = 'Error: "{word}" looks like a command, not something to remember\n'
RECALLED = (
    '(recalled "window" -- a bare existing key READS. To overwrite: '
    '`bd remember "<new content>" --key window`)\n'
)


def test_no_tokens_is_never_engaged() -> None:
    verdict = engaged_by_receipts(accepted_write(VALUE), ())
    assert verdict == EngagementVerdict(engaged=False, row_index=None, rule=RULE_NO_TOKENS)


def test_no_receipts_is_not_engaged() -> None:
    verdict = engaged_by_receipts((), (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=None, rule=RULE_NO_RECEIPTS)


def test_a_start_row_alone_is_not_engaged() -> None:
    start, _finish = accepted_write(VALUE)
    verdict = engaged_by_receipts((start,), (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=0, rule=RULE_NOT_FINISH)


def test_a_malformed_row_is_not_engaged() -> None:
    rows = ({"instrumentation_error": "incomplete or malformed receipt", "raw": VALUE},)
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=0, rule=RULE_NOT_FINISH)


@pytest.mark.parametrize("caller", [CALLER_HARNESS, CALLER_HOOK])
def test_a_call_the_agent_did_not_make_is_not_engaged(caller: str) -> None:
    verdict = engaged_by_receipts(accepted_write(VALUE, caller=caller), (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_NOT_AGENT)


def test_a_recall_of_the_token_is_not_engaged() -> None:
    rows = receipt_rows(["recall", TOKEN], stdout=f"{VALUE}\n")
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_NOT_REMEMBER)


def test_a_memories_search_for_the_token_is_not_engaged() -> None:
    rows = receipt_rows(["memories", TOKEN], stdout=f"window: {VALUE}\n")
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_NOT_REMEMBER)


def test_a_refused_bare_token_is_not_engaged() -> None:
    rows = receipt_rows(["remember", TOKEN], returncode=1, stderr=REFUSAL.format(word=TOKEN))
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_NONZERO_RETURNCODE)


def test_a_nonzero_returncode_is_not_engaged_whatever_stdout_says() -> None:
    rows = receipt_rows(
        ["remember", VALUE, "--key", "window"],
        returncode=2,
        stdout=f"Remembered [window]: {VALUE}\n",
    )
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_NONZERO_RETURNCODE)


def test_a_bare_existing_key_read_is_not_engaged_though_its_output_states_the_token() -> None:
    rows = receipt_rows(["remember", "window"], stdout=f"{RECALLED}{VALUE}\n")
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_RECALL)


def test_an_argv_only_match_without_an_acknowledgement_is_not_engaged() -> None:
    rows = receipt_rows(["remember", VALUE, "--key", "window"])
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_NOT_ACKNOWLEDGED)


def test_an_acknowledged_write_of_another_value_is_not_engaged() -> None:
    verdict = engaged_by_receipts(accepted_write("the retention window is ZZZ-OLD"), (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_TOKEN_ABSENT)


def test_a_token_inside_a_longer_word_is_absent() -> None:
    verdict = engaged_by_receipts(accepted_write(f"{VALUE}X"), (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_TOKEN_ABSENT)


def test_an_acknowledged_write_stating_the_token_is_engaged() -> None:
    verdict = engaged_by_receipts(accepted_write(VALUE), (TOKEN,))
    assert verdict == EngagementVerdict(engaged=True, row_index=1, rule=RULE_ACKNOWLEDGED_WRITE)


def test_an_acknowledged_write_receipt_from_a_compound_command_is_engaged() -> None:
    rows = receipt_rows(
        ["remember", VALUE, "--key", "window"], stdout=f"Remembered [window]: {VALUE}\n"
    )
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=True, row_index=1, rule=RULE_ACKNOWLEDGED_WRITE)


@given(st.from_regex(r"[A-Z]{3}-[A-Z]{3}", fullmatch=True))
def test_every_acknowledged_write_stating_its_token_is_engaged(token: str) -> None:
    value = f"the retained value is {token}"
    assert engaged_by_receipts(accepted_write(value), (token,)).engaged is True


def test_an_update_of_an_existing_key_is_an_acknowledged_write() -> None:
    rows = receipt_rows(
        ["remember", VALUE, "--key", "window"], stdout=f"Updated [window]: {VALUE}\n"
    )
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=True, row_index=1, rule=RULE_ACKNOWLEDGED_WRITE)


def test_the_token_counts_from_argv_when_the_acknowledgement_truncates_it() -> None:
    padding = "x" * 70
    rows = receipt_rows(
        ["remember", f"{padding} ZZZ-LONG tail", "--key", "long"],
        stdout=f"Remembered [long]: {padding} ZZZ-LO...\n",
    )
    verdict = engaged_by_receipts(rows, ("ZZZ-LONG",))
    assert verdict == EngagementVerdict(engaged=True, row_index=1, rule=RULE_ACKNOWLEDGED_WRITE)


def test_the_token_counts_from_stdout_when_argv_does_not_carry_it() -> None:
    rows = receipt_rows(
        ["remember", "the window", "--key", "window"], stdout=f"Updated [window]: {VALUE}\n"
    )
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=True, row_index=1, rule=RULE_ACKNOWLEDGED_WRITE)


def test_a_json_acknowledgement_with_a_global_flag_before_the_verb_is_engaged() -> None:
    stdout = json.dumps(
        {"action": "remembered", "key": "window", "schema_version": 1, "value": VALUE}, indent=2
    )
    rows = receipt_rows(["--json", "remember", VALUE, "--key", "window"], stdout=stdout)
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=True, row_index=1, rule=RULE_ACKNOWLEDGED_WRITE)


def test_a_verb_hidden_inside_the_stored_content_is_not_a_verb() -> None:
    rows = receipt_rows(["recall", "bd remember ZZZ-CUR"], stdout=f"Remembered [x]: {VALUE}\n")
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=1, rule=RULE_NOT_REMEMBER)


def test_the_first_acknowledged_write_wins() -> None:
    rows = (
        *receipt_rows(["remember", VALUE, "--key", "a"]),
        *accepted_write(VALUE, key="b"),
        *accepted_write(VALUE, key="c"),
    )
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=True, row_index=3, rule=RULE_ACKNOWLEDGED_WRITE)


def test_the_verdict_names_the_row_that_got_furthest_when_none_engaged() -> None:
    rows = (
        *receipt_rows(["recall", "window"]),
        *receipt_rows(["remember", VALUE, "--key", "a"]),
        *receipt_rows(["memories", TOKEN]),
    )
    verdict = engaged_by_receipts(rows, (TOKEN,))
    assert verdict == EngagementVerdict(engaged=False, row_index=3, rule=RULE_NOT_ACKNOWLEDGED)


def test_any_of_several_tokens_counts() -> None:
    assert engaged_by_receipts(accepted_write(VALUE), ("ZZZ-OTHER", TOKEN)).engaged is True


def test_the_verdict_is_immutable() -> None:
    verdict = engaged_by_receipts(accepted_write(VALUE), (TOKEN,))
    for field in fields(verdict):
        with pytest.raises(FrozenInstanceError):
            setattr(verdict, field.name, None)
