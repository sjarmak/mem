import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import {
  type GitRunner,
  attachProvenance,
  defaultGitPipeRunner,
  defaultGitRunner,
  deriveProvenance,
  exitStatus,
  isAncestor,
  isAncestorOrNull,
  isNonZeroExit,
  makeGitPipeRunner,
  provenanceInput,
  toGitUtc,
} from '../src/ingest/provenance.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

/** A full 40-hex commit SHA, the shape `git rev-list -1` emits and the form
 * ProvenanceSchema.base_commit enforces. */
const SHA = '0123456789abcdef0123456789abcdef01234567';

/** A spine record with arbitrary metadata + lifecycle, validated like ingest.
 * `rig` defaults to `dec`, an UNMAPPED rig, so a test exercises pure metadata
 * resolution unless it opts into a mapped rig (e.g. `mem`) for backfill. */
const record = (
  workId: string,
  metadata: Record<string, unknown>,
  lifecycle: { created: string; started?: string },
  rig = 'dec'
): WorkRecord =>
  WorkRecordSchema.parse({
    work_id: workId,
    rig,
    title: `work ${workId}`,
    metadata,
    lifecycle: { ...lifecycle, status: 'closed' },
  });

describe('toGitUtc', () => {
  it('appends an explicit UTC offset to a TZ-less dolt timestamp', () => {
    expect(toGitUtc('2026-06-07 02:19:05')).toBe('2026-06-07 02:19:05 +0000');
  });

  it('rewrites a trailing Z to an explicit offset (git approxidate is local-biased)', () => {
    expect(toGitUtc('2026-06-05T03:24:03Z')).toBe('2026-06-05T03:24:03 +0000');
  });

  it('leaves an already-explicit offset untouched', () => {
    expect(toGitUtc('2026-06-07 02:19:05 +0000')).toBe('2026-06-07 02:19:05 +0000');
    expect(toGitUtc('2026-06-07T02:19:05-04:00')).toBe('2026-06-07T02:19:05-04:00');
  });

  it('throws on a value that is not a recognizable datetime (upstream corruption)', () => {
    expect(() => toGitUtc('--output=/tmp/pwned')).toThrow(/not a recognizable datetime/);
    expect(() => toGitUtc('')).toThrow(/not a recognizable datetime/);
  });
});

