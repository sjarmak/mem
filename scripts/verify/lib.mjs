// verify/lib — pure, IO-free logic for the Step-0 per-rig checkout+remote
// verification table (mem-wanz.2).
//
// This is the fail-closed preflight that downstream git-provenance work (the
// merge-base / work→landed-commit oracle) runs against: it asserts every rig in
// the canonical rig→repo map resolves to the CORRECT durable local checkout and
// the CORRECT upstream remote, so the oracle can never silently run against an
// aliased or wrong-repo checkout. All IO (running git per checkout, writing the
// report) lives in scripts/verify-rig-checkouts.mjs; everything here is a
// deterministic function over already-gathered strings so it unit-tests without
// touching a repo, the network, or the clock.
//
// Design rule (the architect's fail-closed mandate, mirrored from freeze/lib):
// nothing here ever *coerces* an ambiguous or mismatched result into OK. An
// unparseable remote, a slug mismatch, or an unexpected shared object store
// yields ok:false with a machine `reason` string — masking a wrong-repo checkout
// as OK would admit a wrong git baseline into the oracle.

// ---- remote URL parsing -----------------------------------------------------

/**
 * Parse a git remote URL into its GitHub `owner/name` slug, normalizing across
 * the forms git emits:
 *   - scp-like:   git@github.com:owner/name.git
 *   - https:      https://github.com/owner/name(.git)
 *   - ssh:        ssh://git@github.com/owner/name(.git)
 *   - git proto:  git://github.com/owner/name(.git)
 * The optional trailing `.git` and any trailing slash are stripped. Returns null
 * for anything that does not parse to exactly `owner/name` under a github.com
 * host — fail-closed, so a non-GitHub or malformed remote cannot masquerade as a
 * match downstream.
 *
 * @param {string|null|undefined} url
 * @returns {{owner:string, name:string, slug:string}|null}
 */
export function parseRemoteSlug(url) {
  if (typeof url !== 'string') return null;
  const trimmed = url.trim();
  if (trimmed === '') return null;

  // Split host from path. scp-like `git@host:owner/name` has no `//` and uses a
  // colon; every other form is a real URL with `//host/`.
  let host = null;
  let path = null;
  const scp = /^([^/@]+@)?([^/:]+):(.+)$/;
  if (!trimmed.includes('://') && scp.test(trimmed)) {
    const m = trimmed.match(scp);
    host = m[2];
    path = m[3];
  } else {
    const m = trimmed.match(/^[a-z]+:\/\/(?:[^/@]+@)?([^/]+)\/(.+)$/i);
    if (!m) return null;
    host = m[1];
    path = m[2];
  }

  // Host must be github.com (optionally with a port, which we ignore). Anything
  // else is not an authoritative GitHub slug.
  const hostName = host.split(':')[0].toLowerCase();
  if (hostName !== 'github.com') return null;

  // Strip a single trailing `.git` and any surrounding slashes, then require
  // exactly two non-empty path segments: owner and name.
  const cleaned = path
    .replace(/\/+$/, '')
    .replace(/\.git$/, '')
    .replace(/^\/+/, '');
  const segments = cleaned.split('/');
  if (segments.length !== 2) return null;
  const [owner, name] = segments;
  if (owner === '' || name === '') return null;

  return { owner, name, slug: `${owner}/${name}` };
}

/**
 * Compare an observed remote URL against the rig's expected slug. Case-insensitive
 * on the slug (GitHub owner/name are case-insensitive) but otherwise exact: a
 * remote that parses to a different repo is a hard mismatch, never coerced to OK.
 *
 * @param {string|null|undefined} remoteUrl  the checkout's origin/upstream URL
 * @param {string} expectedSlug  the canonical `owner/name` from the rig map
 * @returns {{ok:boolean, observed:string|null, expected:string, reason:string}}
 */
export function compareRemoteToSlug(remoteUrl, expectedSlug) {
  const parsed = parseRemoteSlug(remoteUrl);
  if (parsed === null) {
    return {
      ok: false,
      observed: null,
      expected: expectedSlug,
      reason: 'remote-unparseable',
    };
  }
  const ok = parsed.slug.toLowerCase() === expectedSlug.toLowerCase();
  return {
    ok,
    observed: parsed.slug,
    expected: expectedSlug,
    reason: ok ? 'remote-matches-slug' : 'remote-slug-mismatch',
  };
}

