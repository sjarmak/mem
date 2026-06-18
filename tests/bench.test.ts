import { describe, expect, it } from 'vitest';
import type { z } from 'zod';

import {
  assertNoSharedBranchRootAcrossPartitions,
  branchRoot,
  canonicalIdentity,
  changedLineJaccard,
  diffOverlapThreshold,
  isTemporallySoundTask,
  leaksGoldPatch,
  looPartitions,
  memoryPredatesTaskStart,
  parseUnifiedDiff,
  sharesHunkAnchor,
  stripDiffsAndShas,
  temporalWallDrop,
} from '../src/bench/index.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

/** A minimal admissible record, overridable per test. Overrides take the schema
 * INPUT shape, so a `lifecycle` literal need not restate defaulted fields. */
const rec = (overrides: Partial<z.input<typeof WorkRecordSchema>> = {}): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: 'demo-1a2b',
    rig: 'demo',
    title: 'work',
    lifecycle: {
      created: '2026-06-01T00:00:00Z',
      started: '2026-06-01T01:00:00Z',
      closed: '2026-06-01T05:00:00Z',
      status: 'closed',
    },
    ...overrides,
  });

describe('temporal wall (gate a)', () => {
  it('admits a record with an exact start and an outcome strictly after it', () => {
    expect(temporalWallDrop(rec())).toBeNull();
    expect(isTemporallySoundTask(rec())).toBe(true);
  });

  it('drops a task whose baseline is commit-by-date (approximate start)', () => {
    const r = rec({
      provenance: {
        work_dir: '/w',
        repo: 'demo',
        base_branch: 'main',
        history_state: 'commit-by-date',
      },
    });
    expect(temporalWallDrop(r)).toBe('approximate_start');
    expect(isTemporallySoundTask(r)).toBe(false);
  });

  it('keeps a task with an exact (unresolved-base) provenance', () => {
    const r = rec({ provenance: { work_dir: '/w', repo: 'demo', history_state: 'unresolved' } });
    expect(temporalWallDrop(r)).toBeNull();
  });

  it('drops when start, outcome time, or their order is missing/wrong', () => {
    expect(temporalWallDrop(rec({ lifecycle: { created: 'c', status: 'open' } }))).toBe(
      'missing_start'
    );
    expect(
      temporalWallDrop(rec({ lifecycle: { created: 'c', started: 's', status: 'open' } }))
    ).toBe('missing_outcome_time');
    expect(
      temporalWallDrop(
        rec({
          lifecycle: {
            created: '2026-06-01T00:00:00Z',
            started: '2026-06-01T05:00:00Z',
            closed: '2026-06-01T05:00:00Z', // equal → not strictly after
            status: 'closed',
          },
        })
      )
    ).toBe('outcome_not_after_start');
  });

  it('admits a memory only when its outcome strictly predates the task start', () => {
    const memory = rec({
      lifecycle: { created: 'c', closed: '2026-06-01T00:30:00Z', status: 'closed' },
    });
    expect(memoryPredatesTaskStart(memory, '2026-06-01T01:00:00Z')).toBe(true);
    expect(memoryPredatesTaskStart(memory, '2026-06-01T00:30:00Z')).toBe(false); // not strict
  });

  it('treats a memory with no outcome instant as ineligible (fail-closed)', () => {
    const memory = rec({ lifecycle: { created: 'c', status: 'open' } });
    expect(memoryPredatesTaskStart(memory, '2026-06-01T01:00:00Z')).toBe(false);
  });
});

