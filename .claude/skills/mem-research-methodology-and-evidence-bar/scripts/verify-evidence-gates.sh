#!/usr/bin/env bash
# Read-only diagnostic for the mem evidence bar (skill:
# mem-research-methodology-and-evidence-bar). Runs the fast in-repo checks for
# Gate 1 (oracle soundness), Gate 2 (paired deltas / bootstrap CI), and Gate 5
# (judge report-only + safety gates). Offline: every test here runs on stub
# judges and fakes; no model, no network, no store mutation.
#
# Usage: scripts/verify-evidence-gates.sh [repo-root]   (default: repo of this script)
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
BENCH="$ROOT/memory-bench"
PY="$BENCH/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "ERROR: $PY not found. Create the env first:" >&2
  echo "  cd $BENCH && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

echo "== Doctrine anchors (grep must find each; a miss means the doctrine moved) =="
grep -n "the spec governs" "$ROOT/docs/architecture-decisions.md" | head -1
grep -n "supersede in place, do not rewrite history" "$ROOT/docs/architecture-decisions.md" | head -1
grep -n "Do not re-litigate this" "$ROOT/docs/architecture-decisions.md" | head -1
grep -n "SIDE SIGNAL" "$BENCH/membench/grading/graded.py" | head -1
grep -n "POPULATION_PRIMARY" "$BENCH/membench/grading/paired_ci.py" | head -1
grep -n "PREREGISTERED_FPR_MAX" "$BENCH/membench/grading/safety_gates.py" | head -1

echo
echo "== Gate 1: oracle validity gate (gold reproduces AND empty fails) =="
(cd "$BENCH" && "$PY" -m pytest tests/test_validity_gate.py tests/test_admit_validity_gate.py -q)

echo
echo "== Gate 2: paired per-task deltas + bootstrap CI =="
(cd "$BENCH" && "$PY" -m pytest tests/ -q -k "paired")

echo
echo "== Gate 5: judge report-only + safety gates never averaged =="
(cd "$BENCH" && "$PY" -m pytest tests/test_safety_gates.py tests/test_judge.py -q)

echo
echo "All evidence-bar diagnostics passed."
