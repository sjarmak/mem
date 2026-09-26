import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import * as hegel from '@hegeldev/hegel';
import * as gs from '@hegeldev/hegel/generators';
import { afterEach, describe, expect, it } from 'vitest';

import { passiveRollupCommand } from '../src/cli/commands/passive-rollup.js';
import { parsePassiveCallLines, rollupPassiveCalls } from '../src/ingest/passive-call-rollup.js';
import {
  PASSIVE_CALL_SCHEMA,
  PassiveCallSchema,
  type PassiveCall,
} from '../src/schemas/passive-call.js';

const labels = {
  agent: 'mem-worker',
  template: 'worker-pool',
  rig: 'mem',
  runtime: 'codex',
  runtime_version: '0.155.1',
  model: 'gpt-5.6-sol',
};

function call(overrides: Partial<PassiveCall> = {}): PassiveCall {
  return PassiveCallSchema.parse({
    schema: PASSIVE_CALL_SCHEMA,
    ts: '2026-09-23T12:00:00.000Z',
    session: 'gc-1',
    labels,
    origin: 'agent',
    verb: 'list',
    flags: ['--json'],
    positional_count: 0,
    argv_chars: 11,
    key_hash: null,
    positional_hashes: [],
    exit: 0,
    duration_ms: 12,
    ...overrides,
  });
}

describe('bd-passive-call.v1 schema', () => {
  it('accepts the exact privacy-safe line format', () => {
    const parsed = call({
      verb: 'update',
      flags: ['--claim', '--json'],
      positional_count: 1,
      key_hash: '0123456789abcdef',
      positional_hashes: ['fedcba9876543210'],
    });

    expect(parsed.schema).toBe('bd-passive-call.v1');
  });

  it('rejects raw output or argument fields', () => {
    expect(() => PassiveCallSchema.parse({ ...call(), stdout: 'secret bead contents' })).toThrow();
    expect(() => PassiveCallSchema.parse({ ...call(), argv: ['list', '--json'] })).toThrow();
  });

  it('rejects malformed hashes, non-UTC timestamps, and unsorted flags', () => {
    expect(() => PassiveCallSchema.parse({ ...call(), key_hash: 'raw-key' })).toThrow();
    expect(() => PassiveCallSchema.parse({ ...call(), ts: '2026-09-23T08:00:00-04:00' })).toThrow();
    expect(() => PassiveCallSchema.parse({ ...call(), ts: '2026-02-31T08:00:00Z' })).toThrow();
    expect(() => PassiveCallSchema.parse({ ...call(), ts: '2026-09-23T08:00Z' })).toThrow();
    expect(() => PassiveCallSchema.parse({ ...call(), flags: ['--json', '--all'] })).toThrow();
    expect(() => PassiveCallSchema.parse({ ...call(), verb: '--json' })).toThrow();
    expect(() => PassiveCallSchema.parse({ ...call(), verb: 'show raw-value' })).toThrow();
  });

  it('requires one positional hash per positional argument', () => {
    expect(() =>
      PassiveCallSchema.parse({ ...call(), positional_count: 1, positional_hashes: [] })
    ).toThrow();
  });

  it.each(['rig', 'runtime_version', 'model'] as const)('accepts a null %s label', label => {
    expect(call({ labels: { ...labels, [label]: null } }).labels[label]).toBeNull();
  });

  it.each(['agent', 'template', 'runtime'] as const)('keeps %s non-nullable', label => {
    expect(() =>
      PassiveCallSchema.parse({ ...call(), labels: { ...labels, [label]: null } })
    ).toThrow();
  });

  it.each(['<unknown>', '<flag>'])('accepts the %s verb sentinel', verb => {
    expect(call({ verb }).verb).toBe(verb);
  });

  it('rejects unrecognized angle-bracket verbs', () => {
    expect(() => PassiveCallSchema.parse({ ...call(), verb: '<other>' })).toThrow();
  });

  it('keeps every generated old-schema line valid', () =>
    hegel.test(tc => {
      const nonEmpty = gs.text({
        alphabet: 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/',
        minSize: 1,
        maxSize: 24,
      });
      const hash = gs.fromRegex('[0-9a-f]{16}');
      const positional_hashes = tc.draw(gs.arrays(hash, { maxSize: 4 }));
      const flags = [
        ...tc.draw(
          gs.arrays(gs.fromRegex('--[A-Za-z0-9][A-Za-z0-9-]{0,12}'), {
            maxSize: 4,
            unique: true,
          })
        ),
      ].sort();
      const verb = tc.draw(gs.fromRegex('[A-Za-z0-9][A-Za-z0-9-]{0,20}'));
      const origin = tc.draw(gs.sampledFrom(['agent', 'hook'] as const));
      const exit = tc.draw(gs.integers({ minValue: 0, maxValue: 255 }));
      const duration_ms = tc.draw(gs.integers({ minValue: 0, maxValue: 60_000 }));
      const oldValidLine = {
        ...call(),
        session: tc.draw(nonEmpty),
        origin,
        verb,
        flags,
        positional_count: positional_hashes.length,
        argv_chars: tc.draw(gs.integers({ minValue: 0, maxValue: 10_000 })),
        key_hash: tc.draw(gs.optional(hash)),
        positional_hashes,
        exit,
        duration_ms,
        labels: {
          agent: tc.draw(nonEmpty),
          template: tc.draw(nonEmpty),
          rig: tc.draw(nonEmpty),
          runtime: tc.draw(nonEmpty),
          runtime_version: tc.draw(nonEmpty),
          model: tc.draw(nonEmpty),
        },
      };

      expect(PassiveCallSchema.safeParse(oldValidLine).success).toBe(true);
    }));
});

