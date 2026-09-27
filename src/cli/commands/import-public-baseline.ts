import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';

import { z } from 'zod';

import type { WorkRecord } from '../../schemas/workrecord.js';
import { WorkRecordSchema } from '../../schemas/workrecord.js';
import { importLessons, openStore, queryRecords, type LessonInput } from '../../store/index.js';
import type { CommandContext } from '../index.js';
import { readStdin } from '../io.js';
import { storePath } from '../store.js';
import { buildStoreFromRecords } from './build-store.js';

const PublicIdSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]*$/);
const PublicAliasSchema = z.string().regex(/^k-[0-9a-f]+$/);
const PublicTimestampSchema = z.string().datetime({ offset: true });
const NullableKeySchema = z.string().nullable();
const PublicWorkRefSchema = z.object({
  id: z.string().min(1),
  closed: PublicTimestampSchema.nullable().optional(),
  convoy_key: NullableKeySchema,
  pr_key: NullableKeySchema,
  external_ref_key: NullableKeySchema,
  parent_key: NullableKeySchema,
});
const PublicBaselineRecordSchema = z.object({
  record_id: PublicIdSchema,
  question: z.object({
    asked_at: PublicTimestampSchema,
    scope_id: z.string().min(1),
    text: z.string().min(1),
  }),
  provenance: z.object({ world_id: z.string().min(1) }),
  candidate_pool: z.object({ ids: z.array(PublicAliasSchema) }),
  loo: z.object({
    boundary: PublicTimestampSchema,
    candidates: z.array(PublicWorkRefSchema),
    query: PublicWorkRefSchema,
  }),
  evidence: z.object({
    gold: z.record(z.string(), z.string()),
    distractors: z.record(z.string(), z.string()),
    superseded: z.record(z.string(), z.string()),
  }),
});

type PublicBaselineRecord = z.infer<typeof PublicBaselineRecordSchema>;

export interface PublicBaselineConversion {
  records: WorkRecord[];
  lessons: LessonInput[];
  queries: number;
  candidates: number;
}

export interface ImportPublicBaselineCommandResult {
  imported: number;
  queries: number;
  candidates: number;
  lessons_appended: number;
  lessons_skipped: number;
  store: string;
}

export function publicBaselineRig(recordId: string): string {
  return `public-baseline/${PublicIdSchema.parse(recordId)}`;
}

export function publicBaselineQueryWorkId(recordId: string): string {
  return `${publicBaselineRig(recordId)}/query`;
}

export function publicBaselineCandidateWorkId(recordId: string, alias: string): string {
  return `${publicBaselineRig(recordId)}/candidate/${PublicAliasSchema.parse(alias)}`;
}

export function publicBaselineAliasFromWorkId(recordId: string, workId: string): string {
  const prefix = `${publicBaselineRig(recordId)}/candidate/`;
  if (!workId.startsWith(prefix)) {
    throw new Error(`work id ${workId} is outside public baseline record namespace ${recordId}`);
  }
  return PublicAliasSchema.parse(workId.slice(prefix.length));
}

function publicBaselineSearchToken(recordId: string): string {
  return `publicbaseline${createHash('sha256').update(recordId).digest('hex')}`;
}

function payloadsByAlias(record: PublicBaselineRecord): Map<string, string> {
  const entries = [
    ...Object.entries(record.evidence.gold),
    ...Object.entries(record.evidence.distractors),
    ...Object.entries(record.evidence.superseded),
  ];
  const payloads = new Map<string, string>();
  for (const [alias, text] of entries) {
    PublicAliasSchema.parse(alias);
    if (payloads.has(alias)) {
      throw new Error(`${record.record_id}: duplicate evidence alias ${alias}`);
    }
    payloads.set(alias, text);
  }
  const pool = record.candidate_pool.ids;
  const poolSet = new Set(pool);
  if (poolSet.size !== pool.length) {
    throw new Error(`${record.record_id}: candidate_pool contains duplicate aliases`);
  }
  const unexpected = [...payloads.keys()].filter(alias => !poolSet.has(alias));
  const missing = pool.filter(alias => !payloads.has(alias));
  if (unexpected.length > 0 || missing.length > 0) {
    throw new Error(
      `${record.record_id}: candidate_pool and evidence differ; missing=${missing.join(',')}; unexpected=${unexpected.join(',')}`
    );
  }
  return payloads;
}

function workRefByAlias(
  record: PublicBaselineRecord
): Map<string, z.infer<typeof PublicWorkRefSchema>> {
  const refs = new Map(record.loo.candidates.map(candidate => [candidate.id, candidate]));
  if (refs.size !== record.loo.candidates.length) {
    throw new Error(`${record.record_id}: loo.candidates contains duplicate aliases`);
  }
  return refs;
}

function queryRecord(record: PublicBaselineRecord): WorkRecord {
  const searchText = `${publicBaselineSearchToken(record.record_id)} ${record.question.text}`;
  return WorkRecordSchema.parse({
    work_id: publicBaselineQueryWorkId(record.record_id),
    rig: publicBaselineRig(record.record_id),
    title: record.question.text,
    lifecycle: {
      created: record.question.asked_at,
      started: record.question.asked_at,
      status: 'open',
    },
    trace: {
      jsonl_path: `public-baseline/${record.record_id}/query`,
      errors: [
        {
          tool: 'public-baseline-query',
          severity: 'error',
          message: searchText,
          file: record.question.scope_id,
          line: 0,
        },
      ],
    },
    metadata: {
      public_baseline_record_id: record.record_id,
      public_baseline_world_id: record.provenance.world_id,
      public_baseline_alias: record.loo.query.id,
    },
  });
}

