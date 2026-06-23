import { describe, expect, it } from 'vitest';

import {
  attachSessionCommits,
  deriveSessionCommits,
  parseSessionCommits,
} from '../src/ingest/sessionCommits.js';
import type { GitRunner } from '../src/ingest/provenance.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const PARENT = 'a'.repeat(40);

describe('parseSessionCommits', () => {
  it('extracts commit SHAs from git [branch sha] success lines in trace order', () => {
    const trace = [
      'noise before',
      '[main 2b7a2e5fcb] feat: first thing',
      'some tool output',
      '[main 9f3c1a0de4] fix: second thing',
    ].join('\n');
    expect(parseSessionCommits(trace)).toEqual(['2b7a2e5fcb', '9f3c1a0de4']);
  });

  it('handles the (root-commit) first-commit form', () => {
    expect(parseSessionCommits('[main (root-commit) c0ffee1234] init')).toEqual(['c0ffee1234']);
  });

  it('handles branch names with slashes and dots', () => {
    expect(parseSessionCommits('[feat/x.y abcdef1] msg')).toEqual(['abcdef1']);
  });

  it('matches a commit line embedded in JSON-escaped transcript text', () => {
    // The transcript is JSONL: git's success line survives JSON-string escaping
    // verbatim, so the same regex matches it inside a tool_result value.
    const jsonl =
      '{"type":"user","toolUseResult":{"stdout":"[main 2b7a2e5fcb] feat: x\\n 1 file"}}';
    expect(parseSessionCommits(jsonl)).toEqual(['2b7a2e5fcb']);
  });

  it('returns empty when the session made no local commit (no success line)', () => {
    // A bare bracketed hex that is NOT a `[branch sha]` commit line must not match.
    expect(parseSessionCommits('see commit deadbeef or [123456] note')).toEqual([]);
    expect(parseSessionCommits('git commit was discussed but never run')).toEqual([]);
  });
});

describe('deriveSessionCommits', () => {
  // A runner that returns a fixed parent for `rev-parse <first>^`.
  const resolving: GitRunner = (_clone, args) => {
    expect(args[0]).toBe('rev-parse');
    return `${PARENT}\n`;
  };

  it('resolves the true base = parent of the FIRST local commit', () => {
    const out = deriveSessionCommits(['2b7a2e5fcb', '9f3c1a0de4'], '/clone', resolving);
    expect(out).toEqual({
      commits: ['2b7a2e5fcb', '9f3c1a0de4'],
      first_commit: '2b7a2e5fcb',
      true_base: PARENT,
      base_state: 'resolved',
    });
  });

  it('marks base commit-absent when the first commit is squashed/rebased out of the clone', () => {
    // rev-parse exits non-zero when the commit is gone — the SHAs are still recorded,
    // the base is left absent (never invented).
    const gone: GitRunner = () => {
      throw Object.assign(new Error('fatal: bad revision'), { status: 128 });
    };
    const out = deriveSessionCommits(['2b7a2e5fcb'], '/clone', gone);
    expect(out).toEqual({
      commits: ['2b7a2e5fcb'],
      first_commit: '2b7a2e5fcb',
      base_state: 'commit-absent',
    });
    expect(out?.true_base).toBeUndefined();
  });

  it('treats empty rev-parse stdout as commit-absent (not a guessed base)', () => {
    const empty: GitRunner = () => '   \n';
    expect(deriveSessionCommits(['2b7a2e5fcb'], '/clone', empty)?.base_state).toBe('commit-absent');
  });

  it('returns null when the session made no local commit', () => {
    expect(deriveSessionCommits([], '/clone', resolving)).toBeNull();
  });

  it('propagates a missing-git-binary failure (a misconfiguration, not commit-absent)', () => {
    const enoent: GitRunner = () => {
      throw Object.assign(new Error('spawn git ENOENT'), { code: 'ENOENT' });
    };
    expect(() => deriveSessionCommits(['2b7a2e5fcb'], '/clone', enoent)).toThrow(/ENOENT/);
  });
});

describe('attachSessionCommits', () => {
  const baseRecord = (over: Partial<WorkRecord>): WorkRecord =>
    WorkRecordSchema.parse({
      work_id: 'w1',
      rig: 'mem',
      title: 't',
      lifecycle: { created: '2026-06-01T00:00:00Z', status: 'closed' },
      ...over,
    });

  const resolving: GitRunner = () => `${PARENT}\n`;
  const read = (): string => '[main 2b7a2e5fcb] feat: x';

  it('attaches session_commits when both a transcript path and a clone are present', () => {
    const rec = baseRecord({
      trace: { jsonl_path: '/t/w1.jsonl' },
      provenance: {
        work_dir: '/clone',
        repo: 'mem',
        base_branch: 'main',
        history_state: 'recorded',
      },
    });
    const [out] = attachSessionCommits([rec], { run: resolving, read });
    expect(out.session_commits).toEqual({
      commits: ['2b7a2e5fcb'],
      first_commit: '2b7a2e5fcb',
      true_base: PARENT,
      base_state: 'resolved',
    });
  });

  it('is a no-op when the record has no resolved transcript path', () => {
    const rec = baseRecord({
      provenance: {
        work_dir: '/clone',
        repo: 'mem',
        base_branch: 'main',
        history_state: 'recorded',
      },
    });
    const [out] = attachSessionCommits([rec], { run: resolving, read });
    expect(out.session_commits).toBeUndefined();
  });

  it('is a no-op when the record has no clone (provenance work_dir)', () => {
    const rec = baseRecord({ trace: { jsonl_path: '/t/w1.jsonl' } });
    const [out] = attachSessionCommits([rec], { run: resolving, read });
    expect(out.session_commits).toBeUndefined();
  });

  it('skips a reaped transcript (ENOENT) rather than dropping the record', () => {
    const rec = baseRecord({
      trace: { jsonl_path: '/gone.jsonl' },
      provenance: {
        work_dir: '/clone',
        repo: 'mem',
        base_branch: 'main',
        history_state: 'recorded',
      },
    });
    const missing = (): string => {
      throw Object.assign(new Error('no such file'), { code: 'ENOENT' });
    };
    const [out] = attachSessionCommits([rec], { run: resolving, read: missing });
    expect(out.session_commits).toBeUndefined();
  });

  it('does not mutate the input record', () => {
    const rec = baseRecord({
      trace: { jsonl_path: '/t/w1.jsonl' },
      provenance: {
        work_dir: '/clone',
        repo: 'mem',
        base_branch: 'main',
        history_state: 'recorded',
      },
    });
    attachSessionCommits([rec], { run: resolving, read });
    expect(rec.session_commits).toBeUndefined();
  });
});
