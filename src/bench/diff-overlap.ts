/**
 * Leakage gate (b) — DIFF-OVERLAP (PRD §6b). A retrieved memory must not hand the
 * agent the gold solution. Two mechanical tests over the memory's diff vs the
 * task's gold patch, plus a sanitizer that keeps raw diffs out of the store:
 *
 *  - **Shared file+hunk-anchor** ({@link sharesHunkAnchor}) — a hard reject
 *    regardless of similarity: if the memory edits the same file at the same hunk
 *    location as the gold patch, it is the gold patch by another name.
 *  - **Hunk-level Jaccard** ({@link changedLineJaccard}) over changed lines,
 *    rejected at a PER-RIG threshold ({@link diffOverlapThreshold}). The headline
 *    dashboard rig is ~59% trivial title-copy work, so a loose global 0.6 leaks
 *    the gold there — it gets a tight 0.2 (premortem R4).
 *  - **Sanitize before store** ({@link stripDiffsAndShas}) — strip unified-diff
 *    blocks and SHA-like tokens from memory text, so the leak vector never enters
 *    the store in the first place.
 *
 * This is the one calibrated mechanical-similarity step the ZFC rule allows
 * (patterns.md): a deterministic overlap measure, not a semantic judgment.
 */

/** Per-rig changed-line Jaccard threshold (≥ → reject). The trivial-work rigs are
 * tightened well below the default; an unlisted rig takes {@link DEFAULT_THRESHOLD}. */
export const DIFF_OVERLAP_THRESHOLDS: Readonly<Record<string, number>> = {
  // ~59% title-copy issue→commit links: a loose threshold leaks the gold here.
  gascity_dashboard: 0.2,
};

/** The fallback threshold for any rig not in {@link DIFF_OVERLAP_THRESHOLDS}. */
export const DEFAULT_THRESHOLD = 0.6;

/** The reject threshold for `rig` — its calibrated value, else the default. */
export function diffOverlapThreshold(rig: string): number {
  return DIFF_OVERLAP_THRESHOLDS[rig] ?? DEFAULT_THRESHOLD;
}

/** A new-file path and the hunks touched in it, parsed from a unified diff. */
interface ParsedDiff {
  /** `file:newStartLine` anchors — the locations the diff edits. */
  anchors: Set<string>;
  /** Trimmed, non-empty added/removed content lines — the Jaccard token set. */
  changedLines: Set<string>;
}

/** The new-side path of a `+++ b/<path>` header, or null for `/dev/null`. */
function newPath(headerLine: string): string | null {
  const path = headerLine.slice('+++ '.length).trim();
  if (path === '/dev/null' || path === '') return null;
  return path.replace(/^b\//, '');
}

/** The new-side start line of a `@@ -a,b +c,d @@` header, or null when unparsable. */
function hunkNewStart(headerLine: string): number | null {
  const m = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(headerLine);
  return m ? Number(m[1]) : null;
}

/** Parse a unified diff into its edit anchors and changed-line set. Tolerant of
 * partial input: lines outside a recognized hunk simply do not contribute. */
export function parseUnifiedDiff(diff: string): ParsedDiff {
  const anchors = new Set<string>();
  const changedLines = new Set<string>();
  let file: string | null = null;
  let inHunk = false;

  for (const line of diff.split('\n')) {
    // A new hunk or a new file block, in any state. `diff --git` is the only
    // header that can legitimately appear after hunk content (git emits it
    // between files), so it ends the current hunk.
    if (line.startsWith('@@')) {
      const start = hunkNewStart(line);
      if (file !== null && start !== null) anchors.add(`${file}:${start}`);
      inHunk = true;
      continue;
    }
    if (line.startsWith('diff ')) {
      inHunk = false;
      continue;
    }
    // Inside a hunk, a `+`/`-` line is content even when it reads like a header
    // (a removed `--- a comment` is a `-` line, not a `---` file header). Only
    // outside a hunk are `--- `/`+++ `/`index ` the file-block structure.
    if (inHunk) {
      if (line.startsWith('+') || line.startsWith('-')) {
        const content = line.slice(1).trim();
        if (content !== '') changedLines.add(content);
      }
      continue;
    }
    if (line.startsWith('+++ ')) file = newPath(line);
  }
  return { anchors, changedLines };
}

/** Size of the intersection of two sets. */
function intersectionSize<T>(a: ReadonlySet<T>, b: ReadonlySet<T>): number {
  let n = 0;
  for (const x of a) if (b.has(x)) n += 1;
  return n;
}

/** True when memory and gold edit a common file+hunk location — a hard reject. */
export function sharesHunkAnchor(memoryDiff: string, goldDiff: string): boolean {
  const mem = parseUnifiedDiff(memoryDiff).anchors;
  const gold = parseUnifiedDiff(goldDiff).anchors;
  return intersectionSize(mem, gold) > 0;
}

/** Jaccard of the two diffs' changed-line sets (0 when both are empty). */
export function changedLineJaccard(memoryDiff: string, goldDiff: string): number {
  const a = parseUnifiedDiff(memoryDiff).changedLines;
  const b = parseUnifiedDiff(goldDiff).changedLines;
  const inter = intersectionSize(a, b);
  const union = a.size + b.size - inter;
  return union === 0 ? 0 : inter / union;
}

/**
 * Whether a memory leaks the gold patch for a task on `rig`: a hard reject on a
 * shared file+hunk-anchor, or a changed-line Jaccard at/above the rig's
 * calibrated threshold.
 */
export function leaksGoldPatch(memoryDiff: string, goldDiff: string, rig: string): boolean {
  if (sharesHunkAnchor(memoryDiff, goldDiff)) return true;
  return changedLineJaccard(memoryDiff, goldDiff) >= diffOverlapThreshold(rig);
}

/** A SHA-like hex token: 7–40 hex chars with at least one a–f letter. The letter
 * requirement spares plain decimals (issue/PR numbers, counts) while still
 * catching real commit SHAs and abbreviations (`deadbeef`), which always carry a
 * hex letter; a ≥7-char all-letter prose word is vanishingly rare. */
const SHA_RE = /\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b/g;

/**
 * Strip unified-diff blocks and SHA-like tokens from memory text before it enters
 * the store — the leak vector removed at the source (PRD §6b). Diff headers and
 * any `@@`-introduced hunk body (`+`/`-`/` `/`\` lines) are dropped; remaining
 * SHA-like tokens are redacted. Prose `-`/`+` bullets are preserved because they
 * are only dropped while inside a hunk.
 */
export function stripDiffsAndShas(text: string): string {
  const kept: string[] = [];
  let inHunk = false;
  for (const line of text.split('\n')) {
    if (line.startsWith('@@')) {
      inHunk = true;
      continue;
    }
    if (line.startsWith('diff ') || line.startsWith('index ') || /^(---|\+\+\+) /.test(line)) {
      continue;
    }
    if (inHunk) {
      if (/^[+\- \\]/.test(line)) continue;
      inHunk = false; // a non-hunk line ends the hunk body
    }
    kept.push(line.replace(SHA_RE, '[sha]'));
  }
  return kept.join('\n');
}
