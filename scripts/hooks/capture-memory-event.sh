#!/usr/bin/env bash
#
# capture-memory-event.sh — PostToolUse(Read|Write|Edit|NotebookEdit|Grep|Glob)
# hook: record a write-time MEMORY event when a session reads/writes a memory
# file, keyed by session (+ work_id when the env carries one). The forward-
# capture dual of capture-provenance.sh (which records bead-claim GIT
# provenance, not memory ops) — see mem-31kz / docs/mem-31kz-forward-capture.md.
#
# This is the THIN pipe: a cheap bash pre-filter so node only boots when the
# tool input plausibly touches a memory path, then `mem memory-event capture`
# does the authoritative projection + append (the real allow-list + leak-safe
# field selection live there, unit-tested).
#
# Best-effort by contract: a capture miss must NEVER block a tool call, so every
# failure path exits 0 silently. Config via env:
#   MEM_STORE         store to record into (default: ~/projects/mem/.mem/store.db)
#   MEM_BIN           the mem binary (default: mem on PATH, then ~/.mem-cli/bin/mem)
#   MEM_MEMORY_DIRS   colon-separated path substrings that mark a memory path
#                     (default: structural — `/brains/`, a claude `/memory/`
#                     dir, or a `MEMORY.md` index)

set -uo pipefail

INPUT=$(cat) || exit 0
command -v jq >/dev/null 2>&1 || exit 0

TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
case "$TOOL" in
  Read | Write | Edit | NotebookEdit | Grep | Glob) ;;
  *) exit 0 ;;
esac

# Cheap pre-filter: the operated-on path. Only boot node if it looks like a
# memory path under the configured/default markers. This is a BROADER match than
# the authoritative isMemoryPath() test (e.g. a `/memory/` path here does not
# also require `.claude`), so non-memory paths like /repo/src/memory/x.ts may
# pass the pre-filter, boot node, and then be dropped by the capture command.
# That is by design — a false positive costs one spurious node boot, never a
# wrong row. Tighten MEM_MEMORY_DIRS to cut the boot overhead if it matters.
TARGET=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)
[ -n "$TARGET" ] || exit 0

MARKERS="${MEM_MEMORY_DIRS:-/brains/:/memory/:MEMORY.md}"
HIT=0
IFS=':' read -ra PARTS <<<"$MARKERS"
for m in "${PARTS[@]}"; do
  [ -n "$m" ] || continue
  case "$TARGET" in
    *"$m"*)
      HIT=1
      break
      ;;
  esac
done
[ "$HIT" -eq 1 ] || exit 0

MEM_BIN="${MEM_BIN:-mem}"
if ! command -v "$MEM_BIN" >/dev/null 2>&1; then
  MEM_BIN="$HOME/.mem-cli/bin/mem"
  [ -x "$MEM_BIN" ] || exit 0
fi

STORE="${MEM_STORE:-$HOME/projects/mem/.mem/store.db}"

# Pipe the original payload to the capture command; it projects + appends (or
# no-ops if the path is not actually in scope). Swallow all output/errors — the
# tool call must never see a capture failure.
printf '%s' "$INPUT" | "$MEM_BIN" memory-event capture --store "$STORE" --json >/dev/null 2>&1 || true
exit 0
