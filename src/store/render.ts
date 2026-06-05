import { readFileSync, writeFileSync } from 'node:fs';

import type { RecurrenceInsight } from '../parse/recurrence.js';

/**
 * Marker-bounded deterministic render (P1.5) — engram's `apply` mechanism
 * generalized. The store is truth; a context file is a regenerated projection
 * between HTML-comment markers. Everything outside the markers is hand-written
 * and never touched; everything between them is overwritten wholesale on each
 * render. This is what prevents the context-file bloat failure mode: the
 * rendered section can only ever be exactly what the store says, never an
 * accumulation of past renders.
 */

/**
 * Replace the content between `<!-- BEGIN: label -->` and `<!-- END: label -->`
 * with `body`, keeping the markers and all surrounding content. Throws when the
 * markers are missing or disordered, or when `body` itself contains a marker
 * (which would corrupt every subsequent render).
 */
export function replaceBetweenMarkers(content: string, body: string, label: string): string {
  const begin = `<!-- BEGIN: ${label} -->`;
  const end = `<!-- END: ${label} -->`;

  if (body.includes(begin) || body.includes(end)) {
    throw new Error(`Render body must not contain the ${label} markers`);
  }

  const beginIdx = content.indexOf(begin);
  const endIdx = content.indexOf(end);
  if (beginIdx === -1 || endIdx === -1 || endIdx <= beginIdx) {
    throw new Error(
      `Content is missing ordered ${begin} / ${end} markers — cannot render projection`
    );
  }

  return content.slice(0, beginIdx + begin.length) + '\n' + body + '\n' + content.slice(endIdx);
}

/**
 * Render recurrence insights (parse/recurrence, Decision 8 signal) as a
 * deterministic markdown block. Input order is preserved — `computeRecurrence`
 * already returns a fully deterministic ranking.
 */
export function renderRecurrence(insights: RecurrenceInsight[]): string {
  const header = '## Recurring failures';
  if (insights.length === 0) {
    return `${header}\n\n(none)`;
  }

  const lines = insights.map(
    insight =>
      `- \`${insight.signature}\` — confidence ${insight.confidence.toFixed(2)} ` +
      `(${insight.trace_count} traces, ${insight.frequency} occurrences)\n` +
      `  ${insight.sample_message}`
  );
  return `${header}\n\n${lines.join('\n')}`;
}

/**
 * Re-render the marker-bounded section of the file at `path` to `body`.
 * Returns true when the file changed, false when it was already current.
 * A missing file or missing markers propagate as errors — a projection target
 * is hand-created once with markers in place, never auto-created (silent
 * resource creation would hide a misconfigured path).
 */
export function renderProjection(path: string, body: string, label: string): boolean {
  const content = readFileSync(path, 'utf8');
  const next = replaceBetweenMarkers(content, body, label);
  if (next === content) {
    return false;
  }
  writeFileSync(path, next, 'utf8');
  return true;
}
