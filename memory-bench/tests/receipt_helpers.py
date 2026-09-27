from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import Any

from membench.runner.bd_receipts import CALLER_AGENT

BINARY = "/opt/bd"
STORE = "/tmp/cell/store"
LEG_ID = "bd-0123456789abcdef-leg-1"


def receipt_rows(
    operation_argv: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    caller: str = CALLER_AGENT,
    invocation_id: str = "inv-1",
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity: dict[str, Any] = {
        "invocation_id": invocation_id,
        "argv": [BINARY, "-C", STORE, *operation_argv],
        "operation_argv": list(operation_argv),
        "start_monotonic_ns": 1,
        "tool_use_id": "toolu_01",
        "session_id": "beads-trusted-0",
        "leg_id": LEG_ID,
        "caller": caller,
    }
    start = {**identity, "event": "start"}
    finish = {
        **identity,
        "event": "finish",
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_base64": base64.b64encode(stdout.encode("utf-8")).decode("ascii"),
        "stderr_base64": base64.b64encode(stderr.encode("utf-8")).decode("ascii"),
        "end_monotonic_ns": 2,
    }
    return start, finish


def accepted_write(
    value: str, *, key: str = "window", caller: str = CALLER_AGENT
) -> tuple[dict[str, Any], dict[str, Any]]:
    return receipt_rows(
        ["remember", value, "--key", key],
        stdout=f"Remembered [{key}]: {value}\n",
        caller=caller,
    )


def receipt_text(rows: Sequence[dict[str, Any]]) -> str:
    return "".join(f"{json.dumps(row, sort_keys=True)}\n" for row in rows)