describe('passive call rollup', () => {
  const calls = [
    call({
      ts: '2026-09-23T12:00:00Z',
      verb: 'create',
      positional_count: 1,
      positional_hashes: ['aaaaaaaaaaaaaaaa'],
    }),
    call({
      ts: '2026-09-23T12:01:00Z',
      verb: 'show',
      positional_count: 1,
      positional_hashes: ['aaaaaaaaaaaaaaaa'],
    }),
    call({
      ts: '2026-09-23T12:02:00Z',
      verb: 'update',
      positional_count: 1,
      positional_hashes: ['bbbbbbbbbbbbbbbb'],
      exit: 1,
    }),
    call({ ts: '2026-09-23T12:03:00Z' }),
    call({
      ts: '2026-09-23T12:04:00Z',
      session: 'gc-2',
      labels: { ...labels, agent: 'reviewer', model: 'gpt-6-astra' },
      verb: 'show',
      positional_count: 1,
      positional_hashes: ['aaaaaaaaaaaaaaaa'],
    }),
  ];

  it('computes calls/session, verb mix, refused calls, and session-local temporal joins', () => {
    const result = rollupPassiveCalls(calls);

    expect(result.schema).toBe('bd-passive-rollup.v1');
    expect(result.overall).toEqual({
      calls: 5,
      sessions: 2,
      calls_per_session: { min: 1, max: 4, mean: 2.5 },
      verb_mix: { create: 1, list: 1, show: 2, update: 1 },
      refused_calls: 1,
      write_read_joins: { matched_reads: 1, distinct_keys: 1 },
    });
  });

  it('breaks the same metrics down independently by every label', () => {
    const result = rollupPassiveCalls(calls);

    expect(Object.keys(result.by_label)).toEqual([
      'agent',
      'template',
      'rig',
      'runtime',
      'runtime_version',
      'model',
    ]);
    expect(result.by_label.agent['mem-worker']?.calls).toBe(4);
    expect(result.by_label.agent.reviewer?.calls).toBe(1);
    expect(result.by_label.model['gpt-5.6-sol']?.refused_calls).toBe(1);
    expect(result.by_label.model['gpt-6-astra']?.write_read_joins.matched_reads).toBe(0);
  });

  it('sorts by timestamp before evaluating write-to-read direction', () => {
    expect(rollupPassiveCalls([calls[1], calls[0]]).overall.write_read_joins).toEqual({
      matched_reads: 1,
      distinct_keys: 1,
    });
  });

  it('orders mixed RFC3339 fractional precision chronologically', () => {
    const write = call({
      ts: '2026-09-23T12:00:00Z',
      verb: 'update',
      positional_count: 1,
      positional_hashes: ['eeeeeeeeeeeeeeee'],
    });
    const read = call({
      ts: '2026-09-23T12:00:00.100Z',
      verb: 'show',
      positional_count: 1,
      positional_hashes: ['eeeeeeeeeeeeeeee'],
    });

    expect(rollupPassiveCalls([read, write]).overall.write_read_joins.matched_reads).toBe(1);
  });

  it('preserves sub-millisecond order and never infers order from equal timestamps', () => {
    const write = call({
      ts: '2026-09-23T12:00:00.0001Z',
      verb: 'update',
      positional_count: 1,
      positional_hashes: ['ffffffffffffffff'],
    });
    const laterRead = call({
      ts: '2026-09-23T12:00:00.0002Z',
      verb: 'show',
      positional_count: 1,
      positional_hashes: ['ffffffffffffffff'],
    });
    const simultaneousRead = call({ ...laterRead, ts: write.ts });

    expect(rollupPassiveCalls([laterRead, write]).overall.write_read_joins.matched_reads).toBe(1);
    expect(
      rollupPassiveCalls([write, simultaneousRead]).overall.write_read_joins.matched_reads
    ).toBe(0);
  });

  it('counts opaque strings that match object prototype property names', () => {
    const result = rollupPassiveCalls([
      call({ session: '__proto__', verb: 'constructor' }),
      call({ session: '__proto__', verb: 'constructor' }),
    ]);

    expect(result.overall.sessions).toBe(1);
    expect(result.overall.verb_mix.constructor).toBe(2);
  });

  it('does not classify ambiguous management verbs as reads', () => {
    const write = call({
      verb: 'update',
      positional_count: 1,
      positional_hashes: ['abababababababab'],
    });
    const ambiguous = call({
      ts: '2026-09-23T12:01:00Z',
      verb: 'comments',
      positional_count: 1,
      positional_hashes: ['abababababababab'],
    });

    expect(rollupPassiveCalls([write, ambiguous]).overall.write_read_joins.matched_reads).toBe(0);
  });

  it('does not join refused writes or reads', () => {
    const failedWrite = call({
      verb: 'update',
      positional_count: 1,
      positional_hashes: ['cccccccccccccccc'],
      exit: 1,
    });
    const successfulRead = call({
      ts: '2026-09-23T12:01:00Z',
      verb: 'show',
      positional_count: 1,
      positional_hashes: ['cccccccccccccccc'],
    });
    const successfulWrite = call({
      ts: '2026-09-23T12:02:00Z',
      verb: 'update',
      positional_count: 1,
      positional_hashes: ['dddddddddddddddd'],
    });
    const failedRead = call({
      ts: '2026-09-23T12:03:00Z',
      verb: 'show',
      positional_count: 1,
      positional_hashes: ['dddddddddddddddd'],
      exit: 1,
    });

    expect(
      rollupPassiveCalls([failedWrite, successfulRead, successfulWrite, failedRead]).overall
        .write_read_joins
    ).toEqual({ matched_reads: 0, distinct_keys: 0 });
  });

  it('buckets nullable labels under a visible null key', () => {
    const result = rollupPassiveCalls([
      call({ labels: { ...labels, rig: null, runtime_version: null, model: null } }),
      call({ labels: { ...labels, rig: 'null' } }),
    ]);

    expect(result.by_label.rig['<null>']?.calls).toBe(1);
    expect(result.by_label.rig.null?.calls).toBe(1);
    expect(result.by_label.runtime_version['<null>']?.calls).toBe(1);
    expect(result.by_label.model['<null>']?.calls).toBe(1);
  });

  it('counts verb sentinels without treating them as reads or writes', () => {
    const result = rollupPassiveCalls([
      call({
        verb: 'update',
        positional_count: 1,
        positional_hashes: ['abababababababab'],
      }),
      call({
        ts: '2026-09-23T12:01:00Z',
        verb: '<unknown>',
        positional_count: 1,
        positional_hashes: ['abababababababab'],
      }),
      call({
        ts: '2026-09-23T12:02:00Z',
        verb: '<flag>',
        positional_count: 1,
        positional_hashes: ['abababababababab'],
      }),
    ]);

    expect(result.overall.verb_mix).toEqual({ '<flag>': 1, '<unknown>': 1, update: 1 });
    expect(result.overall.write_read_joins).toEqual({ matched_reads: 0, distinct_keys: 0 });
  });
});

