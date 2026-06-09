import type { TraceError } from '../schemas/trace.js';

/**
 * Deterministic file:line error extractors — the ported engram mechanism
 * (build/test/lint output → structured `file:line` errors → recurring-failure
 * signal). Each extractor parses ONE tool's output format; format specificity
 * keeps them from cross-matching, so running every extractor over a wrapper
 * command's combined output (e.g. `npm run check`, which fans out to tsc + eslint
 * + vitest) and unioning the results is safe — see {@link extractErrors}.
 *
 * Scope covers the high-volume toolchains across the rig corpus: TypeScript +
 * ESLint (this repo's own stack), plus go, mypy, ruff, cargo/rustc, pytest, and
 * gradle (javac + kotlinc). Each extractor is anchored on its toolchain's file
 * extension and a distinct format token, so running the whole set over one
 * command's combined output never cross-attributes a line (see the polyglot
 * cross-match test). A runner with no extractor still yields a pass/fail
 * {@link Execution} outcome — only its `file:line` errors are unparsed until an
 * extractor is added, which is one entry in {@link EXTRACTORS}; nothing else
 * changes.
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

/** Go build & vet diagnostics: `./pkg/file.go:12:5: message` (the column is
 *  absent in some vet output). Anchored on `.go`; leading indentation from
 *  test-log framing is dropped. Panic stack frames (`file.go:12 +0x…`, with no
 *  `: message`) don't match and are skipped. No diagnostic code, so the
 *  recurrence class falls back to the normalized message. */
const GO_PATTERN = /^\s*(\S+?\.go):(\d+)(?::(\d+))?: (.+)$/gm;

const goExtractor: ErrorExtractor = {
  tool: 'go',
  extract(output) {
    const errors: TraceError[] = [];
    for (const m of output.matchAll(GO_PATTERN)) {
      errors.push({
        tool: 'go',
        severity: 'error',
        message: m[4].trim(),
        file: m[1],
        line: Number(m[2]),
        ...(m[3] ? { column: Number(m[3]) } : {}),
      });
    }
    return errors;
  },
};

/** mypy diagnostics: `app.py:12: error: msg  [code]`, optionally with a column
 *  (`app.py:12:5: …`). Anchored on `.py`/`.pyi`, which is the *sole* discriminator
 *  from an identically-shaped javac `Foo.java:12: error:` line — so the anchor is
 *  kept strict (extension immediately followed by `:`). `note:` lines are dropped:
 *  they carry no code and aren't failures. The `[code]` is left in the message
 *  for {@link errorClass} to lift. */
const MYPY_PATTERN = /^(\S+?\.pyi?):(\d+)(?::(\d+))?: (error|warning): (.+)$/gm;

const mypyExtractor: ErrorExtractor = {
  tool: 'mypy',
  extract(output) {
    const errors: TraceError[] = [];
    for (const m of output.matchAll(MYPY_PATTERN)) {
      errors.push({
        tool: 'mypy',
        severity: m[4] === 'warning' ? 'warning' : 'error',
        message: m[5].trim(),
        file: m[1],
        line: Number(m[2]),
        ...(m[3] ? { column: Number(m[3]) } : {}),
      });
    }
    return errors;
  },
};

/** ruff diagnostics: `app.py:12:5: F401 [*] msg` (the `[*]` auto-fix marker is
 *  optional). The leading rule code (F401, E501, PLC0414, …) is the stable class
 *  and is preserved at the head of the message for {@link errorClass}. Anchored on
 *  `.py`/`.pyi` plus the uppercase code, which keeps it disjoint from mypy (whose
 *  token is the `error:`/`warning:` keyword) and pytest (whose lines start
 *  `FAILED`/`ERROR`). The `[*]`/`[x]` fixability marker is optional. */
const RUFF_PATTERN = /^(\S+?\.pyi?):(\d+):(\d+): ([A-Z]+\d+)(?: \[[*x]\])? (.+)$/gm;

const ruffExtractor: ErrorExtractor = {
  tool: 'ruff',
  extract(output) {
    const errors: TraceError[] = [];
    for (const m of output.matchAll(RUFF_PATTERN)) {
      errors.push({
        tool: 'ruff',
        severity: 'error',
        message: `${m[4]} ${m[5].trim()}`,
        file: m[1],
        line: Number(m[2]),
        column: Number(m[3]),
      });
    }
    return errors;
  },
};

