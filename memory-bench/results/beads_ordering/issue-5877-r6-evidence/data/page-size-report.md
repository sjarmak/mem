# Beads Memory pre-pagination ordering experiment

This report isolates ordering after the existing literal matcher has produced one fixed candidate set. Server-side matching/scoring time is reported separately from what the agent ingested.

## Direct answers

1. Realistic frozen match sets in this corpus range from 8 to 280 candidates.
2. Under key ordering, a useful Memory was visible on page 1 in 67% of measured runs.
3. Per-page cost is visible in the page-size table and burial correlations below; compact tokens and tool calls are model-facing costs, while Beads compute is separate.
4. Across the recorded grid, BM25F changed mean compact ingestion by +1746.8 tokens relative to key order.
5. BM25F changed task success by +24.9%; interpret retrieval-cost and outcome effects separately.
6. The navigation-effects table compares matched search-only and navigation cells; primary-reach and graph-hop fields show whether an entry point closed the gap.
7. The pre-registered mechanical-versus-BM25F crossover by mode is {"navigation": 20, "search-only": 20}.
8. Compare each structural prior against BM25F rather than treating structural rank as one undifferentiated policy.
9. Bounded-versus-unbounded deltas isolate the cost of limiting initial visibility.
10. This PoC supports added Beads complexity only if the measured ingestion/round-trip reduction is material without a success regression; it does not establish a production indexing design.

## Page-size curves

| mode | order | page | page-1 useful | pages p50 / p90 | compact tokens p50 / p90 | tool calls p50 | time-to-useful p50 ms | recalls p50 | success | server order p50 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| navigation | bm25f | 5 | 100% | 1.00 / 1.00 | 623.00 / 729.00 | 1.00 | 4124.39 | 2.00 | 70% | 1.05 |
| navigation | bm25f | 10 | 100% | 1.00 / 1.00 | 1252.00 / 1464.00 | 1.00 | 4149.07 | 1.50 | 79% | 1.09 |
| navigation | bm25f | 20 | 100% | 1.00 / 1.00 | 2597.50 / 2909.50 | 1.00 | 4305.81 | 1.00 | 77% | 1.39 |
| navigation | bm25f | all | 100% | 1.00 / 1.00 | 5505.50 / 22917.10 | 1.00 | 4231.59 | 1.00 | 79% | 1.55 |
| navigation | hits-hub | 5 | 58% | 1.00 / 1.50 | 721.50 / 2347.00 | 1.00 | 4347.98 | 5.50 | 36% | 0.57 |
| navigation | hits-hub | 10 | 62% | 1.00 / 1.40 | 1399.00 / 4310.00 | 1.00 | 4122.20 | 4.50 | 42% | 0.59 |
| navigation | hits-hub | 20 | 82% | 1.00 / 1.00 | 2705.50 / 3784.10 | 1.00 | 4012.99 | 2.00 | 68% | 0.51 |
| navigation | hits-hub | all | 100% | 1.00 / 1.00 | 5505.50 / 22917.10 | 1.00 | 4202.48 | 1.00 | 62% | 0.73 |
| navigation | key | 5 | 46% | 1.00 / 1.00 | 752.50 / 1972.90 | 2.00 | 4197.73 | 5.50 | 21% | 0.58 |
| navigation | key | 10 | 54% | 1.00 / 1.60 | 1460.00 / 4177.40 | 1.00 | 4154.58 | 5.50 | 50% | 0.58 |
| navigation | key | 20 | 67% | 1.00 / 2.00 | 2805.50 / 5628.10 | 1.00 | 4392.43 | 4.50 | 62% | 0.93 |
| navigation | key | all | 100% | 1.00 / 1.00 | 5505.50 / 22917.10 | 1.00 | 4287.89 | 1.00 | 58% | 0.79 |
| navigation | reverse-pagerank | 5 | 94% | 1.00 / 1.00 | 674.00 / 746.90 | 1.00 | 4018.04 | 3.00 | 60% | 0.44 |
| navigation | reverse-pagerank | 10 | 100% | 1.00 / 1.00 | 1320.00 / 1390.00 | 1.00 | 4122.10 | 2.00 | 60% | 0.34 |
| navigation | reverse-pagerank | 20 | 100% | 1.00 / 1.00 | 2622.00 / 2777.60 | 1.00 | 4116.77 | 2.00 | 82% | 0.51 |
| navigation | reverse-pagerank | all | 100% | 1.00 / 1.00 | 5505.50 / 22917.10 | 1.00 | 4197.26 | 1.00 | 79% | 0.77 |
| search-only | bm25f | 5 | 100% | 1.00 / 1.00 | 636.00 / 739.00 | 1.00 | 4219.76 | 1.00 | 67% | 0.89 |
| search-only | bm25f | 10 | 100% | 1.00 / 1.00 | 1275.00 / 1466.00 | 1.00 | 4076.18 | 1.00 | 64% | 1.10 |
| search-only | bm25f | 20 | 100% | 1.00 / 1.00 | 2597.50 / 2909.50 | 1.00 | 4223.99 | 1.00 | 69% | 1.45 |
| search-only | bm25f | all | 100% | 1.00 / 1.00 | 5505.50 / 22917.10 | 1.00 | 4174.21 | 1.00 | 75% | 1.39 |
| search-only | hits-hub | 5 | 54% | 1.00 / 3.00 | 757.00 / 2104.00 | 1.00 | 4452.74 | 2.00 | 30% | 0.61 |
| search-only | hits-hub | 10 | 68% | 1.00 / 2.00 | 1399.00 / 3210.20 | 1.00 | 4140.11 | 3.00 | 52% | 0.77 |
| search-only | hits-hub | 20 | 83% | 1.00 / 2.00 | 2713.00 / 5501.20 | 1.00 | 4242.99 | 2.00 | 55% | 0.81 |
| search-only | hits-hub | all | 100% | 1.00 / 1.00 | 5505.50 / 22917.10 | 1.00 | 4085.75 | 1.00 | 83% | 0.71 |
| search-only | key | 5 | 46% | 1.00 / 1.00 | 746.00 / 2720.00 | 2.00 | 3900.12 | 2.00 | 12% | 0.44 |
| search-only | key | 10 | 54% | 1.00 / 3.00 | 1563.00 / 4351.60 | 1.00 | 4526.73 | 3.00 | 33% | 0.95 |
| search-only | key | 20 | 67% | 1.00 / 2.00 | 2750.50 / 5628.10 | 1.00 | 4207.86 | 2.00 | 62% | 0.80 |
| search-only | key | all | 100% | 1.00 / 1.00 | 5505.50 / 22917.10 | 1.00 | 4005.76 | 1.00 | 75% | 0.79 |
| search-only | reverse-pagerank | 5 | 98% | 1.00 / 1.00 | 676.00 / 750.50 | 1.00 | 4143.52 | 2.00 | 37% | 0.70 |
| search-only | reverse-pagerank | 10 | 100% | 1.00 / 1.00 | 1344.00 / 1489.70 | 1.00 | 4169.46 | 3.50 | 34% | 0.78 |
| search-only | reverse-pagerank | 20 | 100% | 1.00 / 1.00 | 2620.50 / 2775.20 | 1.00 | 3937.13 | 2.00 | 63% | 0.64 |
| search-only | reverse-pagerank | all | 100% | 1.00 / 1.00 | 5505.50 / 22917.10 | 1.00 | 4260.79 | 1.00 | 83% | 0.80 |

## Mechanical versus BM25F crossover

Material means at least one p50 page or 20% mean compact-token reduction, with no success regression.

```json
[
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.528864165977599,
    "bm25f_success_rate_minus_policy": 0.4916666666666666,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "10",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.42517387815154145,
    "bm25f_success_rate_minus_policy": 0.29166666666666663,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "20",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.45892913057092155,
    "bm25f_success_rate_minus_policy": 0.14423076923076927,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "all",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.0,
    "bm25f_success_rate_minus_policy": 0.20833333333333326,
    "material_gap": false
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 0.5,
    "bm25f_compact_token_reduction_fraction": 0.32249366185545403,
    "bm25f_success_rate_minus_policy": 0.5489130434782609,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "10",
    "policy_pages_p50_minus_bm25f": 2.0,
    "bm25f_compact_token_reduction_fraction": 0.3617636076403849,
    "bm25f_success_rate_minus_policy": 0.3066666666666667,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "20",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.34483612865139524,
    "bm25f_success_rate_minus_policy": 0.06730769230769229,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "all",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.0,
    "bm25f_success_rate_minus_policy": 0.0,
    "material_gap": false
  }
]
```

## Structural policies versus BM25F

