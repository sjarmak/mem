// verify/git — shared git IO for the verify/ scripts (mem-j1r2w).
//
// gitOut + readRemotes were a third copy across verify-rig-checkouts.mjs,
// measure-live-ref.mjs, and measure-false-close.mjs: each shelled `git remote`
// then `git remote get-url` once per remote to build the same name→url map.
// NOT verify/lib.mjs — its header declares that module IO-free on purpose (it
// unit-tests without a repo), and this one shells git.

import { execFileSync } from 'node:child_process';

/** Run `git -C <dir> <args>`, returning trimmed stdout, or null if the command
 * failed for ANY reason — a non-zero exit (missing ref, unknown revision,
 * work_dir gone) but equally a missing `git` binary, a signal kill, or a
 * `maxBuffer` overrun.
 *
 * The catch is deliberately total, and narrowing it to non-zero exits is a
 * regression (mem-j1r2w reject #2), not a hardening. Every consumer sweeps all
 * of RIG_REPOS with an unguarded loop body, and each is built around null
 * meaning "this rig has no answer": verify-rig-checkouts.mjs degrades to
 * `exists: false` and fails closed in its per-rig verdict table; measure-false-
 * close.mjs and measure-live-ref.mjs record a named entry in `skipped_rigs`.
 * A throw escapes the loop, so one rig's transient fault (a signal kill, a
 * maxBuffer overrun) takes every other rig's result down with it — a rethrow
 * trades a reported skip for strictly worse telemetry, the opposite of
 * surfacing the fault.
 *
 * The cost of the total catch, stated plainly: it cannot tell "this rig has no
 * answer" from "git is broken everywhere", so a missing binary degrades every
 * rig alike rather than being named. That is the pre-dedup behaviour this
 * restores, not a new hazard, and it is tracked separately (mem-hycs9) — the
 * fix belongs in a preflight that checks git ONCE, not in per-call error
 * shapes, which is what the narrowing attempted and got wrong twice.
 *
 * `maxBuffer` is sized for the largest caller, not the typical one: measure-
 * false-close.mjs's `for-each-ref --format=%(refname) refs/heads refs/remotes`
 * can list thousands of lines on a many-remote checkout (gascity alone carries
 * 17), not just the single sha/url most callers here read. */
export function gitOut(dir, args) {
  try {
    return execFileSync('git', ['-C', dir, ...args], {
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return null;
  }
}

/** Run `git -C <dir> <args>`, throwing (with stderr captured) on a non-zero
 * exit — for callers that already know the command should succeed and want a
 * real fault surfaced with diagnostics, not `gitOut`'s soft null. Used by
 * verify-rig-checkouts.mjs's `gitDir`/`commonDir` reads, which only run after
 * `gitOut`'s own `rev-parse --git-dir` check has confirmed a real repo. */
export function git(dir, args) {
  return execFileSync('git', ['-C', dir, ...args], {
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
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
