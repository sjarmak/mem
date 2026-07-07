#!/usr/bin/env bash
# check-env.sh — read-only diagnostic for both mem development environments.
# Reports versions, install state, the stale-dist condition, and optional-dep
# availability. Changes NOTHING. Exit 0 always (it is a report, not a gate).
#
# Usage: run from anywhere inside the repo:
#   .claude/skills/mem-build-test-env/scripts/check-env.sh

set -u

# Resolve repo root from this script's location: scripts/ -> skill -> skills -> .claude -> root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
MB="$ROOT/memory-bench"

ok()   { printf '  [ok]   %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; }
info() { printf '  [--]   %s\n' "$1"; }

echo "mem environment check — $ROOT"
echo

echo "== TypeScript half (repo root) =="
if command -v node >/dev/null 2>&1; then
  ok "node $(node --version) on PATH (CI runs Node 20; engines >=18)"
else
  warn "node not on PATH — TS half unusable; CLI-driving pytest tests will skip"
fi

if [ -d "$ROOT/node_modules" ]; then
  ok "node_modules/ present"
else
  warn "node_modules/ missing — run: npm ci"
fi

if [ -f "$ROOT/dist/main.js" ]; then
  ok "dist/main.js present"
  # Stale-dist detection: any src/**/*.ts newer than dist/main.js means
  # ./bin/mem would silently run stale compiled code (build-first trap).
  NEWER_COUNT=$(find "$ROOT/src" -name '*.ts' -newer "$ROOT/dist/main.js" 2>/dev/null | wc -l)
  if [ "$NEWER_COUNT" -gt 0 ]; then
    warn "STALE DIST: $NEWER_COUNT src/*.ts file(s) newer than dist/main.js — run: npm run build"
    find "$ROOT/src" -name '*.ts' -newer "$ROOT/dist/main.js" 2>/dev/null | head -5 | sed 's/^/         /'
  else
    ok "dist/ is fresh (no src/*.ts newer than dist/main.js)"
  fi
else
  warn "dist/main.js missing — ./bin/mem cannot run; run: npm run build"
fi

echo
echo "== Python half (memory-bench/) =="
if [ ! -d "$MB" ]; then
  warn "memory-bench/ not found under $ROOT"
else
  if command -v python3 >/dev/null 2>&1; then
    ok "python3 $(python3 --version 2>&1 | awk '{print $2}') on PATH (requires-python >=3.12; CI runs 3.12)"
  else
    warn "python3 not on PATH"
  fi

  PYBIN=""
  if [ -x "$MB/.venv/bin/python" ]; then
    ok "venv present at memory-bench/.venv ($("$MB/.venv/bin/python" --version 2>&1))"
    PYBIN="$MB/.venv/bin/python"
  else
    warn "no venv at memory-bench/.venv — create one and: pip install -e \".[dev]\""
  fi

  if [ -n "$PYBIN" ]; then
    # membench editable install?
    if "$PYBIN" -c 'import membench' 2>/dev/null; then
      ok "membench importable from the venv"
    else
      warn "membench NOT importable — run in memory-bench/: pip install -e \".[dev]\""
    fi
    # dev gate tools
    for tool in pytest ruff mypy black; do
      if [ -x "$MB/.venv/bin/$tool" ]; then
        ok "$tool available in venv"
      else
        warn "$tool missing from venv — part of the [dev] extra"
      fi
    done
    # Optional deps: absence is EXPECTED (tests skip by design), so info not warn.
    for mod in harbor mem0 agentic_memory graphiti_core data_designer sentence_transformers; do
      if "$PYBIN" -c "import $mod" 2>/dev/null; then
        ok "optional dep '$mod' importable (related tests will RUN)"
      else
        info "optional dep '$mod' absent (related tests SKIP — by design)"
      fi
    done
  fi
fi

echo
echo "== Gate parity =="
if [ -f "$ROOT/.git/hooks/pre-commit" ]; then
  ok "pre-commit hook installed"
else
  info "pre-commit hook not installed (optional): pre-commit install"
fi
echo
echo "Done (read-only; nothing was modified)."
exit 0
