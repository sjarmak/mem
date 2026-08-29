# Follow-up agent retrieval evidence

1424 observations were averaged into 672 equally weighted policy cells.

## Navigation at page size 5

| policy | success | compact tokens p50 / p90 | pages p50 / p90 |
|---|---:|---:|---:|
| control-automatic | 0.444 [0.270, 0.619] | 665 / 1890 | 1.3 / 3.0 |
| control-raw | 0.635 [0.508, 0.762] | 626 / 632 | 1.0 / 2.3 |
| control-semantic | 0.603 [0.460, 0.730] | 624 / 654 | 1.0 / 1.0 |
| control-strategy | 0.476 [0.333, 0.619] | 659 / 2604 | 1.0 / 4.3 |

Success intervals are 90% graph-family-clustered bootstrap intervals. Raw model text is intentionally excluded from this derived evidence.
