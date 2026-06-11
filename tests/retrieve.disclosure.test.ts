import { describe, expect, it } from 'vitest';

import { estimateTokens, lessonUri, recordUri, toDetails, toIndex } from '../src/retrieve/index.js';
import type { RetrievalResult, RetrievedItem } from '../src/retrieve/index.js';

const item = (workId: string, overrides: Partial<RetrievedItem> = {}): RetrievedItem => ({
  work_id: workId,
  rig: 'rigA',
  title: `Work ${workId}`,
  match: 'signature',
  matched_signatures: ['sig-1'],
  matched_classes: [],
  citation: { work_id: workId },
  lessons: [
    {
      id: 1,
      work_id: workId,
      extracted_at: '2026-06-01T00:00:00Z',
      payload: { subtitle: 'a lesson', concepts: ['gotcha'] },
    },
  ],
  ...overrides,
});

const result = (
  items: RetrievedItem[],
  overrides: Partial<RetrievalResult> = {}
): RetrievalResult => ({
  scope: 'cross_rig',
  work_id: 'query-1',
  trigger_count: 2,
  total_matched: items.length,
  near_duplicate_top: items[0]?.match === 'signature',
  fts_truncated: false,
  items,
  ...overrides,
});

describe('estimateTokens', () => {
  it('approximates 1 token per 4 characters, rounding up', () => {
    expect(estimateTokens('')).toBe(0);
    expect(estimateTokens('abcd')).toBe(1);
    expect(estimateTokens('abcde')).toBe(2);
  });
});

describe('citation URIs', () => {
  it('builds mem://lesson/{work_id}/{commit_sha} when the outcome has a sha', () => {
    expect(lessonUri({ work_id: 'w-1', commit_sha: 'abc123' })).toBe('mem://lesson/w-1/abc123');
  });

  it('omits the sha segment when there is none', () => {
    expect(lessonUri({ work_id: 'w-1' })).toBe('mem://lesson/w-1');
  });

  it('points L3 at the record', () => {
    expect(recordUri('w-1')).toBe('mem://record/w-1');
  });
});

describe('toIndex (L1)', () => {
  it('projects items in D10 order with per-item hydration costs', () => {
    const index = toIndex(result([item('w-1'), item('w-2', { match: 'message' })]));

    expect(index.items.map(i => i.work_id)).toEqual(['w-1', 'w-2']);
    expect(index.items[0]).toMatchObject({
      uri: 'mem://lesson/w-1',
      source_uri: 'mem://record/w-1',
      rig: 'rigA',
      match: 'signature',
      lesson_count: 1,
    });
    // The index row must not carry the lessons themselves — that is the
    // point of the layer split.
    expect(index.items[0]).not.toHaveProperty('lessons');
  });

  it('token_cost prices the L2 detail payload, and the total sums them', () => {
    const index = toIndex(result([item('w-1'), item('w-2')]));
    const details = toDetails(result([item('w-1'), item('w-2')]));

    index.items.forEach((row, i) => {
      expect(row.token_cost).toBe(estimateTokens(JSON.stringify(details.items[i])));
      expect(row.token_cost).toBeGreaterThan(0);
    });
    expect(index.token_cost_total).toBe(index.items.reduce((sum, row) => sum + row.token_cost, 0));
  });

  it('carries the precision-guard flags through the projection', () => {
    const index = toIndex(
      result([item('w-1')], { fts_truncated: true, total_matched: 40, trigger_count: 3 })
    );

    expect(index).toMatchObject({
      scope: 'cross_rig',
      work_id: 'query-1',
      trigger_count: 3,
      total_matched: 40,
      near_duplicate_top: true,
      fts_truncated: true,
    });
  });
});

describe('toDetails (L2)', () => {
  it('hydrates every item when no pick is given, with URIs attached', () => {
    const details = toDetails(result([item('w-1'), item('w-2')]));

    expect(details.items.map(i => i.work_id)).toEqual(['w-1', 'w-2']);
    expect(details.items[0].uri).toBe('mem://lesson/w-1');
    expect(details.items[0].lessons).toHaveLength(1);
  });

  it('carries the precision-guard flags like the index does', () => {
    const details = toDetails(result([item('w-1')], { fts_truncated: true, total_matched: 7 }));

    expect(details).toMatchObject({
      trigger_count: 2,
      total_matched: 7,
      near_duplicate_top: true,
      fts_truncated: true,
    });
  });

  it('hydrates only the picked work_ids, preserving result order', () => {
    const details = toDetails(result([item('w-1'), item('w-2'), item('w-3')]), ['w-3', 'w-1']);

    expect(details.items.map(i => i.work_id)).toEqual(['w-1', 'w-3']);
  });

  it('throws on a pick that is not in the result', () => {
    expect(() => toDetails(result([item('w-1')]), ['w-1', 'w-9'])).toThrow('w-9');
  });

  it('includes the commit snapshot in the lesson URI when present', () => {
    const details = toDetails(
      result([item('w-1', { citation: { work_id: 'w-1', commit_sha: 'beef01' } })])
    );

    expect(details.items[0].uri).toBe('mem://lesson/w-1/beef01');
  });
});
