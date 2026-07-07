#!/usr/bin/env bash
# verify-contract.sh — read-only anchor check for the mem architecture contract.
#
# Confirms that every file/line anchor cited in
# mem-decision-ledger-and-architecture-contract/SKILL.md still exists in the
# working tree. Pure greps and ls; executes nothing, mutates nothing.
#
# Run from the repo root:
#   bash .claude/skills/mem-decision-ledger-and-architecture-contract/scripts/verify-contract.sh
#
# Exit code: 0 = all anchors hold; 1 = at least one DRIFT (re-verify the skill
# against the code before trusting its pointers).

set -u

fail=0

check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf 'PASS  %s\n' "$label"
  else
    printf 'DRIFT %s\n' "$label"
    fail=1
  fi
}

# Run from the repo root regardless of invocation cwd.
cd "$(dirname "$0")/../../../.." || exit 1

# Invariant 1 — source of truth / schema version constant exists.
check "schema version constant (src/store/schema.ts SCHEMA_VERSION)" \
  grep -q "SCHEMA_VERSION" src/store/schema.ts

# Invariant 2 — temporal LOO enforcement points, both languages.
check "strict temporal cut (reader.ts closedBefore)" \
  grep -q "closedBefore" src/store/reader.ts
check "canonical UTC timestamps (store/timestamp.ts toIsoUtc)" \
  grep -q "toIsoUtc" src/store/timestamp.ts
check "sibling exclusions (retrieve/exclusions.ts isSibling)" \
  grep -q "isSibling" src/retrieve/exclusions.ts
check "supersedes closure (reader.ts supersedesClosure)" \
  grep -q "supersedesClosure" src/store/reader.ts
check "python LOO mirror (validity.py canonical_ts)" \
  grep -q "canonical_ts" memory-bench/membench/validity.py
check "python leak guard (grading/leak_guard.py)" \
  ls memory-bench/membench/grading/leak_guard.py

# Invariant 3 — the three append-only, non-regenerable tables.
check "lessons table (schema.ts)" \
  grep -q "CREATE TABLE lessons" src/store/schema.ts
check "provenance_events table (schema.ts)" \
  grep -q "CREATE TABLE provenance_events" src/store/schema.ts
check "memory_events table (schema.ts)" \
  grep -q "CREATE TABLE memory_events" src/store/schema.ts

# Invariant 4 — landed oracle enum and fail-closed states.
check "landed oracle states (ingest/landed.ts landed_state)" \
  grep -aq "landed_state" src/ingest/landed.ts
check "ambiguous-window state present (landed.ts)" \
  grep -aq "ambiguous-window" src/ingest/landed.ts

# Invariant 5 — synthetic one-path marker.
check "synthetic origin marker (generators/synthetic_corpus.py)" \
  grep -q 'origin' memory-bench/membench/generators/synthetic_corpus.py

# Decision 22 — forward-capture post-close re-scan on the live path.
check "post-close value re-scan (forward_capture.py rescan_closed_work)" \
  grep -q "rescan_closed_work" memory-bench/membench/forward_capture.py

# Decision 23 — separable issue-trigger control flag.
check "ours issue-trigger control (retrieve.ts --no-trace-query)" \
  grep -q -- "--no-trace-query" src/cli/commands/retrieve.ts

# Weak point 6 — zero-links guard location.
check "zero-links guard (build-store.ts checkRecordLinks)" \
  grep -q "checkRecordLinks" src/cli/commands/build-store.ts

# Ledger — decision count (skill distills exactly 24; more means new rulings).
n=$(grep -cE "^[0-9]+\. \*\*" docs/architecture-decisions.md 2>/dev/null || echo 0)
if [ "$n" -eq 24 ]; then
  printf 'PASS  decision ledger count = 24\n'
elif [ "$n" -gt 24 ]; then
  printf 'DRIFT decision ledger count = %s (>24): new Decisions exist — update the skill\n' "$n"
  fail=1
else
  printf 'DRIFT decision ledger count = %s (<24): ledger moved or reformatted\n' "$n"
  fail=1
fi

exit "$fail"
