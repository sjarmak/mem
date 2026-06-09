import type { TraceError } from '../schemas/trace.js';
import type { WorkRecord } from '../schemas/workrecord.js';

/**
 * Cross-task recurrence confidence (P1.6) — engram's `reflect` ported.
 *
 * Group every build/test/lint error by its **failure signature** (normalized
 * `file:line` + error-class, per Decision 8 — rig-agnostic by construction) and
 * score how broadly it recurs: `confidence = unique traces with this signature /
 * total traces considered`, capped at 1. A signature that shows up across many
 * independent traces is a durable, transferable failure pattern; one confined to
 * a single trace is noise. This is the intermediate "retrieval precision" signal
 * that the failure-triggered retrieval (Decision 8) keys on.
 *
 * Faithful to engram's `unique_traces / total` formula; the 0.5 cutoff engram
 * baked into reflect is a *retrieval-time* policy, so it lives in the caller —
 * here `minConfidence` defaults to 0 (return the full ranked set).
 */

/** A trace's parsed errors, keyed by the trace's owning work id. */
export interface FailureTrace {
  trace_id: string;
  errors: TraceError[];
}

/** One recurring failure pattern across traces. */
export interface RecurrenceInsight {
  /** `tool:file:line:error-class` — the de-duplication key. */
  signature: string;
  tool: string;
  file: string;
  line: number;
  error_class: string;
  /** A representative full message (first occurrence) for human/audit display. */
  sample_message: string;
  /** Total occurrences across all traces (repeats within a trace count). */
  frequency: number;
  /** Unique traces exhibiting the signature — the confidence numerator. */
  trace_count: number;
  /** `trace_count / total traces`, capped at 1. */
  confidence: number;
  /** Sorted unique ids of the traces exhibiting the signature. */
  trace_ids: string[];
}

/** Options for {@link computeRecurrence}. */
export interface RecurrenceOptions {
  /** Drop insights below this confidence (default 0 — keep all). */
  minConfidence?: number;
}

/** Per-tool extractor for the explicit diagnostic-code half of a signature, each
 * lifting the stable class token from that tool's native message position: a tsc
 * code (`TS2345`), an ESLint rule id (`(no-console)`), a mypy `[name-defined]`, a
 * leading ruff code (`F401`), a cargo/rustc `E0382`, or a pytest exception type
 * (`AssertionError`). Tool-gating is deliberate, not incidental: a tool-blind
 * `(...)$` rule wrongly lifted `int` from a go message like `…want (int))`, and a
 * tool-blind `TS\d+` rule fired on any tool whose message merely contained that
 * token. A tool with no entry (go, gradle) — or whose message lacks its code —
 * falls through to {@link classFallback}. `Partial` is load-bearing: it makes the
 * absent-tool case (`go`, `gradle`) an honest `RegExp | undefined`, so the
 * optional chain in {@link errorClass} is type-checked rather than an unverified
 * runtime guard. */
const CLASS_BY_TOOL: Readonly<Partial<Record<string, RegExp>>> = {
  tsc: /\b(TS\d+)\b/,
  eslint: /\(([^)]+)\)\s*$/,
  mypy: /\[([a-z][\w-]*)\]\s*$/,
  ruff: /^([A-Z]+\d+)\b/,
  cargo: /\b(E\d{3,})\b/,
  pytest: /\b(\w*(?:Error|Exception|Failed))\b/,
};

/** The fallback class for a tool with no code entry, or a message that didn't
 * carry its code: a digit-normalized, length-capped message prefix. A deliberate
 * *similarity-merge* threshold (the ZFC calibrated-threshold exception, not a
 * semantic judgment) — it collapses messages that agree once digits are masked.
 * For codeless toolchains (go, gradle/javac) this is the intended degraded mode:
 * identifier-free messages (`undefined: helper`) recur well; identifier-bearing
 * ones recur only when the embedded names happen to match. */
function classFallback(message: string): string {
  return message.toLowerCase().replace(/\d+/g, '#').replace(/\s+/g, ' ').trim().slice(0, 80);
}

/** The error-class half of a failure signature — see {@link CLASS_BY_TOOL} for
 * the per-tool code lift and {@link classFallback} for the codeless default. */
export function errorClass(error: TraceError): string {
  const match = CLASS_BY_TOOL[error.tool]?.exec(error.message);
  // Cap the lifted code the same way classFallback caps its prefix, so a
  // pathologically long token (e.g. a fully-qualified pytest exception path)
  // can't bloat the persisted signature.
  return match ? match[1].slice(0, 80) : classFallback(error.message);
}

