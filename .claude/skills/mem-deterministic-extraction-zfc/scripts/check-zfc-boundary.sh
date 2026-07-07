#!/usr/bin/env bash
# check-zfc-boundary.sh — read-only diagnostic for mem's ZFC boundary.
#
# Verifies, against the working tree, the structural facts the
# mem-deterministic-extraction-zfc skill relies on:
#   1. Model invocation (spawnSync of `claude`) is confined to src/distill/.
#   2. The parse layer's five files exist.
#   3. The one-definition rule: failureSignature/errorClass are imported from
#      src/parse/recurrence.js by the known consumers, never reimplemented.
#   4. The task-type model artifact is consumed by lookup only (no model call
#      in src/ingest/task-type.ts).
#
# Exit 0 = all checks pass; exit 1 = at least one FAIL (boundary drifted —
# re-verify the skill against HEAD before trusting it). Read-only: no writes,
# no network, no model calls.

set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"

fail=0
pass() { printf 'PASS  %s\n' "$1"; }
flunk() {
  printf 'FAIL  %s\n' "$1"
  fail=1
}

# 1. spawnSync confined to src/distill/ (other child_process sites are
#    mechanical IO: git/gh/bd/gc in src/ingest/).
outside=$(grep -rln "spawnSync" src --include='*.ts' | grep -v '^src/distill/' || true)
if [ -z "$outside" ]; then
  pass "spawnSync appears only under src/distill/"
else
  flunk "spawnSync outside src/distill/: $outside"
fi

claude_calls=$(grep -rln "spawnSync(\s*'claude'\|spawnSync('claude'" src --include='*.ts' | grep -v '^src/distill/' || true)
if [ -z "$claude_calls" ]; then
  pass "no 'claude' subprocess call outside src/distill/"
else
  flunk "'claude' invoked outside src/distill/: $claude_calls"
fi

# 2. Parse-layer inventory.
for f in runners.ts error-extractors.ts recurrence.ts trace-parse.ts index.ts; do
  if [ -f "src/parse/$f" ]; then
    pass "src/parse/$f present"
  else
    flunk "src/parse/$f missing (parse layer restructured — update the skill)"
  fi
done

# 3. One-definition rule: consumers import from parse/recurrence, and no file
#    outside src/parse/ declares its own failureSignature/errorClass.
for consumer in src/store/writer.ts src/retrieve/retrieval.ts src/cli/commands/extract-errors.ts; do
  if grep -q "from '.*parse/recurrence.js'" "$consumer" 2>/dev/null; then
    pass "$consumer imports signature primitives from parse/recurrence"
  else
    flunk "$consumer no longer imports from parse/recurrence.js"
  fi
done

redefs=$(grep -rln "function failureSignature\|function errorClass" src --include='*.ts' | grep -v '^src/parse/recurrence.ts' || true)
if [ -z "$redefs" ]; then
  pass "failureSignature/errorClass defined only in src/parse/recurrence.ts"
else
  flunk "reimplemented signature primitives in: $redefs"
fi

# 4. task-type.ts is lookup-only (no subprocess, no model call).
if ! grep -q "child_process\|spawnSync\|execFile" src/ingest/task-type.ts 2>/dev/null; then
  pass "src/ingest/task-type.ts is artifact-lookup only (no subprocess)"
else
  flunk "src/ingest/task-type.ts gained a subprocess call — ZFC drift"
fi

# Informational: extractor count (tracks EXTRACTORS registrations).
n=$(grep -c "ErrorExtractor = {" src/parse/error-extractors.ts 2>/dev/null || echo '?')
printf 'INFO  extractor tool registrations in error-extractors.ts: %s (8 as of 2026-07-07)\n' "$n"

exit "$fail"
