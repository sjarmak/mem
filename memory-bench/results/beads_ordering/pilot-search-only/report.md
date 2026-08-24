# Beads Memory pre-pagination ordering experiment

This report isolates ordering after the existing literal matcher has produced one fixed candidate set. Server-side matching/scoring time is reported separately from what the agent ingested.

## Direct answers

1. Realistic frozen match sets in this corpus range from 12 to 12 candidates.
2. Under key ordering, a useful Memory was visible on page 1 in 0% of measured runs.
3. Per-page cost is visible in the page-size table and burial correlations below; compact tokens and tool calls are model-facing costs, while Beads compute is separate.
4. Across the recorded grid, BM25F changed mean compact ingestion by +233.0 tokens relative to key order.
5. BM25F changed task success by +0.0%; interpret retrieval-cost and outcome effects separately.
6. The navigation-effects table compares matched search-only and navigation cells; primary-reach and graph-hop fields show whether an entry point closed the gap.
7. The pre-registered mechanical-versus-BM25F crossover by mode is {"search-only": 5}.
8. Compare each structural prior against BM25F rather than treating structural rank as one undifferentiated policy.
9. Bounded-versus-unbounded deltas isolate the cost of limiting initial visibility.
10. This PoC supports added Beads complexity only if the measured ingestion/round-trip reduction is material without a success regression; it does not establish a production indexing design.

## Page-size curves

| mode | order | page | page-1 useful | pages p50 / p90 | compact tokens p50 / p90 | tool calls p50 | time-to-useful p50 ms | recalls p50 | success | server order p50 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| search-only | bm25f | 5 | 100% | 1.00 / 1.00 | 654.00 / 654.00 | 1.00 | 3519.61 | 2.00 | 100% | 0.47 |
| search-only | key | 5 | 0% | 3.00 / 3.00 | 1481.00 / 1481.00 | 6.00 | 19484.80 | 3.00 | 100% | 0.33 |
| search-only | pagerank | 5 | 0% | 3.00 / 3.00 | 1481.00 / 1481.00 | 7.00 | 24053.56 | 3.00 | 100% | 0.34 |

## Mechanical versus BM25F crossover

Material means at least one p50 page or 20% mean compact-token reduction, with no success regression.

```json
[
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.1573261309925726,
    "bm25f_success_rate_minus_policy": 0.0,
    "material_gap": true
  }
]
```

## Structural policies versus BM25F

```json
[
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.1573261309925726,
    "bm25f_success_rate_minus_policy": 0.0,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "pagerank",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.1573261309925726,
    "bm25f_success_rate_minus_policy": 0.0,
    "material_gap": true
  }
]
```

## Search-only versus navigation

```json
[]
```

## Bounded versus unbounded

```json
[]
```

## Cost per additional page

```json
[
  {
    "mode": "search-only",
    "arm": "bm25f",
    "page_size": "5",
    "n": 1,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "search-only",
    "arm": "key",
    "page_size": "5",
    "n": 1,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "search-only",
    "arm": "pagerank",
    "page_size": "5",
    "n": 1,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  }
]
```

## Baseline burial correlations

```json
{
  "search-only": {
    "pages_requested": null,
    "compact_result_tokens": null,
    "compact_tokens_to_first_useful": null,
    "retrieval_tokens_to_first_useful": null,
    "tool_calls_to_first_useful": null,
    "retrieval_tool_calls": null,
    "retrieval_latency_ms": null,
    "server_candidate_generation_ms": null,
    "server_ordering_ms": null,
    "end_to_end_ms": null,
    "full_recalls": null,
    "graph_hops_after_first_useful": null,
    "graph_hops_total": null,
    "reference_edges_exposed": null
  }
}
```
