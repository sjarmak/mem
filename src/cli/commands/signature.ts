import { CommandContext } from '../index.js';
import { withReadStore } from '../store.js';
import { workIdsBySignature } from '../../store/index.js';

export interface SignatureResult {
  signature: string;
  count: number;
  work_ids: string[];
}

/**
 * `mem signature <signature> [--store PATH]` — the bead ids whose traces carry
 * a given failure signature (the Decision-8 retrieval key, e.g.
 * `tsc:src/a.ts:12:TS2345`), sorted. Exact-match only — the failure-triggered
 * retrieval that composes this with the FTS tiebreaker is Phase-2 policy.
 */
export function signatureCommand(ctx: CommandContext): SignatureResult {
  const signature = ctx.args[0];
  if (signature === undefined) {
    throw new Error('signature requires a value: mem signature <signature>');
  }

  const workIds = withReadStore(ctx.options, db => workIdsBySignature(db, signature));

  if (!ctx.options.json) {
    for (const id of workIds) console.error(id);
    console.error(`${workIds.length} work id(s) for ${signature}`);
  }

  return { signature, count: workIds.length, work_ids: workIds };
}
