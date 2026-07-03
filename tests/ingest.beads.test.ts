import { describe, it, expect } from 'vitest';
import {
  assertIdentifier,
  beadToWorkRecord,
  epicParent,
  groupLabels,
  groupLinks,
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

  it('throws on valid JSON that is not an object', () => {
    expect(() => parseMetadata('[1,2]')).toThrow(/not a JSON object/);
    expect(() => parseMetadata('"a string"')).toThrow(/not a JSON object/);
  });
});

describe('assertIdentifier', () => {
  it('accepts real rig db names (alphanumeric, underscore, hyphen)', () => {
    expect(() => assertIdentifier('gascity_dashboard')).not.toThrow();
    expect(() => assertIdentifier('code-intel-digest')).not.toThrow();
    expect(() => assertIdentifier('information_schema')).not.toThrow();
  });

  it('rejects names that could break out of backtick quoting', () => {
    expect(() => assertIdentifier('bad`name')).toThrow(/Unsafe SQL identifier/);
    expect(() => assertIdentifier('a; drop table issues')).toThrow(/Unsafe SQL identifier/);
    expect(() => assertIdentifier('')).toThrow(/Unsafe SQL identifier/);
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

describe('epicParent', () => {
  it('derives the epic parent from a dotted child id', () => {
    expect(epicParent('mem-lvp.1')).toBe('mem-lvp');
    expect(epicParent('mem-lvp.12')).toBe('mem-lvp');
    expect(epicParent('mem-0rrf.3')).toBe('mem-0rrf');
  });

  it('returns undefined for an undotted id (the epic itself)', () => {
    expect(epicParent('mem-lvp')).toBeUndefined();
    expect(epicParent('gc-05qle')).toBeUndefined();
  });

  it('only strips a numeric child suffix (the beads epic convention)', () => {
    expect(epicParent('mem-x.final')).toBeUndefined();
    expect(epicParent('mem-a.1.2')).toBe('mem-a.1');
  });
});

describe('groupLinks', () => {
  it('maps parent-child edges to the child links.parent', () => {
    const links = groupLinks([
      { issue_id: 'mem-lvp.4', type: 'parent-child', depends_on_issue_id: 'mem-lvp' },
    ]);
    expect(links.get('mem-lvp.4')).toEqual({ deps: [], supersedes: [], parent: 'mem-lvp' });
  });

  it('maps tracks edges to the tracked member links.convoy_id', () => {
    // (issue_id = the convoy bead, depends_on = the member it tracks)
    const links = groupLinks([
      { issue_id: 'mem-blas', type: 'tracks', depends_on_issue_id: 'mem-bxhh.3' },
    ]);
    expect(links.get('mem-bxhh.3')).toEqual({ deps: [], supersedes: [], convoy_id: 'mem-blas' });
    expect(links.has('mem-blas')).toBe(false);
  });

  it('maps supersedes edges to links.supersedes', () => {
    const links = groupLinks([
      { issue_id: 'mem-new', type: 'supersedes', depends_on_issue_id: 'mem-old' },
    ]);
    expect(links.get('mem-new')).toEqual({ deps: [], supersedes: ['mem-old'] });
  });

  it('maps every other dependency type to links.deps, sorted and deduped', () => {
    const links = groupLinks([
      { issue_id: 'mem-a', type: 'blocks', depends_on_issue_id: 'mem-z' },
      { issue_id: 'mem-a', type: 'discovered-from', depends_on_issue_id: 'mem-b' },
      { issue_id: 'mem-a', type: 'related', depends_on_issue_id: 'mem-z' },
    ]);
    expect(links.get('mem-a')).toEqual({ deps: ['mem-b', 'mem-z'], supersedes: [] });
  });

  it('skips rows missing a column', () => {
    const links = groupLinks([
      { issue_id: 'mem-a', type: 'blocks' },
      { type: 'blocks', depends_on_issue_id: 'mem-b' },
    ]);
    expect(links.size).toBe(0);
  });

  it('keeps the sorted-first value and warns when parent or convoy conflict', () => {
    const warnings: string[] = [];
    const links = groupLinks(
      [
        { issue_id: 'mem-c', type: 'parent-child', depends_on_issue_id: 'mem-p2' },
        { issue_id: 'mem-c', type: 'parent-child', depends_on_issue_id: 'mem-p1' },
        { issue_id: 'cv-2', type: 'tracks', depends_on_issue_id: 'mem-m' },
        { issue_id: 'cv-1', type: 'tracks', depends_on_issue_id: 'mem-m' },
      ],
      message => warnings.push(message)
    );
    expect(links.get('mem-c')?.parent).toBe('mem-p1');
    expect(links.get('mem-m')?.convoy_id).toBe('cv-1');
    expect(warnings).toHaveLength(2);
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

  it('carries explicit dependency links into record.links', () => {
    const record = beadToWorkRecord(fullRow, 'gascity', [], {
      deps: ['gc-aaa'],
      supersedes: ['gc-old'],
      convoy_id: 'gc-cv',
    });
    expect(record.links).toEqual({
      deps: ['gc-aaa'],
      supersedes: ['gc-old'],
      convoy_id: 'gc-cv',
    });
  });

  it('derives the epic parent from a dotted id when no explicit parent edge exists', () => {
    const record = beadToWorkRecord({ ...fullRow, id: 'mem-lvp.12' }, 'mem', []);
    expect(record.links.parent).toBe('mem-lvp');
  });

  it('prefers an explicit parent-child edge over the dotted-id derivation', () => {
    const record = beadToWorkRecord({ ...fullRow, id: 'mem-lvp.12' }, 'mem', [], {
      deps: [],
      supersedes: [],
      parent: 'mem-other',
    });
    expect(record.links.parent).toBe('mem-other');
  });

  it('leaves links empty for an undotted id with no dependency rows', () => {
    const record = beadToWorkRecord(fullRow, 'gascity', []);
    expect(record.links).toEqual({ deps: [], supersedes: [] });
  });

  it('degrades malformed metadata to {} with a warning instead of throwing', () => {
    const warnings: string[] = [];
    const record = beadToWorkRecord(
      { ...fullRow, metadata: '{not json' },
      'gascity',
      [],
      undefined,
      message => warnings.push(message)
    );
    expect(record.work_id).toBe('gc-05qle');
    expect(record.metadata).toEqual({});
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/gascity\/gc-05qle/);
    expect(warnings[0]).toMatch(/malformed bead metadata/);
  });

  it('degrades non-object metadata to {} with a warning', () => {
    const warnings: string[] = [];
    const record = beadToWorkRecord(
      { ...fullRow, metadata: '[1,2]' },
      'gascity',
      [],
      undefined,
      message => warnings.push(message)
    );
    expect(record.metadata).toEqual({});
    expect(warnings).toHaveLength(1);
  });
});

// A fake SQL runner backed by an in-memory fixture, keyed by `database::sql`.
function fakeRunner(fixtures: Record<string, DoltRow[]>): SqlRunner {
  return (database, sql) => {
    const table = sql.includes('information_schema.tables')
      ? 'rigs'
      : sql.includes('from labels')
        ? 'labels'
        : sql.includes('from dependencies')
          ? 'dependencies'
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

  it('joins the dependencies table into record.links', async () => {
    const run = fakeRunner({
      'mem::issues': [
        { id: 'mem-cv', title: 'convoy', status: 'open', priority: '2', created_at: '2026-06-01' },
        { id: 'mem-a', title: 'a', status: 'open', priority: '2', created_at: '2026-06-01' },
        { id: 'mem-b', title: 'b', status: 'closed', priority: '1', created_at: '2026-06-02' },
      ],
      'mem::dependencies': [
        { issue_id: 'mem-a', type: 'blocks', depends_on_issue_id: 'mem-b' },
        { issue_id: 'mem-cv', type: 'tracks', depends_on_issue_id: 'mem-a' },
        { issue_id: 'mem-b', type: 'supersedes', depends_on_issue_id: 'mem-z' },
      ],
    });
    const records = await readRig(run, 'mem');
    const byId = new Map(records.map(r => [r.work_id, r]));
    expect(byId.get('mem-a')?.links).toEqual({
      deps: ['mem-b'],
      supersedes: [],
      convoy_id: 'mem-cv',
    });
    expect(byId.get('mem-b')?.links).toEqual({ deps: [], supersedes: ['mem-z'] });
    expect(byId.get('mem-cv')?.links).toEqual({ deps: [], supersedes: [] });
  });

  it('survives a single bead with malformed metadata (warns, keeps the rest)', async () => {
    const run = fakeRunner({
      'gascity::issues': [
        { id: 'gc-1', title: 'a', status: 'open', priority: '2', created_at: '2026-06-01' },
        {
          id: 'gc-2',
          title: 'b',
          status: 'open',
          priority: '1',
          created_at: '2026-06-02',
          metadata: '{broken',
        },
      ],
    });
    const warnings: string[] = [];
    const records = await readRig(run, 'gascity', message => warnings.push(message));
    expect(records.map(r => r.work_id)).toEqual(['gc-1', 'gc-2']);
    expect(records[1].metadata).toEqual({});
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/gc-2/);
  });
});

describe('listRigs', () => {
  it('returns sorted rig names from information_schema', async () => {
    const run = fakeRunner({
      'information_schema::rigs': [{ rig: 'mem' }, { rig: 'gascity' }, { rig: 'codeprobe' }],
    });
    expect(await listRigs(run)).toEqual(['codeprobe', 'gascity', 'mem']);
  });

  it('excludes system schemas and gascity test-suite leak databases', async () => {
    // The shared dolt server accumulates ephemeral databases from gascity's
    // test suites (testdb_<hex>, test_<feature>_<port>, fixdepkeys_<hex>) plus
    // dolt/MySQL system schemas. They carry an `issues` table — sometimes a
    // schema-conformant one — so they must be excluded structurally, not
    // trusted: projecting their fixture beads would corrupt the eval corpus.
    const run = fakeRunner({
      'information_schema::rigs': [
        { rig: 'mem' },
        { rig: 'gascity' },
        { rig: 'mysql' },
        { rig: 'sys' },
        { rig: 'testdb_8212308f205f_shared' },
        { rig: 'testdb_ae2dea539651_a' },
        { rig: 'test_cloud_auth_route_0_32819' },
        { rig: 'test_federation_credentials_32825' },
        { rig: 'test_guard_both_32819' },
        { rig: 'fixdepkeys_489f1e277a97' },
        { rig: '__gc_probe' },
        { rig: 'dolt_pkg_shared' },
      ],
    });
    expect(await listRigs(run)).toEqual(['gascity', 'mem']);
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
