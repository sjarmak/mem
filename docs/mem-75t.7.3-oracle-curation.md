# mem-75t.7.3 — Oracle curation over the validation bundles

Ran `membench.oracle.curate_bundle` (the codeprobe consensus + curator port) over the admitted bundles in `.mem/bundles/`, checking out each `repo@base_commit` as a detached worktree.

## Result

- **10 bundles curated**; `oracle_context` populated on each.
- provenance over kept files: {'gold_diff': 10}.

## Backend availability (the deciding finding)

All admitted bundles are the TS `gascity-dashboard` rig. Only one backend runs in this environment:

| backend | status | why |
|---|---|---|
| grep | available | `git grep -w` over the base_commit worktree |
| sourcegraph | unavailable | `SRC_ENDPOINT`/`SRC_ACCESS_TOKEN` unset; demo SG does not index the private repo |
| ast | not built | TS AST resolver deferred (plan §7.3), not built speculatively |

With one backend no symbol can ship 2-backend consensus, so no reference context is admitted: every oracle is exactly its **gold-diff required tier** (`oracle_backends_consensus=("gold_diff",)`). This is the conservative, precision-guarded result the design intends — the mem-75t.7.6 gate measured unfiltered context REGRESSING a bundle, so context enters only when a second backend vouches for it. Reference-context expansion and the empirical Tier-2 quarantine rate are blocked on a second backend (SG indexing the private repo, or the deferred TS-AST resolver).

## Per-bundle

| work_id | required | supp | symbol quarantines | truncated | provenance |
|---|---|---|---|---|---|
| gascity-dashboard-4lf62 | 15 | 0 | 15 | 0 | gold_diff |
| gascity-dashboard-8n3to | 11 | 0 | 11 | 0 | gold_diff |
| gascity-dashboard-e29gw | 2 | 0 | 2 | 0 | gold_diff |
| gascity-dashboard-e9y0d | 6 | 0 | 6 | 0 | gold_diff |
| gascity-dashboard-j18zz | 7 | 0 | 7 | 0 | gold_diff |
| gascity-dashboard-jai2y | 10 | 0 | 10 | 0 | gold_diff |
| gascity-dashboard-km0wj | 14 | 0 | 14 | 0 | gold_diff |
| gascity-dashboard-tkhkg | 3 | 0 | 3 | 0 | gold_diff |
| gascity-dashboard-ytvbs | 7 | 0 | 7 | 0 | gold_diff |
| gascity-dashboard-zhy00 | 10 | 0 | 10 | 0 | gold_diff |
