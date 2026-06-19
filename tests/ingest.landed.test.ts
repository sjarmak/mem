import { describe, expect, it } from 'vitest';

import { attachLanded, deriveLanded, landedInput, type LandedInput } from '../src/ingest/landed.js';
import type { GitRunner } from '../src/ingest/provenance.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

const BASE = 'a'.repeat(40);
const END = 'b'.repeat(40);
const C1 = 'c'.repeat(40);

/** A git runner backed by per-subcommand handlers. A `rev-list` with `-1` is the
 * tip lookup; a `rev-list` with a `base..end` range is the commit listing. */
interface Handlers {
  tip?: () => string;
  range?: () => string;
  mergeBase?: () => string; // throw to signal not-ancestor / error
  log?: () => string;
}
const git = (h: Handlers): GitRunner => {
  return (_workDir, args) => {
    if (args[0] === 'rev-list' && args.includes('-1')) return (h.tip ?? (() => `${END}\n`))();
    if (args[0] === 'rev-list') return (h.range ?? (() => `${END}\n${C1}\n`))();
    if (args[0] === 'merge-base') return (h.mergeBase ?? (() => ''))();
    if (args[0] === 'log') return (h.log ?? (() => ''))();
    throw new Error(`unexpected git ${args.join(' ')}`);
  };
};

const input: LandedInput = {
  work_dir: '/repo',
  base_branch: 'main',
  base_commit: BASE,
  started_at: '2026-06-01 00:00:00',
  ended_at: '2026-06-01 01:00:00',
};

describe('deriveLanded', () => {
  it('classifies surviving in-window commits as landed', () => {
    const out = deriveLanded(input, git({}));
    expect(out).toEqual({
      base_commit: BASE,
      landed_commit: END,
      n_commits: 2,
      landed_state: 'landed',
    });
  });

  it('classifies an empty window (tip unchanged from base) as empty-window', () => {
    const out = deriveLanded(input, git({ tip: () => `${BASE}\n` }));
    expect(out).toEqual({ base_commit: BASE, n_commits: 0, landed_state: 'empty-window' });
  });

  it('degrades to unresolved when the base object is missing locally (bad range exit)', () => {
    // A base READ from a producer-recorded cut may not be a commit in this
    // checkout; rev-list base..end then exits non-zero. Must not crash the batch.
    const badRange = () => {
      throw Object.assign(new Error('fatal: bad revision'), { status: 128 });
    };
    const out = deriveLanded(input, git({ range: badRange }));
    expect(out).toEqual({ base_commit: BASE, landed_state: 'unresolved' });
  });

  it('rethrows a non-exit failure from the range listing (e.g. missing git)', () => {
    const noGit = () => {
      throw new Error('spawn git ENOENT'); // no .status → not a non-zero exit
    };
    expect(() => deriveLanded(input, git({ range: noGit }))).toThrow();
  });

  it('classifies a window with no forward commits as empty-window', () => {
    const out = deriveLanded(input, git({ range: () => '\n' }));
    expect(out).toEqual({ base_commit: BASE, n_commits: 0, landed_state: 'empty-window' });
  });

  it('classifies a tip no longer reachable from the branch as abandoned', () => {
    const out = deriveLanded(
      input,
      git({
        mergeBase: () => {
          throw Object.assign(new Error('not an ancestor'), { status: 1 });
        },
      })
    );
    expect(out).toMatchObject({ landed_commit: END, n_commits: 2, landed_state: 'abandoned' });
  });

  it('classifies a window whose commit is later reverted as reverted', () => {
    const out = deriveLanded(input, git({ log: () => `This reverts commit ${C1}\n` }));
    expect(out).toMatchObject({ landed_commit: END, landed_state: 'reverted' });
  });

  it('does not flag a revert that targets a commit outside the window', () => {
    const out = deriveLanded(input, git({ log: () => `This reverts commit ${'d'.repeat(40)}\n` }));
    expect(out.landed_state).toBe('landed');
  });

  it('marks unresolved when the close tip cannot be resolved (empty stdout)', () => {
    const out = deriveLanded(input, git({ tip: () => '   \n' }));
    expect(out).toEqual({ base_commit: BASE, landed_state: 'unresolved' });
  });

  it('marks unresolved on a non-zero git exit (checkout gone / unknown branch)', () => {
    const run: GitRunner = () => {
      throw Object.assign(new Error('fatal: not a git repository'), { status: 128 });
    };
    expect(deriveLanded(input, run).landed_state).toBe('unresolved');
  });

  it('propagates a missing-git-binary failure (a misconfiguration, not unresolved)', () => {
    const run: GitRunner = () => {
      throw Object.assign(new Error('spawn git ENOENT'), { code: 'ENOENT' });
    };
    expect(() => deriveLanded(input, run)).toThrow(/ENOENT/);
  });
});

