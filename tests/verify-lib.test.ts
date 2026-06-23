import { describe, expect, it } from 'vitest';

import {
  aliasesFor,
  buildVerdict,
  classifyCheckout,
  compareRemoteToSlug,
  groupByObjectStore,
  parseRemoteSlug,
  pickRemoteForSlug,
} from '../scripts/verify/lib.mjs';

describe('parseRemoteSlug (normalize git@ / https / ssh / .git)', () => {
  it('parses scp-like git@github.com:owner/name.git', () => {
    expect(parseRemoteSlug('git@github.com:gastownhall/gascity.git')).toEqual({
      owner: 'gastownhall',
      name: 'gascity',
      slug: 'gastownhall/gascity',
    });
  });
  it('parses https with and without a trailing .git', () => {
    expect(parseRemoteSlug('https://github.com/sjarmak/mem.git')?.slug).toBe('sjarmak/mem');
    expect(parseRemoteSlug('https://github.com/sjarmak/mem')?.slug).toBe('sjarmak/mem');
  });
  it('parses ssh://git@github.com/owner/name', () => {
    expect(parseRemoteSlug('ssh://git@github.com/sjarmak/geo.git')?.slug).toBe('sjarmak/geo');
  });
  it('tolerates a trailing slash', () => {
    expect(parseRemoteSlug('https://github.com/sjarmak/mem/')?.slug).toBe('sjarmak/mem');
  });
  it('returns null for a non-github host (fail-closed)', () => {
    expect(parseRemoteSlug('https://gitlab.com/sjarmak/mem.git')).toBeNull();
    expect(parseRemoteSlug('git@bitbucket.org:sjarmak/mem.git')).toBeNull();
  });
  it('returns null for malformed / partial paths', () => {
    expect(parseRemoteSlug('https://github.com/onlyowner')).toBeNull();
    expect(parseRemoteSlug('https://github.com/a/b/c')).toBeNull();
    expect(parseRemoteSlug('not a url')).toBeNull();
  });
  it('returns null for empty / non-string input', () => {
    expect(parseRemoteSlug('')).toBeNull();
    expect(parseRemoteSlug(null)).toBeNull();
    expect(parseRemoteSlug(undefined)).toBeNull();
  });
});

describe('compareRemoteToSlug (hard, fail-closed comparison)', () => {
  it('matches when the parsed slug equals the expected slug', () => {
    const r = compareRemoteToSlug('https://github.com/sjarmak/mem.git', 'sjarmak/mem');
    expect(r).toMatchObject({ ok: true, observed: 'sjarmak/mem', reason: 'remote-matches-slug' });
  });
  it('matches case-insensitively (GitHub owner/name are case-insensitive)', () => {
    expect(compareRemoteToSlug('https://github.com/sjarmak/GEO.git', 'sjarmak/geo').ok).toBe(true);
  });
  it('is a hard mismatch when the remote is a different repo', () => {
    const r = compareRemoteToSlug('https://github.com/gastownhall/gascity.git', 'sjarmak/mem');
    expect(r.ok).toBe(false);
    expect(r.reason).toBe('remote-slug-mismatch');
    expect(r.observed).toBe('gastownhall/gascity');
  });
  it('an unparseable remote is never coerced to OK', () => {
    const r = compareRemoteToSlug(null, 'sjarmak/mem');
    expect(r.ok).toBe(false);
    expect(r.reason).toBe('remote-unparseable');
    expect(r.observed).toBeNull();
  });
});

describe('pickRemoteForSlug (scan all remotes, fail-closed)', () => {
  it('prefers origin when it matches', () => {
    const r = pickRemoteForSlug(
      {
        origin: 'https://github.com/sjarmak/mem.git',
        upstream: 'https://github.com/other/mem.git',
      },
      'sjarmak/mem'
    );
    expect(r).toEqual({ remote: 'origin', url: 'https://github.com/sjarmak/mem.git' });
  });
  it('falls back to upstream when origin is a different repo', () => {
    const r = pickRemoteForSlug(
      {
        origin: 'https://github.com/myfork/mem.git',
        upstream: 'https://github.com/sjarmak/mem.git',
      },
      'sjarmak/mem'
    );
    expect(r?.remote).toBe('upstream');
  });
  it('accepts a canonical upstream under a NON-standard remote name', () => {
    // The real gascity_dashboard case: no origin; fork=jsgerman-oss, the canonical
    // upstream is under the remote literally named `gascity-dashboard`.
    const r = pickRemoteForSlug(
      {
        fork: 'https://github.com/jsgerman-oss/gascity-dashboard.git',
        'gascity-dashboard': 'https://github.com/gastownhall/gascity-dashboard.git',
      },
      'gastownhall/gascity-dashboard'
    );
    expect(r?.remote).toBe('gascity-dashboard');
  });
  it('returns null when NO remote resolves to the expected slug (fail-closed)', () => {
    expect(
      pickRemoteForSlug({ origin: 'https://github.com/someone/else.git' }, 'sjarmak/mem')
    ).toBeNull();
  });
  it('returns null for no remotes', () => {
    expect(pickRemoteForSlug({}, 'sjarmak/mem')).toBeNull();
  });
});

