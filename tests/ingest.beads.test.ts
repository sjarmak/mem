import { describe, it, expect } from 'vitest';
import {
  beadToWorkRecord,
  groupLabels,
  listRigs,
  parseAssignee,
  parseDoltRows,
  parseMetadata,
  readAllRigs,
  readRig,
  type DoltRow,
  type SqlRunner,
} from '../src/ingest/beads.js';

describe('parseAssignee', () => {
  it('splits a role-prefixed session id into role + agent_id', () => {
    expect(parseAssignee('polecat-gc-335825')).toEqual({
      agent_id: 'gc-335825',
      role: 'polecat',
    });
  });

  it('handles a multi-word role prefix', () => {
    expect(parseAssignee('mem-worker-gc-340057')).toEqual({
      agent_id: 'gc-340057',
      role: 'mem-worker',
    });
  });

  it('keeps a bare session id without a role', () => {
    expect(parseAssignee('gc-335825')).toEqual({ agent_id: 'gc-335825' });
  });

  it('falls back to the whole value when there is no session id', () => {
    expect(parseAssignee('control-dispatcher')).toEqual({ agent_id: 'control-dispatcher' });
  });

  it('returns null for empty/whitespace assignees', () => {
    expect(parseAssignee('')).toBeNull();
    expect(parseAssignee('   ')).toBeNull();
  });
});

describe('parseMetadata', () => {
  it('decodes a JSON-encoded object string', () => {
    expect(parseMetadata('{"gc.kind":"retry","gc.max_attempts":"3"}')).toEqual({
      'gc.kind': 'retry',
      'gc.max_attempts': '3',
    });
  });

  it('defaults empty/undefined to an empty object', () => {
    expect(parseMetadata(undefined)).toEqual({});
    expect(parseMetadata('')).toEqual({});
  });

  it('throws on malformed JSON rather than swallowing it', () => {
    expect(() => parseMetadata('{not json')).toThrow();
  });
});

describe('groupLabels', () => {
  it('groups labels by issue id', () => {
    const rows: DoltRow[] = [
      { issue_id: 'gc-1', label: 'phase1' },
      { issue_id: 'gc-1', label: 'epic' },
      { issue_id: 'gc-2', label: 'bug' },
    ];
    const grouped = groupLabels(rows);
    expect(grouped.get('gc-1')).toEqual(['phase1', 'epic']);
    expect(grouped.get('gc-2')).toEqual(['bug']);
  });

  it('skips rows missing a column', () => {
    const grouped = groupLabels([{ issue_id: 'gc-1' }, { label: 'orphan' }]);
    expect(grouped.size).toBe(0);
  });
});

describe('parseDoltRows', () => {
  it('extracts the rows array', () => {
    expect(parseDoltRows('{"rows": [{"id":"gc-1"},{"id":"gc-2"}]}')).toEqual([
      { id: 'gc-1' },
      { id: 'gc-2' },
    ]);
  });

  it('treats an empty result ({}) and empty string as no rows', () => {
    expect(parseDoltRows('{}')).toEqual([]);
    expect(parseDoltRows('   ')).toEqual([]);
  });
});

describe('beadToWorkRecord', () => {
  const fullRow: DoltRow = {
    id: 'gc-05qle',
    title: 'Finalize the work item',
    status: 'closed',
    assignee: 'polecat-gc-188186',
    external_ref: 'gh-1873',
    priority: '2',
    created_at: '2026-05-10 13:47:42',
    started_at: '2026-05-10 13:50:00',
    closed_at: '2026-05-10 14:06:00',
    metadata: '{"gc.kind":"retry"}',
  };

  it('maps a fully populated row to a validated spine', () => {
    const record = beadToWorkRecord(fullRow, 'gascity', ['phase1', 'epic']);
    expect(record.work_id).toBe('gc-05qle');
    expect(record.rig).toBe('gascity');
    expect(record.title).toBe('Finalize the work item');
    expect(record.labels).toEqual(['phase1', 'epic']);
    expect(record.metadata).toEqual({ 'gc.kind': 'retry' });
    expect(record.priority).toBe(2);
    expect(record.external_ref).toBe('gh-1873');
    expect(record.lifecycle).toEqual({
      created: '2026-05-10 13:47:42',
      started: '2026-05-10 13:50:00',
      closed: '2026-05-10 14:06:00',
      status: 'closed',
      status_history: [],
    });
    expect(record.agents).toEqual([{ agent_id: 'gc-188186', role: 'polecat' }]);
  });

  it('handles a minimal open bead (no assignee, ref, or timestamps)', () => {
    const record = beadToWorkRecord(
      { id: 'mem-1', title: 'scaffold', status: 'open', priority: '1', created_at: '2026-06-04' },
      'mem',
      []
    );
    expect(record.agents).toEqual([]);
    expect(record.external_ref).toBeUndefined();
    expect(record.lifecycle.started).toBeUndefined();
    expect(record.lifecycle.closed).toBeUndefined();
    expect(record.lifecycle.status).toBe('open');
    expect(record.metadata).toEqual({});
  });

  it('rejects a row missing the required id', () => {
    expect(() =>
      beadToWorkRecord({ title: 't', status: 'open', created_at: 'x' }, 'mem', [])
    ).toThrow();
  });
});

// A fake SQL runner backed by an in-memory fixture, keyed by `database::sql`.
function fakeRunner(fixtures: Record<string, DoltRow[]>): SqlRunner {
  return (database, sql) => {
    const table = sql.includes('information_schema.tables')
      ? 'rigs'
      : sql.includes('from labels')
        ? 'labels'
        : 'issues';
    return Promise.resolve(fixtures[`${database}::${table}`] ?? []);
  };
}

describe('readRig', () => {
  it('joins issues with their labels into WorkRecords', async () => {
    const run = fakeRunner({
      'gascity::issues': [
        { id: 'gc-1', title: 'a', status: 'open', priority: '2', created_at: '2026-06-01' },
        { id: 'gc-2', title: 'b', status: 'closed', priority: '1', created_at: '2026-06-02' },
      ],
      'gascity::labels': [
        { issue_id: 'gc-1', label: 'phase1' },
        { issue_id: 'gc-1', label: 'epic' },
      ],
    });
    const records = await readRig(run, 'gascity');
    expect(records).toHaveLength(2);
    expect(records[0].labels).toEqual(['phase1', 'epic']);
    expect(records[1].labels).toEqual([]);
  });
});

describe('listRigs', () => {
  it('returns sorted rig names from information_schema', async () => {
    const run = fakeRunner({
      'information_schema::rigs': [{ rig: 'mem' }, { rig: 'gascity' }, { rig: 'codeprobe' }],
    });
    expect(await listRigs(run)).toEqual(['codeprobe', 'gascity', 'mem']);
  });
});

describe('readAllRigs', () => {
  it('reads every rig listed and concatenates their records', async () => {
    const run = fakeRunner({
      'information_schema::rigs': [{ rig: 'mem' }, { rig: 'codeprobe' }],
      'codeprobe::issues': [
        { id: 'cp-1', title: 'x', status: 'open', priority: '2', created_at: '2026-06-01' },
      ],
      'mem::issues': [
        { id: 'mem-1', title: 'y', status: 'open', priority: '2', created_at: '2026-06-02' },
      ],
    });
    const records = await readAllRigs(run);
    expect(records.map(r => r.work_id).sort()).toEqual(['cp-1', 'mem-1']);
    expect(records.map(r => r.rig).sort()).toEqual(['codeprobe', 'mem']);
  });
});
