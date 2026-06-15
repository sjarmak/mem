#!/usr/bin/env python3
"""Apply the two-stage grid-admission gate to the bundle pool (mem-75t.7.7 scope +
mem-1eph oracle-soundness).

The mem-75t.7.6 gate verdict named the first failure mode: bundles whose issue bead
fanned out to many sibling work beads (e29gw = 31 siblings, km0wj) carry an issue leg
that over-describes their narrow gold diff, corrupting per-bundle paired deltas. The
mem-apg.9 honest null named the second: of 5 carved native tasks only N=2 were
admissible; 3 had BROKEN ORACLES (a gold diff that doesn't reproduce, or a gold test
that passes on the empty diff) yet still passed admission and consumed the N, because
admission gated on scope match ALONE while the CSB validity check ran only as a
post-hoc report annotation AFTER grid assembly. This runner closes that ordering bug
by running BOTH gates before the manifest is written:

STAGE 1 — scope (mem-75t.7.7): `membench.bundle.assemble.fanout_scope_guard` over the
materialized bundles in ``.mem/bundles/``. Mechanical fanout (count of corpus records
sharing the bundle's issue bead — pure dependency-graph arithmetic) routes high-fanout
candidates to a SEMANTIC scope-match judge. The judge here is `ClaudeScopeJudge` —
headless ``claude -p`` via the oracle curator's completer (the OAuth runtime, not a
paid API), the same seam as the Tier-2 oracle curator. It reads the issue text + the
gold-diff file list and votes keep (scope matches) / reject (issue spans far more than
this slice). A clean bundle and a confound can sit at the same fanout count (gye8 vs
035r both = 2), so only the model separates them.

STAGE 2 — oracle soundness (mem-1eph): `membench.grading.validity_gate.validity_gate`
over the SCOPE-ADMITTED bundles, run with the SAME `LiveReproRunner` the direct-scoring
leg uses (a live repro run is expensive, and a scope-rejected bundle never enters the
grid, so its oracle is moot). The CSB invariant: the gold diff applied as the candidate
must REPRODUCE and the empty diff must FAIL. A bundle that breaches it has a broken
oracle and is rejected HERE, before it can consume a grid N. The criterion is already
defined + mechanical (reproduce-then-fail-on-empty); this is harness ordering, not a
lift-definition change (ZFC).

A bundle is grid-ready (``admitted``) iff it clears BOTH stages. Output: a grid-ready
manifest (``.mem/grid-ready-pool.json`` — admitted work_ids + per-bundle scope AND
oracle-soundness provenance) and a report (``docs/mem-1eph-oracle-soundness-gate.md``).

Run from memory-bench/:  PYTHONPATH=. python scripts/admit_batch_guarded.py --write
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from membench.bundle.assemble import (
    ISSUE_REF_KEY,
    FanoutDecision,
    ScopeMatchJudge,
    ScopeVerdict,
    fanout_scope_guard,
)
from membench.grading.dual_verifier import ReproOutcome, ReproRunner
from membench.grading.validity_gate import ValidityResult, validity_gate
from membench.harbor.repro_live import LiveReproRunner
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
DEFAULT_REPORT_OUT = Path(__file__).resolve().parents[2] / "docs/mem-1eph-oracle-soundness-gate.md"

# The manifest schema: v2 adds the oracle-soundness stage (mem-1eph). ``admitted``
# now means scope-admitted AND oracle-sound; the reader (load_grid_ready_work_ids)
# is unchanged because it consumes only that key.
MANIFEST_SCHEMA = "grid-ready-pool.v2"

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


class _DryReproRunner:
    """Offline stand-in for `LiveReproRunner` used by ``--dry-run``: declares every
    gold diff sound (a non-empty candidate passes, the empty diff fails) so the
    admission PLUMBING runs end-to-end with no checkout. It asserts NOTHING about a
    real oracle — only ``--write`` with the live runner produces a defensible yield.
    Distinct from `StubReproRunner` (one fixed outcome), which cannot satisfy the
    validity invariant's gold-passes-AND-empty-fails contrast."""

    def run(self, *, bundle: TaskBundle, candidate_diff: Mapping[str, str]) -> ReproOutcome:
        if candidate_diff:
            return ReproOutcome(passed=True, tests_passed=1, tests_total=1)
        return ReproOutcome(passed=False, tests_passed=0, tests_total=1)