describe('classifyCheckout (primary clone vs linked worktree)', () => {
  it('primary clone: git-dir equals common-dir', () => {
    const c = classifyCheckout('/home/ds/projects/mem/.git', '/home/ds/projects/mem/.git');
    expect(c.kind).toBe('primary-clone');
  });
  it('linked worktree: git-dir is …/.git/worktrees/<name>, common-dir is …/.git', () => {
    // The real gascity aliasing case: gascity-main is a worktree of gascity.
    const c = classifyCheckout(
      '/home/ds/gascity/.git/worktrees/gascity-main',
      '/home/ds/gascity/.git'
    );
    expect(c.kind).toBe('linked-worktree');
    expect(c.commonDir).toBe('/home/ds/gascity/.git');
  });
  it('normalizes a trailing slash so …/.git and …/.git/ compare equal', () => {
    expect(classifyCheckout('/x/.git/', '/x/.git').kind).toBe('primary-clone');
  });
});

describe('groupByObjectStore / aliasesFor (shared-store detection)', () => {
  it('groups distinct rigs that share one common-dir', () => {
    const groups = groupByObjectStore([
      { rig: 'gascity', commonDir: '/home/ds/gascity/.git' },
      { rig: 'gascity_alt', commonDir: '/home/ds/gascity/.git' },
      { rig: 'mem', commonDir: '/home/ds/projects/mem/.git' },
    ]);
    expect(groups.get('/home/ds/gascity/.git')).toEqual(['gascity', 'gascity_alt']);
    expect(groups.get('/home/ds/projects/mem/.git')).toEqual(['mem']);
  });
  it('aliasesFor excludes the rig itself and is sorted', () => {
    const groups = groupByObjectStore([
      { rig: 'b', commonDir: '/s/.git' },
      { rig: 'a', commonDir: '/s/.git' },
      { rig: 'c', commonDir: '/s/.git' },
    ]);
    expect(aliasesFor(groups, 'b', '/s/.git')).toEqual(['a', 'c']);
  });
  it('aliasesFor is empty when a rig owns its store', () => {
    const groups = groupByObjectStore([{ rig: 'mem', commonDir: '/m/.git' }]);
    expect(aliasesFor(groups, 'mem', '/m/.git')).toEqual([]);
  });
});

