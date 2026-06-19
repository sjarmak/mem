import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { provenanceCommand, type RecordProvenanceResult } from '../src/cli/commands/provenance.js';
import { openStore } from '../src/store/index.js';
import type { CliOptions } from '../src/cli/index.js';
import type { ProvenanceEvent } from '../src/schemas/provenance-event.js';

const SHA = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80910';

let dir: string;
let store: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-prov-cli-'));
  store = join(dir, 'store.db');
  openStore(store).close(); // withWriteStore requires the file to exist
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const ctx = (args: string[], options: Partial<CliOptions>) => ({
  args,
  options: { json: false, verbose: false, store, ...options } as CliOptions,
});

const recordCut = (overrides: Partial<CliOptions> = {}) =>
  provenanceCommand(
    ctx(['record'], {
      issue: 'demo-1',
      kind: 'cut',
      ref: SHA,
      'ref-kind': 'git-sha',
      source: 'git-hook',
      ...overrides,
    })
  ) as RecordProvenanceResult;

describe('mem provenance record', () => {
  it('records a producer cut event and is queryable', () => {
    const res = recordCut();
    expect(res.recorded).toBe(1);
    expect(res.kind).toBe('cut');

    const events = provenanceCommand(ctx(['log', 'demo-1'], {})) as ProvenanceEvent[];
    expect(events).toHaveLength(1);
    expect(events[0].ref).toBe(SHA);
    expect(events[0].source).toBe('git-hook');
  });

  it('is append-only/idempotent: re-recording the same event adds nothing', () => {
    expect(recordCut().recorded).toBe(1);
    expect(recordCut().recorded).toBe(0); // same deterministic id → INSERT OR IGNORE
    expect(provenanceCommand(ctx(['log', 'demo-1'], {})) as ProvenanceEvent[]).toHaveLength(1);
  });

  it('answers by-ref for a recorded SHA', () => {
    recordCut();
    const events = provenanceCommand(ctx(['by-ref', SHA], {})) as ProvenanceEvent[];
    expect(events.map(e => e.work_id)).toEqual(['demo-1']);
  });

  it('rejects the reserved ingest-backfill source', () => {
    expect(() => recordCut({ source: 'ingest-backfill' })).toThrow(/reserved/);
  });

  it('requires --issue and --kind, and validates the kind', () => {
    expect(() => provenanceCommand(ctx(['record'], { kind: 'cut' }))).toThrow(/--issue/);
    expect(() => provenanceCommand(ctx(['record'], { issue: 'demo-1' }))).toThrow(/--kind/);
    expect(() => recordCut({ kind: 'merged' })).toThrow(/--kind must be one of/);
  });

  it('rejects malformed --payload', () => {
    expect(() => recordCut({ payload: '{not json' })).toThrow(/valid JSON/);
  });

  it('rejects an unknown subcommand', () => {
    expect(() => provenanceCommand(ctx(['frobnicate'], {}))).toThrow(/record\|log\|by-ref/);
  });
});
