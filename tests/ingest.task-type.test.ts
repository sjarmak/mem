import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { attachTaskTypes, deriveMechanicalType, loadTaskTypes } from '../src/ingest/task-type.js';
import { openStore } from '../src/store/index.js';
import { writeRecords } from '../src/store/writer.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const record = (title: string, metadata: Record<string, unknown> = {}): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: `demo-${Math.abs(title.length * 7919) % 100000}-${title.slice(0, 4)}`,
    rig: 'demo',
    title,
    metadata,
    lifecycle: { created: '2026-06-01T00:00:00Z', status: 'closed' },
  });

describe('deriveMechanicalType', () => {
  it('types molecule beads by their formula name', () => {
    expect(deriveMechanicalType(record('mol-focus-review'))).toEqual({
      task_type: 'mol-focus-review',
      task_type_source: 'formula',
    });
  });

  it('types step beads by their formula.step ref', () => {
    expect(
      deriveMechanicalType(record('Signal completion', { 'gc.step_ref': 'mol-do-work.drain' }))
    ).toEqual({ task_type: 'mol-do-work.drain', task_type_source: 'formula' });
  });

  it('types generator grammars as structural', () => {
    expect(deriveMechanicalType(record('Rollup(mem): things happened'))).toEqual({
      task_type: 'rollup',
      task_type_source: 'structural',
    });
    expect(deriveMechanicalType(record('input convoy for mem-75t.4'))).toEqual({
      task_type: 'convoy',
      task_type_source: 'structural',
    });
    expect(
      deriveMechanicalType(record('Iterate copilot review 4290708910 on owner/repo PR #15'))
    ).toEqual({ task_type: 'pr-review-iterate', task_type_source: 'structural' });
    expect(deriveMechanicalType(record('Human review checkpoint'))).toEqual({
      task_type: 'review-checkpoint',
      task_type_source: 'structural',
    });
    expect(deriveMechanicalType(record('sling-gc-336i3'))).toEqual({
      task_type: 'sling-dispatch',
      task_type_source: 'structural',
    });
  });

  it('types synthetic-metadata convoys', () => {
    expect(deriveMechanicalType(record('anything', { 'gc.synthetic': 'true' }))).toEqual({
      task_type: 'convoy',
      task_type_source: 'structural',
    });
  });

  it('returns null for free-form titles', () => {
    expect(deriveMechanicalType(record('fix: dashboard Activity tab blank'))).toBeNull();
  });
});

describe('loadTaskTypes', () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'mem-tt-'));
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  const entry = { task_type: 'bugfix', model: 'claude-haiku-4-5', classified_at: '2026-06-11' };

  it('parses entries', () => {
    const path = join(dir, 'tt.json');
    writeFileSync(path, JSON.stringify({ entries: { 'demo-1': entry } }));
    expect(loadTaskTypes(path).get('demo-1')?.task_type).toBe('bugfix');
  });

  it('rejects labels outside the taxonomy', () => {
    const path = join(dir, 'bad.json');
    writeFileSync(
      path,
      JSON.stringify({ entries: { 'demo-1': { ...entry, task_type: 'nonsense' } } })
    );
    expect(() => loadTaskTypes(path)).toThrow(/outside the taxonomy/);
  });

  it('rejects an artifact without entries', () => {
    const path = join(dir, 'empty.json');
    writeFileSync(path, JSON.stringify({}));
    expect(() => loadTaskTypes(path)).toThrow(/no entries/);
  });
});

describe('attachTaskTypes', () => {
  it('mechanical beats model; model covers residue; rest stays untyped', () => {
    const formula = record('mol-do-work');
    const freeform = record('fix: dashboard blank');
    const untyped = record('mysterious work item');
    const artifact = new Map([
      [formula.work_id, { task_type: 'other', model: 'm', classified_at: 't' }],
      [freeform.work_id, { task_type: 'bugfix', model: 'm', classified_at: 't' }],
    ]);
    const [a, b, c] = attachTaskTypes([formula, freeform, untyped], artifact);
    expect(a.task_type).toBe('mol-do-work'); // mechanical wins over the artifact
    expect(a.task_type_source).toBe('formula');
    expect(b.task_type).toBe('bugfix');
    expect(b.task_type_source).toBe('model');
    expect(c.task_type).toBeUndefined();
    expect(c.task_type_source).toBeUndefined();
  });
});

describe('writer task-type projection', () => {
  it('persists task_type, source, and molecule_id columns', () => {
    const db = openStore(':memory:');
    const rec = {
      ...record('Signal completion', {
        'gc.step_ref': 'mol-do-work.drain',
        molecule_id: 'gc-mol-123',
      }),
      task_type: 'mol-do-work.drain',
      task_type_source: 'formula' as const,
    };
    writeRecords(db, [rec]);
    const row = db
      .prepare(
        'SELECT task_type, task_type_source, molecule_id FROM work_records WHERE work_id = ?'
      )
      .get(rec.work_id) as Record<string, unknown>;
    expect(row).toEqual({
      task_type: 'mol-do-work.drain',
      task_type_source: 'formula',
      molecule_id: 'gc-mol-123',
    });
    db.close();
  });
});