```json
[
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.528864165977599,
    "bm25f_success_rate_minus_policy": 0.4916666666666666,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "reverse-pagerank",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.23780986107327706,
    "bm25f_success_rate_minus_policy": 0.09999999999999998,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "hits-hub",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.4461129492944204,
    "bm25f_success_rate_minus_policy": 0.33999999999999997,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "10",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.42517387815154145,
    "bm25f_success_rate_minus_policy": 0.29166666666666663,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "reverse-pagerank",
    "page_size": "10",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.10802817449507725,
    "bm25f_success_rate_minus_policy": 0.1875,
    "material_gap": false
  },
  {
    "mode": "navigation",
    "policy": "hits-hub",
    "page_size": "10",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.3797032159126787,
    "bm25f_success_rate_minus_policy": 0.37499999999999994,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "20",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.45892913057092155,
    "bm25f_success_rate_minus_policy": 0.14423076923076927,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "reverse-pagerank",
    "page_size": "20",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.08269660167600949,
    "bm25f_success_rate_minus_policy": -0.05219780219780212,
    "material_gap": false
  },
  {
    "mode": "navigation",
    "policy": "hits-hub",
    "page_size": "20",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.20260336492075784,
    "bm25f_success_rate_minus_policy": 0.09065934065934067,
    "material_gap": true
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "all",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.0,
    "bm25f_success_rate_minus_policy": 0.20833333333333326,
    "material_gap": false
  },
  {
    "mode": "navigation",
    "policy": "reverse-pagerank",
    "page_size": "all",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.0,
    "bm25f_success_rate_minus_policy": 0.0,
    "material_gap": false
  },
  {
    "mode": "navigation",
    "policy": "hits-hub",
    "page_size": "all",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.0,
    "bm25f_success_rate_minus_policy": 0.16666666666666663,
    "material_gap": false
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 0.5,
    "bm25f_compact_token_reduction_fraction": 0.32249366185545403,
    "bm25f_success_rate_minus_policy": 0.5489130434782609,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "reverse-pagerank",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 2.0,
    "bm25f_compact_token_reduction_fraction": 0.4285452023498694,
    "bm25f_success_rate_minus_policy": 0.30434782608695654,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "hits-hub",
    "page_size": "5",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.3413039900308011,
    "bm25f_success_rate_minus_policy": 0.3695652173913043,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "10",
    "policy_pages_p50_minus_bm25f": 2.0,
    "bm25f_compact_token_reduction_fraction": 0.3617636076403849,
    "bm25f_success_rate_minus_policy": 0.3066666666666667,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "reverse-pagerank",
    "page_size": "10",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.3000894567079931,
    "bm25f_success_rate_minus_policy": 0.3,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "hits-hub",
    "page_size": "10",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.24258785833588978,
    "bm25f_success_rate_minus_policy": 0.12,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "20",
    "policy_pages_p50_minus_bm25f": 1.0,
    "bm25f_compact_token_reduction_fraction": 0.34483612865139524,
    "bm25f_success_rate_minus_policy": 0.06730769230769229,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "reverse-pagerank",
    "page_size": "20",
    "policy_pages_p50_minus_bm25f": 0.5,
    "bm25f_compact_token_reduction_fraction": 0.31525813268343855,
    "bm25f_success_rate_minus_policy": 0.05897435897435899,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "hits-hub",
    "page_size": "20",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.3238515729060511,
    "bm25f_success_rate_minus_policy": 0.14058355437665782,
    "material_gap": true
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "all",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.0,
    "bm25f_success_rate_minus_policy": 0.0,
    "material_gap": false
  },
  {
    "mode": "search-only",
    "policy": "reverse-pagerank",
    "page_size": "all",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.0,
    "bm25f_success_rate_minus_policy": -0.08333333333333337,
    "material_gap": false
  },
  {
    "mode": "search-only",
    "policy": "hits-hub",
    "page_size": "all",
    "policy_pages_p50_minus_bm25f": 0.0,
    "bm25f_compact_token_reduction_fraction": 0.0,
    "bm25f_success_rate_minus_policy": -0.08333333333333337,
    "material_gap": false
  }
]
```

## Paired task-clustered policy deltas

Positive cost deltas mean BM25F saved cost relative to the named policy. Repeats are averaged within task before p50/p90 are computed.

