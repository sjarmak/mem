import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { exportProvenanceEventsCommand } from '../src/cli/commands/export-provenance-events.js';
import {
  importProvenanceEventsCommand,
  parseProvenanceEventLines,
} from '../src/cli/commands/import-provenance-events.js';
import { openStore, producerProvenanceEvents, recordProvenanceEvents } from '../src/store/index.js';
import { BACKFILL_SOURCE, type ProvenanceEvent } from '../src/schemas/provenance-event.js';

const SHA = '0123456789abcdef0123456789abcdef01234567';

const producerEvent = (id: string, workId: string): ProvenanceEvent => ({
  id,
  work_id: workId,
  kind: 'cut',
  actor: 'gc-1',
  ref: SHA,
  ref_kind: 'git-sha',
  payload: { base_branch: 'main' },
  source: 'git-hook',
  occurred_at: '2026-07-01T00:00:00Z',
  created_at: '2026-07-01T00:00:01Z',
});

const backfillEvent = (workId: string): ProvenanceEvent => ({
  id: `${BACKFILL_SOURCE}:${workId}:claim:gc-1:idx0`,
  work_id: workId,
  kind: 'claim',
  actor: 'gc-1',
  source: BACKFILL_SOURCE,
  occurred_at: '2026-07-01T00:00:00Z',
  created_at: '2026-07-01T00:00:01Z',
});

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-prov-events-'));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

/** Store with two producer events and one backfilled event. */
function seedStore(name: string): string {
  const path = join(dir, name);
  const db = openStore(path);
  try {
    recordProvenanceEvents(db, [
      producerEvent('git-hook:demo-1:cut:a', 'demo-1'),
      producerEvent('git-hook:demo-2:cut:b', 'demo-2'),
      backfillEvent('demo-1'),
    ]);
  } finally {
    db.close();
  }
  return path;
}

describe('parseProvenanceEventLines', () => {
  it('parses NDJSON and a JSON array, accepts empty input', () => {
    const line = JSON.stringify(producerEvent('git-hook:a:cut:x', 'a'));
    expect(parseProvenanceEventLines(`${line}\n${line}\n`)).toHaveLength(2);
    expect(parseProvenanceEventLines(`[${line}]`)).toHaveLength(1);
    expect(parseProvenanceEventLines('')).toEqual([]);
    expect(parseProvenanceEventLines('  \n')).toEqual([]);
  });

  it('rejects a malformed line with its line number', () => {
    const line = JSON.stringify(producerEvent('git-hook:a:cut:x', 'a'));
    expect(() => parseProvenanceEventLines(`${line}\n{broken`)).toThrow(/line 2/);
  });
});

describe('export-provenance-events / import-provenance-events round trip', () => {
  it('carries producer rows to a rebuilt store via --out NDJSON, excluding backfill', async () => {
    const sourcePath = seedStore('source.db');
    const outFile = join(dir, 'events.ndjson');

    const exported = exportProvenanceEventsCommand({
      args: [],
      options: { json: true, verbose: false, store: sourcePath, out: outFile },
    });
    // Backfilled rows re-derive on rebuild; only the 2 producer rows export.
    expect(exported.count).toBe(2);
    expect(exported.out).toBe(outFile);
    expect(readFileSync(outFile, 'utf8').trim().split('\n')).toHaveLength(2);

    const destPath = join(dir, 'dest.db');
    openStore(destPath).close();

    const imported = await importProvenanceEventsCommand({
      args: [],
      options: { json: true, verbose: false, store: destPath, file: outFile },
    });
    expect(imported).toEqual({ appended: 2, skipped: 0 });

    // Idempotent on re-import (dedup on the id PK).
    const again = await importProvenanceEventsCommand({
      args: [],
      options: { json: true, verbose: false, store: destPath, file: outFile },
    });
    expect(again).toEqual({ appended: 0, skipped: 2 });

    // Rows arrive identical to what the source store held.
    const source = openStore(sourcePath);
    const dest = openStore(destPath);
    try {
      expect(producerProvenanceEvents(dest)).toEqual(producerProvenanceEvents(source));
    } finally {
      source.close();
      dest.close();
    }
  });

  it('exports across a schema-version mismatch (the stranded-store rescue)', () => {
    const sourcePath = seedStore('stale.db');
    const stale = openStore(sourcePath);
    stale.pragma('user_version = 8');
    stale.close();

    const exported = exportProvenanceEventsCommand({
      args: [],
      options: { json: true, verbose: false, store: sourcePath },
    });
    expect(exported.count).toBe(2);
    expect(exported.events.map(e => e.work_id)).toEqual(['demo-1', 'demo-2']);
  });

  it('refuses to import backfill-source rows', async () => {
    const destPath = join(dir, 'dest.db');
    openStore(destPath).close();
    const file = join(dir, 'forged.ndjson');
    writeFileSync(file, `${JSON.stringify(backfillEvent('demo-1'))}\n`, 'utf8');

    await expect(
      importProvenanceEventsCommand({
        args: [],
        options: { json: true, verbose: false, store: destPath, file },
      })
    ).rejects.toThrow(new RegExp(BACKFILL_SOURCE));
  });

  it('import into a missing store is a loud user error', async () => {
    await expect(
      importProvenanceEventsCommand({
        args: [],
        options: {
          json: true,
          verbose: false,
          store: join(dir, 'absent.db'),
          file: join(dir, 'nope.ndjson'),
        },
      })
    ).rejects.toThrow();
  });
});
