import { readFile } from 'node:fs/promises';

import { CommandContext } from '../index.js';
import {
  parsePassiveCallLines,
  rollupPassiveCalls,
  type PassiveCallRollup,
} from '../../ingest/passive-call-rollup.js';

export async function passiveRollupCommand(ctx: CommandContext): Promise<PassiveCallRollup> {
  if (ctx.args.length === 0) {
    throw new Error('usage: mem passive-rollup FILE [FILE ...]');
  }
  const contents = await Promise.all(ctx.args.map(path => readFile(path, 'utf8')));
  const calls = contents.flatMap(parsePassiveCallLines);
  const result = rollupPassiveCalls(calls);

  if (!ctx.options.json) console.log(JSON.stringify(result, null, 2));
  return result;
}
