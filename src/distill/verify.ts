import { failureSignature } from '../parse/recurrence.js';
import type { WorkRecord } from '../schemas/workrecord.js';
import type { ResolutionEvidence } from './distiller.js';

/**
 * Verified-memory-write gate (mem-0r7l): "a memory write is accepted only if
 * the replay now passes, regression-aware against the K past fixes." Literal
 * sandbox replay of a historical session is a settled NULL (base_commit is a
 * timestamp-approximate anchor, not the session's true base — see
 * docs/mem-7q6e-replay-engine-null.md); this module instead operationalizes
 * "replay" at the mechanical failure-signature level (Decision 8's
 * `file:line` + error-class primitive, already used for retrieval
 * triggering), which is exactly the granularity mem's data actually
 * supports. Pure functions only — no IO, no DB, no model calls (ZFC): the
 * caller (`distiller.ts`) resolves evidence and queries the store.
 */

/** The outcome of a single admission check — a discriminated union that
 * carries the narrowed, non-null evidence in the admitted branch. The
 * caller can only reach a real {@link ResolutionEvidence} through this
 * check (never past a bare `=== null` test it could drift from), and a
 * refusal always carries its reason at the type level. */
export type AdmissionCheck =
  | { admitted: true; evidence: ResolutionEvidence }
  | { admitted: false; reason: string };

/**
 * "The write is accepted only if the replay now passes": refuse admission
 * when there is no resolution evidence at all — neither a landed diff nor a
 * readable transcript exists to verify the fix against. This is checked
 * BEFORE the model is invoked, so an unverifiable candidate never spends a
 * distillation call.
 */
export function verifyFixEvidence(evidence: ResolutionEvidence | null): AdmissionCheck {
  if (evidence === null) {
    return {
      admitted: false,
      reason:
        'no-resolution-evidence: neither a landed diff nor a readable transcript exists for ' +
        'this record — the fix is unverifiable, refusing to admit a lesson for it',
    };
  }
  return { admitted: true, evidence };
}

/** The distinct failure signatures a record's trace exhibits (Decision 8) —
 * the K-past-fix regression check's unit of comparison. */
export function recordSignatures(record: WorkRecord): string[] {
  return [...new Set((record.trace?.errors ?? []).map(failureSignature))];
}

/** One later-closed record considered for a signature-recurrence match.
 * `is_sibling` is computed by the caller (Decision-6 convoy/PR/parent/
 * supersedes exclusions, `src/retrieve/exclusions.ts` + `supersedesClosure`)
 * so this module stays IO-free: a later record on the SAME work continuing
 * through a convoy follow-up is expected iteration, not a regression. */
export interface RegressionCandidate {
  work_id: string;
  is_sibling: boolean;
}

/** One K-past-fix signature that recurred in later, unrelated work — the fix
 * that lesson documented did not durably prevent recurrence. */
export interface RegressionFlag {
  lesson_work_id: string;
  extracted_at: string;
  signature: string;
  recurred_in: string[];
}

/** The `work_id` of the one entry in a `skipped` report that is not a lesson:
 * the orphan diagnostic's truncation notice (see {@link RegressionSkip}).
 *
 * The parens make a collision with a bd-generated id vanishingly unlikely, but
 * that is a convention rather than a proof: `work_id` is `z.string().min(1)`
 * with no format constraint (schemas/workrecord.ts), and `importLessons`
 * deliberately gates nothing (writer.ts). A colliding id would misread this one
 * diagnostic line; it could not corrupt the check itself, since the notice is
 * appended after the loop and never reaches the recurrence check. */
export const ORPHAN_TRUNCATION_WORK_ID = '(orphan-lessons)';

/** One lesson the regression check could not evaluate — its source record was
 * deleted/re-ingested, or its `extracted_at` (which crosses the unvalidated
 * `import-lessons` boundary) failed to parse. Surfaced rather than silently
 * dropped, so a skip is never indistinguishable from "checked, clean".
 *
 * Two caveats for `--json` consumers, both rig-scoped-only (mem-c7mf3). A
 * skipped ORPHAN — a lesson whose source record is absent — is NOT necessarily
 * in the K-window, and NOT necessarily in the requested rig: `rig` lives only
 * on `work_records`, so an orphan has no derivable rig and every rig's report
 * receives all of them. Read a skip as "this could not be checked", never as
 * "this lesson is mine". The over-report is deliberate and safe — a skip is a
 * diagnostic, and an orphan exits before any signature is derived, so it can
 * never become a {@link RegressionFlag}.
 *
 * The orphan slice is bounded like the window, so one final entry may carry
 * {@link ORPHAN_TRUNCATION_WORK_ID} as its `work_id` instead of a lesson's:
 * the notice that more orphans exist than were listed. */
export interface RegressionSkip {
  work_id: string;
  reason: string;
}

/**
 * Pure signature-recurrence check for ONE prior lesson. `candidatesBySignature`
 * maps each of the lesson's originating failure signatures to the later,
 * same-signature records already found for it (closed strictly after the
 * lesson's `extracted_at`) — assembling that map is `computeRegressions`'
 * (DB-bound) job; this function only applies the self/sibling exclusion and
 * builds the flags.
 */
export function checkPriorFixRegression(
  lessonWorkId: string,
  extractedAt: string,
  candidatesBySignature: ReadonlyMap<string, readonly RegressionCandidate[]>
): RegressionFlag[] {
  const flags: RegressionFlag[] = [];
  for (const [signature, candidates] of candidatesBySignature) {
    const recurredIn = candidates
      .filter(c => c.work_id !== lessonWorkId && !c.is_sibling)
      .map(c => c.work_id)
      .sort();
    if (recurredIn.length > 0) {
      flags.push({
        lesson_work_id: lessonWorkId,
        extracted_at: extractedAt,
        signature,
        recurred_in: recurredIn,
      });
    }
  }
  return flags;
}
