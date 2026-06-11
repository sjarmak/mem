import type { Citation, RetrievalResult, RetrievedItem } from './retrieval.js';

/**
 * Progressive disclosure (P2.5) — the engram three-layer retrieval model
 * (sjarmak/engram CLAUDE_MEM_INTEGRATION.md, E14: index → details → source,
 * designed Oct–Nov 2025, never built there) as projections over retrieval-v1's
 * {@link RetrievalResult}. The minimal-useful-injection lever and Decision-10
 * precision guard made concrete: the index makes the cost of every item
 * explicit *before* it is injected, and the agent — not the pipeline — chooses
 * hydration depth.
 *
 * - **L1 index**: per-item title, match tier, citation URI, and the token
 *   cost of hydrating its details. ~order-10 tokens per item.
 * - **L2 details**: the full retrieved item (lessons, matched signatures,
 *   literal refs) for the work_ids the agent picked.
 * - **L3 source**: not a payload — the citation URI navigates to it
 *   (`mem query <work_id>` for the record, the trace path for the transcript).
 *
 * Pure projection: deterministic arithmetic over an existing result, no
 * store access, no ranking changes (D10 order is preserved).
 */

/** Estimated token count — engram's documented approximation (1 token ≈ 4
 * characters). A cost signal for hydration decisions, not an exact meter. */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

/** Citation URI for a retrieved item's lessons — the engram
 * `engram://observation/{id}` pattern adapted to mem's provenance chain:
 * `mem://lesson/{work_id}` plus the commit snapshot when the outcome has one.
 * Segments are interpolated raw: work_ids are store-validated bead ids and
 * the URI is an opaque navigation handle for agents, never URL-parsed here. */
export function lessonUri(citation: Citation): string {
  const base = `mem://lesson/${citation.work_id}`;
  return citation.commit_sha === undefined ? base : `${base}/${citation.commit_sha}`;
}

/** Source (L3) URI — the full WorkRecord behind an item, recoverable via
 * `mem query <work_id>`. */
export function recordUri(workId: string): string {
  return `mem://record/${workId}`;
}

/** One L1 index row: everything an agent needs to decide whether the item is
 * worth its `token_cost`, and nothing it would have to pay for blindly. */
export interface IndexItem {
  /** The item's lesson citation ({@link lessonUri}); hydrate the item by
   * passing its `work_id` to `--pick`. */
  uri: string;
  /** L3 source pointer ({@link recordUri}). */
  source_uri: string;
  work_id: string;
  rig: string;
  title: string;
  match: RetrievedItem['match'];
  lesson_count: number;
  /** Estimated tokens to hydrate this item's L2 details. */
  token_cost: number;
}

/** The L1 envelope. Carries the same precision-guard flags as the full
 * result — truncation and near-duplicate signals must survive projection. */
export interface RetrievalIndex {
  scope: RetrievalResult['scope'];
  work_id: string;
  trigger_count: number;
  total_matched: number;
  near_duplicate_top: boolean;
  fts_truncated: boolean;
  /** Sum of the items' hydration costs — what "inject everything" would pay. */
  token_cost_total: number;
  items: IndexItem[];
}

/** One L2 row: the full retrieved item plus its disclosure URIs. */
export interface DetailItem extends RetrievedItem {
  uri: string;
  source_uri: string;
}

/** The L2 envelope for the picked items. Like the index, it keeps the
 * precision-guard flags: a consumer that hydrates without an L1 call first
 * must still see truncation and near-duplicate signals. */
export interface RetrievalDetails {
  scope: RetrievalResult['scope'];
  work_id: string;
  trigger_count: number;
  total_matched: number;
  near_duplicate_top: boolean;
  fts_truncated: boolean;
  items: DetailItem[];
}

function toDetailItem(item: RetrievedItem): DetailItem {
  return { uri: lessonUri(item.citation), source_uri: recordUri(item.work_id), ...item };
}

/** Project a retrieval result to its L1 index. Item order is the D10 ranking,
 * untouched; each `token_cost` prices the item's L2 row exactly as it would
 * be serialized (URIs included). */
export function toIndex(result: RetrievalResult): RetrievalIndex {
  const items = result.items.map(item => {
    const detail = toDetailItem(item);
    return {
      uri: detail.uri,
      source_uri: detail.source_uri,
      work_id: item.work_id,
      rig: item.rig,
      title: item.title,
      match: item.match,
      lesson_count: item.lessons.length,
      token_cost: estimateTokens(JSON.stringify(detail)),
    };
  });
  return {
    scope: result.scope,
    work_id: result.work_id,
    trigger_count: result.trigger_count,
    total_matched: result.total_matched,
    near_duplicate_top: result.near_duplicate_top,
    fts_truncated: result.fts_truncated,
    token_cost_total: items.reduce((sum, item) => sum + item.token_cost, 0),
    items,
  };
}

/**
 * Project a retrieval result to L2 details for the picked work_ids (every
 * item when `pick` is omitted). An unknown pick throws — a typo'd id is a
 * caller bug, not an empty hydration.
 */
export function toDetails(result: RetrievalResult, pick?: string[]): RetrievalDetails {
  let selected = result.items;
  if (pick !== undefined) {
    const available = new Set(result.items.map(item => item.work_id));
    const missing = pick.filter(id => !available.has(id));
    if (missing.length > 0) {
      throw new Error(`--pick id(s) not in the retrieval result: ${missing.join(', ')}`);
    }
    const picked = new Set(pick);
    selected = result.items.filter(item => picked.has(item.work_id));
  }
  return {
    scope: result.scope,
    work_id: result.work_id,
    trigger_count: result.trigger_count,
    total_matched: result.total_matched,
    near_duplicate_top: result.near_duplicate_top,
    fts_truncated: result.fts_truncated,
    items: selected.map(toDetailItem),
  };
}
