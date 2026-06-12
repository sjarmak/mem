#!/usr/bin/env python3
"""Apply the issue-fanout scope guard to the bundle pool (mem-75t.7.7).

The mem-75t.7.6 gate verdict named the failure mode: bundles whose issue bead fanned
out to many sibling work beads (e29gw = 31 siblings, km0wj) carry an issue leg that
over-describes their narrow gold diff, corrupting per-bundle paired deltas. This
runner applies `membench.bundle.assemble.fanout_scope_guard` over the materialized
bundles in ``.mem/bundles/`` (each already carries its replay output + curated
oracle from mem-75t.7.3), producing the GRID-READY admitted pool.

The guard is two-stage (the bead's ZFC split): mechanical fanout (count of corpus
records sharing the bundle's issue bead — pure dependency-graph arithmetic) routes
high-fanout candidates to a SEMANTIC scope-match judge. The judge here is
`ClaudeScopeJudge` — headless ``claude -p`` via the oracle curator's completer (the
OAuth runtime, not a paid API), the same seam as the Tier-2 oracle curator. It reads
the issue text + the gold-diff file list and votes keep (scope matches) / reject
(issue spans far more than this slice). A clean bundle and a confound can sit at the
same fanout count (gye8 vs 035r both = 2), so only the model separates them.

Output: a grid-ready manifest (``.mem/grid-ready-pool.json`` — admitted work_ids +
per-bundle admission provenance) and a report (``docs/mem-75t.7.7-fanout-guard.md``).

Run from memory-bench/:  PYTHONPATH=. python scripts/admit_batch_guarded.py --write
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from membench.bundle.assemble import (
    ISSUE_REF_KEY,
    FanoutDecision,
    ScopeMatchJudge,
    ScopeVerdict,
    fanout_scope_guard,
)
from membench.oracle.curator import (
    ClaudeOracleCurator,
    OracleCurator,
    OracleCuratorError,
    parse_curator_reply,
)
from membench.schemas.bundle import TaskBundle

DEFAULT_BUNDLES_DIR = Path("/home/ds/projects/mem/.mem/bundles")
DEFAULT_STORE = Path("/home/ds/projects/mem/.mem/store.db")
DEFAULT_MANIFEST = Path("/home/ds/projects/mem/.mem/grid-ready-pool.json")
DEFAULT_REPORT_OUT = Path(__file__).resolve().parents[2] / "docs/mem-75t.7.7-fanout-guard.md"

_SCOPE_PROMPT = """\
You are an admission reviewer for an agent-evaluation benchmark. This task bundle's
issue text comes from a bead that was DECOMPOSED into multiple sibling work beads, so
the issue may describe far more work than THIS bundle's change actually covers. You
are given the issue text and the files the change actually touched (its gold diff).
Decide whether the gold diff's scope MATCHES the issue's stated scope.

- KEEP (scope matches): the changed files plausibly implement the bulk of what the
  issue asks — issue and change are the same unit of work.
- REJECT (scope mismatch): the issue describes substantially more than these files
  cover (an epic spanning many components, of which this change is one small slice);
  scoring this narrow change against the broad issue would be unfair.

ISSUE TITLE: {title}
ISSUE BODY: {body}
GOLD-DIFF FILES ({n}):
{files}