```json
[
  {
    "mode": "navigation",
    "policy": "hits-hub",
    "page_size": "5",
    "n_tasks": 24,
    "n_pairs": 50,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.5972222222222222,
      "p50": 0.16666666666666666,
      "p90": 1.6666666666666667,
      "min": -1.0,
      "max": 2.3333333333333335
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 400.3194444444444,
      "p50": 64.5,
      "p90": 1331.1333333333332,
      "min": -124.0,
      "max": 1960.3333333333333
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1381.7777777777776,
      "p50": 74.0,
      "p90": 5427.033333333332,
      "min": -124.0,
      "max": 6216.333333333333
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 2.486111111111111,
      "p50": 0.0,
      "p90": 9.999999999999998,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 429.0438401944445,
      "p50": 319.63850566666673,
      "p90": 1104.1825317333332,
      "min": -1073.967002,
      "max": 2763.563321
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 11739.49930023611,
      "p50": 5621.440983166665,
      "p90": 33401.036186,
      "min": -20674.998354666663,
      "max": 39686.526248999995
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.6666666666666667,
      "p50": 0.8333333333333333,
      "p90": 4.8999999999999995,
      "min": -2.0,
      "max": 7.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 1.3055555555555556,
      "p50": 0.6666666666666667,
      "p90": 4.0,
      "min": -2.3333333333333335,
      "max": 7.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.1970030998821319,
      "p50": 0.09554069938289744,
      "p90": 0.633273223007338,
      "min": -0.2016260162601626,
      "max": 0.7747122148463145
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.23611111111111108,
      "p50": 0.0,
      "p90": 0.8999999999999997,
      "min": -0.6666666666666666,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 19,
      "mean": 3713.761607605264,
      "p50": 235.14698399999997,
      "p90": 11771.055828066663,
      "min": -721.4994623333331,
      "max": 32345.965797500005
    }
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "5",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 1.1666666666666667,
      "p50": 1.0,
      "p90": 3.0,
      "min": 0.0,
      "max": 4.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 508.75,
      "p50": 138.5,
      "p90": 1353.9999999999998,
      "min": -87.0,
      "max": 3120.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1948.0416666666667,
      "p50": 608.0,
      "p90": 5782.2,
      "min": -87.0,
      "max": 7221.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 3.5833333333333335,
      "p50": 1.0,
      "p90": 11.0,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 881.907863,
      "p50": 651.7915720000001,
      "p90": 2524.1085667999996,
      "min": -355.6722490000002,
      "max": 2814.277475
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 21074.757944833334,
      "p50": 14351.897409,
      "p90": 49762.6670238,
      "min": -29573.385960999993,
      "max": 63813.472141000006
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 2.7083333333333335,
      "p50": 2.5,
      "p90": 6.699999999999999,
      "min": -3.0,
      "max": 8.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 2.0416666666666665,
      "p50": 2.0,
      "p90": 5.0,
      "min": -4.0,
      "max": 7.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.2864579177677007,
      "p50": 0.19318181818181818,
      "p90": 0.6736459021629003,
      "min": -0.1334355828220859,
      "max": 0.8106001558846454
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.5,
      "p50": 1.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 12,
      "mean": 1440.9697267499996,
      "p50": 61.663755999999466,
      "p90": 2692.430162300001,
      "min": -801.6341000000002,
      "max": 14587.384377999999
    }
  },
  {
    "mode": "navigation",
    "policy": "reverse-pagerank",
    "page_size": "5",
    "n_tasks": 24,
    "n_pairs": 50,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.2638888888888889,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 2.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 55.833333333333336,
      "p50": 35.5,
      "p90": 110.39999999999998,
      "min": -81.0,
      "max": 680.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 117.375,
      "p50": 35.5,
      "p90": 110.39999999999998,
      "min": -81.0,
      "max": 2157.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.18055555555555555,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 4.333333333333333
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 276.3416241388889,
      "p50": 108.2443881666667,
      "p90": 817.6880450666665,
      "min": -747.7478980000001,
      "max": 1697.7387873333334
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 3920.2180957361106,
      "p50": 2368.3059525,
      "p90": 14236.0470794,
      "min": -26205.150166999996,
      "max": 36432.995499
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.9583333333333334,
      "p50": 0.6666666666666666,
      "p90": 3.466666666666666,
      "min": -3.3333333333333335,
      "max": 4.666666666666667
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.9027777777777777,
      "p50": 1.0,
      "p90": 3.2666666666666657,
      "min": -4.0,
      "max": 5.333333333333333
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.06270581137731412,
      "p50": 0.04793317985779229,
      "p90": 0.16584507042253518,
      "min": -0.12310030395136778,
      "max": 0.5379746835443038
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.06944444444444443,
      "p50": 0.0,
      "p90": 0.3333333333333333,
      "min": -0.6666666666666666,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 403.5590860555555,
      "p50": -98.74779233333342,
      "p90": 690.8620064999998,
      "min": -1414.7582849999994,
      "max": 11391.402097999999
    }
  },
  {
    "mode": "navigation",
    "policy": "hits-hub",
    "page_size": "10",
    "n_tasks": 24,
    "n_pairs": 48,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.47222222222222227,
      "p50": 0.3333333333333333,
      "p90": 1.0,
      "min": 0.0,
      "max": 1.6666666666666667
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 466.90277777777777,
      "p50": 104.0,
      "p90": 2188.6333333333323,
      "min": -185.0,
      "max": 2615.6666666666665
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1216.4027777777778,
      "p50": 104.0,
      "p90": 5694.399999999999,
      "min": -185.0,
      "max": 6801.666666666667
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 2.0416666666666665,
      "p50": 0.0,
      "p90": 9.766666666666664,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 469.6090636805555,
      "p50": 315.9131518333333,
      "p90": 1151.4083530999999,
      "min": -1231.89125,
      "max": 4859.610112666666
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 13689.406064208333,
      "p50": 9173.581279,
      "p90": 33172.69340646666,
      "min": -766.0174079999997,
      "max": 52375.129602
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 2.375,
      "p50": 2.0,
      "p90": 5.033333333333332,
      "min": 0.0,
      "max": 8.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 2.055555555555556,
      "p50": 2.0,
      "p90": 3.6666666666666665,
      "min": 0.0,
      "max": 8.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.14700573270219433,
      "p50": 0.07556880902735189,
      "p90": 0.5872068979453425,
      "min": -0.1450980392156863,
      "max": 0.6647769379216552
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.25,
      "p50": 0.0,
      "p90": 1.0,
      "min": -0.6666666666666666,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 21,
      "mean": 3179.646127595238,
      "p50": 87.700605,
      "p90": 11824.224447666667,
      "min": -1090.7858969999997,
      "max": 37792.5884515
    }
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "10",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.7083333333333334,
      "p50": 0.5,
      "p90": 2.0,
      "min": 0.0,
      "max": 2.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 866.9583333333334,
      "p50": 229.0,
      "p90": 2799.2,
      "min": -129.0,
      "max": 3094.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 2360.0416666666665,
      "p50": 315.5,
      "p90": 7132.5,
      "min": -129.0,
      "max": 8027.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 3.7916666666666665,
      "p50": 0.0,
      "p90": 11.0,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 548.3991295,
      "p50": 422.10400899999996,
      "p90": 994.9114098000001,
      "min": -1380.453434,
      "max": 3777.8415030000006
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 21009.888122,
      "p50": 20599.2494385,
      "p90": 42209.1603622,
      "min": -7179.585558999999,
      "max": 64285.945287999995
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 3.5833333333333335,
      "p50": 4.0,
      "p90": 7.0,
      "min": -1.0,
      "max": 8.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 2.7916666666666665,
      "p50": 3.0,
      "p90": 5.699999999999999,
      "min": -1.0,
      "max": 7.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.2688559211430697,
      "p50": 0.16447848285922684,
      "p90": 0.6682740172320514,
      "min": -0.09662921348314607,
      "max": 0.7119190059825127
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.25,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 15,
      "mean": 2173.5644534666662,
      "p50": -89.54532900000049,
      "p90": 9663.851318999994,
      "min": -2023.965471999999,
      "max": 19293.470719999998
    }
  },
  {
    "mode": "navigation",
    "policy": "reverse-pagerank",
    "page_size": "10",
    "n_tasks": 24,
    "n_pairs": 48,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.06944444444444443,
      "p50": 0.0,
      "p90": 0.5666666666666664,
      "min": -1.0,
      "max": 1.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 35.041666666666664,
      "p50": 57.0,
      "p90": 172.5,
      "min": -144.0,
      "max": 245.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 35.041666666666664,
      "p50": 57.0,
      "p90": 172.5,
      "min": -144.0,
      "max": 245.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 127.03222859722224,
      "p50": 70.43802799999997,
      "p90": 988.7752218999997,
      "min": -1477.4527643333333,
      "max": 1281.9621490000002
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 5218.158493208333,
      "p50": 2021.4889366666669,
      "p90": 16973.56932809999,
      "min": -12073.803723333333,
      "max": 46089.048611666665
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.8194444444444445,
      "p50": 0.0,
      "p90": 3.6333333333333315,
      "min": -2.0,
      "max": 6.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.7222222222222222,
      "p50": 0.0,
      "p90": 2.966666666666665,
      "min": -2.0,
      "max": 6.333333333333333
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.026253214403910704,
      "p50": 0.04433389499250251,
      "p90": 0.13114220990618122,
      "min": -0.11472602739726027,
      "max": 0.18716577540106952
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.125,
      "p50": 0.0,
      "p90": 0.5666666666666664,
      "min": -0.3333333333333333,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 182.76839543055544,
      "p50": 228.4942965000004,
      "p90": 976.6642673999994,
      "min": -1056.4704450000004,
      "max": 3195.816852999999
    }
  },
  {
    "mode": "navigation",
    "policy": "hits-hub",
    "page_size": "20",
    "n_tasks": 24,
    "n_pairs": 25,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.20833333333333334,
      "p50": 0.0,
      "p90": 1.0,
      "min": 0.0,
      "max": 1.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 470.2916666666667,
      "p50": 44.0,
      "p90": 2161.499999999998,
      "min": -404.0,
      "max": 3164.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 969.125,
      "p50": 44.0,
      "p90": 4914.5,
      "min": -404.0,
      "max": 6722.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.3333333333333333,
      "p50": 0.0,
      "p90": 5.399999999999999,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 113.3966605,
      "p50": 57.42214250000001,
      "p90": 924.7555158999996,
      "min": -2586.4524610000003,
      "max": 2078.5317480000003
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 31138.414232458334,
      "p50": 2704.0244995000003,
      "p90": 37511.25545389998,
      "min": -16355.97253,
      "max": 588020.338583
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.5416666666666667,
      "p50": 0.0,
      "p90": 7.0,
      "min": -2.0,
      "max": 8.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 1.5,
      "p50": 0.5,
      "p90": 6.699999999999999,
      "min": -1.0,
      "max": 7.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.10038832480596761,
      "p50": 0.01592782818271687,
      "p90": 0.41134376533580147,
      "min": -0.15695415695415696,
      "max": 0.5755866836456249
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.0625,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 22,
      "mean": 997.3730785454544,
      "p50": -124.66717050000034,
      "p90": 1507.1367179,
      "min": -2655.587592,
      "max": 14765.664906999998
    }
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "20",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.75,
      "p50": 1.0,
      "p90": 1.6999999999999993,
      "min": 0.0,
      "max": 3.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1078.5416666666667,
      "p50": 424.5,
      "p90": 3089.1,
      "min": -173.0,
      "max": 5229.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1827.5416666666667,
      "p50": 439.5,
      "p90": 5505.3,
      "min": -173.0,
      "max": 7141.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 2.25,
      "p50": 0.0,
      "p90": 7.399999999999999,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 394.302324,
      "p50": 377.6925269999999,
      "p90": 1513.2002491999997,
      "min": -2766.566032,
      "max": 1769.039973
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 17447.7525795,
      "p50": 13974.306627500002,
      "p90": 45013.52589089999,
      "min": -17515.515696,
      "max": 69534.881964
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 2.7083333333333335,
      "p50": 3.0,
      "p90": 5.699999999999999,
      "min": -2.0,
      "max": 7.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 1.9166666666666667,
      "p50": 2.0,
      "p90": 4.0,
      "min": -1.0,
      "max": 6.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.20883378894871732,
      "p50": 0.15157648139992516,
      "p90": 0.5489524178560409,
      "min": -0.06376704754883893,
      "max": 0.6371390276593152
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.125,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 21,
      "mean": 4513.4789969047615,
      "p50": -98.4992059999995,
      "p90": 20346.030681,
      "min": -1138.8398770000003,
      "max": 25889.754511
    }
  },
  {
    "mode": "navigation",
    "policy": "reverse-pagerank",
    "page_size": "20",
    "n_tasks": 24,
    "n_pairs": 26,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.08333333333333333,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 1.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 41.375,
      "p50": 0.0,
      "p90": 353.1,
      "min": -359.0,
      "max": 525.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 41.375,
      "p50": 0.0,
      "p90": 353.1,
      "min": -359.0,
      "max": 525.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 141.97438280555556,
      "p50": 25.615790500000003,
      "p90": 673.9159977,
      "min": -715.8860980000004,
      "max": 2016.9114590000001
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 3358.658356152778,
      "p50": 2027.9574475000009,
      "p90": 19299.063113399985,
      "min": -14870.806536999999,
      "max": 30628.119120000003
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.9166666666666666,
      "p50": 0.0,
      "p90": 4.4999999999999964,
      "min": -2.0,
      "max": 9.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.875,
      "p50": 0.0,
      "p90": 3.099999999999998,
      "min": -1.0,
      "max": 9.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.015949569069880664,
      "p50": 0.0,
      "p90": 0.1345460319670063,
      "min": -0.13707521954944635,
      "max": 0.2008416220351951
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": -0.05555555555555555,
      "p50": 0.0,
      "p90": 0.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -316.1059788055556,
      "p50": 23.574768999999378,
      "p90": 676.6191297999992,
      "min": -3199.507839,
      "max": 1133.3126519999996
    }
  },
  {
    "mode": "navigation",
    "policy": "hits-hub",
    "page_size": "all",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 215.85666070833335,
      "p50": 91.64091299999998,
      "p90": 971.6985984,
      "min": -1249.949737,
      "max": 2474.458544
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 8256.151565041668,
      "p50": 1589.2790009999999,
      "p90": 36277.70956689998,
      "min": -2752.005873000002,
      "max": 48515.83431
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.125,
      "p50": 0.0,
      "p90": 5.0,
      "min": -1.0,
      "max": 7.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.875,
      "p50": 0.0,
      "p90": 3.6999999999999993,
      "min": -1.0,
      "max": 6.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.16666666666666666,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -25.211309333333304,
      "p50": 142.45824900000002,
      "p90": 829.6935775000001,
      "min": -2124.4716869999993,
      "max": 1407.2802279999996
    }
  },
  {
    "mode": "navigation",
    "policy": "key",
    "page_size": "all",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 397.61505241666663,
      "p50": 66.86098349999997,
      "p90": 750.1889482,
      "min": -1269.672909,
      "max": 7720.034422999999
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 11722.651222291666,
      "p50": 1146.8885690000006,
      "p90": 42853.04503549999,
      "min": -5584.385904000001,
      "max": 54520.052997000006
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.625,
      "p50": 0.0,
      "p90": 6.699999999999999,
      "min": -2.0,
      "max": 8.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 1.4583333333333333,
      "p50": 0.0,
      "p90": 6.099999999999998,
      "min": -2.0,
      "max": 9.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.20833333333333334,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 195.5840914583333,
      "p50": 101.61904649999974,
      "p90": 1450.6124358999996,
      "min": -1887.5140209999995,
      "max": 1905.1091250000004
    }
  },
  {
    "mode": "navigation",
    "policy": "reverse-pagerank",
    "page_size": "all",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 8.272746541666665,
      "p50": 91.22072200000002,
      "p90": 383.4202200999999,
      "min": -1494.657751,
      "max": 686.5008949999999
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 2290.448989,
      "p50": 905.0527735000005,
      "p90": 7812.440418999996,
      "min": -3204.772713000002,
      "max": 13941.948078999998
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.4583333333333333,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 7.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.4166666666666667,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 7.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.6999999999999993,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -41.52311370833329,
      "p50": 118.89054950000036,
      "p90": 881.8914838999997,
      "min": -2132.9548499999996,
      "max": 1383.1417140000003
    }
  },
  {
    "mode": "search-only",
    "policy": "hits-hub",
    "page_size": "5",
    "n_tasks": 24,
    "n_pairs": 46,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.3194444444444444,
      "p50": 1.0,
      "p90": 2.0,
      "min": -4.0,
      "max": 3.6666666666666665
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 504.3611111111111,
      "p50": 74.0,
      "p90": 1417.0999999999997,
      "min": -124.0,
      "max": 3868.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1077.3472222222222,
      "p50": 74.0,
      "p90": 2703.566666666666,
      "min": -124.0,
      "max": 6776.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.888888888888889,
      "p50": 0.0,
      "p90": 5.499999999999998,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 88.94845013888887,
      "p50": 141.74530416666664,
      "p90": 946.7088021333333,
      "min": -1499.4789369999999,
      "max": 1430.6646326666666
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 3624.730321444444,
      "p50": 4227.823981166667,
      "p90": 26891.292427433324,
      "min": -60311.053581,
      "max": 41631.189677999995
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.8888888888888888,
      "p50": 1.0,
      "p90": 3.2333333333333334,
      "min": -3.0,
      "max": 4.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.2031925921947,
      "p50": 0.11257035647279551,
      "p90": 0.6797545445075264,
      "min": -0.2016260162601626,
      "max": 0.8741242937853108
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.23611111111111108,
      "p50": 0.0,
      "p90": 0.8999999999999997,
      "min": -0.3333333333333333,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 21,
      "mean": 4669.963911666667,
      "p50": 341.1607860000001,
      "p90": 18438.477076,
      "min": -2798.5218,
      "max": 38020.996908
    }
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "5",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.125,
      "p50": 0.0,
      "p90": 3.6999999999999993,
      "min": -7.0,
      "max": 6.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 475.9583333333333,
      "p50": 118.5,
      "p90": 2068.3999999999996,
      "min": -87.0,
      "max": 3120.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1389.7916666666667,
      "p50": 608.0,
      "p90": 4986.999999999999,
      "min": -87.0,
      "max": 7341.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 2.0,
      "p50": 1.0,
      "p90": 8.099999999999998,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -58.23828895833332,
      "p50": 34.45279100000002,
      "p90": 1785.5960529999995,
      "min": -3560.30723,
      "max": 2308.571525
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 4750.011671958334,
      "p50": 6674.039069499999,
      "p90": 39919.096629,
      "min": -89607.808832,
      "max": 55195.168002
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.5416666666666666,
      "p50": 0.0,
      "p90": 4.0,
      "min": -5.0,
      "max": 5.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.22690739645707636,
      "p50": 0.1589204001964789,
      "p90": 0.7585778561677534,
      "min": -0.1334355828220859,
      "max": 0.8150728309056364
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.4583333333333333,
      "p50": 0.5,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 12,
      "mean": 1294.9055065833334,
      "p50": -332.91815750000023,
      "p90": 779.2699281000006,
      "min": -2267.3794989999997,
      "max": 20480.889085
    }
  },
  {
    "mode": "search-only",
    "policy": "reverse-pagerank",
    "page_size": "5",
    "n_tasks": 24,
    "n_pairs": 46,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.9027777777777778,
      "p50": 1.0,
      "p90": 2.9,
      "min": -5.0,
      "max": 4.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 55.833333333333336,
      "p50": 35.5,
      "p90": 110.39999999999998,
      "min": -81.0,
      "max": 680.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 98.79166666666667,
      "p50": 35.5,
      "p90": 110.39999999999998,
      "min": -81.0,
      "max": 1711.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.125,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 3.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 257.34734730555556,
      "p50": 237.92332150000001,
      "p90": 1273.1694140999998,
      "min": -2564.8995539999996,
      "max": 2188.9753566666664
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 11599.483853875,
      "p50": 10580.7969605,
      "p90": 31935.190773,
      "min": -33628.396251,
      "max": 66799.935461
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.4583333333333333,
      "p50": 1.0,
      "p90": 4.8,
      "min": -2.0,
      "max": 5.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.06270581137731412,
      "p50": 0.04793317985779229,
      "p90": 0.16584507042253518,
      "min": -0.12310030395136778,
      "max": 0.5379746835443038
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.19444444444444445,
      "p50": 0.0,
      "p90": 1.0,
      "min": -0.3333333333333333,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 137.59278099999986,
      "p50": 9.57712066666636,
      "p90": 588.9350410333336,
      "min": -2838.3691160000003,
      "max": 9725.716922
    }
  },
  {
    "mode": "search-only",
    "policy": "hits-hub",
    "page_size": "10",
    "n_tasks": 24,
    "n_pairs": 50,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.4166666666666667,
      "p50": 0.16666666666666666,
      "p90": 1.5666666666666664,
      "min": -2.0,
      "max": 2.6666666666666665
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 465.09722222222223,
      "p50": 104.0,
      "p90": 1972.4333333333332,
      "min": -185.0,
      "max": 2604.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1086.3194444444446,
      "p50": 104.0,
      "p90": 4807.699999999999,
      "min": -185.0,
      "max": 7157.666666666667
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.4722222222222223,
      "p50": 0.0,
      "p90": 6.2333333333333325,
      "min": 0.0,
      "max": 10.666666666666666
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 155.05611486111115,
      "p50": 107.3366495,
      "p90": 985.3276699333333,
      "min": -1436.0830093333332,
      "max": 1209.203064
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 6832.513962486111,
      "p50": 11111.592648833333,
      "p90": 26354.83207463333,
      "min": -58139.58753699999,
      "max": 52140.713152000004
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.8333333333333334,
      "p50": 1.0,
      "p90": 3.3333333333333335,
      "min": -4.333333333333333,
      "max": 5.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.14801391352379475,
      "p50": 0.07556880902735189,
      "p90": 0.5597054341215225,
      "min": -0.1450980392156863,
      "max": 0.6647769379216552
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.08333333333333333,
      "p50": 0.0,
      "p90": 0.8999999999999997,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 20,
      "mean": 1920.5615826416665,
      "p50": 129.86252016666654,
      "p90": 2819.0321474333496,
      "min": -1402.5291660000003,
      "max": 23731.4688175
    }
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "10",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 1.0416666666666667,
      "p50": 1.0,
      "p90": 3.0,
      "min": -3.0,
      "max": 3.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1084.0833333333333,
      "p50": 269.5,
      "p90": 3089.5,
      "min": -129.0,
      "max": 4238.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 2203.6666666666665,
      "p50": 315.5,
      "p90": 6959.5,
      "min": -129.0,
      "max": 7805.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 2.9583333333333335,
      "p50": 0.0,
      "p90": 10.399999999999999,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 236.848435125,
      "p50": 396.00976750000007,
      "p90": 1241.5189629000001,
      "min": -2264.811322,
      "max": 2893.839279
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 14707.895711291667,
      "p50": 14384.75658,
      "p90": 39361.801751399995,
      "min": -32673.498469,
      "max": 66487.849015
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.3333333333333333,
      "p50": 1.5,
      "p90": 4.0,
      "min": -5.0,
      "max": 7.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.3043516068429796,
      "p50": 0.19178590997086908,
      "p90": 0.7104930515465094,
      "min": -0.09662921348314607,
      "max": 0.7594982078853046
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.20833333333333334,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 19,
      "mean": 6832.910590368421,
      "p50": 347.2741390000001,
      "p90": 25813.3665854,
      "min": -1673.123459,
      "max": 38223.696578999996
    }
  },
  {
    "mode": "search-only",
    "policy": "reverse-pagerank",
    "page_size": "10",
    "n_tasks": 24,
    "n_pairs": 50,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.6805555555555555,
      "p50": 0.5,
      "p90": 1.8999999999999997,
      "min": -1.6666666666666667,
      "max": 4.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 35.041666666666664,
      "p50": 57.0,
      "p90": 172.5,
      "min": -144.0,
      "max": 245.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 35.041666666666664,
      "p50": 57.0,
      "p90": 172.5,
      "min": -144.0,
      "max": 245.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 289.8938111527778,
      "p50": 180.9971930000001,
      "p90": 1370.9045109999995,
      "min": -1367.503765,
      "max": 1695.471164
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 9158.261068652779,
      "p50": 6904.260341500001,
      "p90": 35760.0818506,
      "min": -43265.860199999996,
      "max": 54673.99155866667
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.9722222222222222,
      "p50": 0.16666666666666666,
      "p90": 4.2333333333333325,
      "min": -2.0,
      "max": 5.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.026253214403910704,
      "p50": 0.04433389499250251,
      "p90": 0.13114220990618122,
      "min": -0.11472602739726027,
      "max": 0.18716577540106952
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.20833333333333334,
      "p50": 0.0,
      "p90": 1.0,
      "min": -0.3333333333333333,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 123.39682459722233,
      "p50": 33.964330333333315,
      "p90": 1145.7829958000002,
      "min": -1316.4230710000002,
      "max": 2406.6598083333333
    }
  },
  {
    "mode": "search-only",
    "policy": "hits-hub",
    "page_size": "20",
    "n_tasks": 24,
    "n_pairs": 25,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.5833333333333334,
      "p50": 0.0,
      "p90": 2.0,
      "min": -2.0,
      "max": 3.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 467.75,
      "p50": 44.0,
      "p90": 2208.3999999999983,
      "min": -404.0,
      "max": 3164.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 810.2083333333334,
      "p50": 44.0,
      "p90": 3575.699999999999,
      "min": -404.0,
      "max": 6197.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.875,
      "p50": 0.0,
      "p90": 4.0,
      "min": 0.0,
      "max": 8.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -98.62142870833331,
      "p50": 30.446720499999998,
      "p90": 902.7470307,
      "min": -3072.833521,
      "max": 1868.8926889999998
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 7994.5127534583335,
      "p50": 4698.668712999998,
      "p90": 37213.8688494,
      "min": -33030.814912,
      "max": 40440.27843
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.625,
      "p50": 0.0,
      "p90": 3.0,
      "min": -3.0,
      "max": 4.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.09969378959823516,
      "p50": 0.01592782818271687,
      "p90": 0.43251566597805713,
      "min": -0.15695415695415696,
      "max": 0.5755866836456249
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.20833333333333334,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 23,
      "mean": 2363.1088258913046,
      "p50": 270.9250540000003,
      "p90": 10837.231760400009,
      "min": -1213.8920049999997,
      "max": 22394.846188000003
    }
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "20",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.7083333333333334,
      "p50": 1.0,
      "p90": 2.0,
      "min": -2.0,
      "max": 3.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 963.2083333333334,
      "p50": 393.5,
      "p90": 3089.1,
      "min": -231.0,
      "max": 5535.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 1631.7083333333333,
      "p50": 396.0,
      "p90": 4608.799999999999,
      "min": -173.0,
      "max": 9653.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.7083333333333333,
      "p50": 0.0,
      "p90": 4.699999999999999,
      "min": 0.0,
      "max": 11.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -98.14900349999999,
      "p50": -13.110538499999961,
      "p90": 734.6872027999998,
      "min": -3582.266187,
      "max": 1344.828781
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 10910.699326791668,
      "p50": 11426.082653,
      "p90": 33993.614902099995,
      "min": -32267.186802999997,
      "max": 48111.179893
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.9583333333333334,
      "p50": 1.0,
      "p90": 4.0,
      "min": -4.0,
      "max": 6.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.18539996001683876,
      "p50": 0.13502518986389955,
      "p90": 0.5489524178560409,
      "min": -0.08409173643975246,
      "max": 0.6751646743108075
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.08333333333333333,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 20,
      "mean": 3154.9013101499995,
      "p50": 47.518348999999944,
      "p90": 13789.3947559,
      "min": -780.1834500000004,
      "max": 20958.070424999998
    }
  },
  {
    "mode": "search-only",
    "policy": "reverse-pagerank",
    "page_size": "20",
    "n_tasks": 24,
    "n_pairs": 26,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.4166666666666667,
      "p50": 0.0,
      "p90": 1.6999999999999993,
      "min": -1.0,
      "max": 2.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 41.375,
      "p50": 0.0,
      "p90": 353.1,
      "min": -359.0,
      "max": 525.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 41.375,
      "p50": 0.0,
      "p90": 353.1,
      "min": -359.0,
      "max": 525.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 99.89698008333336,
      "p50": 41.857507999999996,
      "p90": 1337.2728341999998,
      "min": -3219.306811,
      "max": 3206.1337670000003
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 11245.158192750001,
      "p50": 4700.209065999999,
      "p90": 41814.216681900005,
      "min": -46590.90160899999,
      "max": 51961.886891999995
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": 1.4305555555555556,
      "p50": 0.16666666666666666,
      "p90": 5.0,
      "min": -5.0,
      "max": 8.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.015949569069880664,
      "p50": 0.0,
      "p90": 0.1345460319670063,
      "min": -0.13707521954944635,
      "max": 0.2008416220351951
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.05555555555555556,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -203.40722883333333,
      "p50": -236.32456600000023,
      "p90": 767.9413421999993,
      "min": -1534.6773359999997,
      "max": 2692.053505
    }
  },
  {
    "mode": "search-only",
    "policy": "hits-hub",
    "page_size": "all",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -201.985280375,
      "p50": -75.64683749999998,
      "p90": 539.8420295,
      "min": -5037.001411,
      "max": 1131.28545
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 1625.4740251249993,
      "p50": 409.42893549999917,
      "p90": 6690.6055723,
      "min": -45052.652537,
      "max": 69976.17315599999
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": -0.125,
      "p50": 0.0,
      "p90": 0.0,
      "min": -6.0,
      "max": 6.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": -0.08333333333333333,
      "p50": 0.0,
      "p90": 0.6999999999999993,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -61.78939741666668,
      "p50": -32.40096549999976,
      "p90": 1011.4186277999999,
      "min": -2043.6267050000001,
      "max": 1994.1490860000004
    }
  },
  {
    "mode": "search-only",
    "policy": "key",
    "page_size": "all",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -156.94490370833333,
      "p50": 33.09384349999999,
      "p90": 620.9334964999998,
      "min": -3997.661073,
      "max": 857.3606219999999
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 240.80750954166652,
      "p50": 530.0340969999997,
      "p90": 10080.2701845,
      "min": -43394.35394,
      "max": 29028.984326
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": -0.375,
      "p50": 0.0,
      "p90": 0.0,
      "min": -7.0,
      "max": 3.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 1.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -141.26437570833332,
      "p50": 32.956552999999985,
      "p90": 684.0509698,
      "min": -1428.8455019999997,
      "max": 946.1968710000001
    }
  },
  {
    "mode": "search-only",
    "policy": "reverse-pagerank",
    "page_size": "all",
    "n_tasks": 24,
    "n_pairs": 24,
    "pages_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_tokens_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "tool_calls_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "retrieval_latency_ms_saved_by_bm25f": {
      "n": 24,
      "mean": -146.74711433333334,
      "p50": 59.171805000000006,
      "p90": 276.55419919999997,
      "min": -5015.368113,
      "max": 2160.3830749999997
    },
    "end_to_end_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 1194.1976069583334,
      "p50": 534.0575050000007,
      "p90": 7555.711696399998,
      "min": -23929.044378,
      "max": 29081.820384000002
    },
    "recalls_saved_by_bm25f": {
      "n": 24,
      "mean": -0.041666666666666664,
      "p50": 0.0,
      "p90": 0.0,
      "min": -4.0,
      "max": 4.0
    },
    "graph_hops_saved_by_bm25f": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "compact_token_reduction_fraction": {
      "n": 24,
      "mean": 0.0,
      "p50": 0.0,
      "p90": 0.0,
      "min": 0.0,
      "max": 0.0
    },
    "success_delta_bm25f_minus_policy": {
      "n": 24,
      "mean": -0.08333333333333333,
      "p50": 0.0,
      "p90": 0.0,
      "min": -1.0,
      "max": 1.0
    },
    "time_to_useful_ms_saved_by_bm25f": {
      "n": 24,
      "mean": 190.89050366666663,
      "p50": 134.65788550000002,
      "p90": 947.4242950999998,
      "min": -1842.4223990000005,
      "max": 5260.052535000002
    }
  }
]
```

