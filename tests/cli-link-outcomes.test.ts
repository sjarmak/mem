import { describe, expect, it } from 'vitest';

import { buildLinkOutcomesReport } from '../src/cli/commands/link-outcomes.js';
import type { GitRunner } from '../src/ingest/provenance.js';

const SHA = (c: string): string => c.repeat(40);

const FS = '\x1f';
const RS = '\x1e';
const block = (
  sha: string,
  subject: string,
  body = '',
  cn = 'GitHub',
  date = '2026-06-07T12:00:00Z'
): string => `${sha}${FS}${date}${FS}${cn}${FS}${subject}${FS}${body}${RS}`;

/** A GitRunner that replays a fixed `git log` for the link query, so the report
 * is exercised without a real checkout. */
const fakeLog =
  (log: string): GitRunner =>
  () =>
    log;

describe('buildLinkOutcomesReport', () => {
  it('emits canonical landing commits sorted by work id', () => {
    const log = [
      block(SHA('a'), 'feat: rollup (codeprobe-zzz)'),
      block(SHA('b'), 'fix: parse (codeprobe-aaa) (#12)'),
    ].join('');

    const report = buildLinkOutcomesReport(
      'codeprobe',
      ['codeprobe-aaa', 'codeprobe-zzz'],
      '/clone',
      'main',
      { run: fakeLog(log) }
    );

    expect(report.rig).toBe('codeprobe');
    expect(report.commits.map(c => c.work_id)).toEqual(['codeprobe-aaa', 'codeprobe-zzz']);
    expect(report.commits[0]).toMatchObject({
      work_id: 'codeprobe-aaa',
      commit_sha: SHA('b'),
      linkage: 'canonical',
      pr: '12',
    });
    expect(report.commits[1]).toMatchObject({
      work_id: 'codeprobe-zzz',
      commit_sha: SHA('a'),
      linkage: 'canonical',
    });
    expect(report.commits[1].pr).toBeUndefined();
  });

  it('omits work ids with no landing commit', () => {
    const log = block(SHA('a'), 'feat: only this one (codeprobe-aaa)');
    const report = buildLinkOutcomesReport(
      'codeprobe',
      ['codeprobe-aaa', 'codeprobe-unlinked'],
      '/clone',
      'main',
      { run: fakeLog(log) }
    );
    expect(report.commits).toHaveLength(1);
    expect(report.commits[0].work_id).toBe('codeprobe-aaa');
  });
});
