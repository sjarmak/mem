# mem-1eph — Oracle-soundness pre-admission gate (scope + CSB validity)

Two-stage grid admission over the materialized `.mem/bundles/` pool: `fanout_scope_guard` (mechanical fanout + `claude -p` scope-match judge), then the CSB `validity_gate` (gold reproduces, empty fails) over the scope-admitted bundles with the live repro runner. A bundle is grid-ready only if it clears BOTH.

## Result

- pool: **10** bundles → **3 admitted** (grid-ready, sound oracle), the defensible denominator.
- stage 1 (scope): **9 admitted**, **1 rejected**; rejection reasons {'issue_fanout_scope_mismatch': 1}.
- stage 2 (oracle): **9** scope-admitted bundles gated; **3 sound**, **6 broken** (gold non-reproducing or empty-passing) — rejected before consuming an N.
- scope-judged (fanout ≥ 2): **3**; the rest were singletons (fanout < 2) admitted without review.

## Per-bundle admission provenance

| work_id | issue bead | fanout | reviewed | verdict | reason |
|---|---|---:|---|---|---|
| gascity-dashboard-e29gw | gascity-dashboard-uzhr | 32 | yes | REJECT (scope) | The issue spans projecting readOnly into a shared DashboardRuntimeConfig and disabling mut |
| gascity-dashboard-km0wj | gascity-dashboard-035r | 2 | yes | REJECT (oracle) | empty diff scored test_ratio 0.6 (expected 0.0; a gold test passes without the fix) |
| gascity-dashboard-8n3to | gascity-dashboard-2j8e.3 | 1 | no | REJECT (oracle) | gold diff did not reproduce (expected repro_pass=True); empty diff scored test_ratio 0.2 ( |
| gascity-dashboard-e9y0d | gascity-dashboard-jkkc | 1 | no | REJECT (oracle) | gold diff did not reproduce (expected repro_pass=True); empty diff scored test_ratio 0.75  |
| gascity-dashboard-tkhkg | gascity-dashboard-3c7u | 1 | no | REJECT (oracle) | gold diff did not reproduce (expected repro_pass=True) |
| gascity-dashboard-ytvbs | gascity-dashboard-bvu4 | 1 | no | REJECT (oracle) | gold diff did not reproduce (expected repro_pass=True); empty diff scored test_ratio 0.333 |
| gascity-dashboard-zhy00 | gascity-dashboard-kxk2 | 1 | no | REJECT (oracle) | gold diff did not reproduce (expected repro_pass=True) |
| gascity-dashboard-4lf62 | gascity-dashboard-gye8 | 2 | yes | ADMIT | The 15 files all center on pending-decision accept/decline actions and pending-response st |
| gascity-dashboard-j18zz | gascity-dashboard-9v00 | 1 | no | ADMIT | no fanout (below review threshold) |
| gascity-dashboard-jai2y | gascity-dashboard-2j8e.4 | 1 | no | ADMIT | no fanout (below review threshold) |
