"""Unit tests for the pure parse logic of build_codeprobe_substrate (mem-bxhh.3).

The Docker curation legs are exercised end-to-end by the substrate build itself;
here we pin the stdout->TraceError parse, which must match the store's own pytest
extractor (src/parse/error-extractors.ts PYTEST_PATTERN) signature-for-signature.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "build_codeprobe_substrate",
    Path(__file__).resolve().parents[1] / "scripts" / "build_codeprobe_substrate.py",
)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
parse_pytest_failures = _MOD.parse_pytest_failures


def test_parses_repo_crash_line() -> None:
    # --tb=line emits the container-absolute repo path; the /app prefix is stripped
    # and line is pinned to 0 (the store's line-invariant pytest signature axis).
    stdout = (
        "=== FAILURES ===\n"
        "/app/tests/test_stats.py:212: AssertionError: assert 3 == 0\n"
        "1 failed in 0.10s\n"
    )
    assert parse_pytest_failures(stdout) == [
        {
            "tool": "pytest",
            "severity": "error",
            "message": "AssertionError: assert 3 == 0",
            "file": "tests/test_stats.py",
            "line": 0,
        }
    ]


def test_ignores_stdlib_frames_and_attributes_collection_error_to_repo_file() -> None:
    # The collection-error import chain: a stdlib frame (NOT a failure), the repo
    # test-file frame (a bare "in <module>" marker, NOT a failure), then the real
    # exception E-line attributed to that repo file.
    stdout = (
        "ImportError while importing test module '/app/tests/test_new_feature.py'.\n"
        "/usr/local/lib/python3.11/importlib/__init__.py:88: in import_module\n"
        "    from codeprobe.feature import thing\n"
        "E   ModuleNotFoundError: No module named 'feature'\n"
        "ERROR tests/test_new_feature.py\n"
    )
    errors = parse_pytest_failures(stdout)
    assert errors == [
        {
            "tool": "pytest",
            "severity": "error",
            "message": "ModuleNotFoundError: No module named 'feature'",
            "file": "tests/test_new_feature.py",
            "line": 0,
        }
    ]


def test_multiple_failures_dedup_by_file_message() -> None:
    stdout = (
        "/app/tests/test_a.py:10: AssertionError: nope\n"
        "/app/tests/test_a.py:10: AssertionError: nope\n"  # duplicate collapses
        "/app/tests/test_b.py:7: TypeError: bad\n"
    )
    errors = parse_pytest_failures(stdout)
    assert [e["file"] for e in errors] == ["tests/test_a.py", "tests/test_b.py"]
    assert [e["message"] for e in errors] == ["AssertionError: nope", "TypeError: bad"]


def test_no_failures_returns_empty() -> None:
    assert parse_pytest_failures("12 passed in 0.21s\n") == []
