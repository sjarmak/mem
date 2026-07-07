#!/usr/bin/env bash
# check-loo-parity.sh — read-only diagnostic for the temporal-LOO parity contract.
#
# Re-derives, mechanically, whether the TypeScript store half and the Python
# eval half still agree on the LOO surface:
#   1. sibling-exclusion axes present in each half (isSibling vs is_sibling)
#   2. timestamp canonicalizers + acceptance-grammar markers
#   3. strict null-safe temporal cut markers
#   4. undirected supersedes closure presence
#   5. outcome-label IDENTIFYING_KEYS
#
# Exit 0 = no NEW drift beyond the known, documented gap(s); exit 1 = drift.
# Pure grep; never writes, never runs project code.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
TS_EXCL="$REPO/src/retrieve/exclusions.ts"
TS_TS="$REPO/src/store/timestamp.ts"
TS_READER="$REPO/src/store/reader.ts"
PY_VAL="$REPO/memory-bench/membench/validity.py"
PY_GUARD="$REPO/memory-bench/membench/grading/leak_guard.py"

for f in "$TS_EXCL" "$TS_TS" "$TS_READER" "$PY_VAL" "$PY_GUARD"; do
  [[ -f "$f" ]] || { echo "MISSING FILE: $f" >&2; exit 1; }
done

drift=0
# Axes documented as a known, Stephanie-gated gap (SKILL.md §3). Remove an
# entry here ONLY in the same change that closes the gap in validity.py.
KNOWN_GAPS="parent"

echo "== 1. Sibling-exclusion axes =="
# Axis -> grep marker per half. Edit this list when adding an axis (SKILL.md §5).
AXES="convoy_id pr external_ref parent"
printf '%-14s %-4s %-6s %s\n' axis TS Python verdict
for axis in $AXES; do
  ts=absent; py=absent
  grep -q "query.$axis" "$TS_EXCL" && ts=OK
  grep -q "query.$axis" "$PY_VAL" && py=OK
  verdict="match"
  if [[ "$ts" != "$py" ]]; then
    if [[ " $KNOWN_GAPS " == *" $axis "* ]]; then
      verdict="PARITY GAP (known, documented in SKILL.md §3)"
    else
      verdict="DRIFT"; drift=1
    fi
  fi
  printf '%-14s %-4s %-6s %s\n' "$axis" "$ts" "$py" "$verdict"
done

echo
echo "== 2. Timestamp canonicalizers =="
grep -q "export function toIsoUtc" "$TS_TS" && echo "TS     toIsoUtc: OK" || { echo "TS     toIsoUtc: MISSING"; drift=1; }
grep -q "def canonical_ts" "$PY_VAL" && echo "Python canonical_ts: OK" || { echo "Python canonical_ts: MISSING"; drift=1; }
# Shared grammar marker: the T-or-space separator both acceptance regexes carry.
ts_sep=$(grep -c '\[T \]' "$TS_TS" || true)
py_sep=$(grep -c '\[T \]' "$PY_VAL" || true)
if [[ "$ts_sep" -ge 1 && "$py_sep" -ge 1 ]]; then
  echo "T/space separator grammar present in both: OK"
else
  echo "T/space separator grammar: TS=$ts_sep Python=$py_sep — DRIFT"; drift=1
fi

echo
echo "== 3. Strict null-safe temporal cut =="
grep -q "closed_at IS NOT NULL AND closed_at < ?" "$TS_READER" \
  && echo "TS     reader strict cut: OK" || { echo "TS     reader strict cut: MISSING"; drift=1; }
grep -q "ref.closed is not None" "$PY_VAL" \
  && echo "Python null-safe closed: OK" || { echo "Python null-safe closed: MISSING"; drift=1; }
grep -Eq "canonical_ts\(ref\.closed\) < boundary" "$PY_VAL" \
  && echo "Python strict canonical cut: OK" || { echo "Python strict canonical cut: MISSING"; drift=1; }

echo
echo "== 4. Undirected supersedes closure =="
grep -q "supersedesClosure" "$TS_READER" \
  && echo "TS     supersedesClosure: OK" || { echo "TS     supersedesClosure: MISSING"; drift=1; }
grep -q "def supersedes_closure" "$PY_VAL" \
  && echo "Python supersedes_closure: OK" || { echo "Python supersedes_closure: MISSING"; drift=1; }

echo
echo "== 5. Outcome-label identifying keys =="
keys=$(grep -o 'IDENTIFYING_KEYS = (.*)' "$PY_GUARD" || true)
if [[ "$keys" == *'"pr"'* && "$keys" == *'"commit_sha"'* && "$keys" == *'"base_commit"'* ]]; then
  echo "IDENTIFYING_KEYS pr/commit_sha/base_commit: OK"
else
  echo "IDENTIFYING_KEYS changed: $keys — re-verify SKILL.md §2.5"; drift=1
fi

echo
if [[ "$drift" -eq 0 ]]; then
  echo "RESULT: no new drift (known gaps above remain Stephanie-gated; see SKILL.md §3)."
else
  echo "RESULT: DRIFT detected — the parity contract moved; re-verify SKILL.md §2–3 before trusting it."
  exit 1
fi
