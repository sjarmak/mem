import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { renderProjection, renderRecurrence, replaceBetweenMarkers } from '../src/store/index.js';
import type { RecurrenceInsight } from '../src/parse/index.js';

const doc = [
  '# Context',
  '',
  'Hand-written intro.',
  '',
  '<!-- BEGIN: MEM_AUDIT -->',
  'stale generated content',
  '<!-- END: MEM_AUDIT -->',
  '',
  'Hand-written outro.',
  '',
].join('\n');

describe('replaceBetweenMarkers', () => {
  it('replaces only the content between markers, keeping markers and prose', () => {
    const next = replaceBetweenMarkers(doc, 'fresh body', 'MEM_AUDIT');

    expect(next).toContain('Hand-written intro.');
    expect(next).toContain('Hand-written outro.');
    expect(next).toContain('<!-- BEGIN: MEM_AUDIT -->\nfresh body\n<!-- END: MEM_AUDIT -->');
    expect(next).not.toContain('stale generated content');
  });

  it('is idempotent for the same body', () => {
    const once = replaceBetweenMarkers(doc, 'body', 'MEM_AUDIT');
    expect(replaceBetweenMarkers(once, 'body', 'MEM_AUDIT')).toBe(once);
  });

  it('throws when markers are missing', () => {
    expect(() => replaceBetweenMarkers('no markers here', 'x', 'MEM_AUDIT')).toThrow(/marker/i);
  });

  it('throws when markers are out of order', () => {
    const disordered = '<!-- END: MEM_AUDIT -->\n<!-- BEGIN: MEM_AUDIT -->';
    expect(() => replaceBetweenMarkers(disordered, 'x', 'MEM_AUDIT')).toThrow(/marker/i);
  });

  it('rejects a body that embeds the markers (would corrupt later renders)', () => {
    expect(() => replaceBetweenMarkers(doc, '<!-- END: MEM_AUDIT -->', 'MEM_AUDIT')).toThrow(
      /marker/i
    );
  });
});

describe('renderRecurrence', () => {
  const insight = (overrides: Partial<RecurrenceInsight> = {}): RecurrenceInsight => ({
    signature: 'tsc:src/a.ts:12:TS2345',
    tool: 'tsc',
    file: 'src/a.ts',
    line: 12,
    error_class: 'TS2345',
    sample_message: 'TS2345: bad argument',
    frequency: 5,
    trace_count: 2,
    confidence: 2 / 3,
    trace_ids: ['demo-1a2b', 'demo-2b3c'],
    ...overrides,
  });

  it('renders a deterministic markdown block', () => {
    const body = renderRecurrence([insight()]);

    expect(body).toBe(
      [
        '## Recurring failures',
        '',
        '- `tsc:src/a.ts:12:TS2345` — confidence 0.67 (2 traces, 5 occurrences)',
        '  TS2345: bad argument',
      ].join('\n')
    );
    // Same input, same output — byte-for-byte.
    expect(renderRecurrence([insight()])).toBe(body);
  });

  it('renders an explicit empty state', () => {
    expect(renderRecurrence([])).toBe('## Recurring failures\n\n(none)');
  });
});

describe('renderProjection', () => {
  let dir: string;

  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it('writes the projection once, then reports no change', () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-render-'));
    const path = join(dir, 'CONTEXT.md');
    writeFileSync(path, doc);

    expect(renderProjection(path, 'fresh body', 'MEM_AUDIT')).toBe(true);
    expect(readFileSync(path, 'utf8')).toContain('fresh body');
    expect(renderProjection(path, 'fresh body', 'MEM_AUDIT')).toBe(false);
  });

  it('propagates missing-marker errors', () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-render-'));
    const path = join(dir, 'CONTEXT.md');
    writeFileSync(path, 'no markers');

    expect(() => renderProjection(path, 'x', 'MEM_AUDIT')).toThrow(/marker/i);
  });

  it('propagates a missing file as an error', () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-render-'));
    expect(() => renderProjection(join(dir, 'absent.md'), 'x', 'MEM_AUDIT')).toThrow();
  });
});