@dataclass(frozen=True)
class GuardRow:
    """One bundle's two-stage admission record. ``decision`` is the scope verdict
    (stage 1); ``validity`` is the oracle-soundness verdict (stage 2), None when the
    bundle was scope-rejected and so never reached the live gate."""

    work_id: str
    issue_work_id: str | None
    decision: FanoutDecision
    validity: ValidityResult | None = None

    @property
    def scope_admitted(self) -> bool:
        return self.decision.admitted

    @property
    def admitted(self) -> bool:
        """Grid-ready iff the bundle clears BOTH gates: scope-admitted AND its oracle
        is sound. A scope-rejected bundle (validity None) is never grid-ready; a
        scope-admitted bundle with a broken oracle (validity.valid False) is rejected
        HERE, before it can consume a grid N (the mem-apg.9 ordering bug)."""
        return self.scope_admitted and self.validity is not None and self.validity.valid


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


def apply_validity_gate(
    rows: Sequence[GuardRow],
    bundles_by_id: Mapping[str, TaskBundle],
    test_runner: ReproRunner,
) -> list[GuardRow]:
    """Stage 2 (mem-1eph): run the CSB oracle-soundness gate over the SCOPE-ADMITTED
    bundles, attaching each one's `ValidityResult`. Scope-rejected rows pass through
    untouched (validity stays None) — their oracle is moot and a live repro run is
    expensive. After this pass a scope-admitted bundle with a broken oracle is caught
    by `GuardRow.admitted` BEFORE it can consume a grid N (the apg.9 ordering bug)."""
    out: list[GuardRow] = []
    for row in rows:
        if not row.scope_admitted:
            out.append(row)
            continue
        validity = validity_gate(bundles_by_id[row.work_id], test_runner=test_runner)
        out.append(replace(row, validity=validity))
    return out


def build_manifest(rows: Sequence[GuardRow]) -> dict[str, Any]:
    """The grid-ready manifest (schema v2). ``admitted`` is the BOTH-stages set the
    reader consumes; ``provenance`` records every bundle's scope verdict and, for the
    scope-admitted ones, its oracle-soundness verdict — so no exclusion is ever
    silent (a scope reject and a broken-oracle reject are distinguishable)."""
    return {
        "schema": MANIFEST_SCHEMA,
        "admitted": [r.work_id for r in rows if r.admitted],
        "provenance": [
            {
                "work_id": r.work_id,
                "issue_work_id": r.issue_work_id,
                "fanout": r.decision.fanout,
                "reviewed": r.decision.reviewed,
                "scope_admitted": r.scope_admitted,
                "scope_reason": (
                    r.decision.rejection.reason.value if r.decision.rejection is not None else None
                ),
                "scope_rationale": r.decision.rationale,
                # Oracle-soundness stage (mem-1eph). Null when scope-rejected (the gate
                # never ran); else the full CSB readout with the breach reason.
                "oracle_sound": None if r.validity is None else r.validity.valid,
                "oracle_reason": None if r.validity is None else r.validity.reason,
                "validity": None if r.validity is None else r.validity.model_dump(),
                "admitted": r.admitted,
            }
            for r in rows
        ],
    }


def _verdict(r: GuardRow) -> str:
    """The bundle's terminal admission verdict across both stages, naming the gate
    that rejected it so the report never collapses scope and oracle failures.
    `apply_validity_gate` populates `validity` on every scope-admitted row, so a
    scope-admitted row with no validity is a pipeline bug, not a scope reject."""
    if not r.scope_admitted:
        return "REJECT (scope)"
    assert r.validity is not None, f"{r.work_id}: scope-admitted but validity gate did not run"
    if not r.validity.valid:
        return "REJECT (oracle)"
    return "ADMIT"