## Pre-registered mechanism gate

The gate remains insufficient until the complete 24-task, four-policy, two-mode, four-page-size initial grid is present.

```json
{
  "initial_grid_complete": true,
  "task_count": 24,
  "expected_initial_cells": 768,
  "observed_initial_cells": 768,
  "thresholds": {
    "median_pages_saved": 1.0,
    "median_compact_token_reduction_fraction": 0.2,
    "requires_no_task_success_regression": true,
    "requires_navigation_advantage": true
  },
  "status": "prefer-query-independent-ordering-navigation",
  "navigation_advantage_material": false,
  "no_task_success_regression": true,
  "comparisons": [
    {
      "policy": "reverse-pagerank",
      "n_tasks": 24,
      "navigation_pages_saved_by_bm25f": {
        "n": 24,
        "mean": 0.1484126984126984,
        "p50": 0.0,
        "p90": 0.42857142857142855,
        "min": -0.42857142857142855,
        "max": 1.0
      },
      "navigation_compact_token_reduction_fraction": {
        "n": 24,
        "mean": 0.03723501658703173,
        "p50": 0.02821519454978587,
        "p90": 0.1395450583515546,
        "min": -0.09007460624481901,
        "max": 0.2139887582108422
      },
      "all_mode_success_delta_bm25f_minus_policy": 0.11686507936507935,
      "navigation_success_delta_bm25f_minus_policy": 0.07222222222222222,
      "navigation_advantage_material": false,
      "no_task_success_regression": true
    },
    {
      "policy": "hits-hub",
      "n_tasks": 24,
      "navigation_pages_saved_by_bm25f": {
        "n": 24,
        "mean": 0.46369047619047615,
        "p50": 0.3333333333333333,
        "p90": 1.2428571428571429,
        "min": -0.3333333333333333,
        "max": 1.5714285714285714
      },
      "navigation_compact_token_reduction_fraction": {
        "n": 24,
        "mean": 0.15332060088244753,
        "p50": 0.09697442547877677,
        "p90": 0.5982417546573771,
        "min": -0.153025663186091,
        "max": 0.6339000492649988
      },
      "all_mode_success_delta_bm25f_minus_policy": 0.19107142857142856,
      "navigation_success_delta_bm25f_minus_policy": 0.20952380952380953,
      "navigation_advantage_material": false,
      "no_task_success_regression": true
    }
  ]
}
```

