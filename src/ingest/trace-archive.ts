import { gunzipSync } from 'node:zlib';
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, sep } from 'node:path';

import { readLines } from '../parse/trace-parse.js';

/**
 * Transcript archive resolution (mem-h3di.4) — make `.mem/transcript-archive/`
 * a first-class trace-resolution root, so a transcript the ~6-week rolling
 * window has reaped still resolves to readable JSONL.
 *
 * The archive is written by `membench.transcript_archive`: every bead-linked
 * transcript is gzip-copied to `<root>/<digest>__<name>.gz` and recorded in an
 * append-only `manifest.jsonl` keyed by ORIGINAL source path. The Python join
 * builder also decompresses pruned copies into `<root>/restored/<digest>/<name>`
 * via `restore_pruned`; this module makes that recovery available to the TS
 * ingest DIRECTLY — it decompresses on demand and does not depend on the join
 * builder having run its restore step first (the decoupling this bead adds).
 *
 * Pure IO — no judgment. Resolution precedence is fixed and deterministic: a
 * live transcript on disk always wins (it is the freshest copy, and a still-live
 * session may have grown after archival); the archive is consulted only when the
 * resolved path is gone.
 */

/** The append-only manifest filename inside the archive root. */
const MANIFEST_NAME = 'manifest.jsonl';
/** Subdir holding decompressed copies, mirroring `transcript_archive.py`. */
const RESTORED_SUBDIR = 'restored';

/** The default archive root, co-located with the store: `<store-dir>/transcript-archive`.
 * Keeps the archive next to the `.mem/store.db` it feeds, with no absolute path. */
export function defaultArchiveRoot(storePath: string): string {
  return join(dirname(storePath), 'transcript-archive');
}

/** One manifest entry — only the fields resolution reads. `size` is the
 * UNCOMPRESSED source byte length (the restored-copy idempotency key). */
interface ManifestEntry {
  source: string;
  name: string;
  size: number;
}

/** Resolves a reaped transcript to a readable restored copy, decompressing on
 * demand. {@link materialize} is the only entry point ingest needs. */
export interface TranscriptArchive {
  /**
   * Apply archive precedence to a resolved transcript path:
   *  - a path that exists on disk is returned unchanged (live wins);
   *  - a reaped path that names an archived transcript — by its original source
   *    path, or by its restored-copy path under the archive root — is
   *    decompressed on demand and the restored path returned;
   *  - any other path is returned unchanged (a reaped, unarchived transcript is
   *    left as-is, never silently dropped).
   */
  materialize(path: string): string;
}

/** Latest manifest entry per source path (append-only; later lines supersede).
 * A manifest line missing the fields resolution needs is skipped, mirroring the
 * Python loader's tolerance of partial writes. */
function loadManifest(root: string): ManifestEntry[] {
  const manifestPath = join(root, MANIFEST_NAME);
  if (!existsSync(manifestPath)) return [];

  const bySource = new Map<string, ManifestEntry>();
  for (const line of readLines(manifestPath)) {
    if (line.trim() === '') continue;
    let parsed: Partial<ManifestEntry>;
    try {
      parsed = JSON.parse(line) as Partial<ManifestEntry>;
    } catch {
      continue;
    }
    if (
      typeof parsed.source === 'string' &&
      typeof parsed.name === 'string' &&
      typeof parsed.size === 'number'
    ) {
      bySource.set(parsed.source, { source: parsed.source, name: parsed.name, size: parsed.size });
    }
  }
  return [...bySource.values()];
}

/** Restored-copy path for an archive entry, mirroring `transcript_archive.py`'s
 * `restore_pruned`: `<root>/restored/<digest>/<name-without-.gz>`. The archive
 * filename is `<digest>__<original-name>.gz`. */
function restoredPath(root: string, name: string): string {
  const sepIdx = name.indexOf('__');
  const digest = name.slice(0, sepIdx);
  const original = name.slice(sepIdx + 2).replace(/\.gz$/, '');
  return join(root, RESTORED_SUBDIR, digest, original);
}

/**
 * Open the archive at `root`. The returned {@link TranscriptArchive} is a no-op
 * for live paths and for paths it does not recognize, so it is always safe to
 * wrap a resolver with it — an absent or empty archive simply never recovers
 * anything. The manifest is read once at open; decompression is lazy.
 */
export function loadTranscriptArchive(root: string): TranscriptArchive {
  const entries = loadManifest(root);
  const bySource = new Map(entries.map(e => [e.source, e]));
  const byName = new Map(entries.map(e => [e.name, e]));
  const restoredPrefix = join(root, RESTORED_SUBDIR) + sep;

  /** Map a resolved path to its manifest entry: a restored-copy path resolves by
   * its archive filename, any other path by exact original source. */
  function entryFor(path: string): ManifestEntry | undefined {
    if (path.startsWith(restoredPrefix)) {
      const rel = path.slice(restoredPrefix.length);
      const [digest, ...rest] = rel.split(sep);
      return byName.get(`${digest}__${rest.join(sep)}.gz`);
    }
    return bySource.get(path);
  }

  /** Decompress an archive entry to its restored path, idempotently (a restored
   * copy whose size matches the manifest's uncompressed size is kept as-is).
   * Atomic via tmp + rename, mirroring the Python writer. */
  function ensureRestored(entry: ManifestEntry): string {
    const out = restoredPath(root, entry.name);
    if (existsSync(out) && statSync(out).size === entry.size) return out;
    const gz = join(root, entry.name);
    const data = gunzipSync(readFileSync(gz));
    mkdirSync(dirname(out), { recursive: true });
    const tmp = `${out}.tmp`;
    writeFileSync(tmp, data);
    renameSync(tmp, out);
    return out;
  }

  return {
    materialize(path: string): string {
      if (existsSync(path)) return path;
      const entry = entryFor(path);
      return entry === undefined ? path : ensureRestored(entry);
    },
  };
}
