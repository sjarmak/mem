# mem-75t.19 — contested-window residue classification

> Ingest-layer diagnostic. Classifies the `landed_state='ambiguous-window'`
> ("contested-window") records that the mem-75t.16 session-commit recovery did
> **not** rescue to a sound TRUE replay base, by blocking reason; extends the
> parser for the one recoverable shape it found; and documents the hard ceiling
> for the rest. No eval / grading / grid-store / validity edits.

## Question

mem-75t.16 (re-applying the stale mem-75t.15 leg, now on main `@b12183c`)
recovers each session's TRUE per-worktree replay base from the `[branch sha]`
commit-success line its trace records, independent of the upstream squash-merge
that erases per-session identity (mem-75t.12, mem-apg.10 both proved post-hoc
attribution dead). It rescues a minority of the contested set; the rest stay
`ambiguous-window`. This unit asks **why** each residue record is blocked:

- **(a)** the session recorded no local commit in its trace, or no trace was
  resolved at all — nothing to anchor;
- **(b)** the session's git-commit output uses a shape the parser did not read;
- **(c)** the commit is genuinely squashed/rebased out of the rig clone — the
  mem-75t.12 wall.

## Method

`scripts/classify-contested-residue.mjs` is a read-only runner over a built
store. For every `ambiguous-window` record it re-runs the real ingest functions
(`parseSessionCommits` / `deriveSessionCommits`, `src/ingest/sessionCommits.ts`)
against the record's resolved transcript (`trace.jsonl_path`) and rig clone
(`provenance.work_dir`), and sorts the outcome into one bucket. It never writes
the store and never invents a SHA.

Reproduce:

```bash
npm run build
node scripts/classify-contested-residue.mjs --store .mem/store.db --json
```

Measured against the canonical store `/home/ds/projects/mem/.mem/store.db`
(schema-v8 rebuild, 7306 records, 2026-06-23):

```json
{
  "contested": 2741,
  "recovered": 344,
  "recovered_legacy": 335,
  "recovered_new_detached": 9,
  "squash_erased": 17,
  "no_local_commit": 922,
  "no_trace_resolved": 1458,
  "trace_reaped": 0,
  "residue": 2397
}
```

The buckets partition the contested set exactly:
`344 recovered + 2397 residue = 2741`, and the residue sub-buckets sum to the
residue: `17 + 922 + 1458 + 0 = 2397`.

> **Store note.** The mem-75t.15-era "439 recovered of ~2,836 contested" was
> measured on the `.15` development build; the canonical schema-v8 store has a
> slightly smaller contested set (2741) and yields 335 legacy recoveries. The
> difference is a store-version / trace-resolution artifact, not a logic
> difference — the residue figure (2397) is identical, and the script
> re-derives the recovery from the same ingest code on whichever store it is
> pointed at.

## Residue classification (2397 records)

| reason | bucket | count | recoverable by SHA-capture? |
| ------ | ------ | ----: | --------------------------- |
| a | `no-trace-resolved` — the trace stage never resolved a transcript, so there is no text to parse | 1458 | No — upstream trace-resolution gap, out of scope for this leg (these are largely unworked / reaped-session beads; see "trace-resolution 45% is a denominator artifact") |
| a | `no-local-commit` — trace + clone present, but the session printed no git commit-success line in any shape | 922 | No — genuinely nothing to anchor (edit-only sessions, sessions that committed through a path that prints no SHA) |
| c | `squash-erased` — parsed ≥1 local commit, but its parent is gone from the clone (`base_state='commit-absent'`) | 17 | No — the mem-75t.12 squash wall; the SHA is recorded, the base is never guessed |

`no-trace-resolved` (1458, 61% of residue) and `no-local-commit` (922, 38%)
together are **99.3%** of the residue and are unrecoverable by any
session-commit mechanism: there is no commit signal to read. The squash wall
(reason c) is a thin **17 records** — the per-session SHA-capture approach has
already drained almost all of what mem-75t.12 predicted it could.

