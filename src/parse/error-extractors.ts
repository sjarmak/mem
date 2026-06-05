import type { TraceError } from '../schemas/trace.js';

/**
 * Deterministic file:line error extractors — the ported engram mechanism
 * (build/test/lint output → structured `file:line` errors → recurring-failure
 * signal). Each extractor parses ONE tool's output format; format specificity
 * keeps them from cross-matching, so running every extractor over a wrapper
 * command's combined output (e.g. `npm run check`, which fans out to tsc + eslint
 * + vitest) and unioning the results is safe — see {@link extractErrors}.
 *
 * Scope is deliberately the formats present in this repo's own stack (TypeScript
 * + ESLint), each self-verifiable against real `npm run check` output. Runners
 * with no extractor (go, cargo, …) still yield a pass/fail {@link Execution}
 * outcome — only their `file:line` errors are unparsed until an extractor is
 * added, which is one entry in {@link EXTRACTORS}; nothing else changes.
 *
 * ZFC: these are mechanical format parsers for known tools, not semantic
 * judgment. The model-backed root-cause extractor is a separate concern.
 */

/** Parses one tool's output format into {@link TraceError}s. Pure. */
export interface ErrorExtractor {
  /** Tool whose format this parses, written onto each produced error. */
  readonly tool: string;
  /** Scan combined stdout+stderr; return every error the format reveals. */
  extract(output: string): TraceError[];
}

/** TypeScript compiler diagnostics, both emitted shapes:
 *   `src/x.ts(12,5): error TS2345: msg`   (default, non-pretty)
 *   `src/x.ts:12:5 - error TS2345: msg`   (`--pretty` plain) */
const TSC_PATTERNS: readonly RegExp[] = [
  /^(.+?)\((\d+),(\d+)\): (error|warning) (TS\d+): (.+)$/gm,
  /^(.+?):(\d+):(\d+) - (error|warning) (TS\d+): (.+)$/gm,
];

const tscExtractor: ErrorExtractor = {
  tool: 'tsc',
  extract(output) {
    const errors: TraceError[] = [];
    for (const re of TSC_PATTERNS) {
      for (const m of output.matchAll(re)) {
        errors.push({
          tool: 'tsc',
          severity: m[4] === 'warning' ? 'warning' : 'error',
          message: `${m[5]}: ${m[6].trim()}`,
          file: m[1],
          line: Number(m[2]),
          column: Number(m[3]),
        });
      }
    }
    return errors;
  },
};

/** A detail line in ESLint's "stylish" output: `  12:5  error  message  rule/id`.
 * The trailing rule id is optional — parser errors (`Parsing error: …`) and some
 * core rules print no rule, and those errors must still be captured. */
const ESLINT_DETAIL = /^\s+(\d+):(\d+)\s+(error|warning)\s+(.+?)(?:\s{2,}(\S+))?\s*$/;
/** Tokens that mark a non-header line (the run summary) in stylish output. */
const ESLINT_SUMMARY = /problems?\b/;
const ESLINT_SUMMARY_GLYPH = /^[✖x✓]/u;

/** A file header in ESLint's default "stylish" output: an unindented path on its
 * own line (absolute or relative), not a summary line. */
function isEslintFileHeader(line: string): boolean {
  if (/^\s/.test(line) || line.trim() === '') return false;
  if (ESLINT_SUMMARY.test(line) || ESLINT_SUMMARY_GLYPH.test(line.trim())) return false;
  return line.includes('/') || /\.\w+$/.test(line.trim());
}

/** ESLint "stylish" diagnostics: a file header, then indented detail lines, each
 * attached to the most recent header. An orphan detail (no header yet) is
 * dropped rather than guessed at. */
const eslintExtractor: ErrorExtractor = {
  tool: 'eslint',
  extract(output) {
    const errors: TraceError[] = [];
    let currentFile: string | null = null;

    for (const line of output.split('\n')) {
      const m = ESLINT_DETAIL.exec(line);
      if (m && currentFile) {
        const text = m[4].trim();
        errors.push({
          tool: 'eslint',
          severity: m[3] === 'warning' ? 'warning' : 'error',
          message: m[5] ? `${text} (${m[5]})` : text,
          file: currentFile,
          line: Number(m[1]),
          column: Number(m[2]),
        });
        continue;
      }
      if (isEslintFileHeader(line)) currentFile = line.trim();
    }
    return errors;
  },
};

/** All registered extractors, run as a set over each execution's output. */
export const EXTRACTORS: ReadonlyArray<ErrorExtractor> = [tscExtractor, eslintExtractor];

/** The stable identity of an error — the single definition shared by every
 * de-duplication pass (within an execution and across a transcript), so the
 * two can never drift. */
export function errorKey(e: TraceError): string {
  return `${e.tool}|${e.file}|${e.line}|${e.column ?? ''}|${e.severity}|${e.message}`;
}

/** De-duplicate by {@link errorKey}, preserving first-seen order. */
export function dedupeErrors(errors: TraceError[]): TraceError[] {
  const seen = new Set<string>();
  const out: TraceError[] = [];
  for (const error of errors) {
    const key = errorKey(error);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(error);
  }
  return out;
}

/** ANSI SGR color escapes, which a tool run with forced color (`FORCE_COLOR`,
 * `tsc --pretty` on a pseudo-TTY) embeds mid-line and would otherwise break the
 * `file:line` patterns. Stripped before extraction. */
const ANSI_SGR = new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, 'g');

/**
 * Run every extractor over `output` and return the de-duplicated union. Strips
 * ANSI color first. Safe for wrapper commands whose output interleaves several
 * tools: format specificity means a tsc line never matches the eslint format and
 * vice versa, and the dedup collapses any genuine repeat.
 */
export function extractErrors(output: string): TraceError[] {
  const clean = output.replace(ANSI_SGR, '');
  return dedupeErrors(EXTRACTORS.flatMap(extractor => extractor.extract(clean)));
}
