// Type contract for the pure freeze helpers in lib.mjs. Hand-maintained because
// lib.mjs stays plain ESM (the Day-0 freeze must run with no TypeScript build in
// the loop); this declaration lets the unit tests and type-aware lint see real
// types instead of `any`.

export const EXPECTED_FLOORS: Readonly<Record<string, number>>;
export const RIG_FLOOR_KEYS: Readonly<Record<string, string>>;

export type CiConclusion = 'success' | 'failure' | 'UNKNOWN';

export function aggregateConclusion(conclusions: Array<string | null | undefined>): {
  conclusion: CiConclusion;
  reason: string;
};

export interface CiRow {
  pr: number;
  merge_oid: string | null;
  head_ref: string | null;
  head_ref_deleted: boolean;
  ci_conclusion: CiConclusion;
  reason: string;
}

export function classifyCiRow(pr: {
  number: number;
  mergeCommit?: { oid: string } | null;
  headRefName?: string | null;
  headRefDeleted?: boolean;
  checkRuns?: Array<{ conclusion?: string | null }> | null;
}): CiRow;

export function summarizeCi(rows: Array<{ ci_conclusion: string }>): {
  total: number;
  success: number;
  failure: number;
  UNKNOWN: number;
};

export function bundleParity(counts: {
  listHeads: number;
  heads: number;
  tags: number;
  collisions?: number;
}): { ok: boolean; expected: number; actual: number; heads: number; tags: number; collisions: number };

export function detachedRecovery(
  passedShas: string[],
  recoveredShas: string[]
): { ok: boolean; missing: string[]; total: number; recovered: number };

export function floorCheck(
  floorKey: string | null,
  branchCount: number,
  table?: Record<string, number>
): { applicable: boolean; ok: boolean; floorKey: string | null; floor: number | null; count: number };

export function isSessionStore(store: { floorKey: string | null; refnames: string[] }): boolean;

export function dedupeStores(
  entries: Array<{ rig: string; dir: string; commonDir: string; floorKey: string | null }>
): Array<{ commonDir: string; dir: string; rigs: string[]; floorKey: string | null }>;

export function parseRefIndex(stdout: string): {
  total: number;
  heads: number;
  tags: number;
  collisions: number;
  refnames: string[];
};

export function parseDetachedHeads(porcelain: string): string[];