describe('buildVerdict (fail-closed verdict row)', () => {
  const base = {
    rig: 'mem',
    dir: '/home/ds/projects/mem',
    slug: 'sjarmak/mem',
    remotes: { origin: 'https://github.com/sjarmak/mem.git' },
    gitDir: '/home/ds/projects/mem/.git',
    commonDir: '/home/ds/projects/mem/.git',
    aliases: [] as string[],
  };

  it('a clean primary clone with a matching origin → ok', () => {
    const v = buildVerdict({ ...base, exists: true });
    expect(v.ok).toBe(true);
    expect(v.remote_ok).toBe(true);
    expect(v.remote_name).toBe('origin');
    expect(v.checkout_kind).toBe('primary-clone');
    expect(v.worktree).toBe(false);
    expect(v.reason).toBe('remote-matches-slug');
  });

  it('a wrong remote is ALWAYS a hard fail', () => {
    const v = buildVerdict({
      ...base,
      exists: true,
      remotes: { origin: 'https://github.com/gastownhall/gascity.git' },
    });
    expect(v.ok).toBe(false);
    expect(v.remote_ok).toBe(false);
    expect(v.reason).toContain('remote-slug-mismatch');
  });

  it('accepts the canonical upstream under a non-standard remote name', () => {
    const v = buildVerdict({
      ...base,
      rig: 'gascity_dashboard',
      slug: 'gastownhall/gascity-dashboard',
      exists: true,
      remotes: {
        fork: 'https://github.com/jsgerman-oss/gascity-dashboard.git',
        'gascity-dashboard': 'https://github.com/gastownhall/gascity-dashboard.git',
      },
    });
    expect(v.ok).toBe(true);
    expect(v.remote_name).toBe('gascity-dashboard');
    expect(v.reason).toContain('remote-matches-slug-via:gascity-dashboard');
  });

  it('no remote configured at all → hard fail remote-none-configured', () => {
    const v = buildVerdict({ ...base, exists: true, remotes: {} });
    expect(v.ok).toBe(false);
    expect(v.reason).toContain('remote-none-configured');
  });

  it('a missing checkout → ok:false checkout-missing', () => {
    const v = buildVerdict({ ...base, exists: false, gitDir: null, commonDir: null });
    expect(v.ok).toBe(false);
    expect(v.reason).toBe('checkout-missing');
    expect(v.exists).toBe(false);
  });

  it('a linked-worktree checkout is reported but not by itself fatal', () => {
    // gascity-main: worktree of gascity, correct remote → ok stays true, but the
    // reason flags the worktree so the orchestrator can apply policy.
    const v = buildVerdict({
      rig: 'gascity',
      dir: '/home/ds/gascity-main',
      slug: 'gastownhall/gascity',
      exists: true,
      remotes: { origin: 'https://github.com/gastownhall/gascity.git' },
      gitDir: '/home/ds/gascity/.git/worktrees/gascity-main',
      commonDir: '/home/ds/gascity/.git',
      aliases: [],
    });
    expect(v.checkout_kind).toBe('linked-worktree');
    expect(v.worktree).toBe(true);
    expect(v.ok).toBe(true);
    expect(v.reason).toContain('checkout-is-linked-worktree');
  });

  it('a worktree with a WRONG remote is still a hard fail', () => {
    const v = buildVerdict({
      rig: 'gascity',
      dir: '/home/ds/gascity-main',
      slug: 'gastownhall/gascity',
      exists: true,
      remotes: { origin: 'https://github.com/someone/else.git' },
      gitDir: '/home/ds/gascity/.git/worktrees/gascity-main',
      commonDir: '/home/ds/gascity/.git',
      aliases: [],
    });
    expect(v.ok).toBe(false);
    expect(v.reason).toContain('remote-slug-mismatch');
    expect(v.reason).toContain('checkout-is-linked-worktree');
  });

  it('records aliases when another rig shares the store', () => {
    const v = buildVerdict({ ...base, exists: true, aliases: ['other_rig'] });
    expect(v.aliases).toEqual(['other_rig']);
    expect(v.reason).toContain('shares-store-with:other_rig');
  });

  it('a multi rig WITH a checkout skips the remote assertion but is ok on existence', () => {
    const v = buildVerdict({
      rig: 'gc',
      dir: '/home/ds/gc',
      slug: '',
      multi: true,
      exists: true,
      remotes: {},
      gitDir: '/home/ds/gc/.git',
      commonDir: '/home/ds/gc/.git',
      aliases: [],
    });
    expect(v.remote_ok).toBeNull();
    expect(v.ok).toBe(true);
    expect(v.reason).toBe('remote-skipped-multi');
  });

  it('a multi rig with NO checkout is recorded ok (it owns no authoritative repo)', () => {
    const v = buildVerdict({
      rig: 'gc',
      dir: '(none)',
      slug: '',
      multi: true,
      exists: false,
      remotes: {},
      gitDir: null,
      commonDir: null,
      aliases: [],
    });
    expect(v.ok).toBe(true);
    expect(v.reason).toBe('multi-rig-no-checkout');
  });

  it('a NON-multi rig with no checkout is a hard fail', () => {
    const v = buildVerdict({
      ...base,
      exists: false,
      gitDir: null,
      commonDir: null,
    });
    expect(v.ok).toBe(false);
    expect(v.reason).toBe('checkout-missing');
  });

  it('returns a fresh aliases array (no shared mutable state)', () => {
    const aliases = ['x'];
    const v = buildVerdict({ ...base, exists: true, aliases });
    expect(v.aliases).not.toBe(aliases);
    expect(v.aliases).toEqual(['x']);
  });
});
