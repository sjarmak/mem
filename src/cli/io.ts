/** Shared CLI input plumbing. */

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
