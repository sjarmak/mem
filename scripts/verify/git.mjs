// verify/git — shared git IO for the verify/ scripts (mem-j1r2w).
//
// gitOut + readRemotes were a third copy across verify-rig-checkouts.mjs,
// measure-live-ref.mjs, and measure-false-close.mjs: each shelled `git remote`
// then `git remote get-url` once per remote to build the same name→url map.
// NOT verify/lib.mjs — its header declares that module IO-free on purpose (it
// unit-tests without a repo), and this one shells git.

import { execFileSync } from 'node:child_process';

/** True when `err` is `execFileSync` failing because git exited non-zero (a
 * missing ref, an unknown revision, work_dir not a repo) — as opposed to the
 * `git` binary itself being missing or unreadable.
 * Mirrors src/ingest/provenance.ts's `isNonZeroExit`, kept as its own copy
 * here rather than imported from dist/: verify-rig-checkouts.mjs (a consumer
 * of this module) is a Step-0 preflight that must still run when the TS build
 * is broken or stale, so this shared module can't take a dist/ dependency even
 * though the other two consumers already have one. */
function isNonZeroExit(err) {
  return typeof err === 'object' && err !== null && typeof err.status === 'number';
}

/** Run `git -C <dir> <args>`, returning trimmed stdout or null on a non-zero
 * exit (missing ref, work_dir gone, unknown revision). A missing `git` binary
 * or any other non-exit failure propagates — every caller here only expects a
 * git question with a legitimate negative answer, never a misconfiguration. */
export function gitOut(dir, args) {
  try {
    return execFileSync('git', ['-C', dir, ...args], {
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch (err) {
    if (isNonZeroExit(err)) return null;
    throw err;
  }
}

/** The full `name -> url` remote map of a checkout: `git remote` lists the
 * names, then `git remote get-url <name>` resolves each one.
 *
 * Deliberately NOT a single `git config --get-regexp remote\..*\.url` read
 * (tried and reverted — mem-j1r2w reject #1): a remote's raw config value is
 * whatever the user typed, which `git remote get-url` resolves through
 * `url.<base>.insteadOf` rewriting before handing it back. Reading config
 * directly skips that resolution, so a checkout using an `insteadOf` shorthand
 * (e.g. `gh:owner/repo` aliased to `git@github.com:owner/repo`) reports the
 * unresolved alias — which may not even parse as a GitHub URL — instead of the
 * real remote `git` itself would use. `get-url` is the only surface that
 * reproduces git's own resolution, so the N+1 process cost (gascity alone
 * carries 17 remotes) is paid deliberately; see the bead notes for why this
 * was accepted as correctness over speed. */
export function readRemotes(dir) {
  const names = gitOut(dir, ['remote']);
  if (names === null) return {};
  const remotes = {};
  for (const name of names.split('\n').filter(n => n.trim() !== '')) {
    const url = gitOut(dir, ['remote', 'get-url', name]);
    if (url !== null) remotes[name] = url;
  }
  return remotes;
}
