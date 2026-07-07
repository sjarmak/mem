#!/usr/bin/env bash
# check-archaeology-sources.sh -- read-only drift check for mem-failure-archaeology.
# Verifies every source document and code path the chronicle cites still exists
# and still carries its key verdict string. Exits non-zero on any drift.
# Run from the mem repo root. Makes NO changes.

set -u

fail=0

say() { printf '%s\n' "$*"; }

check_file() {
  if [ -e "$1" ]; then
    say "OK   file  $1"
  else
    say "MISS file  $1"
    fail=1
  fi
}

check_grep() {
  # $1 = pattern (fixed string), $2 = file, $3 = label
  if [ ! -e "$2" ]; then
    say "MISS file  $2  (needed for: $3)"
    fail=1
    return
  fi
  if grep -qF -- "$1" "$2"; then
    say "OK   claim $3"
  else
    say "GONE claim $3  (pattern not found in $2: '$1')"
    fail=1
  fi
}

say "== mem-failure-archaeology source check ($(date -u +%Y-%m-%d)) =="

# --- source documents ---
check_file docs/mem-7q6e-replay-engine-null.md
check_file docs/mem-bxhh3-ours-substrate-data-wall.md
check_file docs/mem-qarg-oracle-repair-wave2.md
check_file docs/mem-1eph-oracle-soundness-gate.md
check_file docs/mem-eacq-variance-pilot.md
check_file docs/mem-outcome-linkage-lever-status.md
check_file docs/audits/2026-07-03-headline-network-fetch-audit.md
check_file docs/csb-validity-port-map.md
check_file docs/architecture-decisions.md

# --- code paths cited ---
check_file memory-bench/membench/bundle/replay.py
check_file memory-bench/membench/grading/validity_gate.py
check_file memory-bench/membench/grading/safety_gates.py
check_file memory-bench/membench/harbor/task_env.py
check_file memory-bench/scripts/bxhh3_ours_substrate_probe.py
check_file src/ingest/landed.ts
check_file src/ingest/commitLinkage.ts
check_file scripts/validate-linked-bundles.mjs

# --- key verdict strings ---
check_grep "8 of 407" README.md \
  "oracle-validity wall: 8 scorable of 407 recovered (README)"
check_grep "mem-1fl8" README.md \
  "real-corpus null is an open release decision (mem-1fl8)"
check_grep "N = 9 is the native ceiling" docs/mem-7q6e-replay-engine-null.md \
  "replay-engine null: N=9 native ceiling"
check_grep "fabricate gold diffs" docs/mem-7q6e-replay-engine-null.md \
  "replay levers fabricate gold diffs (unsound)"
check_grep "0/6 anchors" docs/mem-bxhh3-ours-substrate-data-wall.md \
  "ours-substrate wall: 0/6 anchors"
check_grep "do not re-attempt the gh re-ingest" docs/architecture-decisions.md \
  "D17 ruling: gh re-ingest is dead"
check_grep "bare-host judge" docs/csb-validity-port-map.md \
  "judge contamination citation chain"
check_grep "confabulation_authority" memory-bench/membench/grading/safety_gates.py \
  "judge report-only doctrine in code (flag-until-kappa)"
check_grep "CONTAMINATED" docs/audits/2026-07-03-headline-network-fetch-audit.md \
  "zhy00.oracle network-fetch verdict"
check_grep "CERTIFIED CLEAN" docs/audits/2026-07-03-headline-network-fetch-audit.md \
  "mem-n9 certified clean"
check_grep '"allowlist"' memory-bench/membench/harbor/task_env.py \
  "allowlist network mode still present"

say ""
if [ "$fail" -ne 0 ]; then
  say "DRIFT DETECTED: one or more sources or verdict strings moved. Update"
  say ".claude/skills/mem-failure-archaeology/SKILL.md before trusting it."
  exit 1
fi
say "All archaeology sources and verdict strings verified."