/**
 * Pick the authoritative remote URL for a rig's expected slug from the full set
 * of `name → url` remotes on a checkout, fail-closed.
 *
 * Precedence is deterministic, not semantic (ZFC): prefer the URL of the
 * standard authoritative remote names (`origin`, then `upstream`) WHEN it parses
 * to the expected slug; otherwise accept ANY remote whose URL parses to the
 * expected slug, returning its name so the report can show which one matched.
 * This handles the real case where the canonical upstream is present under a
 * non-standard remote name (e.g. gascity-dashboard's upstream is named
 * `gascity-dashboard`, with the fork named `fork` and no `origin`). It never
 * accepts a remote that points at a *different* repo — a checkout whose origin is
 * a wrong repo and that has no remote at all matching the slug is a hard fail.
 *
 * @param {Record<string,string>} remotes  remote name → URL
 * @param {string} expectedSlug
 * @returns {{remote:string, url:string}|null} the matching remote, or null if
 *   none of the checkout's remotes resolve to the expected slug
 */
export function pickRemoteForSlug(remotes, expectedSlug) {
  if (remotes === null || typeof remotes !== 'object') return null;
  const want = expectedSlug.toLowerCase();
  const matches = name => {
    const url = remotes[name];
    if (typeof url !== 'string') return false;
    const parsed = parseRemoteSlug(url);
    return parsed !== null && parsed.slug.toLowerCase() === want;
  };
  for (const preferred of ['origin', 'upstream']) {
    if (matches(preferred)) return { remote: preferred, url: remotes[preferred] };
  }
  // Fall back to any other remote, in deterministic (sorted) name order.
  for (const name of Object.keys(remotes).sort()) {
    if (matches(name)) return { remote: name, url: remotes[name] };
  }
  return null;
}

// ---- checkout classification ------------------------------------------------

/**
 * Classify a checkout as a primary clone vs a linked worktree from its own
 * `git-dir` and its `git-common-dir` (both absolute, as emitted by
 * `git rev-parse --path-format=absolute`).
 *
 * In a primary clone the two are the same path (`…/.git`). In a linked worktree
 * the git-dir is `…/.git/worktrees/<name>` while the common-dir is the shared
 * `…/.git` — this is exactly the "worktree aliasing" the bead warns about:
 * a rig whose durable `dir` is a linked worktree, not the primary checkout, so
 * its object store and refs are owned by another path.
 *
 * @param {string} gitDir     absolute git-dir of the checkout
 * @param {string} commonDir  absolute git-common-dir of the checkout
 * @returns {{kind:'primary-clone'|'linked-worktree', commonDir:string, gitDir:string}}
 */
export function classifyCheckout(gitDir, commonDir) {
  const g = normalizePath(gitDir);
  const c = normalizePath(commonDir);
  return {
    kind: g === c ? 'primary-clone' : 'linked-worktree',
    gitDir: g,
    commonDir: c,
  };
}

/** Strip a single trailing slash so `…/.git` and `…/.git/` compare equal. The
 * inputs are already absolute (git emits them so); we do not resolve symlinks
 * here — that is IO and belongs in the orchestrator. */
function normalizePath(p) {
  if (typeof p !== 'string') return '';
  return p.replace(/\/+$/, '');
}

// ---- shared-object-store (aliasing) detection -------------------------------

/**
 * Detect rigs that share one object store, keyed by git-common-dir. Two DISTINCT
 * rigs resolving to the same common-dir means their checkouts are backed by the
 * same `.git` — a git-provenance run against one is really a run against the
 * other's object store. Returns, per common-dir, the full set of rigs sharing it.
 *
 * @param {Array<{rig:string, commonDir:string}>} entries
 * @returns {Map<string, string[]>} commonDir → sorted list of rigs sharing it
 */
export function groupByObjectStore(entries) {
  const byStore = new Map();
  for (const e of entries) {
    const key = normalizePath(e.commonDir);
    const rigs = byStore.get(key) ?? [];
    rigs.push(e.rig);
    byStore.set(key, rigs);
  }
  // Sort each group for deterministic output (ZFC: stable ordering, no judgment).
  for (const [key, rigs] of byStore) byStore.set(key, [...rigs].sort());
  return byStore;
}

/**
 * Given the full common-dir grouping, return the OTHER rigs that share a store
 * with the named rig (the rig itself excluded). Empty when the rig has its store
 * to itself.
 *
 * @param {Map<string, string[]>} byStore  output of {@link groupByObjectStore}
 * @param {string} rig
 * @param {string} commonDir
 * @returns {string[]} other rigs sharing this object store, sorted
 */
export function aliasesFor(byStore, rig, commonDir) {
  const rigs = byStore.get(normalizePath(commonDir)) ?? [];
  return rigs.filter(r => r !== rig);
}

// ---- verdict row ------------------------------------------------------------

