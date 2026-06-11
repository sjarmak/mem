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
