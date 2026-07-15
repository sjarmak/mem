"""Tests for the shared `mem` CLI subprocess seam (`membench/mem_cli.py`).

The seam is the one place the harness shells to the TS CLI; every failure mode
must surface as a `MemCliError` carrying enough context to act on (the command,
the exit/stdout detail, the build hint for a missing binary). Real subprocesses
are exercised with tiny shell stand-ins — no TS build required.

It also owns the CLI's INPUT format (`write_ndjson`), tested here for the same
reason: every seeder that feeds `mem import-records/-lessons` spells it once.
"""

import json

import pytest

from membench.mem_cli import MemCliError, run_mem_json, write_ndjson
from tests.helpers import fake_mem as _fake_mem


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


def test_input_is_piped_to_stdin(tmp_path):
    envelope = {"apiVersion": "v1", "cmd": "extract-errors", "ok": True, "data": {"echoed": None}}
    binary = _fake_mem(
        tmp_path,
        f'stdin=$(cat); echo \'{json.dumps(envelope)}\' | sed "s/null/\\"$stdin\\"/"',
    )
    assert run_mem_json([binary, "extract-errors"], input="hello") == {"echoed": "hello"}


# --- write_ndjson: the import format `mem import-records/-lessons` reads ---------------


def test_write_ndjson_writes_one_terminated_json_object_per_line(tmp_path):
    path = tmp_path / "records.ndjson"
    write_ndjson(path, [{"work_id": "w-0"}, {"work_id": "w-1"}])

    text = path.read_text(encoding="utf-8")
    assert text == '{"work_id": "w-0"}\n{"work_id": "w-1"}\n'
    assert [json.loads(line) for line in text.splitlines()] == [
        {"work_id": "w-0"},
        {"work_id": "w-1"},
    ]


def test_write_ndjson_writes_an_empty_file_for_no_rows(tmp_path):
    """A blank line is not a record. Joining on the separator instead of terminating
    each line would emit one for an empty corpus — the drift that existed while two
    seeders each spelled this format themselves (mem-rsmq7)."""
    path = tmp_path / "empty.ndjson"
    write_ndjson(path, [])

    assert path.read_text(encoding="utf-8") == ""