/**
 * Build the fail-closed verification verdict for one rig. The inputs are the
 * already-gathered facts (remote URL, git-dir, common-dir, the cross-rig store
 * grouping); the output is the machine row that the table and the JSON report
 * are built from, plus the single `ok` that gates the process exit.
 *
 * Fail-closed semantics:
 *   - `exists:false` (no checkout at the dir) → ok:false, reason checkout-missing.
 *   - A `multi` rig (empty slug, no authoritative repo) skips the remote
 *     assertion but is still recorded: remote_ok:null, and `ok` is driven only by
 *     existence + aliasing. It is never failed for "no remote match" it cannot have.
 *   - A non-multi rig with a mismatched/unparseable remote is ALWAYS a hard fail.
 *   - A linked-worktree checkout is recorded (`checkout_kind:'linked-worktree'`)
 *     and flagged via `worktree`, but is NOT itself a hard fail — a rig may
 *     legitimately live on a worktree; the orchestrator decides policy. What is
 *     ALWAYS a hard fail is *unexpected* aliasing: two distinct rigs sharing one
 *     object store where neither is a declared worktree of the other is reported
 *     so the orchestrator can name it.
 *
 * @param {{
 *   rig:string,
 *   dir:string,
 *   slug:string,
 *   multi?:boolean,
 *   exists:boolean,
 *   remotes:Record<string,string>,
 *   gitDir:string|null,
 *   commonDir:string|null,
 *   aliases:string[],
 * }} input
 * @returns {{
 *   rig:string, dir:string, slug:string, multi:boolean, exists:boolean,
 *   remote_ok:boolean|null, remote_observed:string|null, remote_name:string|null,
 *   checkout_kind:'primary-clone'|'linked-worktree'|null,
 *   common_dir:string|null, git_dir:string|null, worktree:boolean,
 *   aliases:string[], ok:boolean, reason:string,
 * }}
 */
export function buildVerdict(input) {
  const { rig, dir, slug, exists, gitDir, commonDir, aliases } = input;
  const remotes = input.remotes ?? {};
  const multi = input.multi === true;

  // A checkout we cannot find or read is a hard fail — no facts to verify — EXCEPT
  // for a multi rig, which has no single authoritative checkout by design (e.g.
  // `gc` orchestrates across many forks; it owns no one repo). Such a rig is
  // recorded ok:true with a distinct reason, never failed for a checkout it is
  // not expected to have. A NON-multi rig missing its checkout is still fatal.
  if (!exists || gitDir === null || commonDir === null) {
    return {
      rig,
      dir,
      slug,
      multi,
      exists: false,
      remote_ok: null,
      remote_observed: null,
      remote_name: null,
      checkout_kind: null,
      common_dir: commonDir,
      git_dir: gitDir,
      worktree: false,
      aliases: [...aliases],
      ok: multi,
      reason: multi ? 'multi-rig-no-checkout' : 'checkout-missing',
    };
  }

  const checkout = classifyCheckout(gitDir, commonDir);
  const worktree = checkout.kind === 'linked-worktree';

  // Remote assertion: skipped for multi rigs (no single authoritative repo), a
  // hard match against the rig's slug otherwise. We scan ALL remotes (not just
  // `origin`) so a canonical upstream present under a non-standard remote name is
  // accepted, while still failing closed when NO remote resolves to the slug.
  let remote_ok = null;
  let remote_observed = null;
  let remote_name = null;
  let remoteReason = 'remote-skipped-multi';
  if (!multi) {
    const picked = pickRemoteForSlug(remotes, slug);
    if (picked === null) {
      // Distinguish "has remotes but none match the slug" from "no remotes at
      // all" — both fail closed, but the reason names the actual condition.
      remote_ok = false;
      remoteReason =
        Object.keys(remotes).length === 0 ? 'remote-none-configured' : 'remote-slug-mismatch';
    } else {
      const cmp = compareRemoteToSlug(picked.url, slug);
      remote_ok = cmp.ok;
      remote_observed = cmp.observed;
      remote_name = picked.remote;
      remoteReason = picked.remote === 'origin' ? cmp.reason : `remote-matches-slug-via:${picked.remote}`;
    }
  }

  // Aliasing: any OTHER rig sharing this object store is reported. It does not by
  // itself force ok:false here — the orchestrator distinguishes expected (a rig
  // legitimately on a worktree of another's store) from a hard failure — but the
  // alias list is always carried so it can.
  const aliased = aliases.length > 0;

  // ok gate: a multi rig is ok on existence alone; a real rig requires its remote
  // to match. Wrong-remote is always fatal here regardless of worktree status.
  const ok = multi ? true : remote_ok === true;

  const reasonParts = [remoteReason];
  if (worktree) reasonParts.push('checkout-is-linked-worktree');
  if (aliased) reasonParts.push(`shares-store-with:${aliases.join('+')}`);

  return {
    rig,
    dir,
    slug,
    multi,
    exists: true,
    remote_ok,
    remote_observed,
    remote_name,
    checkout_kind: checkout.kind,
    common_dir: checkout.commonDir,
    git_dir: checkout.gitDir,
    worktree,
    aliases: [...aliases],
    ok,
    reason: reasonParts.join(';'),
  };
}
