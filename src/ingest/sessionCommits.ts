import { readFileSync } from 'node:fs';

import { defaultGitRunner, isNonZeroExit } from './provenance.js';
import type { GitRunner } from './provenance.js';
import {
  SessionCommitsSchema,
  type SessionCommits,
  type WorkRecord,
} from '../schemas/workrecord.js';

/**
 * ingest/sessionCommits (mem-75t.15) — recover each session's OWN local commit SHAs
 * from its trace, so the contested-window prize gets a TRUE per-worktree replay base
 * that survives the upstream squash-merge.
 *
 * mem-75t.12 proved post-hoc attribution dead (account/commit_sha NULL; the landed
 * commits are squash-merged into upstream gas-city under a single bot identity), and
 * mem-apg.10 proved `base=parent(landed_commit)` dead (97% squash wall). The one signal
 * that DOES survive is in the trace itself: git prints `[<branch> <sha>] <subject>` only
 * when it actually creates a commit, so that line is a reliable record of a commit the
 * session made locally — even though the corpus commits through a wrapper, so the
 * `git commit` COMMAND never appears in the trace.
 *
 * The session's TRUE base is then `parent(firstLocalCommit)` resolved against the rig
 * clone — independent of where the squash later landed on upstream main. When the first
 * local commit no longer exists in the clone (squashed/rebased away) the base is left
 * unresolved, never invented.
 *
 * ZFC: a deterministic regex over git's own output + a single `rev-parse`. No semantic
 * judgment, no model.
 */

/** Git's commit-success line: `[<branch> <sha>] <subject>`, with `<sha>` a 7–40 hex
 * abbreviation. `(root-commit)` is the first-commit form. The global flag drives
 * `matchAll`; the SHA is capture group 1. Anchored on the `[` + branch + space so a
 * bare bracketed hex elsewhere in the transcript cannot masquerade as a commit.
 *
 * The branch token is either a single ref token (`[\w./-]+`) or git's literal
 * `detached HEAD` heading — the one multi-word commit heading git emits, printed
 * when a session commits from a detached HEAD (the default for a replay worktree).
 * Reading it is the same deterministic over-git's-own-output parse, not a guess
 * (mem-75t.19): without it a worktree that committed detached looks like a session
 * that made no local commit at all. */
const COMMIT_LINE_RE = /\[(?:[\w./-]+|detached HEAD) (?:\(root-commit\) )?([0-9a-f]{7,40})\]/g;

/**
 * The session's local commit SHAs, in trace order (first local commit first), parsed
 * from git's `[branch sha]` success outputs. Empty when the session made no local
 * commit (no such line) — a session that only edited or whose commits went through a
 * path that prints no SHA is reported as zero, never imputed.
 *
 * The argument is the raw transcript text (the JSONL file content): git's success line
 * survives JSON-string escaping verbatim, so the same regex that matched a plain trace
 * matches it embedded in a `tool_result` / `toolUseResult.stdout` value.
 */
export function parseSessionCommits(traceText: string): string[] {
  const shas: string[] = [];
  for (const match of traceText.matchAll(COMMIT_LINE_RE)) shas.push(match[1]);
  return shas;
}

/** The parent of `commit` as a full 40-hex SHA, or null when the commit is not in the
 * clone (squashed/rebased away → `rev-parse` exits non-zero) so there is no parent to
 * resolve. `--end-of-options` pins the revision so a hostile value cannot inject a git
 * flag. A non-zero exit is the absent case; any other failure (missing git) propagates. */
function parentOf(run: GitRunner, clone: string, commit: string): string | null {
  let stdout: string;
  try {
    stdout = run(clone, ['rev-parse', '--verify', '-q', '--end-of-options', `${commit}^`]);
  } catch (err) {
    if (isNonZeroExit(err)) return null;
    throw err;
  }
  const sha = stdout.trim();
  return sha === '' ? null : sha;
}

/**
 * Build a validated {@link SessionCommits} from parsed local-commit SHAs and the rig
 * clone, or null when the session made no local commit. `true_base = parent(first)` is
 * set only when the first commit still exists in the clone (`base_state: 'resolved'`);
 * when it was squashed/rebased away the base is left absent (`base_state:
 * 'commit-absent'`) — the SHAs are still recorded, the base is never guessed.
 */
export function deriveSessionCommits(
  commits: string[],
  clone: string,
  run: GitRunner
): SessionCommits | null {
  if (commits.length === 0) return null;
  const first = commits[0];
  const trueBase = parentOf(run, clone, first);
  if (trueBase === null) {
    return SessionCommitsSchema.parse({
      commits,
      first_commit: first,
      base_state: 'commit-absent',
    });
  }
  return SessionCommitsSchema.parse({
    commits,
    first_commit: first,
    true_base: trueBase,
    base_state: 'resolved',
  });
}

/** Reads a transcript file as a single UTF-8 string. Injectable so the attach stage is
 * testable without touching disk; the parser scans the raw JSONL text directly. */
export type TraceTextReader = (path: string) => string;

const defaultTextReader: TraceTextReader = path => readFileSync(path, 'utf8');

/** Options for {@link attachSessionCommits} — the git runner and the transcript reader
 * are injectable so the stage can be exercised with synthetic clones and traces. */
export interface AttachSessionCommitsOptions {
  run?: GitRunner;
  read?: TraceTextReader;
}

/**
 * Ingest stage: for each record that has both a resolved transcript path
 * (`trace.jsonl_path`, set by the trace stage under `--with-traces`) and a rig clone
 * (`provenance.work_dir`, set under `--with-provenance`), parse the session's local
 * commit SHAs from the transcript text and resolve its TRUE per-worktree base against
 * the clone. Records are copied, never mutated; a record with no transcript, no clone,
 * or no local commit is returned unchanged (no `session_commits`).
 *
 * A transcript that cannot be read (reaped, moved) is skipped silently — the same
 * tolerance the parse stage applies to a missing JSONL; the field simply stays absent.
 */
export function attachSessionCommits(
  records: WorkRecord[],
  opts: AttachSessionCommitsOptions = {}
): WorkRecord[] {
  const run = opts.run ?? defaultGitRunner;
  const read = opts.read ?? defaultTextReader;
  return records.map(record => {
    const path = record.trace?.jsonl_path;
    const clone = record.provenance?.work_dir;
    if (path === undefined || clone === undefined) return record;
    let text: string;
    try {
      text = read(path);
    } catch (err) {
      if (isFileNotFound(err)) return record;
      throw err;
    }
    const session = deriveSessionCommits(parseSessionCommits(text), clone, run);
    return session === null ? record : { ...record, session_commits: session };
  });
}

/** ENOENT — a reaped/moved transcript is absence, not corruption; anything else (e.g. a
 * permissions error) propagates rather than silently dropping the record's signal. */
function isFileNotFound(err: unknown): boolean {
  return (err as { code?: string } | null)?.code === 'ENOENT';
}
