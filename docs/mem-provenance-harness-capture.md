# Provenance capture: the agent-harness producer

**Status:** active wiring · **Date:** 2026-06-19 · **Companion to:** `mem-bead-provenance-event-log.md`, `mem-bead-provenance-upstream-contribution.md`

## What is wired, and why here

Provenance must be captured wherever beads are written — by this session, by future
agents, by subagents — without anyone remembering to call anything. The durable
design is *one idempotent sink, a producer at the narrowest waist each context
shares*. For agents that waist is the **Claude Code harness**, so capture runs as a
global hook inherited by every session and subagent. (gascity is deliberately *not*
special — it's just one more producer context; the primitive stays runtime-agnostic.)

This is the **stopgap sink** (the mem provenance log). The durable system-of-record
is bd's dolt store (upstream **beads#4460**); producers repoint to `bd provenance
record` once that lands — the event vocabulary is identical.

## The pieces

| Piece | Location | Role |
|-------|----------|------|
| Hook script | `~/.claude/hooks/capture-provenance.sh` | PostToolUse(Bash) producer |
| Registration | `~/.claude/settings.json` → `hooks.PostToolUse` (matcher `Bash`) | runs it globally |
| Pinned binary | `~/.mem-cli/` (`dist` + `bin` copied, `node_modules` symlinked to the canonical checkout) | a provenance-capable `mem` independent of whatever branch the shared `/home/ds/projects/mem` checkout currently has |
| Sink store | `~/projects/mem/.mem/store-v9.db` (default) | the mem provenance log |

## What it captures

On a bead-claim write (`bd update <id> --claim`, or `--assignee`; the `rtk bd …`
wrapped form too), it records a **`cut`** event for that bead with the **exact fork
point** — `git merge-base HEAD <base>` in the worktree's cwd. merge-base recovers
the precise commit the branch diverged from at any time, so this is the exact base
(`history_state='recorded'`), not the date heuristic. `actor` = the session id;
`source` = `agent-harness`.

It does **not** capture `claim`/`land` yet — the `cut` (base SHA) is the one fact bd
structurally cannot see and the read-first path most needs. Claim/lifecycle are
bd-visible and belong to the bd-native producer.

Best-effort by contract: every failure path exits 0 — a provenance miss must never
block a `bd` command.

## Config (env overrides)

- `MEM_STORE` — sink store (default `~/projects/mem/.mem/store-v9.db`)
- `MEM_BIN` — the mem binary (default `~/.mem-cli/bin/mem`)
- `MEM_BASE_BRANCH` — integration ref to fork-point against (default `origin/main`)

## Maintenance

- The pinned `~/.mem-cli` is a **snapshot**. After a mem change that touches the
  `provenance` command or the store schema, re-pin:
  `rm -rf ~/.mem-cli && mkdir ~/.mem-cli && cp -r <built>/dist <built>/bin <built>/package.json ~/.mem-cli/ && ln -s /home/ds/projects/mem/node_modules ~/.mem-cli/node_modules`
- Verify capture: `mem provenance log <bead> --store <store>` after a claim.

## Migration to durable (dolt)

When beads#4460 lands a `bd provenance` table in dolt: change the hook's sink call
from `mem provenance record` to `bd provenance record` (same args), and the events
become durable across machines and travel with the beads. mem then reads from dolt
instead of being the store. Nothing else in the read-first path changes.