def _render_report(rows: Sequence[GuardRow]) -> str:
    admitted = [r for r in rows if r.admitted]
    scope_admitted = [r for r in rows if r.scope_admitted]
    scope_rejected = [r for r in rows if not r.scope_admitted]
    oracle_checked = [r for r in rows if r.validity is not None]
    oracle_broken = [r for r in oracle_checked if r.validity is not None and not r.validity.valid]
    reviewed = [r for r in rows if r.decision.reviewed]
    reason_hist = Counter(
        r.decision.rejection.reason.value
        for r in scope_rejected
        if r.decision.rejection is not None
    )
    lines = [
        "# mem-1eph — Oracle-soundness pre-admission gate (scope + CSB validity)",
        "",
        "Two-stage grid admission over the materialized `.mem/bundles/` pool: "
        "`fanout_scope_guard` (mechanical fanout + `claude -p` scope-match judge), then "
        "the CSB `validity_gate` (gold reproduces, empty fails) over the scope-admitted "
        "bundles with the live repro runner. A bundle is grid-ready only if it clears BOTH.",
        "",
        "## Result",
        "",
        f"- pool: **{len(rows)}** bundles → **{len(admitted)} admitted** (grid-ready, "
        "sound oracle), the defensible denominator.",
        f"- stage 1 (scope): **{len(scope_admitted)} admitted**, "
        f"**{len(scope_rejected)} rejected**; rejection reasons "
        f"{dict(reason_hist) or '(none)'}.",
        f"- stage 2 (oracle): **{len(oracle_checked)}** scope-admitted bundles gated; "
        f"**{len(oracle_checked) - len(oracle_broken)} sound**, "
        f"**{len(oracle_broken)} broken** (gold non-reproducing or empty-passing) — "
        "rejected before consuming an N.",
        f"- scope-judged (fanout ≥ 2): **{len(reviewed)}**; the rest were singletons "
        "(fanout < 2) admitted without review.",
        "",
        "## Per-bundle admission provenance",
        "",
        "| work_id | issue bead | fanout | reviewed | verdict | reason |",
        "|---|---|---:|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x.admitted, -x.decision.fanout, x.work_id)):
        # A broken-oracle reject shows the CSB breach; every other row shows the scope
        # rationale (an oracle-sound row carries no separate reason to surface).
        if r.validity is not None and not r.validity.valid:
            reason = r.validity.reason
        else:
            reason = r.decision.rationale
        reason = reason.replace("|", "\\|")[:90]
        lines.append(
            f"| {r.work_id} | {r.issue_work_id or '-'} | {r.decision.fanout} | "
            f"{'yes' if r.decision.reviewed else 'no'} | {_verdict(r)} | {reason} |"
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
        help="use a stub scope judge + offline repro runner (admit all sound) to "
        "exercise the plumbing without claude or a checkout",
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
    bundles_by_id = {b.work_id: b for b in bundles}
    corpus = _load_corpus(args.store)

    if args.dry_run:
        from membench.bundle.assemble import StubScopeJudge

        judge: ScopeMatchJudge = StubScopeJudge(keep=True)
    else:
        judge = ClaudeScopeJudge()

    rows = _guard_pool(bundles, corpus, judge)
    # Stage 2: oracle soundness over the scope-admitted bundles. The live runner is a
    # context manager so a crashed gate never strands worktrees on the rig clone.
    if args.dry_run:
        rows = apply_validity_gate(rows, bundles_by_id, _DryReproRunner())
    else:
        with LiveReproRunner() as runner:
            rows = apply_validity_gate(rows, bundles_by_id, runner)
    report = _render_report(rows)

    if args.write:
        manifest = build_manifest(rows)
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n[{'WROTE' if args.write else 'DRY-PRINT'}] {len(bundles)} bundles guarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
