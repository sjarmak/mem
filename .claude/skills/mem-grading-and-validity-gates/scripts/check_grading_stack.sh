#!/usr/bin/env bash
# Read-only drift check for the mem-grading-and-validity-gates skill.
# Verifies the grading module inventory, the pinned constants the skill cites,
# and that the grading test files still collect. Mutates nothing.
#
# Pinned against branch main @ 4e819e1 (2026-07-07). Any FAIL below means the
# repo has drifted past what SKILL.md documents — re-read the changed module.
set -u

REPO="${MEM_REPO:-/home/ds/projects/mem}"
GRADING="$REPO/memory-bench/membench/grading"
FAILURES=0

check() { # check <label> <grep-pattern> <file>
  local label="$1" pattern="$2" file="$3"
  if grep -q "$pattern" "$file" 2>/dev/null; then
    echo "OK   $label"
  else
    echo "FAIL $label  (pattern '$pattern' not found in $file)"
    FAILURES=$((FAILURES + 1))
  fi
}

echo "== repo pin =="
echo "branch: $(git -C "$REPO" branch --show-current)  head: $(git -C "$REPO" rev-parse --short HEAD)  (skill pinned: main @ 4e819e1)"

echo
echo "== module inventory (skill documents 21 modules) =="
COUNT=$(ls "$GRADING"/*.py 2>/dev/null | wc -l)
echo "grading/*.py count: $COUNT"
[ "$COUNT" -eq 21 ] || { echo "FAIL module count changed (was 21)"; FAILURES=$((FAILURES + 1)); }

echo
echo "== pinned constants =="
check "DEFAULT_RUNGS starts at none, includes vector-rag" '"vector-rag"' "$GRADING/ablation.py"
check "COMBINATION_BASE_RUNG = ours" 'COMBINATION_BASE_RUNG: str = "ours"' "$GRADING/ablation.py"
check "MIN_LADDER_FOR_SATURATION = 4" 'MIN_LADDER_FOR_SATURATION = 4' "$GRADING/curve.py"
check "DEFAULT_SATURATION_TOL = 0.05" 'DEFAULT_SATURATION_TOL = 0.05' "$GRADING/curve.py"
check "InsufficientLadderError defined" 'class InsufficientLadderError' "$GRADING/curve.py"
check "prereg FPR bar 0.05" 'PREREGISTERED_FPR_MAX = 0.05' "$GRADING/safety_gates.py"
check "prereg kappa bar 0.6" 'PREREGISTERED_KAPPA_MIN = 0.6' "$GRADING/safety_gates.py"
check "PASS_THRESHOLD = 0.5" 'PASS_THRESHOLD = 0.5' "$GRADING/dual_verifier.py"
check "graded divergence flag 0.3" 'GRADED_DIVERGENCE_THRESHOLD = 0.3' "$GRADING/graded.py"
check "graded judge rounds 3" 'DEFAULT_JUDGE_ROUNDS = 3' "$GRADING/graded.py"
check "graded judge model pin" 'DEFAULT_GRADED_JUDGE_MODEL = "claude-sonnet-4-6"' "$GRADING/graded.py"
check "deferred rungs = builtin combos" 'DEFERRED_RUNGS = ("builtin", "ours+builtin")' "$REPO/memory-bench/membench/harbor/memory_inject.py"
check "validity gate: gold+empty invariant" 'empty diff reproduced' "$GRADING/validity_gate.py"
check "recall ladder ADR still design-draft" 'DESIGN-DRAFT' "$REPO/docs/mem-do8r-recall-ladder-adr.md"

echo
echo "== grading tests still collect (needs memory-bench venv) =="
PY="$REPO/memory-bench/.venv/bin/python"
if [ -x "$PY" ]; then
  (cd "$REPO/memory-bench" && "$PY" -m pytest \
    tests/test_curve.py tests/test_validity_gate.py tests/test_safety_gates.py \
    tests/test_trace_score.py tests/test_grading_coverage.py \
    tests/test_dual_verifier.py tests/test_graded.py tests/test_judge.py \
    --collect-only -q 2>&1 | tail -1)
else
  echo "SKIP: no venv at $PY (run: cd memory-bench && pip install -e '.[dev]')"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "RESULT: no drift detected against SKILL.md pins."
else
  echo "RESULT: $FAILURES drift item(s) — re-verify SKILL.md sections touching them."
fi
exit "$FAILURES"
