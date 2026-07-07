#!/usr/bin/env bash
# ingest-preflight.sh — READ-ONLY diagnostic for the mem ingest path.
#
# Answers "why did my ingest populate nothing / would it?" without building
# anything. Checks, in order:
#   1. entrypoint: ./bin/mem exists and dist/ is not stale vs src/ mtimes
#   2. cwd trap: is `gc` on PATH, and does the CURRENT cwd hold a city.toml
#      (the thing `gc session logs` needs — without it --with-traces resolves
#      zero traces with exit 0)
#   3. dolt: is the shared bead server reachable (port file or 29620)
#   4. store: coverage axes of the store you point it at (via `mem coverage`)
#
# Usage: bash ingest-preflight.sh [STORE_PATH]
#   STORE_PATH default: /home/ds/projects/mem/.mem/store.db
# Exit code: 0 always (it is a report, not a gate). Read the FAIL/WARN lines.

set -u

MEM_REPO="${MEM_REPO:-/home/ds/projects/mem}"
STORE="${1:-$MEM_REPO/.mem/store.db}"

pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; }

echo "== mem ingest preflight (read-only) — cwd: $(pwd) =="

# 1. entrypoint + dist staleness
if [ -x "$MEM_REPO/bin/mem" ] && [ -f "$MEM_REPO/dist/main.js" ]; then
  newest_src=$(find "$MEM_REPO/src" -name '*.ts' -newer "$MEM_REPO/dist/main.js" 2>/dev/null | head -1)
  if [ -n "$newest_src" ]; then
    warn "dist/ is STALE (src newer than dist/main.js, e.g. $newest_src) — ./bin/mem runs old code; run: (cd $MEM_REPO && npm run build)"
  else
    pass "entrypoint $MEM_REPO/bin/mem present, dist/ not older than src/"
  fi
else
  fail "missing $MEM_REPO/bin/mem or dist/main.js — run: (cd $MEM_REPO && npm ci && npm run build)"
fi

# 2. the --with-traces cwd trap
if command -v gc >/dev/null 2>&1; then
  pass "gc binary on PATH ($(command -v gc))"
  if [ -f ./city.toml ]; then
    pass "city.toml present in cwd — 'gc session logs' can resolve sessions from here"
  else
    warn "NO city.toml in cwd — a --with-traces build run from HERE exits 0 with ZERO traces resolved (the silent trap). Run full rebuilds from the gas-city checkout with an absolute --store path."
  fi
else
  fail "gc binary NOT on PATH — --with-traces would error loudly (missing binary propagates); spine-only builds unaffected"
fi

# 3. dolt reachability (port file in cwd, else conventional 29620)
PORT=29620
if [ -f .beads/dolt-server.port ]; then
  PORT=$(tr -d '[:space:]' < .beads/dolt-server.port)
fi
if command -v dolt >/dev/null 2>&1; then
  if dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls sql -q 'select 1' >/dev/null 2>&1; then
    pass "dolt sql-server reachable on 127.0.0.1:$PORT — spine reader has a source"
  else
    fail "dolt sql-server NOT reachable on 127.0.0.1:$PORT — spine reader will fail (do NOT start/stop the server yourself; see the city docs)"
  fi
else
  fail "dolt CLI not on PATH — the beads spine reader cannot run"
fi

# 4. store coverage (read-only report)
if [ -f "$STORE" ]; then
  echo "-- coverage of $STORE --"
  if out=$("$MEM_REPO/bin/mem" coverage --store "$STORE" 2>&1); then
    echo "$out"
    if echo "$out" | grep -Eq '^with_trace +0/'; then
      warn "with_trace is 0 — if this store came from a --with-traces build, it was run from the wrong cwd; do not swap it into place"
    fi
  else
    fail "mem coverage failed: $out"
  fi
else
  warn "no store at $STORE (fresh checkout? build one, or pass a path: bash ingest-preflight.sh /path/store.db)"
fi

echo "== done (report only; nothing was modified) =="
