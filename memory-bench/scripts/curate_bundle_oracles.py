#!/usr/bin/env python3
"""Populate oracle_context on the admitted bundles (mem-75t.7.3, plan §4 P2).

The consensus + curator port (`membench/oracle/`) provides `curate_bundle`; this is
the batch entry point that runs it over the real bundles in ``.mem/bundles/``. For
each bundle it checks out ``repo@base_commit`` as a detached worktree (reusing the
mem-75t.7.2 `add_worktree`/`remove_worktree` helpers — the same tree the replay ran
against), runs the grep + Sourcegraph resolvers, curates, and writes the resulting
`CuratedOracle` back into the bundle.

Backend availability is environment-determined, never assumed:

- ``grep`` — ``git grep -w`` over the base_commit worktree; always available locally.
- ``sourcegraph`` — needs ``SRC_ENDPOINT`` + ``SRC_ACCESS_TOKEN`` and an instance that
  indexes the rig repo. Unset here, so it reports unavailable and the candidate
  degrades to single-backend mode.

CONSEQUENCE (surfaced in the report): with only grep available, no symbol can ship
2-backend consensus, so NO reference context is admitted — every bundle's oracle is
exactly its **gold-diff required tier** (`oracle_backends_consensus=("gold_diff",)`),
the conservative, precision-guarded result the design intends. Reference-context
expansion (and the empirical Tier-2 quarantine rate that would decide whether the TS
rig needs an AST backend, plan §7.3) is blocked on a second backend: an SG instance
indexing the private repo, or the deferred TS-AST resolver.

No model calls are made (``use_llm=False``): the Tier-2 curator only fires on shipped
consensus, which cannot happen with one backend.

Run from memory-bench/:  PYTHONPATH=. python scripts/curate_bundle_oracles.py --write
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from assemble_batch import CheckoutFailedError, add_worktree, remove_worktree

from membench.oracle import (
    GrepResolver,
    SourcegraphResolver,
    SymbolResolver,
    curate_bundle,
)
from membench.schemas.bundle import TaskBundle

DEFAULT_BUNDLES_DIR = Path("/home/ds/projects/mem/.mem/bundles")
DEFAULT_CLONE = Path("/home/ds/gas-city-dashboard")
DEFAULT_REPORT_OUT = Path(__file__).resolve().parents[2] / "docs/mem-75t.7.3-oracle-curation.md"
DEFAULT_WORKTREE_ROOT = Path("/tmp")
WORKTREE_PREFIX = "oracle-cur-"

# Sourcegraph repo identifier for the dashboard rig. Honest provenance only — without
# SRC_ENDPOINT/SRC_ACCESS_TOKEN the resolver reports unavailable regardless.
SG_REPO = "github.com/gastownhall/gascity-dashboard"


@dataclass(frozen=True)
class OracleRow:
    """One bundle's curation outcome, for the report."""

    work_id: str
    n_required: int
    n_supplementary: int
    n_symbol_quarantines: int
    truncated: int
    backends_consensus: tuple[str, ...]


def _curate_one(
    bundle: TaskBundle, clone: Path, worktree_root: Path
) -> tuple[TaskBundle, OracleRow]:
    """Checkout base_commit, curate, tear the worktree down. The worktree is removed
    in a ``finally`` so a curation error never leaks a checkout."""
    worktree = worktree_root / f"{WORKTREE_PREFIX}{bundle.work_id}"
    add_worktree(clone, bundle.env.base_commit, worktree)
    try:
        resolvers: list[SymbolResolver] = [GrepResolver(), SourcegraphResolver(sg_repo=SG_REPO)]
        new_bundle, build = curate_bundle(bundle, worktree, resolvers=resolvers, use_llm=False)
    finally:
        remove_worktree(clone, worktree)

    tiers = dict(build.oracle.oracle_tiers)
    row = OracleRow(
        work_id=bundle.work_id,
        n_required=sum(1 for t in tiers.values() if t == "required"),
        n_supplementary=sum(1 for t in tiers.values() if t == "supplementary"),
        n_symbol_quarantines=len(build.symbol_quarantines),
        truncated=build.truncated,
        backends_consensus=build.oracle.oracle_backends_consensus,
    )
    return new_bundle, row