/** rustc/cargo/clippy diagnostics span two lines — a header carrying severity and
 *  (for rustc errors) an `E0001` code, then an indented location:
 *    `error[E0382]: borrow of moved value: ``x```
 *    `  --> src/main.rs:12:9`
 *  Parsed as a small state machine, like the ESLint header/detail one. The code
 *  is lifted onto the head of the message for {@link errorClass}. A header with no
 *  following `.rs` location (e.g. `error: could not compile ``crate```) is dropped
 *  — the location regex is anchored on `.rs`, so a stray `error:` line from
 *  another tool can't pair with an unrelated location — and a fresh header
 *  replaces an unpaired pending one. */
const CARGO_HEADER = /^(error|warning)(?:\[(E\d+)\])?: (.+)$/;
const CARGO_LOCATION = /^\s*-->\s+(\S+?\.rs):(\d+):(\d+)/;

const cargoExtractor: ErrorExtractor = {
  tool: 'cargo',
  extract(output) {
    const errors: TraceError[] = [];
    let pending: { severity: 'error' | 'warning'; message: string } | null = null;

    for (const line of output.split('\n')) {
      const header = CARGO_HEADER.exec(line);
      if (header) {
        const text = header[3].trim();
        pending = {
          severity: header[1] === 'warning' ? 'warning' : 'error',
          message: header[2] ? `${header[2]}: ${text}` : text,
        };
        continue;
      }
      const loc = CARGO_LOCATION.exec(line);
      if (loc && pending) {
        errors.push({
          tool: 'cargo',
          severity: pending.severity,
          message: pending.message,
          file: loc[1],
          line: Number(loc[2]),
          column: Number(loc[3]),
        });
        pending = null;
        continue;
      }
      // cargo prints the `-->` location on the line immediately after its header.
      // Any other line ends that adjacency window, so a stray `error:`/`warning:`
      // summary from another tool can't reach forward across intervening output
      // to pair with a later, unrelated cargo location.
      pending = null;
    }
    return errors;
  },
};

/** pytest failures from the short-test-summary line — the highest-yield, least
 *  ambiguous pytest signal: `FAILED path::test - ExceptionType: detail` (also
 *  `ERROR` for collection/fixture failures). The `FAILED`/`ERROR` prefix gives it
 *  zero cross-match surface. pytest summaries carry no line number, and the
 *  recurrence avoid-axis is line-invariant (`tool:basename:error_class`), so the
 *  line is set to 0 by design. A summary with no ` - reason` carries no class and
 *  is skipped. */
const PYTEST_PATTERN = /^(?:FAILED|ERROR) (\S+?\.py)(?:::\S+)? - (.+)$/gm;

const pytestExtractor: ErrorExtractor = {
  tool: 'pytest',
  extract(output) {
    const errors: TraceError[] = [];
    for (const m of output.matchAll(PYTEST_PATTERN)) {
      errors.push({
        tool: 'pytest',
        severity: 'error',
        message: m[2].trim(),
        file: m[1],
        line: 0,
      });
    }
    return errors;
  },
};

/** Gradle compile diagnostics for its two main JVM toolchains:
 *    javac:   `/src/Foo.java:12: error: cannot find symbol`
 *    kotlinc: `e: /src/Foo.kt:12:5: unresolved reference: bar` (`e:`/`w:` severity)
 *  Anchored on `.java`/`.kt`. The kotlinc `e:`/`w:` severity prefix is required —
 *  it both carries the severity and keeps the pattern from matching any bare
 *  `path.kt:line:col:` line. No portable diagnostic code, so the recurrence class
 *  falls back to the normalized message. */
const GRADLE_JAVAC = /^(\S+?\.java):(\d+): (error|warning): (.+)$/gm;
const GRADLE_KOTLIN = /^([ew]): (\S+?\.kt):(\d+):(\d+): (.+)$/gm;

const gradleExtractor: ErrorExtractor = {
  tool: 'gradle',
  extract(output) {
    const errors: TraceError[] = [];
    for (const m of output.matchAll(GRADLE_JAVAC)) {
      errors.push({
        tool: 'gradle',
        severity: m[3] === 'warning' ? 'warning' : 'error',
        message: m[4].trim(),
        file: m[1],
        line: Number(m[2]),
      });
    }
    for (const m of output.matchAll(GRADLE_KOTLIN)) {
      errors.push({
        tool: 'gradle',
        severity: m[1] === 'w' ? 'warning' : 'error',
        message: m[5].trim(),
        file: m[2],
        line: Number(m[3]),
        column: Number(m[4]),
      });
    }
    return errors;
  },
};

/** All registered extractors, run as a set over each execution's output. */
export const EXTRACTORS: ReadonlyArray<ErrorExtractor> = [
  tscExtractor,
  eslintExtractor,
  goExtractor,
  mypyExtractor,
  ruffExtractor,
  cargoExtractor,
  pytestExtractor,
  gradleExtractor,
];

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