## Targeted repeat cells

```json
[
  {
    "task_id": "ordering-100-failure-scope",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-failure-scope",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-failure-scope",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-field-alias",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-field-alias",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-field-alias",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-field-alias",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-field-alias",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-field-alias",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-field-alias",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-field-alias",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-lockfile-recovery",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-lockfile-recovery",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-lockfile-recovery",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-lockfile-recovery",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-lockfile-recovery",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-queue-backpressure",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-queue-backpressure",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-queue-backpressure",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-queue-backpressure",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-queue-backpressure",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-relative-check-path",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-relative-check-path",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-relative-check-path",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "agent-or-tool-failure",
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-relative-check-path",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-relative-check-path",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-relative-check-path",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-retry-jitter",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-retry-jitter",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-retry-jitter",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-retry-jitter",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-retry-jitter",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-retry-jitter",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-secret-redaction",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-secret-redaction",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-secret-redaction",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-secret-redaction",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-secret-redaction",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-stable-cursor",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-stable-cursor",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-stable-cursor",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-stable-cursor",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-stable-cursor",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-stable-cursor",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-100-stable-cursor",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-100-stable-cursor",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-config-precedence",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-config-precedence",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-config-precedence",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-config-precedence",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-config-precedence",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-config-precedence",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-config-precedence",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-mechanism-smoke",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-mechanism-smoke",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-mechanism-smoke",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-partial-total",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-sandbox-environment",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-temp-cleanup",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-temp-cleanup",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-temp-cleanup",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-token-refresh",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-token-refresh",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-token-refresh",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-webhook-dedup",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-webhook-dedup",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-webhook-dedup",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-webhook-dedup",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-webhook-dedup",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-webhook-dedup",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-50-webhook-dedup",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-50-webhook-dedup",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-bonus-round-flow",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-bonus-round-flow",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-bonus-round-flow",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-bonus-round-flow",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-bonus-round-flow",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-bonus-round-flow",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-bonus-round-flow",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-canary-rollback",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-canary-rollback",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-canary-rollback",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-canary-rollback",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-canary-rollback",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-manifest-signing",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-manifest-signing",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-manifest-signing",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-shard-rebalance",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-shard-rebalance",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-shard-rebalance",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-shard-rebalance",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-shard-rebalance",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-shard-rebalance",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-shard-rebalance",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-synthesis-shape",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-synthesis-shape",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-synthesis-shape",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-synthesis-shape",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-synthesis-shape",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-synthesis-shape",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-tenancy-fence",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-tenancy-fence",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-tenancy-fence",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-tenancy-fence",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-tenancy-fence",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-tenancy-fence",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-tenancy-fence",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-transcript-join",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-transcript-join",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-transcript-join",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-version-probe",
    "mode": "navigation",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-version-probe",
    "mode": "navigation",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-version-probe",
    "mode": "navigation",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-version-probe",
    "mode": "navigation",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-version-probe",
    "mode": "search-only",
    "page_size": "5",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-version-probe",
    "mode": "search-only",
    "page_size": "10",
    "reasons": [
      "policy-success-disagreement",
      "repeat-outcome-variance"
    ]
  },
  {
    "task_id": "ordering-500-version-probe",
    "mode": "search-only",
    "page_size": "20",
    "reasons": [
      "policy-success-disagreement"
    ]
  },
  {
    "task_id": "ordering-500-version-probe",
    "mode": "search-only",
    "page_size": "all",
    "reasons": [
      "policy-success-disagreement"
    ]
  }
]
```

