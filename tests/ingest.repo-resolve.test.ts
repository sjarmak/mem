import { describe, expect, it } from 'vitest';

import { attachRepo, resolveRepo } from '../src/ingest/repo-resolve.js';
import { RIG_REPOS } from '../src/ingest/rig-repo-map.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

/** A validated spine record on `rig`, optionally carrying an `outcome.repo`. */
const record = (rig: string, outcomeRepo?: string): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: `${rig}-1`,
    rig,
    title: `work in ${rig}`,
    lifecycle: { created: '2026-06-16T00:00:00Z', status: 'closed' },
    ...(outcomeRepo !== undefined && { outcome: { repo: outcomeRepo } }),
  });

describe('resolveRepo', () => {
  it('prefers a verified PR outcome repo over the rig map', () => {
    // gascity maps to gastownhall/gascity, but a real PR outcome wins.
    expect(resolveRepo(record('gascity', 'someone/fork'))).toEqual({
      repo: 'someone/fork',
      repo_source: 'outcome',
    });
  });

  it('falls back to the rig→repo map for a 1:1 rig', () => {
    expect(resolveRepo(record('gascity'))).toEqual({
      repo: 'gastownhall/gascity',
      repo_source: 'rig-map',
    });
  });

  it('leaves a multi-repo rig unmapped rather than guessing one repo', () => {
    expect(RIG_REPOS.gc.multi).toBe(true);
    expect(resolveRepo(record('gc'))).toEqual({ repo_source: 'unmapped' });
  });

  it('leaves an unknown rig unmapped (the coverage-gap signal)', () => {
    expect(resolveRepo(record('not-a-known-rig'))).toEqual({ repo_source: 'unmapped' });
  });

  it('does not read gc.work_dir — a bare basename is not owner/name', () => {
    const withWorkDir = WorkRecordSchema.parse({
      work_id: 'x-1',
      rig: 'unknown-rig',
      title: 'x',
      metadata: { 'gc.work_dir': '/home/ds/projects/somerepo' },
      lifecycle: { created: '2026-06-16T00:00:00Z', status: 'closed' },
    });
    expect(resolveRepo(withWorkDir)).toEqual({ repo_source: 'unmapped' });
  });
});

describe('attachRepo', () => {
  it('attaches repo + repo_source and copies the record (no mutation)', () => {
    const input = record('mem');
    const [out] = attachRepo([input]);
    expect(out.repo).toBe('sjarmak/mem');
    expect(out.repo_source).toBe('rig-map');
    expect(input.repo).toBeUndefined();
    expect(out).not.toBe(input);
  });

  it('omits repo entirely when unmapped, tagging the source', () => {
    const [out] = attachRepo([record('gc')]);
    expect(out.repo).toBeUndefined();
    expect(out.repo_source).toBe('unmapped');
  });

  it('round-trips through the schema (the attached shape is valid)', () => {
    const [out] = attachRepo([record('codeprobe')]);
    expect(() => WorkRecordSchema.parse(out)).not.toThrow();
    expect(out.repo).toBe('sjarmak/codeprobe');
  });
});