Respond with JSON only: {{"keep": true|false, "rationale": "<one short sentence>"}}
No markdown fences, no extra commentary."""


@dataclass(frozen=True)
class ClaudeScopeJudge:
    """`ScopeMatchJudge` backed by headless ``claude -p`` (the oracle curator's
    completer — OAuth runtime, not paid API). Builds the scope-match prompt and parses
    the keep/reject reply with `parse_curator_reply` (shared JSON contract). A
    completer failure becomes a `ScopeVerdict` error, which the guard treats as a
    conservative reject."""

    completer: OracleCurator = field(default_factory=ClaudeOracleCurator)

    def judge(
        self, *, issue_title: str, issue_body: str, gold_files: Sequence[str]
    ) -> ScopeVerdict:
        prompt = _SCOPE_PROMPT.format(
            title=issue_title or "(none)",
            body=issue_body or "(none)",
            n=len(gold_files),
            files="\n".join(f"- {f}" for f in gold_files) or "(none)",
        )
        try:
            reply = self.completer.complete(prompt)
        except OracleCuratorError as exc:
            return ScopeVerdict(keep=False, error=str(exc))
        vote = parse_curator_reply(reply)
        return ScopeVerdict(keep=vote.keep, rationale=vote.rationale, error=vote.error)


@dataclass(frozen=True)
class GuardRow:
    work_id: str
    issue_work_id: str | None
    decision: FanoutDecision


def _load_corpus(store: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        return [json.loads(r[0]) for r in con.execute("SELECT record FROM work_records")]
    finally:
        con.close()


def _synth_record(bundle: TaskBundle) -> dict[str, Any]:
    """The minimal record `fanout_scope_guard` needs from a materialized bundle: the
    issue ref drives the mechanical fanout count. The bundle's ``issue_work_id`` IS
    the ``gc.var.issue`` value the assembler resolved it from."""
    meta = {ISSUE_REF_KEY: bundle.issue_work_id} if bundle.issue_work_id else {}
    return {"work_id": bundle.work_id, "metadata": meta}


def _guard_pool(
    bundles: Sequence[TaskBundle], corpus: Sequence[dict[str, Any]], judge: ScopeMatchJudge
) -> list[GuardRow]:
    rows: list[GuardRow] = []
    for bundle in bundles:
        decision = fanout_scope_guard(bundle, _synth_record(bundle), corpus, judge=judge)
        rows.append(GuardRow(bundle.work_id, bundle.issue_work_id, decision))
    return rows


def _render_report(rows: Sequence[GuardRow]) -> str:
    admitted = [r for r in rows if r.decision.admitted]
    rejected = [r for r in rows if not r.decision.admitted]
    reviewed = [r for r in rows if r.decision.reviewed]
    reason_hist = Counter(
        r.decision.rejection.reason.value for r in rejected if r.decision.rejection is not None
    )
    lines = [
        "# mem-75t.7.7 — Issue-fanout scope guard applied to the bundle pool",
        "",
        "Applied `fanout_scope_guard` (mechanical fanout + `claude -p` scope-match judge) "
        "over the materialized `.mem/bundles/` pool to produce the grid-ready set.",
        "",
        "## Result",
        "",
        f"- pool: **{len(rows)}** bundles; **{len(admitted)} admitted** (grid-ready), "
        f"**{len(rejected)} rejected**.",
        f"- scope-judged (fanout ≥ 2): **{len(reviewed)}**; the rest were singletons "
        "(fanout < 2) admitted without review.",
        f"- rejection reasons: {dict(reason_hist) or '(none)'}.",
        "",
        "## Per-bundle admission provenance",
        "",
        "| work_id | issue bead | fanout | reviewed | verdict | rationale |",
        "|---|---|---:|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (-x.decision.fanout, x.work_id)):
        verdict = "ADMIT" if r.decision.admitted else "REJECT"
        rationale = r.decision.rationale.replace("|", "\\|")[:90]
        lines.append(
            f"| {r.work_id} | {r.issue_work_id or '-'} | {r.decision.fanout} | "
            f"{'yes' if r.decision.reviewed else 'no'} | {verdict} | {rationale} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="use a stub judge (admit all high-fanout) to exercise the plumbing without claude",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the grid-ready manifest + report (default: print only)",
    )
    args = parser.parse_args(argv)

    bundle_paths = sorted(args.bundles_dir.glob("*.json"))
    if not bundle_paths:
        parser.error(f"no bundles under {args.bundles_dir}")
    bundles = [TaskBundle.model_validate_json(p.read_text(encoding="utf-8")) for p in bundle_paths]
    corpus = _load_corpus(args.store)

    if args.dry_run:
        from membench.bundle.assemble import StubScopeJudge

        judge: ScopeMatchJudge = StubScopeJudge(keep=True)
    else:
        judge = ClaudeScopeJudge()

    rows = _guard_pool(bundles, corpus, judge)
    report = _render_report(rows)

    if args.write:
        manifest = {
            "schema": "grid-ready-pool.v1",
            "admitted": [r.work_id for r in rows if r.decision.admitted],
            "provenance": [
                {
                    "work_id": r.work_id,
                    "issue_work_id": r.issue_work_id,
                    "fanout": r.decision.fanout,
                    "reviewed": r.decision.reviewed,
                    "admitted": r.decision.admitted,
                    "reason": (
                        r.decision.rejection.reason.value
                        if r.decision.rejection is not None
                        else None
                    ),
                    "rationale": r.decision.rationale,
                }
                for r in rows
            ],
        }
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n[{'WROTE' if args.write else 'DRY-PRINT'}] {len(bundles)} bundles guarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