## Search-only versus navigation

```json
[
  {
    "arm": "key",
    "page_size": "5",
    "navigation_retrieval_tokens_mean_delta": 659.208333333333,
    "navigation_latency_mean_delta_ms": 577.3693773333334,
    "navigation_success_rate_delta": 0.08333333333333334,
    "navigation_primary_reach_rate": 0.25,
    "navigation_graph_hops_mean": 3.1666666666666665
  },
  {
    "arm": "key",
    "page_size": "10",
    "navigation_retrieval_tokens_mean_delta": -723.75,
    "navigation_latency_mean_delta_ms": 21.47446683333328,
    "navigation_success_rate_delta": 0.16666666666666669,
    "navigation_primary_reach_rate": 0.375,
    "navigation_graph_hops_mean": 3.4583333333333335
  },
  {
    "arm": "key",
    "page_size": "20",
    "navigation_retrieval_tokens_mean_delta": -10.0,
    "navigation_latency_mean_delta_ms": 216.42072224999993,
    "navigation_success_rate_delta": 0.0,
    "navigation_primary_reach_rate": 0.5416666666666666,
    "navigation_graph_hops_mean": 2.4583333333333335
  },
  {
    "arm": "key",
    "page_size": "all",
    "navigation_retrieval_tokens_mean_delta": 628.9166666666661,
    "navigation_latency_mean_delta_ms": 407.57347408333334,
    "navigation_success_rate_delta": -0.16666666666666663,
    "navigation_primary_reach_rate": 0.16666666666666666,
    "navigation_graph_hops_mean": 1.9166666666666667
  },
  {
    "arm": "reverse-pagerank",
    "page_size": "5",
    "navigation_retrieval_tokens_mean_delta": -1349.3078260869565,
    "navigation_latency_mean_delta_ms": -198.1565027295651,
    "navigation_success_rate_delta": 0.23043478260869565,
    "navigation_primary_reach_rate": 0.82,
    "navigation_graph_hops_mean": 2.52
  },
  {
    "arm": "reverse-pagerank",
    "page_size": "10",
    "navigation_retrieval_tokens_mean_delta": -2162.188333333333,
    "navigation_latency_mean_delta_ms": -414.8947079174999,
    "navigation_success_rate_delta": 0.2641666666666666,
    "navigation_primary_reach_rate": 0.5416666666666666,
    "navigation_graph_hops_mean": 1.9166666666666667
  },
  {
    "arm": "reverse-pagerank",
    "page_size": "20",
    "navigation_retrieval_tokens_mean_delta": -2630.1642857142856,
    "navigation_latency_mean_delta_ms": -368.60372309761897,
    "navigation_success_rate_delta": 0.1880952380952381,
    "navigation_primary_reach_rate": 0.5357142857142857,
    "navigation_graph_hops_mean": 1.25
  },
  {
    "arm": "reverse-pagerank",
    "page_size": "all",
    "navigation_retrieval_tokens_mean_delta": 48.75,
    "navigation_latency_mean_delta_ms": 8.033378833333359,
    "navigation_success_rate_delta": -0.04166666666666674,
    "navigation_primary_reach_rate": 0.375,
    "navigation_graph_hops_mean": 0.875
  },
  {
    "arm": "hits-hub",
    "page_size": "5",
    "navigation_retrieval_tokens_mean_delta": 438.11478260869535,
    "navigation_latency_mean_delta_ms": 333.6998865643477,
    "navigation_success_rate_delta": 0.055652173913043446,
    "navigation_primary_reach_rate": 0.24,
    "navigation_graph_hops_mean": 3.3
  },
  {
    "arm": "hits-hub",
    "page_size": "10",
    "navigation_retrieval_tokens_mean_delta": -280.39999999999964,
    "navigation_latency_mean_delta_ms": 368.11378298249986,
    "navigation_success_rate_delta": -0.10333333333333333,
    "navigation_primary_reach_rate": 0.3125,
    "navigation_graph_hops_mean": 3.375
  },
  {
    "arm": "hits-hub",
    "page_size": "20",
    "navigation_retrieval_tokens_mean_delta": -1493.6970443349746,
    "navigation_latency_mean_delta_ms": -52.68608444211816,
    "navigation_success_rate_delta": 0.12684729064039413,
    "navigation_primary_reach_rate": 0.35714285714285715,
    "navigation_graph_hops_mean": 2.25
  },
  {
    "arm": "hits-hub",
    "page_size": "all",
    "navigation_retrieval_tokens_mean_delta": 484.54166666666606,
    "navigation_latency_mean_delta_ms": 270.8554590416667,
    "navigation_success_rate_delta": -0.20833333333333337,
    "navigation_primary_reach_rate": 0.25,
    "navigation_graph_hops_mean": 1.3333333333333333
  },
  {
    "arm": "bm25f",
    "page_size": "5",
    "navigation_retrieval_tokens_mean_delta": -75.54869565217405,
    "navigation_latency_mean_delta_ms": -81.60139672260868,
    "navigation_success_rate_delta": 0.02608695652173909,
    "navigation_primary_reach_rate": 0.52,
    "navigation_graph_hops_mean": 1.46
  },
  {
    "arm": "bm25f",
    "page_size": "10",
    "navigation_retrieval_tokens_mean_delta": -958.7216666666666,
    "navigation_latency_mean_delta_ms": -28.822752511666522,
    "navigation_success_rate_delta": 0.15166666666666662,
    "navigation_primary_reach_rate": 0.5,
    "navigation_graph_hops_mean": 0.875
  },
  {
    "arm": "bm25f",
    "page_size": "20",
    "navigation_retrieval_tokens_mean_delta": -877.5384615384614,
    "navigation_latency_mean_delta_ms": -305.60135349999996,
    "navigation_success_rate_delta": 0.07692307692307698,
    "navigation_primary_reach_rate": 0.38461538461538464,
    "navigation_graph_hops_mean": 0.5
  },
  {
    "arm": "bm25f",
    "page_size": "all",
    "navigation_retrieval_tokens_mean_delta": -117.79166666666606,
    "navigation_latency_mean_delta_ms": -146.9864820416666,
    "navigation_success_rate_delta": 0.04166666666666663,
    "navigation_primary_reach_rate": 0.4166666666666667,
    "navigation_graph_hops_mean": 0.4583333333333333
  }
]
```

