import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { exportLessonsCommand } from '../src/cli/commands/export-lessons.js';
import { importLessonsCommand, parseLessonLines } from '../src/cli/commands/import-lessons.js';
import { allLessons, appendLesson, openStore } from '../src/store/index.js';

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mem-lessons-'));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

/** Create a store on disk with two lessons and return its path. */
function seedStore(name: string): string {
  const path = join(dir, name);
  const db = openStore(path);
  try {
    appendLesson(db, {
      work_id: 'demo-1a2b',
      extracted_at: '2026-06-03T00:00:00Z',
      commit_sha: 'abc123',
      payload: { root_cause: 'missing flag', resolution: 'add --no-tls' },
    });
    appendLesson(db, {
      work_id: 'demo-2b3c',
      extracted_at: '2026-06-04T00:00:00Z',
      payload: { root_cause: 'second' },
    });
  } finally {
    db.close();
  }
  return path;
}

describe('parseLessonLines', () => {
  it('parses NDJSON and drops the source store id', () => {
    const lessons = parseLessonLines(
      '{"id":7,"work_id":"a","extracted_at":"t","payload":{"k":1}}\n' +
        '{"work_id":"b","extracted_at":"t2","commit_sha":"c","payload":{}}\n'
    );
    expect(lessons).toEqual([
      { work_id: 'a', extracted_at: 't', payload: { k: 1 } },
      { work_id: 'b', extracted_at: 't2', commit_sha: 'c', payload: {} },
    ]);
  });

  it('parses a JSON array and accepts empty input', () => {
    expect(parseLessonLines('[{"work_id":"a","extracted_at":"t","payload":{}}]')).toHaveLength(1);
    expect(parseLessonLines('')).toEqual([]);
    expect(parseLessonLines('  \n')).toEqual([]);
  });

  it('rejects a malformed line with its line number', () => {
    expect(() =>
      parseLessonLines('{"work_id":"a","extracted_at":"t","payload":{}}\n{broken')
    ).toThrow(/line 2/);
  });
});

describe('export-lessons / import-lessons round trip', () => {
  it('carries lessons from one store to a rebuilt one via --out NDJSON', async () => {
    const sourcePath = seedStore('source.db');
    const outFile = join(dir, 'lessons.ndjson');

    const exported = exportLessonsCommand({
      args: [],
      options: { json: true, verbose: false, store: sourcePath, out: outFile },
    });
    expect(exported.count).toBe(2);
    expect(exported.out).toBe(outFile);
    expect(readFileSync(outFile, 'utf8').trim().split('\n')).toHaveLength(2);

    // A "rebuilt" destination store: fresh schema, no lessons yet.
    const destPath = join(dir, 'dest.db');
    openStore(destPath).close();

    const imported = await importLessonsCommand({
      args: [],
      options: { json: true, verbose: false, store: destPath, file: outFile },
    });
    expect(imported).toEqual({ appended: 2, skipped: 0 });

    // Idempotent on re-import.
    const again = await importLessonsCommand({
      args: [],
      options: { json: true, verbose: false, store: destPath, file: outFile },
    });
    expect(again).toEqual({ appended: 0, skipped: 2 });

    const db = openStore(destPath);
    try {
      const lessons = allLessons(db);
      expect(lessons.map(l => l.work_id)).toEqual(['demo-1a2b', 'demo-2b3c']);
      expect(lessons[0].commit_sha).toBe('abc123');
      expect(lessons[0].payload).toEqual({
        root_cause: 'missing flag',
        resolution: 'add --no-tls',
      });
    } finally {
      db.close();
    }
  });

  it('export without --out returns lessons on the envelope only', () => {
    const sourcePath = seedStore('source2.db');
    const exported = exportLessonsCommand({
      args: [],
      options: { json: true, verbose: false, store: sourcePath },
    });
    expect(exported.out).toBeNull();
    expect(exported.lessons).toHaveLength(2);
  });

  it('import into a missing store is a loud user error', async () => {
    await expect(
      importLessonsCommand({
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
