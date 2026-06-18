/**
 * bench/ — the eval harness: run an agent with vs without retrieved memory on
 * held-out tasks, measure outcome lift (Phase 2; replay closed beads first).
 *
 * The three LEAKAGE gates (PRD §6, mem-wanz.8) are the harness's admission layer:
 * (a) the temporal wall, (b) diff-overlap vs the gold patch, (c) LOO dedup. Each
 * is a pure mechanical predicate the scored eval (mem-wanz.9) composes.
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
} from './loo-dedup.js';
