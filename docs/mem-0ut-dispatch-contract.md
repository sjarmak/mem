# mem-0ut path-a — wire-level dispatch-hook contract

> Deliverable 1 of mem-0ut.3. Pins the **exact** bead-metadata the mayor's
> dispatch hook must stamp so the landed fork-aware measurement
> (`mem-0ut.1`, commit `883e2a3`, `memory-bench/membench/armcompare.py` +
> `scripts/arm_analysis.py`) can measure the warm-fork vs cold A/B without the
> mismeasurement failure mode (a fork-unaware read inverts the headline metric —
> see mem-0ut.1).
>
> **Read-side is FIXED** (landed, eval-design LOCKED). This contract maps the
> dispatch fields to the exact keys armcompare reads, verified by grep + a
> round-trip test. A field-name drift silently mismeasures the warm arm.

## What the measurement needs (from the bead)

| # | need | satisfied by |
|---|------|--------------|
| (a) | join forked polecat `session_id` ↔ `bead_id` | the bead's **assignee = the forked polecat session** (standard dispatch); mem ingest resolves `bead → assignee(=session) → trace JSONL` (`src/schemas/workrecord.ts`), so the trace carrying the inherited prefix lands on that bead's `work_id`. No new field. |
| (b) | distinguish **warm** vs **cold** arm | `gc.brain_arm` (below) → the harness builds armcompare's `work_id→arm` map. |
| (c) | locate the fork boundary to trim the inherited brain prefix | `gc.brain_fork_ts` (+ `gc.brain_parent_sid`) (below) → the harness sets the record's `fork.fork_ts`, which `fork_boundary_for` reads to trim. |

## Dispatch-side fields (the mayor's hook stamps these on each A/B bead)

Stamped into **bead metadata** (mem ingest carries bead metadata onto
`WorkRecord.metadata`, `src/schemas/workrecord.ts:223` `metadata: z.record(...)`).

| field | type | required | who stamps | meaning |
|-------|------|----------|-----------|---------|
| `gc.brain_arm` | string, **exactly** `"warm"` or `"cold"` | every A/B bead | dispatch | which arm this bead was dispatched under |
| `gc.brain_fork_ts` | string, ISO-8601 (`Z` or `+00:00`) | **warm only** | dispatch | the fork boundary = the brain session's `builtAt` (the timestamp the inherited brain-build prefix ends at). Events at/below it are the brain's, not the fork's. |
| `gc.brain_parent_sid` | string (the brain's `claude` session id) | **warm only** | dispatch | the brain session the polecat forked from (`claude --resume <parent_sid> --fork-session`). Provenance + audit of the boundary; pins which brain. |

Cold beads carry **only** `gc.brain_arm: "cold"` and no `gc.brain_*` fork fields
(cold is never trimmed).

## Read-side this maps to (FIXED — armcompare @883e2a3)

| dispatch field | armcompare read-side (exact) | code |
|----------------|------------------------------|------|
| `gc.brain_arm` ∈ {`warm`,`cold`} | `ARMS = ("warm", "cold")`; the `work_id→arm` map (`load_arm_assignment`); an unknown arm **raises** | `armcompare.py:68`, `load_arm_assignment` |
| `gc.brain_fork_ts` | `record["fork"]["fork_ts"]` (ISO string), read by `fork_boundary_for(record, arm, …)` for `arm=="warm"`; parsed by `_parse_iso_ts` (`fromisoformat` after `Z→+00:00`); cold → `None` (no trim); warm with no boundary → `fork_warnings: [{work_id, reason:"fork_unmeasured"}]` (never silent) | `fork_boundary_for` `armcompare.py:227`; `arm_analysis.py` `fork_warnings` |
| `gc.brain_parent_sid` | not read by the trim (informational/provenance); carried on `record.fork.parent_sid` | — |

**Boundary source precedence** (`fork_boundary_for`): a per-record
`fork.fork_ts` (from `gc.brain_fork_ts`) is **preferred** over a shared
`--scope-manifest` `builtAt`. Path-a forks may come from different brains, so the
per-bead `gc.brain_fork_ts` is the deterministic, correct source — use it.

## Example metadata blobs

Warm dispatch (forked from brain `78b48fa8-…`, built `2026-06-26T04:20:10.683Z`):

```json
{
  "gc.brain_arm": "warm",
  "gc.brain_parent_sid": "78b48fa8-ec60-4c55-b033-7bb48f06f8c9",
  "gc.brain_fork_ts": "2026-06-26T04:20:10.683Z"
}
```

Cold dispatch:

```json
{
  "gc.brain_arm": "cold"
}
```

## Harness mapping (Deliverable 2 — the live-bead measurement path)

`memory-bench/scripts/arm_analysis_live.py` is the bridge (it **reuses**
`armcompare`; it does not re-implement metrics). Given the live store (built by
mem ingest from the rig beads + their forked-polecat traces), it:

1. reads each `work_record`'s `record.metadata["gc.brain_arm"]` → the
   `work_id→arm` assignment armcompare consumes;
2. for warm records, injects `record.fork = {parent_sid:
   metadata["gc.brain_parent_sid"], fork_ts: metadata["gc.brain_fork_ts"]}` →
   exactly the `fork.fork_ts` key `fork_boundary_for` reads to trim;
3. calls the landed `arm_analysis.analyze(...)` → the SAME five-axis
   fork-trimmed metrics as path-b (`tool_calls_before_first_edit`,
   `distractor_read_rate`, `total_tokens` incl. brain-prefix amortization,
   `wall_clock_seconds`, `iterations_to_green`, + `turns`/`tool_calls`/`files_read`).

If a warm record reaches the measurement with no `gc.brain_fork_ts` (drift), it
surfaces in `fork_warnings` — the loud signal of the mismeasurement failure mode,
never a silent inversion.

## Verification (anti-mismeasurement)

- The arm literals and the `fork.fork_ts` read key are asserted against this
  contract in `memory-bench/tests/test_arm_analysis_live.py` (a round-trip:
  contract metadata blob → harness → `arm_analysis` → warm trimmed, cold not,
  `fork_warnings` empty). A drift in any field name fails that test.
- `grep -n 'ARMS' memory-bench/membench/armcompare.py` → `("warm", "cold")`;
  `grep -n "fork.*fork_ts" memory-bench/membench/armcompare.py` →
  `record["fork"]["fork_ts"]` in `fork_boundary_for`.