function candidateRecord(
  record: PublicBaselineRecord,
  alias: string,
  text: string,
  closed: string
): WorkRecord {
  const searchText = `${publicBaselineSearchToken(record.record_id)} ${text}`;
  return WorkRecordSchema.parse({
    work_id: publicBaselineCandidateWorkId(record.record_id, alias),
    rig: publicBaselineRig(record.record_id),
    title: text,
    lifecycle: { created: closed, started: closed, closed, status: 'closed' },
    trace: {
      jsonl_path: `public-baseline/${record.record_id}/candidate/${alias}`,
      errors: [
        {
          tool: 'public-baseline-candidate',
          severity: 'error',
          message: searchText,
          file: alias,
          line: 0,
        },
      ],
    },
    metadata: {
      public_baseline_record_id: record.record_id,
      public_baseline_world_id: record.provenance.world_id,
      public_baseline_alias: alias,
    },
  });
}

function convertRecord(record: PublicBaselineRecord): PublicBaselineConversion {
  if (record.loo.boundary !== record.question.asked_at) {
    throw new Error(`${record.record_id}: loo.boundary differs from question.asked_at`);
  }
  const payloads = payloadsByAlias(record);
  const refs = workRefByAlias(record);
  const records = [queryRecord(record)];
  const lessons: LessonInput[] = [];
  for (const alias of record.candidate_pool.ids) {
    const ref = refs.get(alias);
    if (ref?.closed === undefined || ref.closed === null) {
      throw new Error(`${record.record_id}: candidate ${alias} has no closed instant`);
    }
    if (Date.parse(ref.closed) >= Date.parse(record.loo.boundary)) {
      throw new Error(`${record.record_id}: candidate ${alias} closed outside the LOO boundary`);
    }
    const text = payloads.get(alias);
    if (text === undefined) {
      throw new Error(`${record.record_id}: candidate ${alias} has no evidence payload`);
    }
    const workId = publicBaselineCandidateWorkId(record.record_id, alias);
    records.push(candidateRecord(record, alias, text, ref.closed));
    lessons.push({ work_id: workId, extracted_at: ref.closed, payload: { narrative: text } });
  }
  return { records, lessons, queries: 1, candidates: record.candidate_pool.ids.length };
}

export function parsePublicBaselineLines(input: string): PublicBaselineConversion {
  const trimmed = input.trim();
  if (trimmed === '') return { records: [], lessons: [], queries: 0, candidates: 0 };
  const values = trimmed.startsWith('[')
    ? z.array(PublicBaselineRecordSchema).parse(JSON.parse(trimmed))
    : trimmed.split('\n').map((line, index) => {
        try {
          return PublicBaselineRecordSchema.parse(JSON.parse(line));
        } catch (error: unknown) {
          const detail = error instanceof Error ? error.message : String(error);
          throw new Error(`invalid public baseline record on line ${index + 1}: ${detail}`);
        }
      });
  const converted = values.map(convertRecord);
  return {
    records: converted.flatMap(value => value.records),
    lessons: converted.flatMap(value => value.lessons),
    queries: converted.length,
    candidates: converted.reduce((total, value) => total + value.candidates, 0),
  };
}

function assertCompatibleExistingRecords(path: string, records: WorkRecord[]): void {
  if (!existsSync(path)) return;
  const byRig = new Map<string, WorkRecord[]>();
  for (const record of records) {
    const group = byRig.get(record.rig) ?? [];
    group.push(record);
    byRig.set(record.rig, group);
  }
  const db = openStore(path);
  try {
    for (const [rig, incoming] of byRig) {
      const existing = queryRecords(db, { rig }).sort((left, right) =>
        left.work_id.localeCompare(right.work_id)
      );
      if (existing.length === 0) continue;
      const expected = [...incoming].sort((left, right) =>
        left.work_id.localeCompare(right.work_id)
      );
      if (JSON.stringify(existing) !== JSON.stringify(expected)) {
        throw new Error(`changed public baseline namespace ${rig} cannot be reimported`);
      }
    }
  } finally {
    db.close();
  }
}

export async function importPublicBaselineCommand(
  ctx: CommandContext
): Promise<ImportPublicBaselineCommandResult> {
  const file = ctx.options.file;
  if (file !== undefined && typeof file !== 'string') {
    throw new Error('--file requires a path: mem import-public-baseline --file FILE');
  }
  const input = file === undefined ? await readStdin() : await readFile(file, 'utf8');
  const converted = parsePublicBaselineLines(input);
  const path = storePath(ctx.options);
  assertCompatibleExistingRecords(path, converted.records);
  const counts = buildStoreFromRecords(path, converted.records);
  const db = openStore(path);
  let lessons;
  try {
    lessons = importLessons(db, converted.lessons);
  } finally {
    db.close();
  }
  if (!ctx.options.json) {
    console.error(
      `imported ${converted.queries} public baseline query record(s), ${converted.candidates} candidate record(s), and ${lessons.appended} lesson(s) into ${path}`
    );
  }
  return {
    imported: counts.records,
    queries: converted.queries,
    candidates: converted.candidates,
    lessons_appended: lessons.appended,
    lessons_skipped: lessons.skipped,
    store: path,
  };
}
