// verify/git — shared git IO for the verify/ scripts (mem-j1r2w).
//
// gitOut + readRemotes were a third copy across verify-rig-checkouts.mjs,
// measure-live-ref.mjs, and measure-false-close.mjs: each shelled `git remote`
// then `git remote get-url` once per remote to build the same name→url map.
// NOT verify/lib.mjs — its header declares that module IO-free on purpose (it
// unit-tests without a repo), and this one shells git.

import { execFileSync } from 'node:child_process';

/** True when `err` is `execFileSync` failing because git exited non-zero (a
 * missing ref, an unknown revision, work_dir not a repo, no matching config
 * key) — as opposed to the `git` binary itself being missing or unreadable.
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

/** The full `name -> url` remote map of a checkout, read via
 * `git config -z --get-regexp '^remote\..*\.url$'` rather than `git remote`
 * plus one `git remote get-url` per remote — the N+1 shelling every prior
 * copy paid (gascity alone carries 17 remotes, so 18 processes shrink to 1).
 *
 * NOT `git remote -v` parsed line-by-line: a remote's url is free-form config
 * text and can itself contain an embedded newline (`git config` accepts and
 * stores one verbatim), which `remote -v` then prints raw — splitting that
 * one remote's fetch/push lines into extra lines that a per-line regex can
 * misattribute to a different, forged remote name. Verified: a single
 * crafted remote value can make line-based `remote -v` parsing drop the real
 * remote entirely and fabricate a bogus one in its place. `-z` NUL-delimits
 * each `key\nvalue` record instead, and a config value can never itself
 * contain a NUL, so there is no equivalent injection point. */
export function readRemotes(dir) {
  let out;
  try {
    out = execFileSync(
      'git',
      ['-C', dir, 'config', '-z', '--get-regexp', String.raw`^remote\..*\.url$`],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }
    );
  } catch (err) {
    if (isNonZeroExit(err)) return {};
    throw err;
  }
  const remotes = {};
  for (const entry of out.split('\0')) {
    if (entry === '') continue;
    const nl = entry.indexOf('\n');
    const key = nl === -1 ? entry : entry.slice(0, nl);
    const value = nl === -1 ? '' : entry.slice(nl + 1);
    const m = /^remote\.(.*)\.url$/.exec(key);
    if (m !== null) remotes[m[1]] = value;
  }
  return remotes;
}
