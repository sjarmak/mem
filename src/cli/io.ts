/** Shared CLI input plumbing. */

/** A parsed CLI option value: `--flag value` → string, bare `--flag` → true. */
export type OptionValue = string | boolean | undefined;

/** Require a string value for a flag that takes one; a bare `--flag` throws. */
export function asString(value: OptionValue, flag: string): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw new Error(`--${flag} requires a value`);
  return value;
}

/** Require a value drawn from a fixed set — fail fast on a typo rather than
 * silently building a filter that matches nothing. */
export function asEnum<T extends string>(
  value: OptionValue,
  allowed: readonly T[],
  flag: string
): T | undefined {
  const str = asString(value, flag);
  if (str === undefined) return undefined;
  if (!allowed.includes(str as T)) {
    throw new Error(`--${flag} must be one of: ${allowed.join(', ')}`);
  }
  return str as T;
}

/** Shared integer-flag guard behind {@link asPositiveInt} and
 * {@link asNonNegativeInt} — the two only differ in their floor. */
function asIntAtLeast(value: OptionValue, flag: string, min: 0 | 1): number | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw new Error(`--${flag} requires a value`);
  const n = Number(value);
  if (!Number.isInteger(n) || n < min) {
    const kind = min === 0 ? 'non-negative' : 'positive';
    throw new Error(`--${flag} must be a ${kind} integer, got ${String(value)}`);
  }
  return n;
}

/** Require a positive-integer value for a flag that takes one; `undefined` when absent. */
export function asPositiveInt(value: OptionValue, flag: string): number | undefined {
  return asIntAtLeast(value, flag, 1);
}

/** Require a non-negative-integer value for a flag that takes one (0 is
 * valid — e.g. retrieve's `--limit 0` returns no items but still reports
 * `total_matched`); `undefined` when absent. */
export function asNonNegativeInt(value: OptionValue, flag: string): number | undefined {
  return asIntAtLeast(value, flag, 0);
}

/** Read all of stdin. A TTY never yields EOF on its own, so the `for await`
 * would hang silently — fail loud instead; the consumer always pipes input or
 * uses `--file`. */
export async function readStdin(): Promise<string> {
  if (process.stdin.isTTY) {
    throw new Error('no input: pipe input to stdin, or use --file PATH');
  }
  const chunks: Uint8Array[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Uint8Array);
  return Buffer.concat(chunks).toString('utf8');
}
