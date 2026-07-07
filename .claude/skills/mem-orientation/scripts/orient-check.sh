#!/usr/bin/env bash
# orient-check.sh — read-only orientation diagnostic for the mem repo.
# Prints the facts mem-orientation/SKILL.md pins, so drift is visible at a
# glance. Makes NO writes, NO network calls, runs NO builds or benchmarks.
#
# Usage: bash .claude/skills/mem-orientation/scripts/orient-check.sh
#        (from anywhere inside the repo; it resolves the repo root itself)
set -euo pipefail

root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: not inside a git checkout of mem" >&2
  exit 1
}
cd "$root"

section() { printf '\n== %s\n' "$1"; }

section "Checkout"
printf 'root:   %s\n' "$root"
printf 'branch: %s\n' "$(git branch --show-current)"
printf 'HEAD:   %s\n' "$(git rev-parse --short HEAD)"

section "Schema version (authoritative: code, not docs)"
grep -n "SCHEMA_VERSION" src/store/schema.ts || echo "MISSING: src/store/schema.ts"
printf 'README says:    %s\n' "$(grep -o 'schema v[0-9]*' README.md | head -1 || echo 'no mention')"
printf 'orient.md says: %s\n' "$(grep -o 'schema v[0-9]*' architecture/exports/orient.md | head -1 || echo 'no mention')"

section "TS half"
if [ -f dist/main.js ]; then
  echo "dist/main.js: present (bin/mem will run THIS build — rebuild after editing src/)"
else
  echo "dist/main.js: ABSENT — ./bin/mem will fail; run: npm ci && npm run build"
fi
printf 'TS test files:  %s\n' "$(find tests -name '*.test.ts' 2>/dev/null | wc -l)"

section "Python half"
printf 'membench package: %s\n' "$([ -d memory-bench/membench ] && echo present || echo MISSING)"
printf 'Py test files:  %s\n' "$(find memory-bench/tests \( -name 'test_*.py' -o -name '*_test.py' \) 2>/dev/null | wc -l)"

section "Store sidecar (generated, gitignored)"
if [ -f .mem/store.db ]; then
  ls -la .mem/store.db
else
  echo ".mem/store.db: absent (build via 'mem build-store'; see mem-ingest-and-provenance)"
fi

section "Root residue dirs (stale agent-session dirs — IGNORE, never source)"
count="$(ls -d mem-*/ 2>/dev/null | wc -l | tr -d ' ')"
printf 'mem-*/ dirs at root: %s (skill pinned 60 on 2026-07-07)\n' "$count"

section "Branch population (in-flight work lives in branches, not root dirs)"
printf 'local branches: %s\n' "$(git for-each-ref refs/heads --format='x' | wc -l)"

section "Decision ledger"
printf 'highest numbered Decision: %s (skill pinned 24 on 2026-07-07)\n' \
  "$(grep -oE '^[0-9]+\. \*\*' docs/architecture-decisions.md | grep -oE '[0-9]+' | sort -n | tail -1 || echo 'NOT FOUND')"

section "Reading route (all five must exist)"
for f in README.md ARCHITECTURE.md docs/architecture-decisions.md \
  architecture/exports/orient.md memory-bench/README.md; do
  [ -f "$f" ] && echo "ok      $f" || echo "MISSING $f"
done
