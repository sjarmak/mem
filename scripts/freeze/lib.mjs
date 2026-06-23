// freeze/lib — pure, IO-free logic for the Day-0 perishable freeze (mem-wanz.1).
//
// The freeze captures perishable git provenance (decaying bd-*/gc-* session refs,
// detached polecat worktree HEADs, and dashboard PR→merge-SHA→CI) before gc
// reclaims it. Everything here is a deterministic function over already-gathered
// data so it can be unit-tested without touching a repo, the network, or the
// clock. All IO lives in scripts/day0-freeze.mjs.
//
// Design rule (the architect's fail-closed mandate): nothing here ever *coerces*
// an ambiguous CI result into pass/fail. Unknown stays UNKNOWN, with a machine
// reason — masking a missing signal as "pass" would silently admit unscored work
// into the downstream oracle.

/**
 * Conservative per-store branch-count floors (the architect's expected-floor
 * table). These are empty/truncated-bundle tripwires, NOT exact expectations:
 * `git bundle verify` confirms a bundle is well-formed but cannot tell a bundle
 * that legitimately carries N branches from one that carries zero. A floor below
 * the current live count but far above zero catches the catastrophic case (a
 * store whose session branches were gc'd out from under the freeze) while
 * tolerating normal branch churn. Keyed by short store name.
 */
export const EXPECTED_FLOORS = Object.freeze({
  gascity: 83,
  packs: 50,
  scix: 19,
  zelda: 1,
});

/**
 * Rig → floor-table key. Only rigs whose object store is expected to hold
 * session branches appear here; a rig absent from this map is still indexed and
 * bundled if it carries bd-/gc- refs, but is not floor-gated (we have no
 * architect-blessed minimum for it).
 */
export const RIG_FLOOR_KEYS = Object.freeze({
  gascity: 'gascity',
  gpk: 'packs',
  scix_experiments: 'scix',
  zeldascension: 'zelda',
});

// GitHub check-run conclusion buckets. A run whose conclusion is null/undefined
// has not completed — it is neither, and must not be folded into either bucket.
const FAILURE_CONCLUSIONS = new Set([
  'failure',
  'timed_out',
  'cancelled',
  'action_required',
  'startup_failure',
  'stale',
]);
const SUCCESS_CONCLUSIONS = new Set(['success', 'neutral', 'skipped']);

/**
 * Roll up a list of check-run conclusions into one verdict, fail-closed.
 *
 * Precedence: an incomplete run (null conclusion) or an unrecognized conclusion
 * string makes the whole row UNKNOWN — we cannot assert the merge was green if
 * any signal is missing or unparseable. A single recognized failure makes it a
 * failure. Only an all-recognized-success set yields success.
 *
 * @param {Array<string|null>} conclusions
 * @returns {{conclusion: 'success'|'failure'|'UNKNOWN', reason: string}}
 */
export function aggregateConclusion(conclusions) {
  if (!Array.isArray(conclusions) || conclusions.length === 0) {
    return { conclusion: 'UNKNOWN', reason: 'no-check-runs' };
  }
  if (conclusions.some(c => c === null || c === undefined)) {
    return { conclusion: 'UNKNOWN', reason: 'incomplete-run' };
  }
  const unrecognized = conclusions.find(
    c => !FAILURE_CONCLUSIONS.has(c) && !SUCCESS_CONCLUSIONS.has(c)
  );
  if (unrecognized !== undefined) {
    return { conclusion: 'UNKNOWN', reason: `unrecognized-conclusion:${unrecognized}` };
  }
  if (conclusions.some(c => FAILURE_CONCLUSIONS.has(c))) {
    return { conclusion: 'failure', reason: 'check-run-failure' };
  }
  return { conclusion: 'success', reason: 'all-checks-passed' };
}