describe('passive rollup input and command', () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it('reports a malformed JSONL line with its line number', () => {
    expect(() => parsePassiveCallLines(`${JSON.stringify(call())}\n{"schema":`)).toThrow(
      /invalid passive call on line 2/
    );
  });

  it('never echoes malformed raw input in validation errors', () => {
    const secret = 'PRIVATE_RAW_VALUE';
    for (const input of [secret, `{\"${secret}\":true}`, `{\"verb\":\"${secret}\"}`]) {
      try {
        parsePassiveCallLines(input);
        throw new Error('expected invalid input to be rejected');
      } catch (error: unknown) {
        expect(String(error)).not.toContain(secret);
      }
    }
  });

  it('rolls up one or more JSONL files through the CLI command', async () => {
    dir = mkdtempSync(join(tmpdir(), 'mem-passive-rollup-'));
    const first = join(dir, 'first.jsonl');
    const second = join(dir, 'second.jsonl');
    writeFileSync(first, `${JSON.stringify(call({ session: 'gc-1' }))}\n`);
    writeFileSync(second, `${JSON.stringify(call({ session: 'gc-2' }))}\n`);

    const result = await passiveRollupCommand({
      args: [first, second],
      options: { json: true, verbose: false },
    });

    expect(result.overall.calls).toBe(2);
    expect(result.overall.sessions).toBe(2);
  });

  it('parses and rolls up a mixed legacy and relaxed-format file', () => {
    const input = [
      call(),
      call({
        session: 'gc-2',
        labels: { ...labels, rig: null, runtime_version: null, model: null },
        verb: '<unknown>',
      }),
      call({ session: 'gc-3', verb: '<flag>' }),
    ]
      .map(line => JSON.stringify(line))
      .join('\n');

    const result = rollupPassiveCalls(parsePassiveCallLines(input));

    expect(result.overall.calls).toBe(3);
    expect(result.overall.verb_mix).toEqual({ '<flag>': 1, '<unknown>': 1, list: 1 });
    expect(result.by_label.model['<null>']?.calls).toBe(1);
  });

  it('requires at least one input file', async () => {
    await expect(
      passiveRollupCommand({ args: [], options: { json: true, verbose: false } })
    ).rejects.toThrow(/passive-rollup FILE/);
  });
});