## Bounded versus unbounded

```json
[
  {
    "mode": "navigation",
    "arm": "bm25f",
    "page_size": "5",
    "compact_tokens_mean_delta": -8660.56,
    "time_to_useful_mean_delta_ms": -9.795334656666455,
    "success_rate_delta": -0.09166666666666667
  },
  {
    "mode": "search-only",
    "arm": "bm25f",
    "page_size": "5",
    "compact_tokens_mean_delta": -8225.95652173913,
    "time_to_useful_mean_delta_ms": 78.5702337318844,
    "success_rate_delta": -0.07608695652173914
  },
  {
    "mode": "navigation",
    "arm": "bm25f",
    "page_size": "10",
    "compact_tokens_mean_delta": -8011.4375,
    "time_to_useful_mean_delta_ms": -45.5331685208339,
    "success_rate_delta": 0.0
  },
  {
    "mode": "search-only",
    "arm": "bm25f",
    "page_size": "10",
    "compact_tokens_mean_delta": -7221.98,
    "time_to_useful_mean_delta_ms": -168.26117799333315,
    "success_rate_delta": -0.10999999999999999
  },
  {
    "mode": "navigation",
    "arm": "bm25f",
    "page_size": "20",
    "compact_tokens_mean_delta": -6935.076923076923,
    "time_to_useful_mean_delta_ms": 146.61286396794821,
    "success_rate_delta": -0.022435897435897356
  },
  {
    "mode": "search-only",
    "arm": "bm25f",
    "page_size": "20",
    "compact_tokens_mean_delta": -6256.846153846154,
    "time_to_useful_mean_delta_ms": 87.7576946666668,
    "success_rate_delta": -0.05769230769230771
  },
  {
    "mode": "navigation",
    "arm": "hits-hub",
    "page_size": "5",
    "compact_tokens_mean_delta": -8029.5599999999995,
    "time_to_useful_mean_delta_ms": 3481.547592611111,
    "success_rate_delta": -0.265
  },
  {
    "mode": "search-only",
    "arm": "hits-hub",
    "page_size": "5",
    "compact_tokens_mean_delta": -7594.826086956522,
    "time_to_useful_mean_delta_ms": 4316.58885939762,
    "success_rate_delta": -0.5289855072463768
  },
  {
    "mode": "navigation",
    "arm": "hits-hub",
    "page_size": "10",
    "compact_tokens_mean_delta": -7134.520833333334,
    "time_to_useful_mean_delta_ms": 4001.844629092342,
    "success_rate_delta": -0.20833333333333331
  },
  {
    "mode": "search-only",
    "arm": "hits-hub",
    "page_size": "10",
    "compact_tokens_mean_delta": -6510.3,
    "time_to_useful_mean_delta_ms": 2208.870966570513,
    "success_rate_delta": -0.31333333333333335
  },
  {
    "mode": "navigation",
    "arm": "hits-hub",
    "page_size": "20",
    "compact_tokens_mean_delta": -6297.607142857143,
    "time_to_useful_mean_delta_ms": 1008.4918802166667,
    "success_rate_delta": 0.0535714285714286
  },
  {
    "mode": "search-only",
    "arm": "hits-hub",
    "page_size": "20",
    "compact_tokens_mean_delta": -4730.310344827586,
    "time_to_useful_mean_delta_ms": 2929.5173908333336,
    "success_rate_delta": -0.2816091954022989
  },
  {
    "mode": "navigation",
    "arm": "key",
    "page_size": "5",
    "compact_tokens_mean_delta": -7781.125,
    "time_to_useful_mean_delta_ms": 1232.0183780416655,
    "success_rate_delta": -0.375
  },
  {
    "mode": "search-only",
    "arm": "key",
    "page_size": "5",
    "compact_tokens_mean_delta": -7646.166666666667,
    "time_to_useful_mean_delta_ms": 1529.9159265416665,
    "success_rate_delta": -0.625
  },
  {
    "mode": "navigation",
    "arm": "key",
    "page_size": "10",
    "compact_tokens_mean_delta": -6951.833333333334,
    "time_to_useful_mean_delta_ms": 2030.214090424999,
    "success_rate_delta": -0.08333333333333337
  },
  {
    "mode": "search-only",
    "arm": "key",
    "page_size": "10",
    "compact_tokens_mean_delta": -5962.5,
    "time_to_useful_mean_delta_ms": 7016.948954427632,
    "success_rate_delta": -0.4166666666666667
  },
  {
    "mode": "navigation",
    "arm": "key",
    "page_size": "20",
    "compact_tokens_mean_delta": -4807.041666666667,
    "time_to_useful_mean_delta_ms": 4569.421045529762,
    "success_rate_delta": 0.04166666666666663
  },
  {
    "mode": "search-only",
    "arm": "key",
    "page_size": "20",
    "compact_tokens_mean_delta": -4579.333333333333,
    "time_to_useful_mean_delta_ms": 3382.7797630249997,
    "success_rate_delta": -0.125
  },
  {
    "mode": "navigation",
    "arm": "reverse-pagerank",
    "page_size": "5",
    "compact_tokens_mean_delta": -8416.119999999999,
    "time_to_useful_mean_delta_ms": 629.8093693716673,
    "success_rate_delta": -0.19166666666666665
  },
  {
    "mode": "search-only",
    "arm": "reverse-pagerank",
    "page_size": "5",
    "compact_tokens_mean_delta": -7312.521739130435,
    "time_to_useful_mean_delta_ms": -5.166186804348399,
    "success_rate_delta": -0.46376811594202905
  },
  {
    "mode": "navigation",
    "arm": "reverse-pagerank",
    "page_size": "10",
    "compact_tokens_mean_delta": -7837.9375,
    "time_to_useful_mean_delta_ms": 122.75352137500067,
    "success_rate_delta": -0.1875
  },
  {
    "mode": "search-only",
    "arm": "reverse-pagerank",
    "page_size": "10",
    "compact_tokens_mean_delta": -6269.280000000001,
    "time_to_useful_mean_delta_ms": -101.75182652000058,
    "success_rate_delta": -0.49333333333333335
  },
  {
    "mode": "navigation",
    "arm": "reverse-pagerank",
    "page_size": "20",
    "compact_tokens_mean_delta": -6708.892857142857,
    "time_to_useful_mean_delta_ms": -98.620778458333,
    "success_rate_delta": 0.029761904761904767
  },
  {
    "mode": "search-only",
    "arm": "reverse-pagerank",
    "page_size": "20",
    "compact_tokens_mean_delta": -4789.466666666666,
    "time_to_useful_mean_delta_ms": -102.51802506666718,
    "success_rate_delta": -0.20000000000000007
  }
]
```

## Cost per additional page