describe('provenanceInput — recorded metadata', () => {
  it('reads gc.work_dir (dotted keys are LITERAL flat keys) and gc.var.base_branch', () => {
    const rec = record(
      'w-1',
      { 'gc.work_dir': '/home/ds/projects/mem', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' },
      'mem'
    );
    expect(provenanceInput(rec)).toEqual({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'metadata',
      repo: 'mem',
      base_branch: 'main',
      base_branch_source: 'metadata',
      started_at: '2026-06-05 03:24:03',
    });
  });

  it('falls back to the legacy flat work_dir key and to created when started is absent', () => {
    const rec = record(
      'w-2',
      { work_dir: '/home/ds/gas-city' },
      { created: '2026-06-01T00:00:00Z' }
    );
    expect(provenanceInput(rec)).toEqual({
      work_dir: '/home/ds/gas-city',
      work_dir_source: 'metadata',
      repo: 'gas-city',
      started_at: '2026-06-01T00:00:00Z',
    });
  });

  it('defaults the branch to the rig integration branch when the dir is recorded but the branch is not', () => {
    // mem is a mapped rig: a recorded work_dir but no recorded base_branch still
    // gets the rig's known integration branch (data-backed default).
    const rec = record(
      'w-2b',
      { 'gc.work_dir': '/home/ds/projects/mem' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' },
      'mem'
    );
    expect(provenanceInput(rec)).toMatchObject({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'metadata',
      base_branch: 'main',
      base_branch_source: 'default',
    });
  });
});

describe('provenanceInput — rig-map backfill', () => {
  it('backfills work_dir + branch from the rig when no metadata work_dir was recorded', () => {
    const rec = record(
      'w-bf',
      { workflow_id: 'mem-nokh' },
      { created: '2026-06-01T00:00:00Z' },
      'mem'
    );
    expect(provenanceInput(rec)).toEqual({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'rig-map',
      repo: 'mem',
      base_branch: 'main',
      base_branch_source: 'default',
      started_at: '2026-06-01T00:00:00Z',
    });
  });

  it("honors a rig's non-main integration branch", () => {
    const rec = record('w-zelda', {}, { created: '2026-06-01T00:00:00Z' }, 'zeldascension');
    expect(provenanceInput(rec)).toMatchObject({
      work_dir: '/home/ds/projects/zeldascension',
      work_dir_source: 'rig-map',
      base_branch: 'master',
      base_branch_source: 'default',
    });
  });

  it('backfills from the rig when the metadata work_dir is empty or non-absolute', () => {
    const empty = record('w-e', { 'gc.work_dir': '' }, { created: '2026-06-01T00:00:00Z' }, 'mem');
    const rel = record(
      'w-r',
      { 'gc.work_dir': 'rel/path' },
      { created: '2026-06-01T00:00:00Z' },
      'mem'
    );
    expect(provenanceInput(empty)).toMatchObject({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'rig-map',
    });
    expect(provenanceInput(rel)).toMatchObject({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'rig-map',
    });
  });
});

describe('provenanceInput — no usable source', () => {
  it('returns null for an unmapped rig with no work_dir', () => {
    const rec = record('w-3', { workflow_id: 'mem-nokh' }, { created: '2026-06-01T00:00:00Z' });
    expect(provenanceInput(rec)).toBeNull();
  });

  it('returns null for an unmapped rig with an empty-string work_dir', () => {
    const rec = record('w-4', { 'gc.work_dir': '' }, { created: '2026-06-01T00:00:00Z' });
    expect(provenanceInput(rec)).toBeNull();
  });

  it('returns null for an unmapped rig with a non-absolute work_dir', () => {
    const rec = record(
      'w-5',
      { 'gc.work_dir': 'relative/path' },
      { created: '2026-06-01T00:00:00Z' }
    );
    expect(provenanceInput(rec)).toBeNull();
  });

  it('returns null for a multi-repo rig (gc) — never backfills a single dir', () => {
    const rec = record('w-gc', {}, { created: '2026-06-01T00:00:00Z' }, 'gc');
    expect(provenanceInput(rec)).toBeNull();
  });
});

describe('deriveProvenance', () => {
  const input = {
    work_dir: '/repo',
    work_dir_source: 'metadata' as const,
    repo: 'repo',
    base_branch: 'main',
    base_branch_source: 'metadata' as const,
    started_at: '2026-06-05 03:24:03',
  };

  it('resolves the commit by date, normalizes the timestamp, and guards the ref with --end-of-options', () => {
    const calls: string[][] = [];
    const run: GitRunner = (workDir, args) => {
      calls.push([workDir, ...args]);
      return `${SHA}\n`;
    };
    const prov = deriveProvenance(input, run);
    expect(prov).toEqual({
      work_dir: '/repo',
      work_dir_source: 'metadata',
      repo: 'repo',
      base_branch: 'main',
      base_branch_source: 'metadata',
      base_commit: SHA,
      history_state: 'commit-by-date',
    });
    // --end-of-options before the DB-sourced ref blocks git argument injection.
    expect(calls).toEqual([
      ['/repo', 'rev-list', '-1', '--before=2026-06-05 03:24:03 +0000', '--end-of-options', 'main'],
    ]);
  });

  it('carries a defaulted branch source through to the Provenance', () => {
    const prov = deriveProvenance({ ...input, base_branch_source: 'default' }, () => `${SHA}\n`);
    expect(prov).toMatchObject({
      base_branch: 'main',
      base_branch_source: 'default',
      base_commit: SHA,
    });
  });

  it('fails the schema parse when git output is not a 40-hex SHA (corruption guard)', () => {
    expect(() => deriveProvenance(input, () => 'not-a-sha\n')).toThrow();
  });

  it('marks unresolved when no base_branch is known (never falls back to HEAD)', () => {
    const run: GitRunner = () => {
      throw new Error('git must not be called without a base branch');
    };
    const prov = deriveProvenance(
      {
        work_dir: '/repo',
        work_dir_source: 'metadata',
        repo: 'repo',
        started_at: '2026-06-05 03:24:03',
      },
      run
    );
    expect(prov).toEqual({
      work_dir: '/repo',
      work_dir_source: 'metadata',
      repo: 'repo',
      history_state: 'unresolved',
    });
  });

  it('marks unresolved on a zero-exit empty stdout (valid branch, no commit before start)', () => {
    const run: GitRunner = () => '   \n';
    const prov = deriveProvenance(input, run);
    expect(prov).toEqual({
      work_dir: '/repo',
      work_dir_source: 'metadata',
      repo: 'repo',
      base_branch: 'main',
      base_branch_source: 'metadata',
      history_state: 'unresolved',
    });
  });

  it('marks unresolved on a non-zero git exit (work_dir gone / unknown branch)', () => {
    const run: GitRunner = () => {
      throw Object.assign(new Error('fatal: not a git repository'), { status: 128 });
    };
    expect(deriveProvenance(input, run).history_state).toBe('unresolved');
  });

  it('propagates a missing-git-binary failure (a misconfiguration, not unresolved)', () => {
    const run: GitRunner = () => {
      throw Object.assign(new Error('spawn git ENOENT'), { code: 'ENOENT' });
    };
    expect(() => deriveProvenance(input, run)).toThrow(/ENOENT/);
  });
});

describe('attachProvenance', () => {
  it('attaches provenance to work_dir-bearing records and leaves unresolvable rigs untouched', () => {
    const withDir = record(
      'w-dir',
      { 'gc.work_dir': '/repo', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' },
      'mem'
    );
    // Unmapped rig with no work_dir → no provenance source at all.
    const noDir = record('w-none', {}, { created: '2026-06-01T00:00:00Z' });

    const run: GitRunner = () => `${SHA}\n`;
    const [a, b] = attachProvenance([withDir, noDir], { run });

    expect(a.provenance).toEqual({
      work_dir: '/repo',
      work_dir_source: 'metadata',
      repo: 'repo',
      base_branch: 'main',
      base_branch_source: 'metadata',
      base_commit: SHA,
      history_state: 'commit-by-date',
    });
    expect(b.provenance).toBeUndefined();
  });

  it('attaches a rig-map baseline to a mapped record that recorded no work_dir', () => {
    const rec = record('w-rigonly', {}, { created: '2026-06-01T00:00:00Z' }, 'mem');
    const [out] = attachProvenance([rec], { run: () => `${SHA}\n` });
    expect(out.provenance).toMatchObject({
      work_dir: '/home/ds/projects/mem',
      work_dir_source: 'rig-map',
      base_branch: 'main',
      base_branch_source: 'default',
      base_commit: SHA,
      history_state: 'commit-by-date',
    });
  });

  it('copies records — never mutates the input', () => {
    const rec = record(
      'w-imm',
      { 'gc.work_dir': '/repo', 'gc.var.base_branch': 'main' },
      { created: '2026-06-01T00:00:00Z', started: '2026-06-05 03:24:03' },
      'mem'
    );
    attachProvenance([rec], { run: () => `${SHA}\n` });
    expect(rec.provenance).toBeUndefined();
  });
});

/** Run `fn`, returning what it throws (or `undefined` if it doesn't). */
const throwsWith = (fn: () => unknown): unknown => {
  try {
    fn();
    return undefined;
  } catch (err) {
    return err;
  }
};

describe('defaultGitPipeRunner', () => {
  // The one seam no fake can stand in for: whether a REAL `a | b` pipeline
  // reports a first-stage failure, and whether args reach git as args rather
  // than as shell syntax. Both are properties of the bash script itself.
  let repo: string;

  beforeAll(() => {
    repo = mkdtempSync(join(tmpdir(), 'mem-pipe-'));
    const git = (...args: string[]): void => {
      execFileSync('git', ['-C', repo, ...args], { stdio: 'ignore' });
    };
    git('init', '-q', '-b', 'main');
    git('config', 'user.email', 'test@example.com');
    git('config', 'user.name', 'test');
    execFileSync('bash', ['-c', 'echo hello > "$1/f.txt"', 'seed', repo]);
    git('add', 'f.txt');
    git('commit', '-qm', 'seed');
  });

  afterAll(() => rmSync(repo, { recursive: true, force: true }));

  it('returns the second stage stdout, keeping the patch text out of the process', () => {
    const out = defaultGitPipeRunner(repo, ['log', '-p', '--no-color', 'HEAD']);
    // `<patch-id> <commit-id>`, both 40-hex — and NOT the patch text that
    // produced it, which is the whole point of piping in the kernel.
    expect(out.trim()).toMatch(/^[0-9a-f]{40} [0-9a-f]{40}$/);
    expect(out).not.toContain('hello');
  });

  it('surfaces a first-stage failure instead of patch-id exiting 0 over it (pipefail)', () => {
    // A pipeline's status is its LAST command's, and `patch-id` exits 0 on the
    // empty input a failed `git log` leaves it. Without `set -o pipefail` this
    // returns '' with status 0 — which landedContent reads as an empty patch-id
    // map, i.e. `no-branch-content`: a false undecidable quietly standing in for
    // a real `range-unreadable`. Verified: the same pipeline without pipefail
    // exits 0 here. So this asserts the exit is visible AND is the shape
    // isNonZeroExit recognizes, since that is what routes it to the right cause.
    const caught = throwsWith(() =>
      defaultGitPipeRunner(repo, ['log', '-p', 'no-such-rev-000..HEAD'])
    );
    expect(isNonZeroExit(caught)).toBe(true);
  });

  it('passes args positionally, so a git arg is never read as shell syntax', () => {
    // args are POSITIONAL params to the script, never interpolated into it. A
    // metacharacter-laden ref must reach git as one revision string — an unknown
    // one, hence a non-zero exit — not as a command separator bash acts on.
    //
    // The payload names an ABSOLUTE path because the runner passes no `cwd`: an
    // injection would fire in the vitest process's cwd, not in `repo`, so a
    // bare `touch pwned` would land somewhere this assertion never looks and
    // would pass against a vulnerable implementation.
    const pwned = join(repo, 'pwned');
    const caught = throwsWith(() =>
      defaultGitPipeRunner(repo, ['log', '-p', '--end-of-options', `; touch ${pwned}`])
    );
    // A non-zero exit, not merely "something threw": git rejecting the payload
    // as an unknown revision is the pass. An interpolating implementation would
    // instead run `log -p --end-of-options` (lists HEAD, exit 0) piped from a
    // successful `touch`, so nothing would throw at all.
    expect(isNonZeroExit(caught)).toBe(true);
    expect(existsSync(pwned)).toBe(false);
  });

  it('emits nothing for an empty range, so an empty patch-id map is already the answer', () => {
    // The claim that let the `diff.trim() === ''` guards go from landedContent:
    // patch-id on an empty patch emits no line, so `no-branch-content` (empty
    // map) and a null combined id fall out without Node inspecting a diff. The
    // fakes elsewhere assert this; only real git can check it.
    expect(defaultGitPipeRunner(repo, ['log', '-p', '--no-color', 'HEAD..HEAD'])).toBe('');
  });

  it('silenceStderr preserves the functional contract — stdout intact, failures still surface', () => {
    // The sweep runner suppresses git's stderr (a degraded range is a coverage
    // hole to record, not console noise). The one way that could go wrong is
    // mis-wiring the stdio triple: routing stdout to `ignore` would silently
    // return '' for a good range, and swallowing the exit would hide a real
    // fault. Pin both against the default runner rather than the stderr bytes
    // themselves, which execFileSync writes to fd 2 (uncapturable in-process).
    const quiet = makeGitPipeRunner({ silenceStderr: true });
    expect(quiet(repo, ['log', '-p', '--no-color', 'HEAD'])).toBe(
      defaultGitPipeRunner(repo, ['log', '-p', '--no-color', 'HEAD'])
    );
    const caught = throwsWith(() => quiet(repo, ['log', '-p', 'no-such-rev-000..HEAD']));
    expect(isNonZeroExit(caught)).toBe(true);
  });
});

describe('isAncestor / isAncestorOrNull', () => {
  // `merge-base --is-ancestor` answers ONLY through its exit code, and the
  // whole point of this seam is telling git's documented codes apart: 0 = yes,
  // 1 = no, 128 = could not read the objects. A fake runner cannot pin that —
  // it would only assert that the status this test invents maps to the branch
  // this test chose. Real git is the seam no fake can stand in for, so the
  // codes here come from git itself (mem-y2x7n).
  let repo: string;
  let first: string;
  let second: string;
  let orphan: string;

  beforeAll(() => {
    repo = mkdtempSync(join(tmpdir(), 'mem-ancestor-'));
    const git = (...args: string[]): string =>
      execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8' }).trim();
    /** Write, stage, commit — returns the new sha. */
    const commit = (file: string, body: string, message: string): string => {
      writeFileSync(join(repo, file), body);
      git('add', file);
      git('commit', '-qm', message);
      return git('rev-parse', 'HEAD');
    };
    git('init', '-q', '-b', 'main');
    git('config', 'user.email', 'test@example.com');
    git('config', 'user.name', 'test');

    first = commit('f.txt', 'one\n', 'first');
    second = commit('f.txt', 'two\n', 'second');

    // An unrelated history: shares no ancestor with main, so is-ancestor is a
    // genuine NO (exit 1) rather than a fault.
    git('checkout', '-q', '--orphan', 'other');
    orphan = commit('g.txt', 'other\n', 'orphan');
    git('checkout', '-q', 'main');
  });

  afterAll(() => rmSync(repo, { recursive: true, force: true }));

  /** A syntactically valid 40-hex sha that names no object in this repo — the
   * pruned/corrupt-object case, which git reports as exit 128, NOT exit 1. */
  const MISSING = 'dead' + 'beef'.repeat(8) + 'dead';

  it('answers true when the commit is an ancestor (git exit 0)', () => {
    expect(isAncestor(defaultGitRunner, repo, first, second)).toBe(true);
  });

  it('answers false for a real non-ancestor (git exit 1)', () => {
    expect(isAncestor(defaultGitRunner, repo, second, first)).toBe(false);
    expect(isAncestor(defaultGitRunner, repo, orphan, second)).toBe(false);
  });

  it('THROWS on an unreadable object rather than reading it as not-an-ancestor', () => {
    // The 0985d82 guard: only the exact status 1 may be read as an answer.
    const caught = throwsWith(() => isAncestor(defaultGitRunner, repo, MISSING, second));
    expect(exitStatus(caught)).toBe(128);
  });

  it('degrades an unreadable object to null — and null is NOT false (mem-y2x7n)', () => {
    // The bead's VERIFY line, executable: a garbage/pruned sha must not
    // silently classify as not-an-ancestor. `false` is a CLAIM ("git answered
    // no"); null is the absence of an answer. Asserting `not.toBe(false)`
    // explicitly is the point — a bare `toBeNull()` would still pass against an
    // implementation that returned false for its own reasons.
    const out = isAncestorOrNull(defaultGitRunner, repo, MISSING, second);
    expect(out).toBeNull();
    expect(out).not.toBe(false);
  });

  it('still reports a REAL no as false, so the fault branch has not eaten the answer', () => {
    // The other half of the above: degrading faults to null must not degrade
    // genuine negatives with them, or the R3 gate would stop detecting the
    // off-authoritative bases it exists to catch.
    expect(isAncestorOrNull(defaultGitRunner, repo, second, first)).toBe(false);
    expect(isAncestorOrNull(defaultGitRunner, repo, first, second)).toBe(true);
  });

  it('degrades a missing git binary to null rather than killing an unguarded sweep', () => {
    const noGit: GitRunner = () => {
      throw Object.assign(new Error('spawn git ENOENT'), { code: 'ENOENT' });
    };
    expect(isAncestorOrNull(noGit, repo, first, second)).toBeNull();
  });

  it('degrades a signal-killed git to null (git was asked but could not answer)', () => {
    const killed: GitRunner = () => {
      throw Object.assign(new Error('git terminated'), { signal: 'SIGKILL' });
    };
    expect(isAncestorOrNull(killed, repo, first, second)).toBeNull();
  });

  it('RETHROWS a mis-wired runner rather than reporting null for every ref (mem-egxu2)', () => {
    // The whole defect: a programming error carries no git verdict, so it must
    // NOT be laundered into null. A mis-wired GitRunner that throws a TypeError
    // (e.g. an undefined work_dir) has no `status`, no ENOENT, no signal — it is
    // our bug, not git declining to answer. Swallowing it makes the
    // measure-live-ref sweep exit 0 claiming git could not answer for EVERY ref.
    const misWired: GitRunner = () => {
      throw new TypeError("Cannot read properties of undefined (reading 'trim')");
    };
    expect(() => isAncestorOrNull(misWired, repo, first, second)).toThrow(TypeError);
  });

  it('RETHROWS a maxBuffer overrun rather than degrading it to null (mem-egxu2)', () => {
    // A RangeError from an execFileSync maxBuffer overrun is likewise our fault,
    // not a git answer — it must surface, not become a null measurement.
    const overrun: GitRunner = () => {
      throw new RangeError('stdout maxBuffer length exceeded');
    };
    expect(() => isAncestorOrNull(overrun, repo, first, second)).toThrow(RangeError);
  });

  it('passes --end-of-options, so a ref that looks like a flag cannot be read as one', () => {
    const seen: string[][] = [];
    const spy: GitRunner = (dir, args) => {
      seen.push(args);
      return '';
    };
    isAncestor(spy, repo, first, second);
    expect(seen[0]).toEqual(['merge-base', '--is-ancestor', '--end-of-options', first, second]);
  });
});
