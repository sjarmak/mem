import { exitStatus, isNonZeroExit, toGitUtc } from './provenance.js';
import type { GitRunner } from './provenance.js';
import { defaultGitRunner } from './provenance.js';
import { LandedSchema, type Landed, type WorkRecord } from '../schemas/workrecord.js';

/**
 * ingest/landed — the forward mirror of ingest/provenance. Where provenance
 * reconstructs the commit a session STARTED from, this reconstructs what the
 * session LEFT on the integration branch: the work→landed-commit oracle for the
 * direct-to-main majority of the corpus, where no PR/CI workflow exists and the
 * verifiable question is "did this work land on the branch and survive" — a pure
 * git fact that needs no GitHub linkage.
 *
 * The window is `[started, closed]`. Its start anchor is `provenance.base_commit`
 * (the branch tip dated at session start); its end anchor is the branch tip dated
 * at session close. Commits in `base..end` are the session's contribution.
 *
 * Attribution is by time window, which is unambiguous ONLY when one session held
 * the branch over the interval. When two sessions on the same checkout+branch
 * overlap in time, their commits cannot be told apart by time alone, so the
 * record is marked `ambiguous-window` and left for author/SHA attribution —
 * never guessed. ZFC: git reports the commits and ancestry; we map them onto the
 * Landed schema with no semantic judgment.
 */

/** The inputs needed to reconstruct one record's landed outcome. All are present
 * only when provenance resolved a `base_commit` against a known `base_branch` and
 * the bead recorded a close time. */
export interface LandedInput {
  work_dir: string;
  base_branch: string;
  base_commit: string;
  /** Session start — the window's lower bound, for overlap detection. */
  started_at: string;
  /** Session close — the timestamp the end anchor is dated against. */
  ended_at: string;
}

/** A leading `YYYY-MM-DD[T ]HH:MM` — the recognizable dolt timestamp shapes. A
 * value outside this shape cannot be dated against git, so the record is not a
 * landed candidate (rather than throwing mid-batch). */
const DATETIME_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

/**
 * Derive the landed inputs from a record, or null when it is not a candidate:
 * provenance must have resolved a `base_commit` on a known `base_branch`, and the
 * bead must carry a parseable close timestamp. `started_at` falls back to the
 * record's creation time, mirroring provenance.
 */
export function landedInput(record: WorkRecord): LandedInput | null {
  const prov = record.provenance;
  if (prov?.base_commit === undefined || prov.base_branch === undefined) return null;

  const ended_at = record.lifecycle.closed;
  if (ended_at === undefined || !DATETIME_RE.test(ended_at)) return null;

  const started_at = record.lifecycle.started ?? record.lifecycle.created;

  return {
    work_dir: prov.work_dir,
    base_branch: prov.base_branch,
    base_commit: prov.base_commit,
    started_at,
    ended_at,
  };
}

/** The newest commit on `branch` at or before `when`, or null when the branch has
 * none before the cutoff (empty stdout) or git exits non-zero (checkout gone /
 * unknown branch). Mirrors provenance's resolveSessionCommit; `--end-of-options`
 * pins the branch as a revision so a hostile value cannot inject a git flag. */
function resolveTipBefore(
  run: GitRunner,
  work_dir: string,
  branch: string,
  when: string
): string | null {
  let stdout: string;
  try {
    stdout = run(work_dir, [
      'rev-list',
      '-1',
      `--before=${toGitUtc(when)}`,
      '--end-of-options',
      branch,
    ]);
  } catch (err) {
    if (isNonZeroExit(err)) return null;
    throw err;
  }
  const sha = stdout.trim();
  return sha === '' ? null : sha;
}

/** The full 40-hex SHAs in `base..end` (commits reachable from `end` but not
 * `base`) — the session's landed commits, newest first. */
function rangeCommits(run: GitRunner, work_dir: string, base: string, end: string): string[] {
  const stdout = run(work_dir, ['rev-list', '--end-of-options', `${base}..${end}`]);
  return stdout
    .split('\n')
    .map(line => line.trim())
    .filter(line => line !== '');
}

/** True when `commit` is still an ancestor of `branch`'s current tip (the work
 * survives), false when it is not (history was rewritten — the work was dropped).
 * `merge-base --is-ancestor` exits 1 for "not an ancestor"; any other non-zero
 * exit (bad object, etc.) propagates as a real error. */
function survives(run: GitRunner, work_dir: string, commit: string, branch: string): boolean {
  try {
    run(work_dir, ['merge-base', '--is-ancestor', '--end-of-options', commit, branch]);
    return true;
  } catch (err) {
    if (exitStatus(err) === 1) return false;
    throw err;
  }
}

/** Reverted SHAs referenced by `This reverts commit <sha>` trailers on commits
 * that landed on `branch` AFTER `end`. Returns the set of full SHAs git named as
 * reverted; the caller intersects it with the window's commits. Empty when `end`
 * is already the branch tip (nothing landed after it). */
