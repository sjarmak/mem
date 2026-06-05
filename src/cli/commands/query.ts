import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import { getRecord, queryRecords, type RecordFilter } from '../../store/index.js';
import type { WorkRecord } from '../../schemas/workrecord.js';

export interface QueryResult {
  count: number;
  records: WorkRecord[];
}

type OptionValue = string | boolean | undefined;

/** Require a string value for a flag that takes one; a bare `--flag` throws. */
function asString(value: OptionValue, flag: string): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw new Error(`--${flag} requires a value`);
  return value;
}

/** Require a value drawn from a fixed set — fail fast on a typo rather than
 * silently building a filter that matches nothing. */
function asEnum<T extends string>(
  value: OptionValue,
  allowed: readonly T[],
  flag: string
): T | undefined {
  const str = asString(value, flag);
  if (str === undefined) return undefined;
  if (!allowed.includes(str as T)) {
    throw new Error(`--${flag} must be one of: ${allowed.join(', ')}`);
  }
  return str as T;
}

/** Build a validated {@link RecordFilter} from the CLI options. */
function buildFilter(options: CommandContext['options']): RecordFilter {
  const rig = asString(options.rig, 'rig');
  const status = asString(options.status, 'status');
  const agent = asString(options.agent, 'agent');
  const ci = asEnum(options.ci, ['pass', 'fail'] as const, 'ci');
  const prState = asEnum(options['pr-state'], ['merged', 'closed'] as const, 'pr-state');
  const closedBefore = asString(options['closed-before'], 'closed-before');

  return {
    ...(rig !== undefined && { rig }),
    ...(status !== undefined && { status }),
    ...(agent !== undefined && { agent }),
    ...(ci !== undefined && { ci }),
    ...(prState !== undefined && { pr_state: prState }),
    ...(closedBefore !== undefined && { closedBefore }),
  };
}

/**
 * `mem query [<work_id>] [--rig R] [--agent A] [--status S] [--ci pass|fail]
 *  [--pr-state merged|closed] [--closed-before T] [--store PATH]`
 *
 * Query the work-audit graph by the four axes the bead names — work_id (exact,
 * via the positional), or agent / rig / outcome (and status / temporal) via the
 * promoted-column filters. A positional work_id and filters are mutually
 * exclusive: the id is an exact lookup, filters are a scan. Output ordering is
 * the reader's deterministic `ORDER BY work_id`.
 */
export function queryCommand(ctx: CommandContext): QueryResult {
  const filter = buildFilter(ctx.options);
  const workId = ctx.args[0];
  if (workId !== undefined && Object.keys(filter).length > 0) {
    throw new Error('query takes either a work_id or filters, not both');
  }

  const records = withReadStore(ctx.options, (db): WorkRecord[] => {
    if (workId !== undefined) {
      const record = getRecord(db, workId);
      return record ? [record] : [];
    }
    return queryRecords(db, filter);
  });

  if (!ctx.options.json) {
    for (const r of records) {
      console.error(`${r.work_id}\t${r.rig}\t${r.lifecycle.status}\t${r.title}`);
    }
    console.error(`${records.length} record(s)`);
  }

  return { count: records.length, records };
}