### Per-rig shape (largest buckets)

- `no-trace-resolved`: gascity 877, gascity_dashboard 158, mem 140,
  scix_experiments 79, zeldascension 75 …
- `no-local-commit`: gascity 360, mem 142, gascity_dashboard 128,
  scix_experiments 96, zeldascension 51 …

Both tails track raw contested volume per rig, not a rig-specific parser gap.

## Recovery extended: reason (b) — the detached-HEAD heading

The one residue subset blocked **only** by an unparsed output shape: a replay
worktree commits from a **detached HEAD**, so git prints
`[detached HEAD <sha>] <subject>` — a *two-word* heading. The mem-75t.15 regex
accepted a single-token branch only (`[\w./-]+`), so `detached HEAD` (with its
space) fell through and the session looked like it made no local commit.

`detached HEAD` is the only multi-word heading git emits for a commit, so the
fix is a targeted, deterministic extension of the branch token — still a parse
over git's own documented output, no guessing (ZFC):

```
\[(?:[\w./-]+|detached HEAD) (?:\(root-commit\) )?([0-9a-f]{7,40})\]
```

This recovers **9** previously-blocked contested records to a sound TRUE base,
moving them from `no-local-commit` into `recovered` (legacy 335 → total 344).
Every new recovery is SHA-verified — its `first_commit` exists as a commit in
the named rig's clone (`git cat-file -e <sha>^{commit}`), and its `true_base`
resolves as that commit's parent:

| work_id | rig | first_commit | true_base | verified |
| ------- | --- | ------------ | --------- | :------: |
| gc-7uzar | gascity | `0a73e2151` | `47e79bbbb5…` | ✓ |
| gascity-dashboard-165b | gascity_dashboard | `d7727ee` | `84fabb9658…` | ✓ |
| gascity-dashboard-5nu67 | gascity_dashboard | `11c7203` | `3aaa359887…` | ✓ |
| gascity-dashboard-gfx9 | gascity_dashboard | `66fec84` | `0c181f4ee1…` | ✓ |
| gascity-dashboard-lwyl | gascity_dashboard | `e50fce4` | `3aaa359887…` | ✓ |
| gascity-dashboard-pvkwp | gascity_dashboard | `66fec84` | `0c181f4ee1…` | ✓ |
| gascity-dashboard-qavqa | gascity_dashboard | `e50fce4` | `3aaa359887…` | ✓ |
| gascity-dashboard-ye7d3 | gascity_dashboard | `d7727ee` | `84fabb9658…` | ✓ |
| gascity-dashboard-yn8v | gascity_dashboard | `11c7203` | `3aaa359887…` | ✓ |

(Shared `first_commit`/`true_base` values are convoy siblings / re-runs over the
same worktree commit — distinct work records, each soundly anchored.)

The extension is covered by `tests/ingest.sessionCommits.test.ts` (detached-HEAD
heading, the detached root-commit form, JSON-escaped embedding, and a negative
case proving an arbitrary two-word prose heading is **not** matched).

## Verdict — documented hard ceiling (PASS)

- The residue is classified by blocking reason with counts that sum to the
  measured residue (2397), reproducible from a committed script.
- The one reason-(b) subset (detached-HEAD heading) is recovered: +9 records,
  SHA-verified, with a regression test for the new shape.
- The remaining **2380 of 2397 (99.3%)** carry no readable commit signal
  (`no-trace-resolved` + `no-local-commit`); the **17** squash-erased records
  are the mem-75t.12 wall. No further SHA-capture recovery is feasible without
  fabricating SHAs, which this leg does not do.

This is the honest-null ceiling the bead admits as a PASS: per-session
SHA-capture has reached its limit on the contested window. The dominant residue
bucket — `no-trace-resolved` (1458) — is an upstream trace-resolution gap (many
of these are unworked / reaped-session beads), not a session-commit parsing
gap, and is out of scope for this ingest leg.
