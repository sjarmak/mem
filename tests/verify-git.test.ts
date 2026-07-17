import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { gitOut, readRemotes } from '../scripts/verify/git.mjs';

const dirs: string[] = [];
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

function repo(): string {
  const dir = mkdtempSync(join(tmpdir(), 'verify-git-'));
  dirs.push(dir);
  execFileSync('git', ['-C', dir, 'init', '-q']);
  return dir;
}

function addRemote(dir: string, name: string, url: string): void {
  execFileSync('git', ['-C', dir, 'remote', 'add', name, url]);
}

describe('gitOut', () => {
  it('returns trimmed stdout for a successful command', () => {
    const dir = repo();
    addRemote(dir, 'origin', 'https://github.com/sjarmak/mem.git');
    expect(gitOut(dir, ['remote', 'get-url', 'origin'])).toBe('https://github.com/sjarmak/mem.git');
  });

  it('returns null on a non-zero git exit (unknown remote)', () => {
    const dir = repo();
    expect(gitOut(dir, ['remote', 'get-url', 'nope'])).toBeNull();
  });
});

describe('readRemotes', () => {
  it('returns {} for a repo with no remotes', () => {
    const dir = repo();
    expect(readRemotes(dir)).toEqual({});
  });

  it('reads multiple remotes by name', () => {
    const dir = repo();
    addRemote(dir, 'origin', 'https://github.com/sjarmak/mem.git');
    addRemote(dir, 'upstream', 'git@github.com:gastownhall/gascity.git');
    expect(readRemotes(dir)).toEqual({
      origin: 'https://github.com/sjarmak/mem.git',
      upstream: 'git@github.com:gastownhall/gascity.git',
    });
  });

  it('resolves a remote through an insteadOf rewrite, matching `git remote get-url`', () => {
    const dir = repo();
    // A shorthand alias: typing `gh:sjarmak/mem` resolves to the real GitHub
    // SSH URL. The raw `remote.origin.url` config value is the literal
    // `gh:sjarmak/mem` the user typed -- not a github.com URL at all -- so any
    // reader that skips git's own resolution (e.g. parsing raw config) sees an
    // unparseable, non-github value instead of the real remote.
    execFileSync('git', ['-C', dir, 'config', 'url.git@github.com:.insteadOf', 'gh:']);
    addRemote(dir, 'origin', 'gh:sjarmak/mem');

    const resolved = execFileSync('git', ['-C', dir, 'remote', 'get-url', 'origin'], {
      encoding: 'utf8',
    }).trim();
    expect(resolved).toBe('git@github.com:sjarmak/mem');

    expect(readRemotes(dir)).toEqual({ origin: resolved });
  });
});
