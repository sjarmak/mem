#!/usr/bin/env bash
# ingest-trace-substrate.sh — nightly trace-substrate ingest (mem-75t.4).
#
# Mechanical cron-triggered exec, no LLM. Runs `mem ingest-traces` from the
# gas-city checkout (the cwd `--with-traces` needs to resolve transcripts via
# `gc session logs`), builds into a scratch store, verifies the coverage axes
# came back non-zero, and only then swaps it over the live sidecar. Appends a
# one-line coverage-delta record to the log.
#
# Idempotent: the writer upserts records and rebuilds child rows, so re-running
# converges. A run that resolves zero traces (wrong cwd) is detected and aborts
# the swap rather than clobbering a good store with an empty one.
set -eo pipefail

GC_CITY="${GC_CITY:-/home/ds/gas-city}"
MEM_REPO="${MEM_REPO:-/home/ds/projects/mem}"
STORE="${MEM_STORE:-$MEM_REPO/.mem/store.db}"
MEM_BIN="${MEM_BIN:-$MEM_REPO/bin/mem}"
LOG="${INGEST_LOG:-$MEM_REPO/.mem/ingest-trace-substrate.log}"

SCRATCH_DIR="$(mktemp -d)"
SCRATCH="$SCRATCH_DIR/store.db"
trap 'rm -rf "$SCRATCH_DIR"' EXIT
DATE_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cd "$GC_CITY"

# Full rebuild into the scratch path. --json so we can read the coverage report
# back deterministically instead of scraping the human lines.
RESULT=$("$MEM_BIN" ingest-traces --store "$SCRATCH" --json)

# Pull the after-coverage out of the success envelope.
read -r WITH_TRACE TRACE_ERRORS WITH_BASE RECORDS <<EOF
$(echo "$RESULT" | jq -r '.data.after | "\(.with_trace) \(.trace_errors) \(.with_base_commit) \(.records)"')
EOF

DELTA=$(echo "$RESULT" | jq -c '.data.delta')

# Guard: a real full run lifts traces off zero. Zero across the board means the
# transcripts did not resolve (wrong cwd / city) — do NOT swap, the live store
# is better than an empty one. Fail loudly so the cron surfaces it.
if [ "${WITH_TRACE:-0}" -eq 0 ] && [ "${TRACE_ERRORS:-0}" -eq 0 ] && [ "${WITH_BASE:-0}" -eq 0 ]; then
  echo "$DATE_UTC ABORT records=$RECORDS with_trace=0 trace_errors=0 with_base_commit=0 (no traces resolved; store NOT swapped)" >>"$LOG"
  echo "ingest-trace-substrate: no traces resolved — refusing to swap an empty store" >&2
  exit 1
fi

mkdir -p "$(dirname "$STORE")"
mv "$SCRATCH" "$STORE"

echo "$DATE_UTC OK records=$RECORDS with_trace=$WITH_TRACE trace_errors=$TRACE_ERRORS with_base_commit=$WITH_BASE delta=$DELTA" >>"$LOG"
echo "ingest-trace-substrate: swapped store records=$RECORDS with_trace=$WITH_TRACE trace_errors=$TRACE_ERRORS with_base_commit=$WITH_BASE"