describe('diff-overlap (gate b)', () => {
  const gold = [
    'diff --git a/src/app.ts b/src/app.ts',
    'index 111..222 100644',
    '--- a/src/app.ts',
    '+++ b/src/app.ts',
    '@@ -10,3 +10,4 @@ function f() {',
    ' const x = 1;',
    '-  return x;',
    '+  const y = x + 1;',
    '+  return y;',
  ].join('\n');

  it('parses files, hunk anchors, and changed lines from a unified diff', () => {
    const parsed = parseUnifiedDiff(gold);
    expect([...parsed.anchors]).toEqual(['src/app.ts:10']);
    expect(parsed.changedLines).toContain('const y = x + 1;');
    expect(parsed.changedLines).toContain('return x;'); // a removed line counts too
    expect(parsed.changedLines).not.toContain('const x = 1;'); // context does not
  });

  it('hard-rejects a memory sharing a file+hunk-anchor with the gold, ignoring similarity', () => {
    const memorySameSpot = [
      '--- a/src/app.ts',
      '+++ b/src/app.ts',
      '@@ -10,1 +10,1 @@',
      '+  totally different code here;',
    ].join('\n');
    expect(sharesHunkAnchor(memorySameSpot, gold)).toBe(true);
    expect(leaksGoldPatch(memorySameSpot, gold, 'demo')).toBe(true); // even at low Jaccard
  });

  it('computes changed-line Jaccard and rejects at/above the rig threshold', () => {
    expect(changedLineJaccard(gold, gold)).toBe(1);
    // A different file (no shared anchor) but identical changed lines → high Jaccard.
    const elsewhere = gold.replace(/app\.ts/g, 'other.ts');
    expect(sharesHunkAnchor(elsewhere, gold)).toBe(false);
    expect(changedLineJaccard(elsewhere, gold)).toBe(1);
    expect(leaksGoldPatch(elsewhere, gold, 'demo')).toBe(true);
  });

  it('calibrates the threshold per rig — tight on the trivial dashboard rig', () => {
    expect(diffOverlapThreshold('gascity_dashboard')).toBe(0.2);
    expect(diffOverlapThreshold('some_other_rig')).toBe(0.6);
  });

  it('treats a removed line that reads like a header as hunk content, not a file header', () => {
    // A removed Lua/SQL comment `--- gone` is a `-` line; the parser must keep it
    // (and the lines after it) as changed content, not exit the hunk early.
    const diff = [
      '--- a/q.sql',
      '+++ b/q.sql',
      '@@ -1,3 +1,3 @@',
      '--- a removed sql comment',
      '+-- a new sql comment',
      '+SELECT 1;',
    ].join('\n');
    const parsed = parseUnifiedDiff(diff);
    expect(parsed.changedLines).toContain('-- a removed sql comment');
    expect(parsed.changedLines).toContain('SELECT 1;'); // not dropped by an early hunk exit
  });

  it('redacts SHA-like hex but spares plain decimals (issue/PR numbers)', () => {
    const stripped = stripDiffsAndShas('Fixes gh-1234567 via commit deadbeef and 9fae12c.');
    expect(stripped).toContain('gh-1234567'); // a decimal is not a SHA
    expect(stripped).not.toContain('deadbeef'); // all-letter hex abbreviation IS a SHA
    expect(stripped).not.toContain('9fae12c');
  });

  it('does not flag an unrelated memory as a leak', () => {
    const unrelated = [
      '--- a/README.md',
      '+++ b/README.md',
      '@@ -1,1 +1,1 @@',
      '+# A docs tweak nobody shares',
    ].join('\n');
    expect(leaksGoldPatch(unrelated, gold, 'demo')).toBe(false);
  });

  it('strips diff blocks and SHA-like tokens but keeps prose bullets', () => {
    const memory = [
      'Lesson: guard the null case.',
      '- a prose bullet, kept',
      'See commit deadbeef1234 and abc1234.',
      '```',
      'diff --git a/x.ts b/x.ts',
      '@@ -1,2 +1,2 @@',
      '-old line',
      '+new secret line',
      '```',
      'Done.',
    ].join('\n');
    const stripped = stripDiffsAndShas(memory);
    expect(stripped).toContain('- a prose bullet, kept');
    expect(stripped).toContain('Lesson: guard the null case.');
    expect(stripped).not.toContain('new secret line');
    expect(stripped).not.toContain('old line');
    expect(stripped).not.toContain('deadbeef1234');
    expect(stripped).toContain('[sha]');
  });
});

describe('LOO dedup (gate c)', () => {
  const landed = (work_id: string, ref: string | undefined, commit?: string): WorkRecord =>
    rec({
      work_id,
      ...(ref !== undefined && { external_ref: ref }),
      ...(commit !== undefined && {
        landed: { base_commit: 'a'.repeat(40), landed_commit: commit, landed_state: 'landed' },
      }),
    });

  it('reads canonical identity and normalizes only the bd- branch prefix', () => {
    expect(branchRoot(rec({ external_ref: 'bd-gc-xyz' }))).toBe('gc-xyz');
    expect(branchRoot(rec())).toBeUndefined();
    // A child-bead suffix is NOT stripped — siblings must stay distinct.
    expect(canonicalIdentity(rec({ work_id: 'mem-wanz.7', external_ref: 'mem-wanz.7' }))).toEqual({
      slug: 'mem-wanz.7',
      branchRoot: 'mem-wanz.7',
    });
  });

  it('groups run-1/run-2 that share a branch-root into one partition', () => {
    const parts = looPartitions([
      landed('bead-run1', 'bd-feat-x'),
      landed('bead-run2', 'bd-feat-x'),
      landed('other', 'bd-feat-y'),
    ]);
    expect(parts).toHaveLength(2);
    expect(parts[0].map(r => r.work_id)).toEqual(['bead-run1', 'bead-run2']);
  });

  it('groups two beads that landed the same commit (the merge-collision double-entry)', () => {
    const parts = looPartitions([
      landed('mem-7q6e', undefined, 'c'.repeat(40)),
      landed('mem-us6j', undefined, 'c'.repeat(40)),
    ]);
    expect(parts).toHaveLength(1);
  });

  it('keeps records with no shared key in separate partitions', () => {
    const parts = looPartitions([landed('a', 'bd-a'), landed('b', 'bd-b'), landed('c', undefined)]);
    expect(parts).toHaveLength(3);
  });

  it('passes the build assertion for a clean partitioning', () => {
    const parts = looPartitions([landed('a1', 'bd-a'), landed('a2', 'bd-a'), landed('b', 'bd-b')]);
    expect(() => assertNoSharedBranchRootAcrossPartitions(parts)).not.toThrow();
  });

  it('fails the build assertion when a branch-root straddles two partitions', () => {
    // Hand-built (mis-)partitioning: the same branch-root split across the wall.
    const split = [[landed('a1', 'bd-a')], [landed('a2', 'bd-a')]];
    expect(() => assertNoSharedBranchRootAcrossPartitions(split)).toThrow(/branch-root "a"/);
  });
});
