import { gzipSync } from 'node:zlib';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { defaultArchiveRoot, loadTranscriptArchive } from '../src/ingest/trace-archive.js';
import { attachTraceRefs, type SessionResolver } from '../src/ingest/trace-resolve.js';
import { WorkRecordSchema, type WorkRecord } from '../src/schemas/workrecord.js';

/** A minimal transcript JSONL body. */
function transcript(uuid: string): string {
  return (
    [
      { type: 'user', sessionId: uuid },
      { type: 'assistant', sessionId: uuid },
    ]
      .map(e => JSON.stringify(e))
      .join('\n') + '\n'
  );
}

/** Build a fake archive: gzip `body` under `<digest>__<uuid>.jsonl.gz` and
 * record a manifest entry keyed by the original (reaped) source path. Returns
 * the original source path. */
function seedArchive(root: string, digest: string, uuid: string, source: string): string {
  const body = transcript(uuid);
  const name = `${digest}__${uuid}.jsonl.gz`;
  writeFileSync(join(root, name), gzipSync(Buffer.from(body, 'utf8')));
  const entry = { source, name, size: Buffer.byteLength(body, 'utf8'), mtime_ns: 1, sha256: 'x' };
  writeFileSync(join(root, 'manifest.jsonl'), JSON.stringify(entry) + '\n', { flag: 'a' });
  return source;
}

describe('defaultArchiveRoot', () => {
  it('co-locates the archive with the store dir', () => {
    expect(defaultArchiveRoot('/home/ds/projects/mem/.mem/store.db')).toBe(
      '/home/ds/projects/mem/.mem/transcript-archive'
    );
  });
});

describe('loadTranscriptArchive.materialize', () => {
  let root: string;
  const reaped = '/home/ds/.claude/projects/-p/uuid-r.jsonl';

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'mem-archive-'));
    mkdirSync(root, { recursive: true });
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('recovers a reaped source path to a decompressed restored copy', () => {
    seedArchive(root, 'aaaaaaaaaaaa', 'uuid-r', reaped);
    const archive = loadTranscriptArchive(root);

    const out = archive.materialize(reaped);
    expect(out).toBe(join(root, 'restored', 'aaaaaaaaaaaa', 'uuid-r.jsonl'));
    // The restored file is real, uncompressed JSONL.
    expect(readFileSync(out, 'utf8')).toContain('"assistant"');
  });

  it('lets a live transcript win over the archive', () => {
    seedArchive(root, 'bbbbbbbbbbbb', 'uuid-r', reaped);
    // A live file on disk: materialize must return it untouched.
    const live = join(root, 'live.jsonl');
    writeFileSync(live, transcript('uuid-live'));
    const archive = loadTranscriptArchive(root);

    expect(archive.materialize(live)).toBe(live);
  });

  it('resolves a restored-copy path back to the same decompressed file', () => {
    seedArchive(root, 'cccccccccccc', 'uuid-r', reaped);
    const archive = loadTranscriptArchive(root);
    const restored = join(root, 'restored', 'cccccccccccc', 'uuid-r.jsonl');

    // The join can hand back the restored path before it has been materialized;
    // resolving it must decompress on demand to the same file.
    expect(archive.materialize(restored)).toBe(restored);
    expect(readFileSync(restored, 'utf8')).toContain('"user"');
  });

  it('is idempotent — a materialized restored copy is kept as-is', () => {
    seedArchive(root, 'dddddddddddd', 'uuid-r', reaped);
    const archive = loadTranscriptArchive(root);

    const first = archive.materialize(reaped);
    const mtime1 = statSync(first).mtimeMs;
    const second = archive.materialize(reaped);
    expect(second).toBe(first);
    expect(statSync(second).mtimeMs).toBe(mtime1);
  });

  it('returns an unarchived reaped path unchanged (never silently dropped)', () => {
    seedArchive(root, 'eeeeeeeeeeee', 'uuid-r', reaped);
    const archive = loadTranscriptArchive(root);
    const stranger = '/home/ds/.claude/projects/-p/not-archived.jsonl';
    expect(archive.materialize(stranger)).toBe(stranger);
  });

  it('is a no-op when the archive has no manifest', () => {
    const archive = loadTranscriptArchive(root);
    expect(archive.materialize(reaped)).toBe(reaped);
  });
});

describe('attachTraceRefs with archive fallback', () => {
  let root: string;
  const reaped = '/home/ds/.claude/projects/-p/uuid-r.jsonl';

  const baseRecord = (agentId: string): WorkRecord =>
    WorkRecordSchema.parse({
      work_id: 'mem-h3di',
      rig: 'mem',
      title: 'archive fallback',
      lifecycle: { created: '2026-06-23T00:00:00Z', status: 'closed' },
      agents: [{ agent_id: agentId }],
    });

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'mem-archive-'));
    mkdirSync(root, { recursive: true });
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('rewrites a reaped resolved path to its restored copy', () => {
    seedArchive(root, 'ffffffffffff', 'uuid-r', reaped);
    const archive = loadTranscriptArchive(root);
    const resolve: SessionResolver = id => (id === 'gc-1' ? reaped : null);

    const [rec] = attachTraceRefs([baseRecord('gc-1')], { resolve, archive });
    const restored = join(root, 'restored', 'ffffffffffff', 'uuid-r.jsonl');
    expect(rec.trace?.jsonl_path).toBe(restored);
    expect(rec.agents[0].trace_ref).toBe(restored);
  });

  it('keeps a live resolved path untouched (live wins)', () => {
    seedArchive(root, 'gggggggggggg', 'uuid-r', reaped);
    const archive = loadTranscriptArchive(root);
    const live = join(root, 'live.jsonl');
    writeFileSync(live, transcript('uuid-live'));
    const resolve: SessionResolver = id => (id === 'gc-1' ? live : null);

    const [rec] = attachTraceRefs([baseRecord('gc-1')], { resolve, archive });
    expect(rec.trace?.jsonl_path).toBe(live);
  });

  it('recovers a reaped preset trace_ref from the merged join', () => {
    seedArchive(root, 'hhhhhhhhhhhh', 'uuid-r', reaped);
    const archive = loadTranscriptArchive(root);
    // Emulate the merged-join attach: agent already carries a (reaped) trace_ref,
    // bypassing the session resolver.
    const record = WorkRecordSchema.parse({
      work_id: 'mem-h3di',
      rig: 'mem',
      title: 'preset',
      lifecycle: { created: '2026-06-23T00:00:00Z', status: 'closed' },
      agents: [{ agent_id: 'gc-1', trace_ref: reaped }],
      trace: { jsonl_path: reaped },
    });

    const [rec] = attachTraceRefs([record], { archive });
    const restored = join(root, 'restored', 'hhhhhhhhhhhh', 'uuid-r.jsonl');
    expect(rec.trace?.jsonl_path).toBe(restored);
    expect(rec.agents[0].trace_ref).toBe(restored);
  });
});