def _render_report(rows: Sequence[OracleRow]) -> str:
    n = len(rows)
    backend_hist = Counter(b for r in rows for b in r.backends_consensus)
    lines = [
        "# mem-75t.7.3 — Oracle curation over the validation bundles",
        "",
        "Ran `membench.oracle.curate_bundle` (the codeprobe consensus + curator port) "
        "over the admitted bundles in `.mem/bundles/`, checking out each "
        "`repo@base_commit` as a detached worktree.",
        "",
        "## Result",
        "",
        f"- **{n} bundles curated**; `oracle_context` populated on each.",
        f"- provenance over kept files: {dict(sorted(backend_hist.items())) or '(none)'}.",
        "",
        "## Backend availability (the deciding finding)",
        "",
        "All admitted bundles are the TS `gascity-dashboard` rig. Only one backend "
        "runs in this environment:",
        "",
        "| backend | status | why |",
        "|---|---|---|",
        "| grep | available | `git grep -w` over the base_commit worktree |",
        "| sourcegraph | unavailable | `SRC_ENDPOINT`/`SRC_ACCESS_TOKEN` unset; demo SG "
        "does not index the private repo |",
        "| ast | not built | TS AST resolver deferred (plan §7.3), not built speculatively |",
        "",
        "With one backend no symbol can ship 2-backend consensus, so no reference "
        "context is admitted: every oracle is exactly its **gold-diff required tier** "
        '(`oracle_backends_consensus=("gold_diff",)`). This is the conservative, '
        "precision-guarded result the design intends — the mem-75t.7.6 gate measured "
        "unfiltered context REGRESSING a bundle, so context enters only when a second "
        "backend vouches for it. Reference-context expansion and the empirical Tier-2 "
        "quarantine rate are blocked on a second backend (SG indexing the private repo, "
        "or the deferred TS-AST resolver).",
        "",
        "## Per-bundle",
        "",
        "| work_id | required | supp | symbol quarantines | truncated | provenance |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: x.work_id):
        lines.append(
            f"| {r.work_id} | {r.n_required} | {r.n_supplementary} | "
            f"{r.n_symbol_quarantines} | {r.truncated} | "
            f"{','.join(r.backends_consensus) or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    parser.add_argument("--clone", type=Path, default=DEFAULT_CLONE)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument("--worktree-root", type=Path, default=DEFAULT_WORKTREE_ROOT)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write oracle_context back into the bundle JSONs (default: dry-run)",
    )
    args = parser.parse_args(argv)

    bundle_paths = sorted(args.bundles_dir.glob("*.json"))
    if not bundle_paths:
        parser.error(f"no bundles under {args.bundles_dir}")

    rows: list[OracleRow] = []
    skipped: list[str] = []
    for path in bundle_paths:
        bundle = TaskBundle.model_validate_json(path.read_text(encoding="utf-8"))
        try:
            new_bundle, row = _curate_one(bundle, args.clone, args.worktree_root)
        except CheckoutFailedError as exc:
            # base_commit missing from the clone is a recorded skip, never a crash.
            skipped.append(f"{bundle.work_id}: {exc}")
            continue
        rows.append(row)
        if args.write:
            path.write_text(new_bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")

    report = _render_report(rows)
    if args.write:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(report, encoding="utf-8")

    print(report)
    if skipped:
        print("\nSKIPPED (base_commit not in clone):")
        for s in skipped:
            print(f"  - {s}")
    print(f"\n[{'WROTE' if args.write else 'DRY-RUN'}] {len(rows)} curated, {len(skipped)} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