function revertedShas(run: GitRunner, work_dir: string, end: string, branch: string): Set<string> {
  const stdout = run(work_dir, [
    'log',
    '--grep=This reverts commit',
    '--format=%B',
    '--end-of-options',
    `${end}..${branch}`,
  ]);
  const shas = new Set<string>();
  const re = /This reverts commit ([0-9a-f]{40})/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(stdout)) !== null) shas.add(m[1]);
  return shas;
}

/**
 * Map landed inputs onto a validated {@link Landed} via git. Resolves the close
 * anchor, the window commits, and whether they survived / were reverted. Does NOT
 * detect overlap — that is cross-record and handled in {@link attachLanded},
 * which short-circuits an overlapping record before calling this.
 */
export function deriveLanded(input: LandedInput, run: GitRunner): Landed {
  const base_commit = input.base_commit;
  const end = resolveTipBefore(run, input.work_dir, input.base_branch, input.ended_at);
  if (end === null) {
    return LandedSchema.parse({ base_commit, landed_state: 'unresolved' });
  }
  if (end === base_commit) {
    return LandedSchema.parse({ base_commit, n_commits: 0, landed_state: 'empty-window' });
  }

  const commits = rangeCommits(run, input.work_dir, base_commit, end);
  if (commits.length === 0) {
    // `end` is at or behind `base` (no forward progress) — nothing landed.
    return LandedSchema.parse({ base_commit, n_commits: 0, landed_state: 'empty-window' });
  }

  const common = { base_commit, landed_commit: end, n_commits: commits.length };

  if (!survives(run, input.work_dir, end, input.base_branch)) {
    return LandedSchema.parse({ ...common, landed_state: 'abandoned' });
  }

  const reverted = revertedShas(run, input.work_dir, end, input.base_branch);
  const anyReverted = commits.some(sha => reverted.has(sha));
  return LandedSchema.parse({
    ...common,
    landed_state: anyReverted ? 'reverted' : 'landed',
  });
}

/** Two half-open intervals overlap when each starts before the other ends. */
function overlaps(a: { s: number; e: number }, b: { s: number; e: number }): boolean {
  return a.s < b.e && b.s < a.e;
}

/** Epoch ms for a timestamp, normalized to UTC for TZ-less values, or null when
 * unparseable. Used only for relative overlap comparison, so exactness past the
 * second is immaterial. */
function epoch(when: string): number | null {
  const iso = /[Z+]|[+-]\d{2}:?\d{2}$/.test(when) ? when : `${when.replace(' ', 'T')}Z`;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
}

/** Options for {@link attachLanded}. */
export interface AttachLandedOptions {
  /** work_dir + args → stdout runner. Defaults to provenance's defaultGitRunner. */
  run?: GitRunner;
}

/**
 * Attach a landed outcome to every candidate record (one whose provenance
 * resolved a `base_commit` and that recorded a close time); others pass through
 * unchanged. A record whose `[started, closed]` window overlaps another
 * candidate's on the SAME checkout+branch is marked `ambiguous-window` without a
 * git query — time alone cannot attribute its commits. Records are copied, never
 * mutated.
 */
export function attachLanded(records: WorkRecord[], opts: AttachLandedOptions = {}): WorkRecord[] {
  const run = opts.run ?? defaultGitRunner;

  // Index candidates and their windows, grouped by checkout+branch, so overlap is
  // a single pass rather than an O(n^2) scan across unrelated repos.
  const inputs = new Map<string, LandedInput>();
  const windows = new Map<string, { s: number; e: number }>();
  const groups = new Map<string, string[]>();
  for (const record of records) {
    const input = landedInput(record);
    if (input === null) continue;
    const s = epoch(input.started_at);
    const e = epoch(input.ended_at);
    inputs.set(record.work_id, input);
    if (s !== null && e !== null) {
      windows.set(record.work_id, { s, e: Math.max(e, s) });
      const key = `${input.work_dir} ${input.base_branch}`;
      (groups.get(key) ?? groups.set(key, []).get(key)!).push(record.work_id);
    }
  }

  const ambiguous = new Set<string>();
  for (const ids of groups.values()) {
    for (let i = 0; i < ids.length; i++) {
      const wi = windows.get(ids[i])!;
      for (let j = i + 1; j < ids.length; j++) {
        if (overlaps(wi, windows.get(ids[j])!)) {
          ambiguous.add(ids[i]);
          ambiguous.add(ids[j]);
        }
      }
    }
  }

  return records.map(record => {
    const input = inputs.get(record.work_id);
    if (input === undefined) return record;
    if (ambiguous.has(record.work_id)) {
      return {
        ...record,
        landed: LandedSchema.parse({
          base_commit: input.base_commit,
          landed_state: 'ambiguous-window',
        }),
      };
    }
    return { ...record, landed: deriveLanded(input, run) };
  });
}