```json
[
  {
    "mode": "navigation",
    "arm": "bm25f",
    "page_size": "5",
    "n": 50,
    "compact_tokens_per_additional_page": -7.325396825396827,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 517.1793243571428,
    "end_to_end_ms_per_additional_page": 15761.4178633545
  },
  {
    "mode": "navigation",
    "arm": "bm25f",
    "page_size": "10",
    "n": 48,
    "compact_tokens_per_additional_page": 5.544444444444447,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 1924.0040282444447,
    "end_to_end_ms_per_additional_page": 12991.186501577777
  },
  {
    "mode": "navigation",
    "arm": "bm25f",
    "page_size": "20",
    "n": 26,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "navigation",
    "arm": "bm25f",
    "page_size": "all",
    "n": 24,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "navigation",
    "arm": "hits-hub",
    "page_size": "5",
    "n": 50,
    "compact_tokens_per_additional_page": 717.101907864123,
    "tool_calls_per_additional_page": 3.495114006514658,
    "retrieval_latency_ms_per_additional_page": 432.5285148501629,
    "end_to_end_ms_per_additional_page": 14087.455804324338
  },
  {
    "mode": "navigation",
    "arm": "hits-hub",
    "page_size": "10",
    "n": 48,
    "compact_tokens_per_additional_page": 1122.472972972973,
    "tool_calls_per_additional_page": 4.135135135135135,
    "retrieval_latency_ms_per_additional_page": 1123.1349681486486,
    "end_to_end_ms_per_additional_page": 20254.676509567566
  },
  {
    "mode": "navigation",
    "arm": "hits-hub",
    "page_size": "20",
    "n": 28,
    "compact_tokens_per_additional_page": 1561.1666666666667,
    "tool_calls_per_additional_page": 2.5,
    "retrieval_latency_ms_per_additional_page": 855.5238495000001,
    "end_to_end_ms_per_additional_page": -9445.97957489394
  },
  {
    "mode": "navigation",
    "arm": "hits-hub",
    "page_size": "all",
    "n": 24,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "navigation",
    "arm": "key",
    "page_size": "5",
    "n": 24,
    "compact_tokens_per_additional_page": 237.1883116883117,
    "tool_calls_per_additional_page": 0.7142857142857143,
    "retrieval_latency_ms_per_additional_page": 271.60065794805195,
    "end_to_end_ms_per_additional_page": 13029.507082902597
  },
  {
    "mode": "navigation",
    "arm": "key",
    "page_size": "10",
    "n": 24,
    "compact_tokens_per_additional_page": 1205.1379310344828,
    "tool_calls_per_additional_page": 4.189655172413793,
    "retrieval_latency_ms_per_additional_page": 714.4138789655174,
    "end_to_end_ms_per_additional_page": 19707.18914987931
  },
  {
    "mode": "navigation",
    "arm": "key",
    "page_size": "20",
    "n": 24,
    "compact_tokens_per_additional_page": 892.6034482758621,
    "tool_calls_per_additional_page": 1.4137931034482758,
    "retrieval_latency_ms_per_additional_page": 308.3210793103448,
    "end_to_end_ms_per_additional_page": 19965.941956499995
  },
  {
    "mode": "navigation",
    "arm": "key",
    "page_size": "all",
    "n": 24,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "navigation",
    "arm": "reverse-pagerank",
    "page_size": "5",
    "n": 50,
    "compact_tokens_per_additional_page": 24.66843300529902,
    "tool_calls_per_additional_page": 0.22634367903103716,
    "retrieval_latency_ms_per_additional_page": 481.16763271082516,
    "end_to_end_ms_per_additional_page": 12209.962110854656
  },
  {
    "mode": "navigation",
    "arm": "reverse-pagerank",
    "page_size": "10",
    "n": 48,
    "compact_tokens_per_additional_page": -51.85323741007194,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 521.5242513366907,
    "end_to_end_ms_per_additional_page": 19136.10683423453
  },
  {
    "mode": "navigation",
    "arm": "reverse-pagerank",
    "page_size": "20",
    "n": 28,
    "compact_tokens_per_additional_page": -50.65384615384616,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 1240.8480737307696,
    "end_to_end_ms_per_additional_page": 26553.702754923077
  },
  {
    "mode": "navigation",
    "arm": "reverse-pagerank",
    "page_size": "all",
    "n": 24,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "search-only",
    "arm": "bm25f",
    "page_size": "5",
    "n": 46,
    "compact_tokens_per_additional_page": -5.319796091758708,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 314.56689867969413,
    "end_to_end_ms_per_additional_page": 9894.159671801699
  },
  {
    "mode": "search-only",
    "arm": "bm25f",
    "page_size": "10",
    "n": 50,
    "compact_tokens_per_additional_page": -6.912408759124091,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 451.57872438394156,
    "end_to_end_ms_per_additional_page": 11521.247823532845
  },
  {
    "mode": "search-only",
    "arm": "bm25f",
    "page_size": "20",
    "n": 26,
    "compact_tokens_per_additional_page": -2.354430379746819,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 821.9768930210971,
    "end_to_end_ms_per_additional_page": 16041.069462489453
  },
  {
    "mode": "search-only",
    "arm": "bm25f",
    "page_size": "all",
    "n": 24,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "search-only",
    "arm": "hits-hub",
    "page_size": "5",
    "n": 46,
    "compact_tokens_per_additional_page": 279.5921933085502,
    "tool_calls_per_additional_page": 0.9736059479553903,
    "retrieval_latency_ms_per_additional_page": 332.8721517843866,
    "end_to_end_ms_per_additional_page": 10046.228140459853
  },
  {
    "mode": "search-only",
    "arm": "hits-hub",
    "page_size": "10",
    "n": 50,
    "compact_tokens_per_additional_page": 321.408015146734,
    "tool_calls_per_additional_page": 0.8059324708109815,
    "retrieval_latency_ms_per_additional_page": 271.16887175418117,
    "end_to_end_ms_per_additional_page": 12403.445584040708
  },
  {
    "mode": "search-only",
    "arm": "hits-hub",
    "page_size": "20",
    "n": 29,
    "compact_tokens_per_additional_page": 466.64329896907213,
    "tool_calls_per_additional_page": 0.6536082474226804,
    "retrieval_latency_ms_per_additional_page": 487.5471237061855,
    "end_to_end_ms_per_additional_page": 13718.866452961855
  },
  {
    "mode": "search-only",
    "arm": "hits-hub",
    "page_size": "all",
    "n": 24,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "search-only",
    "arm": "key",
    "page_size": "5",
    "n": 24,
    "compact_tokens_per_additional_page": 224.5625,
    "tool_calls_per_additional_page": 0.7,
    "retrieval_latency_ms_per_additional_page": 373.91026946249997,
    "end_to_end_ms_per_additional_page": 9356.902687975
  },
  {
    "mode": "search-only",
    "arm": "key",
    "page_size": "10",
    "n": 24,
    "compact_tokens_per_additional_page": 516.5909090909091,
    "tool_calls_per_additional_page": 1.25,
    "retrieval_latency_ms_per_additional_page": 386.68392430681826,
    "end_to_end_ms_per_additional_page": 11226.129913545456
  },
  {
    "mode": "search-only",
    "arm": "key",
    "page_size": "20",
    "n": 24,
    "compact_tokens_per_additional_page": 903.3125,
    "tool_calls_per_additional_page": 1.125,
    "retrieval_latency_ms_per_additional_page": 487.81850275,
    "end_to_end_ms_per_additional_page": 14878.558285375
  },
  {
    "mode": "search-only",
    "arm": "key",
    "page_size": "all",
    "n": 24,
    "compact_tokens_per_additional_page": null,
    "tool_calls_per_additional_page": null,
    "retrieval_latency_ms_per_additional_page": null,
    "end_to_end_ms_per_additional_page": null
  },
  {
    "mode": "search-only",
    "arm": "reverse-pagerank",
    "page_size": "5",
    "n": 46,
    "compact_tokens_per_additional_page": 10.98024948024948,
    "tool_calls_per_additional_page": 0.06860706860706861,
    "retrieval_latency_ms_per_additional_page": 278.8472260597713,
    "end_to_end_ms_per_additional_page": 9955.918159391891
  },
  {
    "mode": "search-only",
    "arm": "reverse-pagerank",
    "page_size": "10",
    "n": 50,
    "compact_tokens_per_additional_page": -14.461267605633804,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 609.2014422454728,
    "end_to_end_ms_per_additional_page": 12700.021460701208
  },
  {
    "mode": "search-only",
    "arm": "reverse-pagerank",
    "page_size": "20",
    "n": 30,
    "compact_tokens_per_additional_page": 27.949458483754512,
    "tool_calls_per_additional_page": 0.0,
    "retrieval_latency_ms_per_additional_page": 223.24353018772564,
    "end_to_end_ms_per_additional_page": 9019.486037353789
  },
  {
    "mode": "search-only",
    "arm": "reverse-pagerank",
    "page_size": "all",
    "n": 24,
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
  "navigation": {
    "pages_requested": 0.09152891351781622,
    "compact_result_tokens": -0.1642224212571971,
    "compact_tokens_to_first_useful": -0.13008898585003867,
    "retrieval_tokens_to_first_useful": -0.02480000491210571,
    "tool_calls_to_first_useful": 0.3633510675934819,
    "retrieval_tool_calls": 0.061620773603993206,
    "retrieval_latency_ms": 0.10657362431487677,
    "server_candidate_generation_ms": 0.06718247962578971,
    "server_ordering_ms": 0.1797284268012894,
    "end_to_end_ms": 0.11809616988785711,
    "full_recalls": 0.0381277829813797,
    "graph_hops_after_first_useful": 0.02235814067208168,
    "graph_hops_total": 0.009121311728850553,
    "reference_edges_exposed": -0.017477380591580497
  },
  "search-only": {
    "pages_requested": 0.042431504631240756,
    "compact_result_tokens": -0.17171397986684456,
    "compact_tokens_to_first_useful": -0.11747803790757498,
    "retrieval_tokens_to_first_useful": -0.02972480073062592,
    "tool_calls_to_first_useful": 0.36611608265942996,
    "retrieval_tool_calls": 0.038799969013315054,
    "retrieval_latency_ms": 0.09458068369727146,
    "server_candidate_generation_ms": 0.02884685837069184,
    "server_ordering_ms": -0.010913820262274946,
    "end_to_end_ms": 0.03533744647978604,
    "full_recalls": 0.059781168179351266,
    "graph_hops_after_first_useful": null,
    "graph_hops_total": null,
    "reference_edges_exposed": -0.038016777064341006
  }
}
```
