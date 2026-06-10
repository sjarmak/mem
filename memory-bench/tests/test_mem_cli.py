"""Tests for the shared `mem` CLI subprocess seam (`membench/mem_cli.py`).

The seam is the one place the harness shells to the TS CLI; every failure mode
must surface as a `MemCliError` carrying enough context to act on (the command,
the exit/stdout detail, the build hint for a missing binary). Real subprocesses
are exercised with tiny shell stand-ins — no TS build required.
"""

import json

import pytest

from membench.mem_cli import MemCliError, run_mem_json


def _fake_mem(tmp_path, body: str) -> str:
    """Write an executable stand-in for the `mem` binary and return its path."""
    script = tmp_path / "fake-mem"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def test_returns_envelope_data(tmp_path):
    envelope = {"apiVersion": "v1", "cmd": "query", "ok": True, "data": {"records": [1]}}
    binary = _fake_mem(tmp_path, f"echo '{json.dumps(envelope)}'")
    assert run_mem_json([binary, "query"]) == {"records": [1]}


def test_missing_binary_names_the_fix(tmp_path):
    with pytest.raises(MemCliError, match="npm run build"):
        run_mem_json([str(tmp_path / "absent-mem"), "query"])


def test_nonzero_exit_carries_stderr(tmp_path):
    binary = _fake_mem(tmp_path, "echo 'no store at .mem/store.db' >&2; exit 3")
    with pytest.raises(MemCliError, match=r"exit 3.*no store"):
        run_mem_json([binary, "query"])


def test_malformed_stdout_carries_excerpt(tmp_path):
    binary = _fake_mem(tmp_path, "echo 'WARN: banner line'")
    with pytest.raises(MemCliError, match=r"not a JSON envelope.*banner"):
        run_mem_json([binary, "query"])


def test_error_envelope_raises_with_errors(tmp_path):
    envelope = {"apiVersion": "v1", "cmd": "query", "ok": False, "errors": ["bad flag"]}
    binary = _fake_mem(tmp_path, f"echo '{json.dumps(envelope)}'")
    with pytest.raises(MemCliError, match="bad flag"):
        run_mem_json([binary, "query"])


def test_timeout_is_bounded_and_loud(tmp_path):
    binary = _fake_mem(tmp_path, "sleep 5")
    with pytest.raises(MemCliError, match="timed out"):
        run_mem_json([binary, "query"], timeout_s=0.2)


def test_mem_cli_error_is_a_runtime_error(tmp_path):
    """Call sites that caught RuntimeError keep working."""
    binary = _fake_mem(tmp_path, "exit 1")
    with pytest.raises(RuntimeError):
        run_mem_json([binary, "query"])
