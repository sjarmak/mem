import {
  PASSIVE_CALL_LABELS,
  PassiveCallSchema,
  type PassiveCall,
  type PassiveCallLabels,
} from '../schemas/passive-call.js';
import { ZodError } from 'zod';

const READ_VERBS = new Set([
  'children',
  'count',
  'diff',
  'graph',
  'history',
  'list',
  'query',
  'ready',
  'recall',
  'search',
  'show',
  'stale',
  'status',
]);
const WRITE_VERBS = new Set([
  'assign',
  'close',
  'comment',
  'create',
  'delete',
  'edit',
  'note',
  'promote',
  'reopen',
  'remember',
  'set-state',
  'tag',
  'unclaim',
  'update',
]);

export interface CallsPerSession {
  min: number;
  max: number;
  mean: number;
}

export interface WriteReadJoins {
  matched_reads: number;
  distinct_keys: number;
}

export interface PassiveCallMetrics {
  calls: number;
  sessions: number;
  calls_per_session: CallsPerSession;
  verb_mix: Record<string, number>;
  refused_calls: number;
  write_read_joins: WriteReadJoins;
}

export type PassiveLabelBreakdown = {
  [K in keyof PassiveCallLabels]: Record<string, PassiveCallMetrics>;
};

export interface PassiveCallRollup {
  schema: 'bd-passive-rollup.v1';
  overall: PassiveCallMetrics;
  by_label: PassiveLabelBreakdown;
}

interface JoinState {
  writesBySession: readonly (readonly [string, readonly SeenWrite[]])[];
  matchedReads: number;
  matchedKeys: readonly string[];
}

interface SeenWrite {
  hash: string;
  ts: string;
}

function sortedCounts(values: readonly string[]): Record<string, number> {
  const unique = [...new Set(values)].sort();
  return Object.fromEntries(
    unique.map(value => [value, values.filter(item => item === value).length])
  );
}

function callKeys(call: PassiveCall): readonly string[] {
  return [
    ...new Set([...(call.key_hash === null ? [] : [call.key_hash]), ...call.positional_hashes]),
  ];
}

function compareUtcTimestamps(left: string, right: string): number {
  const [leftSecond, leftFraction = ''] = left.slice(0, -1).split('.');
  const [rightSecond, rightFraction = ''] = right.slice(0, -1).split('.');
  const secondOrder = leftSecond.localeCompare(rightSecond);
  if (secondOrder !== 0) return secondOrder;
  const width = Math.max(leftFraction.length, rightFraction.length);
  return leftFraction.padEnd(width, '0').localeCompare(rightFraction.padEnd(width, '0'));
}

function writeReadJoins(calls: readonly PassiveCall[]): WriteReadJoins {
  const ordered = calls
    .map((call, index) => ({ call, index }))
    .sort(
      (left, right) => compareUtcTimestamps(left.call.ts, right.call.ts) || left.index - right.index
    )
    .map(item => item.call);

  const state = ordered.reduce<JoinState>(
    (current, call) => {
      const keys = callKeys(call);
      if (call.exit !== 0) return current;
      if (WRITE_VERBS.has(call.verb)) {
        const existing =
          current.writesBySession.find(([session]) => session === call.session)?.[1] ?? [];
        return {
          ...current,
          writesBySession: [
            ...current.writesBySession.filter(([session]) => session !== call.session),
            [
              call.session,
              [
                ...existing,
                ...keys
                  .filter(
                    key => !existing.some(write => write.hash === key && write.ts === call.ts)
                  )
                  .map(hash => ({ hash, ts: call.ts })),
              ],
            ],
          ],
        };
      }
      if (!READ_VERBS.has(call.verb)) return current;

      const priorWrites =
        current.writesBySession.find(([session]) => session === call.session)?.[1] ?? [];
      const matches = keys.filter(key =>
        priorWrites.some(write => write.hash === key && compareUtcTimestamps(write.ts, call.ts) < 0)
      );
      if (matches.length === 0) return current;
      return {
        ...current,
        matchedReads: current.matchedReads + 1,
        matchedKeys: [...new Set([...current.matchedKeys, ...matches])],
      };
    },
    { writesBySession: [], matchedReads: 0, matchedKeys: [] }
  );

  return { matched_reads: state.matchedReads, distinct_keys: state.matchedKeys.length };
}

function metrics(calls: readonly PassiveCall[]): PassiveCallMetrics {
  const perSession = Object.values(sortedCounts(calls.map(call => call.session)));
  const callCount = calls.length;
  return {
    calls: callCount,
    sessions: perSession.length,
    calls_per_session: {
      min: perSession.length === 0 ? 0 : Math.min(...perSession),
      max: perSession.length === 0 ? 0 : Math.max(...perSession),
      mean: perSession.length === 0 ? 0 : callCount / perSession.length,
    },
    verb_mix: sortedCounts(calls.map(call => call.verb)),
    refused_calls: calls.filter(call => call.exit !== 0).length,
    write_read_joins: writeReadJoins(calls),
  };
}

function breakdownFor(
  calls: readonly PassiveCall[],
  label: keyof PassiveCallLabels
): Record<string, PassiveCallMetrics> {
  const values = [...new Set(calls.map(call => call.labels[label]))].sort();
  return Object.fromEntries(
    values.map(value => [value, metrics(calls.filter(call => call.labels[label] === value))])
  );
}

export function rollupPassiveCalls(calls: readonly PassiveCall[]): PassiveCallRollup {
  const byLabel = Object.fromEntries(
    PASSIVE_CALL_LABELS.map(label => [label, breakdownFor(calls, label)])
  ) as PassiveLabelBreakdown;
  return { schema: 'bd-passive-rollup.v1', overall: metrics(calls), by_label: byLabel };
}

export function parsePassiveCallLines(input: string): PassiveCall[] {
  const trimmed = input.trim();
  if (trimmed === '') return [];
  return trimmed.split('\n').map((line, index) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      throw new Error(`invalid passive call on line ${index + 1}: invalid JSON`);
    }
    try {
      return PassiveCallSchema.parse(parsed);
    } catch (error: unknown) {
      const locations =
        error instanceof ZodError
          ? [...new Set(error.issues.map(issue => issue.path.join('.') || '<root>'))].join(', ')
          : '<root>';
      throw new Error(
        `invalid passive call on line ${index + 1}: schema validation failed at ${locations}`
      );
    }
  });
}
