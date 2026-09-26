import { z } from 'zod';

export const PASSIVE_CALL_SCHEMA = 'bd-passive-call.v1' as const;
export const PASSIVE_CALL_LABELS = [
  'agent',
  'template',
  'rig',
  'runtime',
  'runtime_version',
  'model',
] as const;

const HASH = /^[0-9a-f]{16}$/;
const UTC_RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;
const TOKEN = /^[A-Za-z0-9][A-Za-z0-9-]*$/;
const FLAG_NAME = /^-{1,2}[A-Za-z0-9][A-Za-z0-9-]*$/;

export const PassiveCallLabelsSchema = z
  .object({
    agent: z.string().min(1),
    template: z.string().min(1),
    rig: z.string().min(1).nullable(),
    runtime: z.string().min(1),
    runtime_version: z.string().min(1).nullable(),
    model: z.string().min(1).nullable(),
  })
  .strict();

export const PassiveCallSchema = z
  .object({
    schema: z.literal(PASSIVE_CALL_SCHEMA),
    ts: z.iso.datetime({ offset: false }).regex(UTC_RFC3339, 'ts must include UTC seconds'),
    session: z.string().min(1),
    labels: PassiveCallLabelsSchema,
    origin: z.enum(['agent', 'hook']),
    verb: z.union([
      z.string().regex(TOKEN, 'verb must be one token'),
      z.enum(['<unknown>', '<flag>']),
    ]),
    flags: z.array(z.string().regex(FLAG_NAME, 'flags must contain names without values')),
    positional_count: z.number().int().nonnegative(),
    argv_chars: z.number().int().nonnegative(),
    key_hash: z.string().regex(HASH, 'key_hash must be 16 lowercase hex characters').nullable(),
    positional_hashes: z.array(
      z.string().regex(HASH, 'positional hashes must be 16 lowercase hex characters')
    ),
    exit: z.number().int().nonnegative(),
    duration_ms: z.number().int().nonnegative(),
  })
  .strict()
  .superRefine((line, ctx) => {
    const sortedFlags = [...line.flags].sort();
    const flagsAreCanonical = line.flags.every(
      (flag, index) => flag === sortedFlags[index] && flag !== line.flags[index - 1]
    );
    if (!flagsAreCanonical) {
      ctx.addIssue({
        code: 'custom',
        path: ['flags'],
        message: 'flags must be sorted and unique',
      });
    }
    if (line.positional_count !== line.positional_hashes.length) {
      ctx.addIssue({
        code: 'custom',
        path: ['positional_hashes'],
        message: 'positional_count must equal positional_hashes length',
      });
    }
  });

export type PassiveCallLabels = z.infer<typeof PassiveCallLabelsSchema>;
export type PassiveCall = z.infer<typeof PassiveCallSchema>;
