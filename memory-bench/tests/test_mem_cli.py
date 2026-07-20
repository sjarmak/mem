"""Tests for the shared `mem` CLI subprocess seam (`membench/mem_cli.py`).

The seam is the one place the harness shells to the TS CLI; every failure mode
must surface as a `MemCliError` carrying enough context to act on (the command,
the exit/stdout detail, the build hint for a missing binary). Real subprocesses
are exercised with tiny shell stand-ins — no TS build required.

The seam also owns the NDJSON import wire format (`write_ndjson`) and the
`import-records`/`import-lessons` argv contract (`import_records` /
`import_lessons`); the contract is observed from the far side by a fake `mem`
that snapshots its argv and the payload file at call time.
"""

import json
from pathlib import Path

import pytest

from membench.mem_cli import (
    MemCliError,
    import_lessons,
    import_records,
    run_mem_json,
    write_ndjson,
)
from tests.helpers import fake_mem as _fake_mem


def test_returns_envelope_data(tmp_path):
    envelope = {"apiVersion": "v1", "cmd": "query", "ok": True, "data": {"records": [1]}}
    binary = _fake_mem(tmp_path, f"echo '{json.dumps(envelope)}'")
    assert run_mem_json([binary, "query"]) == {"records": [1]}


def test_missing_binary_names_the_fix(tmp_path):
    with pytest.raises(MemCliError, match="npm run build"):
        run_mem_json([str(tmp_path / "absent-mem"), "query"])


def test_unspawnable_binary_surfaces_as_mem_cli_error(tmp_path):
    # A present-but-not-executable binary raises PermissionError, NOT
    # FileNotFoundError -- the rung this seam omitted until mem-o9plh, which let a
    # raw OSError escape past `MemCliError` and out of the seam's contract.
    binary = tmp_path / "unexecutable-mem"
    binary.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    binary.chmod(0o644)
    with pytest.raises(MemCliError, match="could not spawn"):
        run_mem_json([str(binary), "query"])


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
    with pytest.raises(MemCliError, match="did not finish within"):
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


def test_a_credential_on_a_non_envelope_stdout_is_redacted(tmp_path):
    """The other echo site `run_checked` cannot see: this child EXITS 0 too.

    `run_mem_json` builds its own message from raw stdout when the envelope will not parse,
    so the choke point's redaction never ran on it. The 200-char bound was already here and
    stays -- it is tighter than the shared one; only the redaction half was missing.
    """
    binary = _fake_mem(tmp_path, "echo 'AuthError: token sk-ant-oat01-MEMCLICANARY42 rejected'")
    with pytest.raises(MemCliError) as caught:
        run_mem_json([binary, "query"])
    assert "sk-ant-oat01-MEMCLICANARY42" not in str(caught.value)
    assert "sk-ant" not in str(caught.value)
    assert "not a JSON envelope" in str(caught.value)  # the diagnosis survives


def test_the_non_envelope_diagnosis_redacts_before_it_slices(tmp_path):
    """Redaction must run BEFORE the 200-char cut, or the cut leaves a live prefix.

    A token that straddles character 200 would otherwise survive as a fragment -- the same
    ordering rule the shared sanitiser follows, restated here because this call does its own
    cutting.
    """
    # The token sits AFTER a separator (as a real one does -- "token sk-...", "key=sk-...")
    # and starts near char 190, so the 200-char cut lands inside it.
    padding = "y" * 184
    binary = _fake_mem(tmp_path, f"echo '{padding} token sk-ant-oat01-STRADDLECANARY0123456789'")
    with pytest.raises(MemCliError) as caught:
        run_mem_json([binary, "query"])
    assert "sk-ant" not in str(caught.value)  # no live prefix survived the slice


# --- import_records / import_lessons: the argv contract to the TS importers ------------

ROWS = [{"work_id": "w-0", "n": 1}, {"work_id": "w-1", "n": 2}]


@pytest.fixture
def capturing_mem(tmp_path):
    """(binary, capture_dir): a fake `mem` that snapshots its argv and the `--file`
    payload AT CALL TIME (the tempfile default deletes the file after the call), then
    emits a success envelope. `cp "$3"` doubles as an ordering assert: if `--file
    <path>` moves, the copy fails and the test breaks loudly."""
    capture = tmp_path / "capture"
    capture.mkdir()
    envelope = {"apiVersion": "v1", "cmd": "import", "ok": True, "data": {"imported": 2}}
    binary = _fake_mem(
        tmp_path,
        f'printf \'%s\\n\' "$@" > "{capture}/argv"\n'
        f'cp "$3" "{capture}/payload.ndjson"\n'
        f"echo '{json.dumps(envelope)}'",
    )
    return binary, capture


def _read_ndjson(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _captured(capture_dir: Path) -> tuple[list[str], list[dict]]:
    argv = (capture_dir / "argv").read_text(encoding="utf-8").splitlines()
    return argv, _read_ndjson(capture_dir / "payload.ndjson")


def test_import_records_argv_contract_and_payload(tmp_path, capturing_mem):
    binary, capture = capturing_mem
    store = tmp_path / "store.db"
    data = import_records(ROWS, store_path=store, mem_bin=binary)
    argv, payload = _captured(capture)
    assert argv[0] == "import-records"
    assert argv[1] == "--file"
    assert argv[3:] == ["--store", str(store), "--json"]
    assert payload == ROWS
    assert data == {"imported": 2}


def test_import_lessons_uses_the_lessons_subcommand(tmp_path, capturing_mem):
    binary, capture = capturing_mem
    import_lessons(ROWS, store_path=tmp_path / "store.db", mem_bin=binary)
    argv, payload = _captured(capture)
    assert argv[0] == "import-lessons"
    assert payload == ROWS


def test_import_default_tempfile_is_gone_after_the_call(tmp_path, capturing_mem):
    binary, capture = capturing_mem
    import_records(ROWS, store_path=tmp_path / "store.db", mem_bin=binary)
    argv, payload = _captured(capture)
    assert not Path(argv[2]).exists()
    assert payload == ROWS


def test_import_file_path_persists_the_artifact(tmp_path, capturing_mem):
    binary, capture = capturing_mem
    dest = tmp_path / "records.ndjson"
    import_records(ROWS, store_path=tmp_path / "store.db", mem_bin=binary, file_path=dest)
    argv, _ = _captured(capture)
    assert argv[2] == str(dest)
    assert _read_ndjson(dest) == ROWS


def test_import_failure_raises_mem_cli_error(tmp_path):
    binary = _fake_mem(tmp_path, "echo 'schema mismatch' >&2; exit 2")
    with pytest.raises(MemCliError, match=r"exit 2.*schema mismatch"):
        import_records(ROWS, store_path=tmp_path / "store.db", mem_bin=binary)