/** Normalize a file path for cross-trace comparison: forward slashes, no leading
 * `./`. Enough to align the same file across runs that print it the same way.
 * Note: an absolute path is kept as-is, so the same logical file printed
 * absolute in one trace and relative in another won't merge, and paths stay
 * rig-specific by construction — cross-rig transfer is handled by the retrieval
 * *scope* (Decision 7), not by collapsing the signature. Making within-rig
 * absolute paths repo-relative needs the trace's `cwd` and is deferred. */
export function normalizePath(file: string): string {
  return file.replace(/\\/g, '/').replace(/^\.\//, '');
}

/** Assemble a failure signature from already-normalized parts — the recurrence
 * de-duplication key. */
function signatureOf(tool: string, file: string, line: number, error_class: string): string {
  return `${tool}:${file}:${line}:${error_class}`;
}

/** The full failure signature — the recurrence de-duplication key. */
export function failureSignature(error: TraceError): string {
  return signatureOf(error.tool, normalizePath(error.file), error.line, errorClass(error));
}

interface Accumulator {
  signature: string;
  tool: string;
  file: string;
  line: number;
  error_class: string;
  sample_message: string;
  frequency: number;
  trace_ids: Set<string>;
}

/**
 * Compute recurrence insights over a set of traces. `total` (the confidence
 * denominator) is `traces.length` — pass the *error-bearing* traces (those with
 * ≥1 parsed build/test/lint error); {@link recurrenceFromRecords} does this
 * selection. This adapts engram's `unique_traces / total` formula: the
 * denominator here is error-bearing traces, not engram's `outcome='failure'`
 * traces. A run that failed but produced no parseable `file:line` error has no
 * signature to group on, so it contributes to neither numerator nor denominator
 * — confidence is "of the traces with parseable errors, how broadly does this
 * signature recur". Ranked by confidence, then frequency, then signature —
 * fully deterministic.
 */
export function computeRecurrence(
  traces: FailureTrace[],
  opts: RecurrenceOptions = {}
): RecurrenceInsight[] {
  const total = traces.length;
  if (total === 0) return [];

  const bySignature = new Map<string, Accumulator>();

  for (const trace of traces) {
    for (const error of trace.errors) {
      const file = normalizePath(error.file);
      const error_class = errorClass(error);
      const signature = signatureOf(error.tool, file, error.line, error_class);
      const existing = bySignature.get(signature);
      if (existing) {
        existing.frequency++;
        existing.trace_ids.add(trace.trace_id);
      } else {
        bySignature.set(signature, {
          signature,
          tool: error.tool,
          file,
          line: error.line,
          error_class,
          sample_message: error.message,
          frequency: 1,
          trace_ids: new Set([trace.trace_id]),
        });
      }
    }
  }

  const minConfidence = opts.minConfidence ?? 0;
  const insights: RecurrenceInsight[] = [];

  for (const acc of bySignature.values()) {
    const trace_count = acc.trace_ids.size;
    const confidence = Math.min(trace_count / total, 1);
    if (confidence < minConfidence) continue;

    insights.push({
      signature: acc.signature,
      tool: acc.tool,
      file: acc.file,
      line: acc.line,
      error_class: acc.error_class,
      sample_message: acc.sample_message,
      frequency: acc.frequency,
      trace_count,
      confidence,
      trace_ids: [...acc.trace_ids].sort(),
    });
  }

  insights.sort(
    (a, b) =>
      b.confidence - a.confidence ||
      b.frequency - a.frequency ||
      a.signature.localeCompare(b.signature)
  );
  return insights;
}

/**
 * Convenience over WorkRecords: select those whose `trace.errors` is non-empty
 * (the traces that hit a parseable build/test/lint error) and compute recurrence
 * over them. The denominator is that error-bearing set — see
 * {@link computeRecurrence} for how this adapts engram's failed-trace denominator.
 */
export function recurrenceFromRecords(
  records: WorkRecord[],
  opts: RecurrenceOptions = {}
): RecurrenceInsight[] {
  const traces: FailureTrace[] = records
    .filter(r => (r.trace?.errors?.length ?? 0) > 0)
    .map(r => ({ trace_id: r.work_id, errors: r.trace?.errors ?? [] }));
  return computeRecurrence(traces, opts);
}
