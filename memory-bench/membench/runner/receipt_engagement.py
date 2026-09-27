from __future__ import annotations

import shlex
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from membench.metrics.scorers import states_value
from membench.runner.bd_receipts import CALLER_AGENT
from membench.runner.tool_surface import (
    MEMORY_COMMAND,
    MEMORY_WRITE_VERBS,
    memory_invocations_in_command,
    remember_was_a_recall,
    remember_was_accepted,
)

RULE_NO_TOKENS = "no_tokens"
RULE_NO_RECEIPTS = "no_receipts"
RULE_NOT_FINISH = "not_finish"
RULE_NOT_AGENT = "not_agent"
RULE_NOT_REMEMBER = "not_remember"
RULE_NONZERO_RETURNCODE = "nonzero_returncode"
RULE_RECALL = "recall"
RULE_NOT_ACKNOWLEDGED = "not_acknowledged"
RULE_TOKEN_ABSENT = "token_absent"
RULE_ACKNOWLEDGED_WRITE = "acknowledged_write"

RULE_LADDER: tuple[str, ...] = (
    RULE_NOT_FINISH,
    RULE_NOT_AGENT,
    RULE_NOT_REMEMBER,
    RULE_NONZERO_RETURNCODE,
    RULE_RECALL,
    RULE_NOT_ACKNOWLEDGED,
    RULE_TOKEN_ABSENT,
    RULE_ACKNOWLEDGED_WRITE,
)


@dataclass(frozen=True)
class EngagementVerdict:
    engaged: bool
    row_index: int | None
    rule: str


def _text_field(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    return value if isinstance(value, str) else ""


def _operation_argv(row: Mapping[str, Any]) -> list[str]:
    argv = row.get("operation_argv")
    if not isinstance(argv, list):
        return []
    return [word for word in argv if isinstance(word, str)]


def _is_a_remember(operation_argv: Sequence[str]) -> bool:
    command = shlex.join([MEMORY_COMMAND, *operation_argv])
    return any(
        invocation.verb in MEMORY_WRITE_VERBS
        for invocation in memory_invocations_in_command(command)
    )


def _states_a_token(operation_argv: Sequence[str], stdout: str, tokens: Collection[str]) -> bool:
    argv_text = " ".join(operation_argv)
    return any(states_value(argv_text, token) or states_value(stdout, token) for token in tokens)


def rule_of(row: Mapping[str, Any], tokens: Collection[str]) -> str:
    if row.get("event") != "finish":
        return RULE_NOT_FINISH
    if row.get("caller") != CALLER_AGENT:
        return RULE_NOT_AGENT
    operation_argv = _operation_argv(row)
    if not _is_a_remember(operation_argv):
        return RULE_NOT_REMEMBER
    if row.get("returncode") != 0:
        return RULE_NONZERO_RETURNCODE
    stdout = _text_field(row, "stdout")
    if remember_was_a_recall(stdout) or remember_was_a_recall(_text_field(row, "stderr")):
        return RULE_RECALL
    if not remember_was_accepted(stdout):
        return RULE_NOT_ACKNOWLEDGED
    if not _states_a_token(operation_argv, stdout, tokens):
        return RULE_TOKEN_ABSENT
    return RULE_ACKNOWLEDGED_WRITE


def engaged_by_receipts(
    rows: Sequence[Mapping[str, Any]], tokens: Collection[str]
) -> EngagementVerdict:
    if not tokens:
        return EngagementVerdict(engaged=False, row_index=None, rule=RULE_NO_TOKENS)
    if not rows:
        return EngagementVerdict(engaged=False, row_index=None, rule=RULE_NO_RECEIPTS)
    furthest_rank = -1
    furthest_index = 0
    for index, row in enumerate(rows):
        rule = rule_of(row, tokens)
        if rule == RULE_ACKNOWLEDGED_WRITE:
            return EngagementVerdict(engaged=True, row_index=index, rule=rule)
        rank = RULE_LADDER.index(rule)
        if rank > furthest_rank:
            furthest_rank, furthest_index = rank, index
    return EngagementVerdict(
        engaged=False, row_index=furthest_index, rule=RULE_LADDER[furthest_rank]
    )


__all__ = [
    "RULE_ACKNOWLEDGED_WRITE",
    "RULE_LADDER",
    "RULE_NONZERO_RETURNCODE",
    "RULE_NOT_ACKNOWLEDGED",
    "RULE_NOT_AGENT",
    "RULE_NOT_FINISH",
    "RULE_NOT_REMEMBER",
    "RULE_NO_RECEIPTS",
    "RULE_NO_TOKENS",
    "RULE_RECALL",
    "RULE_TOKEN_ABSENT",
    "EngagementVerdict",
    "engaged_by_receipts",
    "rule_of",
]
