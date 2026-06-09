import { readFile } from 'node:fs/promises';
import { CommandContext } from '../index.js';
import { extractErrors } from '../../parse/error-extractors.js';
import { errorClass, failureSignature, normalizePath } from '../../parse/recurrence.js';

/**
 * One extracted error as the canonical `trace_errors` STORE PROJECTION — the same
 * shape `store/writer.ts` persists for the held-out side (normalized path,
 * lifted error class, full signature), so a fresh run's rows are byte-identical
 * to the held-out rows the scorer compares against. The grading consumer
 * (`membench/grading/trace_score.py` `TraceErrorRef.from_mapping`) reads
 * `tool`/`file`/`line`/`error_class`/`signature`; the rest is carried for parity
 * with the store row and for debuggability.
 */
export interface ExtractedError {
  tool: string;
  severity: string;
  file: string;
  line: number;
  column: number | null;
  error_class: string;
  message: string;
  signature: string;
}

export interface ExtractErrorsResult {
  errors: ExtractedError[];
}

/**
 * Run the canonical extractors over raw tool output and project each error onto
 * the store row shape. Pure and deterministic. Mirrors `store/writer.ts`'s
 * trace_errors insert field-for-field — `normalizePath(file)`, `errorClass`, and
 * `failureSignature` are imported, never reimplemented, so the signatures match
 * the persisted held-out side exactly.
 */
export function extractErrorRows(output: string): ExtractedError[] {
  return extractErrors(output).map(error => ({
    tool: error.tool,
    severity: error.severity,
    file: normalizePath(error.file),
    line: error.line,
    column: error.column ?? null,
    error_class: errorClass(error),
    message: error.message,
    signature: failureSignature(error),
  }));
}

async function readStdin(): Promise<string> {
  // A TTY never yields EOF on its own, so the `for await` would hang silently.
  // Fail loud instead — the consumer always pipes input or uses --file.
  if (process.stdin.isTTY) {
    throw new Error('no input: pipe build/test/lint output to stdin, or use --file PATH');
  }
  const chunks: Uint8Array[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Uint8Array);
  return Buffer.concat(chunks).toString('utf8');
}

/**
 * `mem extract-errors [--file PATH] [--json]` — read raw build/test/lint output
 * (from `--file` or, by default, stdin) and emit the structured `trace_errors`
 * rows with canonical `tool:file:line:error_class` signatures. The fresh-run
 * extraction path for the ablation grid (mem-apg.3.1): HarborRunner's injected
 * extractor shells to this and reads `data.errors` from the `--json` envelope.
 *
 * Input is raw combined output, NOT a Claude transcript JSONL — it wraps
 * `extractErrors`, not `parseTranscript`.
 */
export async function extractErrorsCommand(ctx: CommandContext): Promise<ExtractErrorsResult> {
  const fileOpt = ctx.options.file;
  let output: string;
  if (fileOpt !== undefined) {
    if (typeof fileOpt !== 'string') {
      throw new Error('--file requires a path: mem extract-errors --file PATH');
    }
    // Trust assumption: the path is operator/harness-supplied — the parsed output
    // text is never reflected into `--file` — so no path sandboxing is applied.
    output = await readFile(fileOpt, 'utf8');
  } else {
    output = await readStdin();
  }

  const errors = extractErrorRows(output);

  if (!ctx.options.json) {
    for (const error of errors) console.error(`${error.signature}\t${error.message}`);
    console.error(`${errors.length} error(s)`);
  }

  return { errors };
}
