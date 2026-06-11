import { readFileSync } from 'node:fs';

import type { WorkRecord } from '../schemas/workrecord.js';

/**
 * Task typing (mem-75t.11). Three sources, provenance always explicit in
 * `task_type_source`:
 *
 * - `formula`: molecule beads title themselves with their formula name
 *   (`mol-focus-review`); step beads carry `metadata["gc.step_ref"]` =
 *   `<formula>.<step>` (`mol-do-work.drain`). Mechanical projection of a
 *   generator-produced value — not semantic classification.
 * - `structural`: machine-generated title grammars from known gc generators
 *   (rollups, convoys, copilot-review iterations, review checkpoints, sling
 *   dispatches). Exact grammars, mechanical.
 * - `model`: everything free-form, classified by a model into a closed
 *   taxonomy (the Python classifier — `scripts/classify_task_types.py` —
 *   writes the artifact consumed here; each entry records the model id and
 *   timestamp). ZFC: the semantic judgment lives in the model, this module
 *   only looks the result up.
 *
 * Mechanical rules take precedence over the artifact: a generator-typed bead
 * is never re-labeled by a model guess.
 */

export interface TaskTypeEntry {
  task_type: string;
  model: string;
  classified_at: string;
}

export type TaskTypeArtifact = Map<string, TaskTypeEntry>;

/** The closed taxonomy the model classifier is allowed to emit. Shared with
 * the Python classifier — both sides validate against it. */
export const MODEL_TASK_TAXONOMY = [
  'feature',
  'bugfix',
  'refactor',
  'testing',
  'docs',
  'research',
  'review',
  'triage',
  'infra',
  'release',
  'coordination',
  'report',
  'other',
] as const;

const FORMULA_TITLE = /^mol-[a-z0-9-]+$/;
const COPILOT_ITERATE = /^Iterate copilot review \d+ on /;
const SLING_TITLE = /^sling-/;

interface DerivedType {
  task_type: string;
  task_type_source: 'formula' | 'structural' | 'model';
}

/** Mechanical typing: formula identity, then known generator grammars.
 * Returns null when only a model could type the record. */
export function deriveMechanicalType(record: WorkRecord): DerivedType | null {
  if (FORMULA_TITLE.test(record.title)) {
    return { task_type: record.title, task_type_source: 'formula' };
  }
  const stepRef = record.metadata['gc.step_ref'];
  if (typeof stepRef === 'string' && stepRef.length > 0) {
    return { task_type: stepRef, task_type_source: 'formula' };
  }
  if (record.title.startsWith('Rollup(')) {
    return { task_type: 'rollup', task_type_source: 'structural' };
  }
  if (record.title.startsWith('input convoy for ') || record.metadata['gc.synthetic'] === 'true') {
    return { task_type: 'convoy', task_type_source: 'structural' };
  }
  if (COPILOT_ITERATE.test(record.title)) {
    return { task_type: 'pr-review-iterate', task_type_source: 'structural' };
  }
  if (record.title === 'Human review checkpoint') {
    return { task_type: 'review-checkpoint', task_type_source: 'structural' };
  }
  if (SLING_TITLE.test(record.title)) {
    return { task_type: 'sling-dispatch', task_type_source: 'structural' };
  }
  return null;
}

/** Parse the classifier artifact (`.mem/task-types.json`). Entries with a
 * label outside the taxonomy are rejected loudly — a drifting classifier must
 * fail the build, not silently write junk types. */
export function loadTaskTypes(path: string): TaskTypeArtifact {
  const payload = JSON.parse(readFileSync(path, 'utf8')) as {
    entries?: Record<string, TaskTypeEntry>;
  };
  if (payload.entries === undefined || typeof payload.entries !== 'object') {
    throw new Error(`task-types artifact ${path} has no entries{} object`);
  }
  const allowed = new Set<string>(MODEL_TASK_TAXONOMY);
  for (const [workId, entry] of Object.entries(payload.entries)) {
    if (!allowed.has(entry.task_type)) {
      throw new Error(
        `task-types artifact ${path}: ${workId} has label '${entry.task_type}' outside the taxonomy`
      );
    }
  }
  return new Map(Object.entries(payload.entries));
}

/**
 * Attach task types: mechanical rules first, then the model artifact for the
 * residue. Records neither rule nor artifact covers stay untyped (absent
 * fields, never a defaulted 'other'). Also promotes `molecule_id` is NOT done
 * here — that is a metadata projection handled by the store writer. Records
 * are copied, never mutated.
 */
export function attachTaskTypes(
  records: WorkRecord[],
  artifact: TaskTypeArtifact = new Map()
): WorkRecord[] {
  return records.map(record => {
    const mechanical = deriveMechanicalType(record);
    if (mechanical !== null) {
      return { ...record, ...mechanical };
    }
    const entry = artifact.get(record.work_id);
    if (entry !== undefined) {
      return { ...record, task_type: entry.task_type, task_type_source: 'model' as const };
    }
    return record;
  });
}
