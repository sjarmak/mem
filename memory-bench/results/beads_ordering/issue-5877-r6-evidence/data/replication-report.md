# Density/linkage model replication

189/189 paired cells were comparable. Infrastructure failures: bridge=0, secondary=0.

Positive savings/reductions favor the secondary model. Intervals are 90% hierarchical cluster bootstraps (graph family, then task).

| links | arm | pairs | success delta [90% CI] | pages saved [90% CI] | compact-token reduction [90% CI] |
|---|---|---:|---:|---:|---:|
| enriched | bm25f | 21 | -23.8% [-47.6, +0.0]% | +0.0 [+0.0, +0.0] | +0.0% [+0.0, +0.0]% |
| enriched | key | 21 | -33.3% [-61.9, -4.8]% | +0.0 [+0.0, +0.0] | +0.0% [+0.0, +0.0]% |
| enriched | pagerank | 21 | -33.3% [-57.1, -14.3]% | +0.0 [+0.0, +0.0] | +0.0% [+0.0, +0.0]% |
| native | bm25f | 21 | -28.6% [-52.4, -4.8]% | +0.0 [+0.0, +0.0] | +0.0% [+0.0, +0.0]% |
| native | key | 21 | -33.3% [-57.1, -14.3]% | +0.0 [+0.0, +0.0] | +0.0% [+0.0, +0.0]% |
| native | pagerank | 21 | +4.8% [-14.3, +23.8]% | +0.0 [+0.0, +0.0] | +0.0% [+0.0, +0.0]% |
| sparse | bm25f | 21 | -9.5% [-23.8, +0.0]% | +0.0 [+0.0, +0.0] | +0.0% [+0.0, +0.0]% |
| sparse | key | 21 | -19.0% [-47.6, +9.5]% | +0.0 [+0.0, +0.0] | +0.0% [-18.3, +0.0]% |
| sparse | pagerank | 21 | -19.0% [-38.1, +0.0]% | +0.0 [+0.0, +0.0] | +0.0% [+0.0, +0.0]% |

## Ordering-effect replication

Within each model, positive values favor the contender. The change column is secondary minus bridge.

| links | comparison | pairs | bridge success delta | secondary success delta | change [90% CI] | bridge compact saving | secondary compact saving |
|---|---|---:|---:|---:|---:|---:|---:|
| enriched | key→pagerank | 21 | -4.8% [-33.3, +23.8]% | -4.8% [-23.8, +14.3]% | +0.0% [-33.3, +28.6]% | +4.8% [+3.5, +9.0]% | +4.1% [-29.2, +8.9]% |
| enriched | pagerank→bm25f | 21 | +4.8% [-23.8, +33.3]% | +14.3% [-14.3, +42.9]% | +9.5% [-14.3, +33.3]% | +8.1% [+6.4, +53.7]% | +8.1% [+6.4, +70.2]% |
| native | key→pagerank | 21 | +4.8% [-28.6, +38.1]% | +42.9% [+14.3, +66.7]% | +38.1% [+9.5, +71.4]% | +8.0% [+4.1, +13.1]% | +8.0% [+3.9, +13.1]% |
| native | pagerank→bm25f | 21 | +4.8% [-23.8, +33.3]% | -28.6% [-47.6, -9.5]% | -33.3% [-66.7, +0.0]% | +8.2% [+4.2, +67.0]% | +8.2% [+4.2, +67.0]% |
| sparse | key→pagerank | 21 | +9.5% [-19.0, +33.3]% | +9.5% [-19.0, +38.1]% | +0.0% [-28.6, +28.6]% | +6.5% [+4.1, +12.1]% | +11.1% [+4.1, +25.5]% |
| sparse | pagerank→bm25f | 21 | -9.5% [-42.9, +23.8]% | +0.0% [-23.8, +23.8]% | +9.5% [-14.3, +33.3]% | +8.0% [+6.3, +50.1]% | +8.0% [+6.3, +50.6]% |