/** Build a record with provenance + lifecycle for attachLanded tests. */
const rec = (
  id: string,
  prov: { base_commit?: string; base_branch?: string; work_dir?: string } | null,
  lifecycle: { started?: string; closed?: string }
): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: id,
    rig: 'mem',
    title: id,
    lifecycle: {
      created: lifecycle.started ?? '2026-06-01 00:00:00',
      ...lifecycle,
      status: 'closed',
    },
    ...(prov !== null && {
      provenance: {
        work_dir: prov.work_dir ?? '/repo',
        repo: 'repo',
        work_dir_source: 'rig-map',
        history_state: 'commit-by-date',
        ...(prov.base_branch !== undefined && {
          base_branch: prov.base_branch,
          base_branch_source: 'default',
        }),
        ...(prov.base_commit !== undefined && { base_commit: prov.base_commit }),
      },
    }),
  });

describe('landedInput', () => {
  it('extracts inputs when provenance resolved a commit + branch and a close exists', () => {
    const r = rec(
      'w',
      { base_commit: BASE, base_branch: 'main' },
      {
        started: '2026-06-01 00:00:00',
        closed: '2026-06-01 01:00:00',
      }
    );
    expect(landedInput(r)).toEqual({
      work_dir: '/repo',
      base_branch: 'main',
      base_commit: BASE,
      started_at: '2026-06-01 00:00:00',
      ended_at: '2026-06-01 01:00:00',
    });
  });

  it('returns null without a resolved base_commit', () => {
    expect(
      landedInput(rec('w', { base_branch: 'main' }, { closed: '2026-06-01 01:00:00' }))
    ).toBeNull();
  });

  it('returns null without a base_branch', () => {
    expect(
      landedInput(rec('w', { base_commit: BASE }, { closed: '2026-06-01 01:00:00' }))
    ).toBeNull();
  });

  it('returns null without a close timestamp', () => {
    expect(landedInput(rec('w', { base_commit: BASE, base_branch: 'main' }, {}))).toBeNull();
  });

  it('returns null when the close timestamp is not a recognizable datetime', () => {
    expect(
      landedInput(rec('w', { base_commit: BASE, base_branch: 'main' }, { closed: 'whenever' }))
    ).toBeNull();
  });
});

