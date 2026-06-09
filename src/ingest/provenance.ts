import { execFileSync } from 'node:child_process';
import { basename, isAbsolute } from 'node:path';

import { ProvenanceSchema, type Provenance, type WorkRecord } from '../schemas/workrecord.js';

/**
 * ingest/provenance — git-provenance ingest, a sibling stage to ingest/outcomes
 * (P1.4). It reconstructs each record's *environment baseline*: the repo it ran
 * in and the commit it started from, so a WorkRecord can later be replayed as a
 * CodeScaleBench-style git-checkout environment.
 *
 * gc records the work dir (`gc.work_dir`, or the legacy flat `work_dir`) and
 * sometimes the base branch (`gc.var.base_branch`), but never the exact base
 * SHA. So the commit is derived by date — the newest commit on the base branch
 * at or before the session's start time. This is an APPROXIMATION, marked in the
 * record via `history_state`.
 *
 * The git→Provenance translation is a pure mapper (`provenanceInput`,
 * `deriveProvenance`) over an injectable `GitRunner`, so the stage is testable
 * without a checked-out repo — the same shape as `outcomes.ts`. ZFC: this is
 * mechanical IO + translation (git reports the commit, we map it), with no
 * semantic judgment; a missing base branch is left `unresolved`, never guessed.
 */

/** Metadata keys that carry the session's working directory, newest first.
 * Dotted keys are LITERAL flat keys on the decoded metadata object (see
 * `parseMetadata` in beads.ts), not a nested path. */
export const WORK_DIR_KEYS = ['gc.work_dir', 'work_dir'] as const;

/** Metadata key carrying the base branch the work was started against. */
export const BASE_BRANCH_KEY = 'gc.var.base_branch';

/** The metadata-derived inputs needed to reconstruct one record's git baseline.
 * `base_branch` is optional because only a minority of records record it; when
 * absent, the commit is left unresolved rather than guessed. */
export interface ProvenanceInput {
  work_dir: string;
  repo: string;
  base_branch?: string;
  started_at: string;
}

/** Read the first present, non-empty string value among `keys` from metadata.
 * Returns undefined when no key holds a usable string. */
function readMetaString(
  metadata: Record<string, unknown>,
  keys: readonly string[]
): string | undefined {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'string' && value.trim() !== '') return value;
  }
  return undefined;
}

/**
 * Derive the provenance inputs from a record, or null when it carries no usable
 * work_dir (the common case — work_dir is present on a minority of records).
 * `started_at` falls back to the record's creation time when the session start
 * was not recorded; the derived commit is an approximation either way.
 */
export function provenanceInput(record: WorkRecord): ProvenanceInput | null {
  const work_dir = readMetaString(record.metadata, WORK_DIR_KEYS);
  // gc.work_dir is by contract an absolute path. A relative (or otherwise
  // non-absolute) value is bad metadata, not a usable repo root — reject it
  // rather than letting `git -C <value>` resolve it against the scan cwd.
  if (work_dir === undefined || !isAbsolute(work_dir)) return null;

  const base_branch = readMetaString(record.metadata, [BASE_BRANCH_KEY]);
  const started_at = record.lifecycle.started ?? record.lifecycle.created;

  return {
    work_dir,
    repo: basename(work_dir),
    ...(base_branch !== undefined && { base_branch }),
    started_at,
  };
}

/** Matches a trailing numeric UTC offset: `+0000`, `+00:00`, `-04:00`. A
 * trailing `Z` is handled separately in {@link toGitUtc} before this is tested. */
const TZ_SUFFIX_RE = /[+-]\d{2}:?\d{2}$/;

/** A leading `YYYY-MM-DD[T ]HH:MM` — the dolt timestamp shapes (`created_at` is
 * NOT NULL and `started_at` is a DATETIME column, so both conform). */
const DATETIME_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

/**
 * Normalize a timestamp to an explicit UTC offset for `git rev-list --before=`.
 * git's approxidate parser reads a timezone-less timestamp (the dolt
 * `started_at` form, e.g. `2026-06-07 02:19:05`) in the host's *local* time,
 * which would make the resolved commit host-dependent. Appending an explicit
 * `+0000` (and rewriting a trailing `Z`, which approxidate does not honor as a
 * zone) pins the cutoff to UTC — the city convention — so the result is the same
 * on every host. Timestamps that already carry an explicit offset are untouched.
 *
 * A value that is not a recognizable datetime throws: it would otherwise reach
 * approxidate as a free-form string with unpredictable results, and given the
 * dolt columns are well-typed it can only mean upstream data corruption.
 */
