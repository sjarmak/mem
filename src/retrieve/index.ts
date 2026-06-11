/**
 * retrieve/ — retrieval-v1 (P2.1): structured/keyword retrieval over the
 * work-audit graph under the locked D6–D10 contract (ARCHITECTURE.md).
 * Deterministic, zero external API. The implementation lives in `retrieval.ts`
 * (ranked query), `exclusions.ts` (the Decision-6 same-work exclusion set),
 * and `disclosure.ts` (the P2.5 progressive-disclosure projections);
 * this barrel is the package's public surface.
 */
export {
  estimateTokens,
  lessonUri,
  recordUri,
  toDetails,
  toIndex,
  type DetailItem,
  type IndexItem,
  type RetrievalDetails,
  type RetrievalIndex,
} from './disclosure.js';
export {
  DEFAULT_LIMIT,
  RetrievalQuerySchema,
  queryFromRecord,
  retrieve,
  type Citation,
  type LiteralRef,
  type MatchTier,
  type RetrievalQuery,
  type RetrievalResult,
  type RetrievalScope,
  type RetrieveOptions,
  type RetrievedItem,
} from './retrieval.js';
