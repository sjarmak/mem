/**
 * bench/ — the eval harness: run an agent with vs without retrieved memory on
 * held-out tasks, measure outcome lift (Phase 2; replay closed beads first).
 *
 * The three LEAKAGE gates (PRD §6, mem-wanz.8) are the harness's admission layer:
 * (a) the temporal wall, (b) diff-overlap vs the gold patch, (c) LOO dedup. The
 * session fan-out gate (PRD §5.7, R1, mem-wanz.10) is the verdict-attribution
 * layer: one session → ≤1 scored outcome. Each is a pure mechanical predicate the
 * scored eval (mem-wanz.9) composes.
 */
export {
  type TemporalDrop,
  isTemporallySoundTask,
  memoryPredatesTaskStart,
  temporalWallDrop,
} from './temporal.js';
export {
  DEFAULT_THRESHOLD,
  DIFF_OVERLAP_THRESHOLDS,
  changedLineJaccard,
  diffOverlapThreshold,
  leaksGoldPatch,
  parseUnifiedDiff,
  sharesHunkAnchor,
  stripDiffsAndShas,
} from './diff-overlap.js';
export {
  type CanonicalIdentity,
  assertNoSharedBranchRootAcrossPartitions,
  branchRoot,
  canonicalIdentity,
  looPartitions,
  mergeKeys,
} from './loo-dedup.js';
export {
  type FanoutReport,
  type ScoreEligibility,
  type SessionAssoc,
  type SessionVerdictTarget,
  type VerdictSource,
  AMBIGUOUS_FANOUT,
  classifySessions,
  COLLAPSED_TO_CANONICAL,
  countMisattributions,
  DISAMBIGUATED,
  SINGLE_WORK_ID,
  summarizeFanout,
} from './session-fanout.js';