/**
 * Classify one merged-PR row into a CI outcome record. Drives off
 * `mergeCommit.oid → check-runs` (NOT statusCheckRollup, which GitHub leaves
 * empty once a squash-merged head ref is deleted) and records head_ref_deleted
 * alongside every UNKNOWN-with-reason.
 *
 * @param {{number:number, mergeCommit?:{oid:string}|null, headRefName?:string,
 *          headRefDeleted?:boolean, checkRuns?:Array<{conclusion:string|null}>|null}} pr
 * @returns {{pr:number, merge_oid:string|null, head_ref:string|null,
 *            head_ref_deleted:boolean, ci_conclusion:'success'|'failure'|'UNKNOWN',
 *            reason:string}}
 */
export function classifyCiRow(pr) {
  const head_ref = pr.headRefName ?? null;
  const head_ref_deleted = pr.headRefDeleted === true;
  const merge_oid = pr.mergeCommit?.oid ?? null;
  const base = { pr: pr.number, merge_oid, head_ref, head_ref_deleted };

  if (!merge_oid) {
    return { ...base, ci_conclusion: 'UNKNOWN', reason: 'no-merge-commit' };
  }
  // A PR that merged but for which we never fetched check-runs is UNKNOWN with a
  // distinct reason — distinguishable from a merge that genuinely had no checks.
  if (pr.checkRuns === undefined || pr.checkRuns === null) {
    return { ...base, ci_conclusion: 'UNKNOWN', reason: 'check-runs-not-fetched' };
  }
  const agg = aggregateConclusion(pr.checkRuns.map(r => r.conclusion ?? null));
  return { ...base, ci_conclusion: agg.conclusion, reason: agg.reason };
}

/**
 * Tally a list of classified CI rows by conclusion. Pure aggregation for the
 * manifest summary.
 * @param {Array<{ci_conclusion:string}>} rows
 */
export function summarizeCi(rows) {
  const summary = { total: rows.length, success: 0, failure: 0, UNKNOWN: 0 };
  for (const r of rows) {
    if (r.ci_conclusion in summary) summary[r.ci_conclusion]++;
  }
  return summary;
}

/**
 * Named-ref bundle parity: a bundle built with `--branches --tags` should list
 * exactly heads+tags named refs. Detached SHAs are carried as objects but never
 * appear in `git bundle list-heads` (verified empirically), so they are checked
 * separately by {@link detachedRecovery}, not folded in here.
 *
 * @param {{listHeads:number, heads:number, tags:number}} counts
 */
export function bundleParity({ listHeads, heads, tags, collisions = 0 }) {
  // A name that exists as BOTH a branch and a tag is ambiguous, so `git bundle`
  // drops both copies from the bundle header rather than emit an ambiguous ref —
  // 2 refs lost per collision (verified against the gascity store). Model that
  // exactly so parity stays a hard equality, not a fuzzy tolerance that would let
  // real truncation slip through.
  const expected = heads + tags - 2 * collisions;
  return { ok: listHeads === expected, expected, actual: listHeads, heads, tags, collisions };
}

/**
 * Detached-HEAD recovery check: every detached worktree SHA passed to
 * `git bundle create` must be fetchable back out of the bundle (the gold-standard
 * proof the object is carried, not merely referenced). Set-based so a recovered
 * superset still passes and order is irrelevant.
 *
 * @param {string[]} passedShas  detached SHAs handed to bundle create
 * @param {string[]} recoveredShas  SHAs confirmed present after fetching the bundle
 */
export function detachedRecovery(passedShas, recoveredShas) {
  const recovered = new Set(recoveredShas);
  const missing = passedShas.filter(s => !recovered.has(s));
  return {
    ok: missing.length === 0,
    missing,
    total: passedShas.length,
    recovered: passedShas.length - missing.length,
  };
}

/**
 * Floor gate for a store. Stores without a floor key are reported applicable:false
 * and ok:true (indexed/bundled but not gated). A store at/above its floor passes;
 * below it fails and the orchestrator aborts naming the store.
 *
 * @param {string|null} floorKey
 * @param {number} branchCount  number of local heads (session branches) bundled
 * @param {Record<string,number>} [table]
 */
