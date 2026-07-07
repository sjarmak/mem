#!/usr/bin/env bash
# Read-only readiness diagnostic for memory-bench eval runs.
# Reports PASS/FAIL per precondition; mutates nothing. Exit 0 always
# (it is a report, not a gate) — read the output.
#
# Usage: bash .claude/skills/mem-eval-harness-run/scripts/check-harness-env.sh
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
BENCH="$REPO_ROOT/memory-bench"

ok()   { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; }
info() { printf 'INFO  %s\n' "$1"; }

echo "== mem eval-harness readiness ($(date -u +%F)) =="
info "repo root: $REPO_ROOT"

# --- Lane 1: in-process deterministic ---
if (cd "$BENCH" && python3 -c "import membench" 2>/dev/null); then
  ok "python can import membench (from $BENCH)"
else
  bad "membench not importable — activate memory-bench/.venv or: cd memory-bench && pip install -e \".[dev]\""
fi

if (cd "$BENCH" && python3 -m membench.cli --help >/dev/null 2>&1); then
  ok "membench CLI answers --help"
else
  bad "membench CLI broken (python3 -m membench.cli --help failed)"
fi

# --- replay / ours prerequisites ---
if [ -f "$REPO_ROOT/dist/main.js" ]; then
  ok "TS build present (dist/main.js) — bin/mem will run current code only if freshly built"
else
  bad "no dist/main.js — run: (cd $REPO_ROOT && npm run build) before replay/ours"
fi

if [ -f "$REPO_ROOT/.mem/store.db" ]; then
  size=$(du -h "$REPO_ROOT/.mem/store.db" | cut -f1)
  ok ".mem/store.db present ($size). NOTE: 'ours' fires only if built --with-traces (see mem-ingest-and-provenance)"
else
  bad "no .mem/store.db — build the store first (mem-ingest-and-provenance)"
fi

# --- Lane 2: Harbor / Docker / OAuth ---
if command -v harbor >/dev/null 2>&1 || (cd "$BENCH" && python3 -c "import harbor" 2>/dev/null); then
  ok "harbor available (CLI or python package)"
else
  bad "harbor missing — pip install -e \".[harbor]\" in memory-bench (only needed for real 'harbor run')"
fi

if docker info >/dev/null 2>&1; then
  ok "docker daemon reachable"
else
  bad "docker unreachable (only needed for Lane 2 / curate-ftp)"
fi

if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  ok "CLAUDE_CODE_OAUTH_TOKEN is set in this process env (required for real harbor runs; never put it in agent.env)"
else
  info "CLAUDE_CODE_OAUTH_TOKEN not set — fine for Lane 1; required before any real 'harbor run'"
fi

# --- launch env vars for mem-CLI arms ---
if [ -n "${MEMBENCH_MEMORY_SYSTEM:-}" ]; then
  info "MEMBENCH_MEMORY_SYSTEM=${MEMBENCH_MEMORY_SYSTEM} (ours/ours-live also need MEMBENCH_MEM_BIN + MEMBENCH_MEM_STORE)"
  case "${MEMBENCH_MEMORY_SYSTEM}" in
    ours|ours-live)
      [ -n "${MEMBENCH_MEM_BIN:-}" ]   && ok "MEMBENCH_MEM_BIN set"   || bad "MEMBENCH_MEM_BIN unset — launch will raise"
      [ -n "${MEMBENCH_MEM_STORE:-}" ] && ok "MEMBENCH_MEM_STORE set" || bad "MEMBENCH_MEM_STORE unset — launch will raise"
      ;;
  esac
else
  info "MEMBENCH_MEMORY_SYSTEM not set — memory_enabled arm comes from the experiment config"
fi

# --- suite sanity (collect only; runs nothing) ---
if (cd "$BENCH" && python3 -m pytest --collect-only -q >/dev/null 2>&1); then
  n=$(cd "$BENCH" && python3 -m pytest --collect-only -q 2>/dev/null | tail -1)
  ok "pytest collects cleanly ($n)"
else
  bad "pytest --collect-only fails in memory-bench"
fi

exit 0