describe('attachLanded', () => {
  it('keeps overlapping windows with non-empty own windows as ambiguous', () => {
    const r1 = rec(
      'r1',
      { base_commit: BASE, base_branch: 'main' },
      {
        started: '2026-06-01 00:00:00',
        closed: '2026-06-01 02:00:00',
      }
    );
    const r2 = rec(
      'r2',
      { base_commit: BASE, base_branch: 'main' },
      {
        started: '2026-06-01 01:00:00',
        closed: '2026-06-01 03:00:00',
      }
    );
    // Each record's own window carries forward commits (the default handlers), so
    // the landed commits genuinely exist but cannot be split between the two
    // overlapping sessions — both stay ambiguous.
    const [a, b] = attachLanded([r1, r2], { run: git({}) });
    expect(a.landed).toEqual({ base_commit: BASE, landed_state: 'ambiguous-window' });
    expect(b.landed).toEqual({ base_commit: BASE, landed_state: 'ambiguous-window' });
  });

  it('downgrades an overlapping record whose own window is empty to empty-window', () => {
    const r1 = rec(
      'r1',
      { base_commit: BASE, base_branch: 'main' },
      {
        started: '2026-06-01 00:00:00',
        closed: '2026-06-01 02:00:00',
      }
    );
    const r2 = rec(
      'r2',
      { base_commit: BASE, base_branch: 'main' },
      {
        started: '2026-06-01 01:00:00',
        closed: '2026-06-01 03:00:00',
      }
    );
    // The branch tip never moved past base over either window: nothing landed,
    // so the overlap is irrelevant — both are deterministically empty, not
    // ambiguous. This needs no per-session attribution.
    const [a, b] = attachLanded([r1, r2], { run: git({ tip: () => `${BASE}\n` }) });
    expect(a.landed).toEqual({ base_commit: BASE, n_commits: 0, landed_state: 'empty-window' });
    expect(b.landed).toEqual({ base_commit: BASE, n_commits: 0, landed_state: 'empty-window' });
  });

  it('keeps an overlapping record ambiguous when its own close tip is unresolved', () => {
    const r1 = rec(
      'r1',
      { base_commit: BASE, base_branch: 'main' },
      { started: '2026-06-01 00:00:00', closed: '2026-06-01 02:00:00' }
    );
    const r2 = rec(
      'r2',
      { base_commit: BASE, base_branch: 'main' },
      { started: '2026-06-01 01:00:00', closed: '2026-06-01 03:00:00' }
    );
    // An unresolvable close tip cannot prove the window empty, so the record stays
    // ambiguous rather than being downgraded on a guess.
    const [a, b] = attachLanded([r1, r2], { run: git({ tip: () => '   \n' }) });
    expect(a.landed?.landed_state).toBe('ambiguous-window');
    expect(b.landed?.landed_state).toBe('ambiguous-window');
  });

  it('resolves non-overlapping candidates via git', () => {
    const r1 = rec(
      'r1',
      { base_commit: BASE, base_branch: 'main' },
      {
        started: '2026-06-01 00:00:00',
        closed: '2026-06-01 01:00:00',
      }
    );
    const r2 = rec(
      'r2',
      { base_commit: BASE, base_branch: 'main' },
      {
        started: '2026-06-01 02:00:00',
        closed: '2026-06-01 03:00:00',
      }
    );
    const [a, b] = attachLanded([r1, r2], { run: git({}) });
    expect(a.landed?.landed_state).toBe('landed');
    expect(b.landed?.landed_state).toBe('landed');
  });

  it('does not treat overlap across different checkouts as ambiguous', () => {
    const r1 = rec(
      'r1',
      { base_commit: BASE, base_branch: 'main', work_dir: '/repo-a' },
      {
        started: '2026-06-01 00:00:00',
        closed: '2026-06-01 02:00:00',
      }
    );
    const r2 = rec(
      'r2',
      { base_commit: BASE, base_branch: 'main', work_dir: '/repo-b' },
      {
        started: '2026-06-01 01:00:00',
        closed: '2026-06-01 03:00:00',
      }
    );
    const [a, b] = attachLanded([r1, r2], { run: git({}) });
    expect(a.landed?.landed_state).toBe('landed');
    expect(b.landed?.landed_state).toBe('landed');
  });

  it('leaves non-candidate records untouched', () => {
    const noProv = rec('np', null, { closed: '2026-06-01 01:00:00' });
    const [out] = attachLanded([noProv], { run: git({}) });
    expect(out.landed).toBeUndefined();
  });

  it('copies records — never mutates the input', () => {
    const r = rec(
      'w',
      { base_commit: BASE, base_branch: 'main' },
      {
        started: '2026-06-01 00:00:00',
        closed: '2026-06-01 01:00:00',
      }
    );
    attachLanded([r], { run: git({}) });
    expect(r.landed).toBeUndefined();
  });
});
