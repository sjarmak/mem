/**
 * Canonical lifecycle-timestamp shape (mem-0rrf.15). The Decision-6 temporal
 * leave-one-out boundary is a lexicographic TEXT comparison in SQL
 * (`closed_at < ?`), which is only a chronological comparison when every value
 * shares one format. The corpus mixes producers: dolt emits space-separated
 * zoneless timestamps (`2026-06-07 02:19:05`) while the synthetic generators
 * (Decision 19) emit ISO `T`/`Z` — and `' ' < 'T'`, so a mixed-format record
 * that closed AFTER the boundary passes the strict filter silently, in the
 * leak direction. The writer canonicalizes every projected lifecycle column
 * through {@link toIsoUtc} and the reader does the same to its temporal filter
 * parameter, so the stored comparison is format-free.
 *
 * Mirrored byte-for-byte by `canonical_ts` in memory-bench/membench/validity.py
 * (the Python firewall duplicates the boundary — parity contract; change both).
 */

/** `YYYY-MM-DD` + `T`/space separator + `HH:MM[:SS[.fff]]` + optional zone
 * (`Z`, `±HH:MM`, `±HHMM`). Zoneless values are UTC — the city convention
 * (`ingest/provenance` `toGitUtc` pins the same shapes for git). Date-only
 * values are rejected: a boundary is an instant, not a day to guess midnight
 * for. */
const TIMESTAMP_RE =
  /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?)(Z|[+-]\d{2}:?\d{2})?$/;

/** Canonicalize a lifecycle timestamp to `YYYY-MM-DDTHH:MM:SS.sssZ`. Throws on
 * anything unparseable — a producer emitting a malformed timestamp must fail
 * loudly at ingest, never silently reorder the LOO boundary. */
export function toIsoUtc(value: string): string {
  const match = TIMESTAMP_RE.exec(value);
  if (match === null) {
    throw new Error(`not a recognizable lifecycle timestamp: ${JSON.stringify(value)}`);
  }
  const [, date, time, zone] = match;
  const offset =
    zone === undefined || zone === 'Z' ? 'Z' : zone.replace(/^([+-]\d{2})(\d{2})$/, '$1:$2');
  // The regex constrains shape, not ranges — Date.parse rejects a month 13.
  const ms = Date.parse(`${date}T${time}${offset}`);
  if (Number.isNaN(ms)) {
    throw new Error(`not a recognizable lifecycle timestamp: ${JSON.stringify(value)}`);
  }
  return new Date(ms).toISOString();
}
