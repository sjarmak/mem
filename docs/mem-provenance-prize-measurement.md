# The prize: what recording exact bases buys the eval

**Status:** findings · **Date:** 2026-06-19 · **Companion to:** `mem-bead-provenance-event-log.md`

## Question

The provenance read-first path lets a record's base commit be *recorded* (exact)
instead of *reconstructed by date* (`commit-by-date`, approximate). Is that worth
the durable (bd/dolt) investment? Measured against the temporal-wall eval contract
(`src/bench/temporal.ts`), which **drops** any record whose base is approximate
(`approximate_start`) because an imprecise start cannot anchor a wall.

## Result (store-v9.db, 7,757 records)

| Temporal status (current) | Count |
|---|---|
| Dropped — `approximate_start` (date-heuristic base) | 6,273 (81%) |
| Dropped — `missing_start` (no start time) | 1,474 |
| **Admissible now** | **10** |

Simulating the flip of every `commit-by-date` base to `recorded`:

- **2,180** of the 6,273 become admissible — they have a clean start + close; the
  *only* disqualifier is the approximate base.
- 4,093 stay dropped — also missing start/close (a separate data-quality issue
  provenance cannot fix).
- **Admissible: 10 → ~2,190 (≈219×).**

## Interpretation

- Recording exact bases is the **single binding constraint** on 2,180 records
  (28% of the corpus) becoming temporally-sound eval tasks.
- The date heuristic is **counterproductive** here: every base it resolves gets
  stamped `commit-by-date`, which disqualifies the record. The 10 currently
  admissible are the ones it *couldn't* resolve. Exact bases don't just add
  signal — they unblock signal the heuristic was actively suppressing.

## Honest caveat: ceiling, not harvest

The 2,180 is the **value of the capability**, not a backfill you can run. Those are
mostly historical records whose true fork SHA is already destroyed (the reason the
heuristic exists). Realizable recovery:

- **Forward** — every newly-claimed bead flows through the harness producer
  (`~/.claude/hooks/capture-provenance.sh`) and gets an exact base, so it lands
  admissible. The forward capture rate is the curve toward the ceiling.
- **Historical** — largely unrecoverable; do not expect a bulk conversion.

## Decision

The per-record value is high (each recorded base ≈ one more admissible task against
a floor of 10), so the durable producer/home is justified — this is the evidence
for investing in the bd-native dolt table (beads#4460) rather than leaving capture
in the per-machine mem store.

## Reproduce

```js
// node, against .mem/store-v9.db, using mem's own temporalWallDrop
const Database = require('better-sqlite3');
const { temporalWallDrop } = require('<mem>/dist/bench/temporal.js');
const recs = new Database('<store>', { readonly: true })
  .prepare('SELECT record FROM work_records').all().map(r => JSON.parse(r.record));
const dist = {};
for (const r of recs) { const d = temporalWallDrop(r) ?? 'ADMISSIBLE'; dist[d] = (dist[d] || 0) + 1; }
let recovered = 0;
for (const r of recs) {
  if (temporalWallDrop(r) !== 'approximate_start') continue;
  const flipped = { ...r, provenance: { ...r.provenance, history_state: 'recorded' } };
  if (temporalWallDrop(flipped) === null) recovered++;
}
console.log(dist, 'recoverable:', recovered);
```