export function floorCheck(floorKey, branchCount, table = EXPECTED_FLOORS) {
  const floor = floorKey == null ? undefined : table[floorKey];
  if (floor === undefined) {
    return { applicable: false, ok: true, floorKey, floor: null, count: branchCount };
  }
  return { applicable: true, ok: branchCount >= floor, floorKey, floor, count: branchCount };
}

/**
 * Does this object store hold session work worth bundling? True if it is
 * floor-gated, or if any ref name carries a bd-/gc- session prefix (the decaying
 * refs the freeze exists to preserve).
 *
 * @param {{floorKey:string|null, refnames:string[]}} store
 */
export function isSessionStore({ floorKey, refnames }) {
  if (floorKey) return true;
  return refnames.some(r => /(^|\/)(bd-|gc-)/.test(r));
}

/**
 * Deduplicate rig checkouts that share one object store. Several rigs (and many
 * worktrees) can point at the same `.git` common-dir — bundling each separately
 * would duplicate gigabytes and double-count refs. Group by common-dir; a store's
 * representative carries the first floor key seen so the floor gate still fires.
 *
 * @param {Array<{rig:string, dir:string, commonDir:string, floorKey:string|null}>} entries
 * @returns {Array<{commonDir:string, dir:string, rigs:string[], floorKey:string|null}>}
 */
export function dedupeStores(entries) {
  const byStore = new Map();
  for (const e of entries) {
    let g = byStore.get(e.commonDir);
    if (!g) {
      g = { commonDir: e.commonDir, dir: e.dir, rigs: [], floorKey: null };
      byStore.set(e.commonDir, g);
    }
    g.rigs.push(e.rig);
    if (e.floorKey && !g.floorKey) {
      g.floorKey = e.floorKey;
      g.dir = e.dir; // anchor IO on the checkout that owns the floor expectation
    }
  }
  return [...byStore.values()];
}

/**
 * Parse `git for-each-ref --format='%(objectname) %(refname)'` output into
 * counts and the raw ref-name list. Pure string handling so the orchestrator can
 * feed captured stdout straight in.
 *
 * @param {string} stdout
 */
export function parseRefIndex(stdout) {
  const lines = stdout.split('\n').filter(l => l.trim() !== '');
  // Format is `<objectname> <refname> <creatordate>`; a ref name can hold no
  // spaces and iso-strict dates none either, so the second field is the refname.
  const refnames = lines.map(l => l.split(' ')[1]);
  const headNames = new Set();
  const tagNames = new Set();
  for (const r of refnames) {
    if (r.startsWith('refs/heads/')) headNames.add(r.slice('refs/heads/'.length));
    else if (r.startsWith('refs/tags/')) tagNames.add(r.slice('refs/tags/'.length));
  }
  let collisions = 0;
  for (const name of headNames) if (tagNames.has(name)) collisions++;
  return { total: refnames.length, heads: headNames.size, tags: tagNames.size, collisions, refnames };
}

/**
 * Extract the detached worktree HEAD SHAs from `git worktree list --porcelain`.
 * These are the polecat/ship worktrees parked on a bare commit with no branch —
 * invisible to refs/heads/* and unrecoverable after gc, so they must be named
 * explicitly to the bundle (the architect's C1).
 *
 * @param {string} porcelain
 * @returns {string[]} detached HEAD SHAs in worktree-list order
 */
export function parseDetachedHeads(porcelain) {
  const shas = [];
  let pendingHead = null;
  for (const line of porcelain.split('\n')) {
    if (line.startsWith('worktree ')) {
      pendingHead = null;
    } else if (line.startsWith('HEAD ')) {
      pendingHead = line.slice('HEAD '.length).trim();
    } else if (line.trim() === 'detached' && pendingHead) {
      shas.push(pendingHead);
      pendingHead = null;
    }
  }
  return shas;
}
