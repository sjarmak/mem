#!/usr/bin/env bash
# rig-process-state.sh — read-only snapshot of mem rig process state.
# Part of the mem-git-and-dispatch-workflow skill. Runs NO mutating commands.
# Degrades gracefully when bd (bead CLI) is absent (public clone).
set -euo pipefail

REPO="${MEM_REPO:-/home/ds/projects/mem}"
cd "$REPO"

echo "== git =="
echo "branch : $(git branch --show-current)"
echo "HEAD   : $(git rev-parse --short HEAD)"
echo "commits on HEAD        : $(git rev-list --count HEAD)"
echo "merge commits          : $(git log --merges --oneline | wc -l | tr -d ' ')"
echo "local branches         : $(git branch | wc -l | tr -d ' ')"
echo "unmerged vs main       : $(git branch --no-merged main | wc -l | tr -d ' ')"
echo "registered worktrees   : $(git worktree list | wc -l | tr -d ' ')"
echo "root mem-*/ state dirs : $(ls -d mem-*/ 2>/dev/null | wc -l | tr -d ' ')"
echo "last 3 commits:"
git log -3 --format='  %h %ad %s' --date=short

echo
echo "== .gc-reports (audit cadence) =="
if [ -d .gc-reports ]; then
  newest="$(ls .gc-reports/audit-*.md 2>/dev/null | sort | tail -1 || true)"
  echo "audit files : $(ls .gc-reports/audit-*.md 2>/dev/null | wc -l | tr -d ' ')"
  echo "newest      : ${newest:-none}"
  if [ -n "${newest:-}" ]; then
    echo "newest header:"
    head -3 "$newest" | sed 's/^/  /'
  fi
else
  echo "no .gc-reports/ directory (public clone?)"
fi

echo
echo "== beads (internal-orchestration; skipped without bd) =="
if command -v bd >/dev/null 2>&1; then
  echo "-- possible finalize-gap wedges (should normally be empty) --"
  bd list --title-contains "Finalize workflow" --status open,in_progress --no-pager 2>/dev/null || echo "  (bd query failed)"
  bd list --title-contains "mol-focus-review" --status in_progress --no-pager 2>/dev/null || echo "  (bd query failed)"
  echo "-- open PL rollups (wedge/freeze reports) --"
  bd list --label rollup --status open --no-pager -n 10 2>/dev/null || echo "  (bd query failed)"
  echo "-- publication-freeze / wedge root-cause beads --"
  bd show mem-0rrf 2>/dev/null | head -3 || echo "  mem-0rrf: not readable"
  bd show mem-cvn3 2>/dev/null | head -3 || echo "  mem-cvn3: not readable"
else
  echo "bd not on PATH — fleet state unavailable (expected on a public clone)"
fi