export function toGitUtc(started_at: string): string {
  if (!DATETIME_RE.test(started_at)) {
    throw new Error(`started_at is not a recognizable datetime: ${JSON.stringify(started_at)}`);
  }
  if (started_at.endsWith('Z')) return `${started_at.slice(0, -1)} +0000`;
  if (TZ_SUFFIX_RE.test(started_at)) return started_at;
  return `${started_at} +0000`;
}

/** Runs `git -C <workDir> <args...>` and returns stdout. Injected so the
 * mapping is testable without a checked-out repo. */
export type GitRunner = (workDir: string, args: string[]) => string;

/** Default runner: invokes the real `git` CLI. */
export const defaultGitRunner: GitRunner = (workDir, args) =>
  execFileSync('git', ['-C', workDir, ...args], {
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  });

/** True when `execFileSync` failed because git exited non-zero (as opposed to
 * the binary being missing). A non-zero exit — work_dir gone, unknown branch —
 * is an expected "unresolved" outcome; a missing `git` binary is not. */
function isNonZeroExit(err: unknown): boolean {
  return typeof (err as { status?: unknown }).status === 'number';
}

/**
 * Resolve the session-start commit by date: the newest commit on `base_branch`
 * at or before `started_at`. Returns null when the branch exists but has no
 * commit before the cutoff (zero exit, empty stdout), or when git exits non-zero
 * (work_dir is not a reachable repo, or the branch is unknown). A missing `git`
 * binary (or any non-exit failure) propagates — that is a misconfiguration, not
 * an unresolved session, and must not be silently swallowed.
 *
 * `base_branch` is DB-sourced, so `--end-of-options` precedes it: git then
 * treats the value strictly as a revision, never as an option. Without it a
 * value like `--output=<path>` or `--all` would be parsed as a git flag
 * (argument injection — git would create files or return a wrong commit); with
 * it, such a value is an unknown revision that exits non-zero → `unresolved`.
 */
export function resolveSessionCommit(
  input: ProvenanceInput & { base_branch: string },
  run: GitRunner
): string | null {
  let stdout: string;
  try {
    stdout = run(input.work_dir, [
      'rev-list',
      '-1',
      `--before=${toGitUtc(input.started_at)}`,
      '--end-of-options',
      input.base_branch,
    ]);
  } catch (err) {
    if (isNonZeroExit(err)) return null;
    throw err;
  }
  const sha = stdout.trim();
  return sha === '' ? null : sha;
}

/**
 * Map provenance inputs onto a validated {@link Provenance}. A commit is
 * resolved ONLY when a base branch was recorded: resolving against the
 * work_dir's HEAD would walk the agent's own feature branch (whose history may
 * contain the solution) — a train/test leak for the checkout environment — so an
 * absent base branch is terminal `unresolved`, never guessed.
 */
export function deriveProvenance(input: ProvenanceInput, run: GitRunner): Provenance {
  const commit =
    input.base_branch !== undefined
      ? resolveSessionCommit({ ...input, base_branch: input.base_branch }, run)
      : null;

  return ProvenanceSchema.parse({
    work_dir: input.work_dir,
    repo: input.repo,
    ...(input.base_branch !== undefined && { base_branch: input.base_branch }),
    ...(commit !== null && { base_commit: commit }),
    history_state: commit !== null ? 'commit-by-date' : 'unresolved',
  });
}

/** Options for {@link attachProvenance}. */
export interface AttachProvenanceOptions {
  /** work_dir + args → stdout runner. Defaults to {@link defaultGitRunner}. */
  run?: GitRunner;
}

/**
 * Attach git provenance to every record that carries a work_dir; records
 * without one pass through unchanged. Records are copied, never mutated.
 */
export function attachProvenance(
  records: WorkRecord[],
  opts: AttachProvenanceOptions = {}
): WorkRecord[] {
  const run = opts.run ?? defaultGitRunner;
  return records.map(record => {
    const input = provenanceInput(record);
    if (input === null) return record;
    return { ...record, provenance: deriveProvenance(input, run) };
  });
}
