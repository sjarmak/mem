import { readdirSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, join } from 'node:path';

import { readLines } from '../parse/trace-parse.js';

/**
 * Trace index (P1.3) — a catalog of every Claude Code session transcript on
 * disk. Transcripts live under `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`,
 * one file per session, named by the Claude session UUID (NOT the Gas City
 * session id). The index is the universe of available traces: it lets the
 * resolver validate a path and read cheap per-trace metadata (turn count, cwd)
 * without re-opening files.
 *
 * Pure IO — no judgment. The semantic parse of trace contents is P1.6.
 */

/** Cheap per-trace metadata, derived from the transcript on disk. */
export interface TraceIndexEntry {
  /** Canonical absolute path to the transcript JSONL. */
  jsonl_path: string;
  /** Filename stem — the Claude session UUID, == each entry's `sessionId`. */
  session_uuid: string;
  /** Encoded project directory name (the transcript's parent dir). */
  project_dir: string;
  /** Working directory the session ran in, from the first entry that records it. */
  cwd?: string;
  /** Git branch at session start, when the transcript records one. */
  git_branch?: string;
  /** Conversation turns: count of `user` + `assistant` entries. */
  n_turns: number;
  /** Transcript mtime in epoch ms — recency tie-breaker. */
  mtime_ms: number;
}

/** The default Claude transcripts root. Account homes symlink their
 * `projects/` here, so scanning this one canonical root avoids duplicates. */
export function defaultProjectsRoot(): string {
  return join(homedir(), '.claude', 'projects');
}

/** One transcript entry — only the fields the index reads. */
interface TranscriptEntry {
  type?: string;
  cwd?: string | null;
  gitBranch?: string | null;
}

/** Derive an index entry's content fields from a transcript's JSONL lines.
 * Transcripts are append-only logs whose final line may be a partial write;
 * unparseable lines are skipped rather than failing the whole file. */
function readTraceContent(
  lines: Iterable<string>
): Pick<TraceIndexEntry, 'cwd' | 'git_branch' | 'n_turns'> {
  let cwd: string | undefined;
  let gitBranch: string | undefined;
  let nTurns = 0;

  for (const line of lines) {
    if (line.trim() === '') continue;

    let entry: TranscriptEntry;
    try {
      entry = JSON.parse(line) as TranscriptEntry;
    } catch {
      continue;
    }

    if (entry.type === 'user' || entry.type === 'assistant') nTurns++;
    if (cwd === undefined && entry.cwd) cwd = entry.cwd;
    if (gitBranch === undefined && entry.gitBranch) gitBranch = entry.gitBranch;
  }

  return { cwd, git_branch: gitBranch, n_turns: nTurns };
}

/** Build the index entry for a single transcript file. */
function indexTraceFile(jsonlPath: string): TraceIndexEntry {
  const content = readTraceContent(readLines(jsonlPath));
  return {
    jsonl_path: jsonlPath,
    session_uuid: basename(jsonlPath, '.jsonl'),
    project_dir: basename(dirname(jsonlPath)),
    mtime_ms: statSync(jsonlPath).mtimeMs,
    ...content,
  };
}

/**
 * Index every transcript under `root`. Scans each project directory for
 * `*.jsonl` files and reads cheap metadata from each. Paths are canonicalized
 * and de-duplicated so symlinked account homes never yield the same file twice.
 */
export function indexTraces(root: string = defaultProjectsRoot()): TraceIndexEntry[] {
  const canonicalRoot = realpathSync(root);
  const seen = new Set<string>();
  const entries: TraceIndexEntry[] = [];

  for (const projectDir of readdirSync(canonicalRoot, { withFileTypes: true })) {
    if (!projectDir.isDirectory()) continue;
    const dirPath = join(canonicalRoot, projectDir.name);

    for (const file of readdirSync(dirPath, { withFileTypes: true })) {
      if (!file.isFile() || !file.name.endsWith('.jsonl')) continue;

      const canonical = realpathSync(join(dirPath, file.name));
      if (seen.has(canonical)) continue;
      seen.add(canonical);

      entries.push(indexTraceFile(canonical));
    }
  }

  return entries;
}

/** Index entries keyed by canonical path, for O(1) lookup during attach. */
export function traceIndexByPath(entries: TraceIndexEntry[]): Map<string, TraceIndexEntry> {
  return new Map(entries.map(e => [e.jsonl_path, e]));
}
