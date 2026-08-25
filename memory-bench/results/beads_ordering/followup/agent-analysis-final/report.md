# Follow-up agent retrieval evidence

2400 observations were averaged into 1008 equally weighted policy cells.

## Navigation at page size 5

| policy | success | compact tokens p50 / p90 | pages p50 / p90 |
|---|---:|---:|---:|
| bm25f | 0.651 [0.587, 0.714] | 582 / 639 | 1.0 / 1.0 |
| indegree | 0.524 [0.444, 0.603] | 1151 / 2833 | 2.0 / 4.7 |
| key | 0.540 [0.397, 0.683] | 661 / 2174 | 2.3 / 3.3 |
| outdegree | 0.476 [0.365, 0.587] | 679 / 1780 | 2.0 / 3.0 |
| pagerank | 0.667 [0.556, 0.778] | 611 / 1153 | 1.0 / 3.0 |
| reverse-pagerank | 0.476 [0.397, 0.556] | 665 / 1791 | 1.0 / 2.7 |

Success intervals are 90% graph-family-clustered bootstrap intervals. Raw model text is intentionally excluded from this derived evidence.
