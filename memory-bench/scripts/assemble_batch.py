#!/usr/bin/env python3
"""First real bundle batch: SELECT top-N -> checkout -> replay -> admit (mem-75t.7.2).

Drives the wave-1 modules end-to-end over the REAL store: takes the top-N ranked
candidates with a non-zero mutation signal from the SELECT artifact
(``.mem/select-ranking.json``, mem-75t.7.4), checks out each candidate's
``repo@base_commit`` as a detached git worktree (``/tmp/bundle-asm-<work_id>``),
replays the transcript against it with the mem-75t.7.1 work-dir inference contract
(`effective_work_dir` over the trace's own mutation paths, parsed ONCE and replayed
via `replay_calls`), and runs the admission filter (`assemble_bundle`) with ALL
store records as corpus -- so the LOO sibling/supersedes exclusion, the
SHARED_TRACE mega-session detection, and the gc.var.issue issue-leg resolution see
every record (the referenced issue beads have no trace/base_commit and are NOT in
the bundle-eligible pool). Candidates still come from the eligible pool only.

Products:

- one ``<bundles_dir>/<work_id>.json`` per ADMITTED `TaskBundle` (pydantic JSON,
  indent=2; the dir lives under the gitignored ``.mem/``), and
- the batch report ``docs/mem-75t.7.2-first-batch.md``: admitted facts table,
  typed-rejection histogram, and the mem-75t.7.6 gate-readiness line.

Failure containment: a base_commit missing from the local clone is a recorded
``checkout_failed`` skip (never a crash); every rejection is typed; every created
worktree is removed in a per-candidate ``finally`` AND swept again at exit, then
``git worktree list`` is verified clean of ``bundle-asm-`` entries -- leftovers
raise.

ZFC boundary: pure mechanism (SQL, git subprocess, replay arithmetic, table
formatting). Which candidates are well-scoped was decided upstream by the SELECT
rubric + the authored eyeball (mem-75t.7.4); whether a bundle is admissible is the
assembler's typed filter. The store is opened STRICTLY READ-ONLY (uri mode=ro).

Run from memory-bench/:  uv run python scripts/assemble_batch.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sqlite3
import subprocess
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from membench.bundle.assemble import Rejection, assemble_bundle
from membench.bundle.replay import effective_work_dir, parse_mutation_calls, replay_calls
from membench.harbor.env_recon import DEFAULT_RIG_REPOS
from membench.schemas.bundle import TaskBundle

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

DEFAULT_STORE = Path("/home/ds/projects/mem/.mem/store.db")
DEFAULT_RANKING = Path("/home/ds/projects/mem/.mem/select-ranking.json")
DEFAULT_BUNDLES_DIR = Path("/home/ds/projects/mem/.mem/bundles")
DEFAULT_REPORT_OUT = Path(__file__).resolve().parents[2] / "docs/mem-75t.7.2-first-batch.md"
DEFAULT_LIMIT = 25
DEFAULT_WORKTREE_ROOT = Path("/tmp")

# The mem-75t.7.6 gate probes none-rung vs cheap-oracle on ~10 admitted bundles.
GATE_TARGET = 10

_WORKTREE_PREFIX = "bundle-asm-"

# Batch-level skip reasons (pre-assembly). Lowercase tokens, same shape as
# `RejectionReason` values, so one histogram covers both layers.
CHECKOUT_FAILED = "checkout_failed"
NO_RIG_CLONE = "no_rig_clone"
NO_BASE_COMMIT = "no_base_commit"
NO_TRACE = "no_trace"
STALE_RANKING = "stale_ranking"

# Bundle-eligible pool -- the same predicate as the mem-75t.7.1 validation and the
# mem-75t.7.4 SELECT run; candidates are drawn from here.
ELIGIBLE_SQL = """
SELECT record FROM work_records
WHERE trace_path IS NOT NULL AND base_commit IS NOT NULL AND status = 'closed'
ORDER BY work_id
"""

# Assembly corpus -- EVERY store record, not just the bundle-eligible pool: the
# gc.var.issue issue beads and the input-convoy beads carry no trace/base_commit,
# yet issue-leg resolution and the LOO sibling group must see them.
CORPUS_SQL = "SELECT record FROM work_records ORDER BY work_id"


@dataclass(frozen=True)
class Admission:
    """One admitted bundle's report facts."""

    work_id: str
    rig: str
    adjusted_rate: float
    diff_files: int
    diff_lines: int
    bundle_path: str


@dataclass(frozen=True)
class BatchRejection:
    """One non-admitted candidate: either a batch-level skip (checkout/clone) or the
    assembler's typed `Rejection`, flattened to its reason token."""

    work_id: str
    rig: str
    reason: str
    detail: str = ""


class CheckoutFailedError(RuntimeError):
    """`git worktree add --detach` failed -- dominantly a base_commit absent from the
    local clone (timestamp-approximate provenance vs a pruned/foreign commit)."""


def _load_records(store: Path, sql: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


def load_pool(store: Path) -> list[dict[str, Any]]:
    """The bundle-eligible WorkRecord pool (candidate source), read-only."""
    return _load_records(store, ELIGIBLE_SQL)


def load_corpus(store: Path) -> list[dict[str, Any]]:
    """EVERY WorkRecord in the store (assembly corpus), read-only (uri mode=ro)."""
    return _load_records(store, CORPUS_SQL)


def top_candidates(
    ranking: Sequence[Mapping[str, Any]], limit: int = DEFAULT_LIMIT
) -> tuple[str, ...]:
    """The top-``limit`` work_ids by SELECT rank with a NON-ZERO mutation signal --
    a zero-mutation candidate has nothing to replay (shell-only session) and would
    only burn a checkout to learn what the ranking already knows."""
    picked: list[str] = []
    for entry in sorted(ranking, key=lambda e: int(e["rank"])):
        if int(entry.get("mutation_calls", 0)) > 0:
            picked.append(str(entry["work_id"]))
        if len(picked) >= limit:
            break
    return tuple(picked)


def record_work_dir(record: Mapping[str, Any], fallback: str) -> str:
    """The record-anchored work_dir fed to `effective_work_dir` (which then corrects
    it against the trace's own mutation paths -- the mem-75t.7.1 contract). Read from
    ``provenance.work_dir``, then the gc metadata, then the rig clone path."""
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping) and provenance.get("work_dir"):
        return str(provenance["work_dir"])
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get("gc.work_dir"):
        return str(metadata["gc.work_dir"])
    return fallback


def base_commit_of(record: Mapping[str, Any]) -> str | None:
    """The checkout anchor, ``outcome`` over ``provenance`` -- the same precedence as
    the assembler's env read (PR-authoritative beats commit-by-date approximate)."""
    for key in ("outcome", "provenance"):
        anchor = record.get(key)
        if isinstance(anchor, Mapping) and anchor.get("base_commit"):
            return str(anchor["base_commit"])
    return None


def diff_line_count(diff: str) -> int:
    """Changed lines in one git diff: ``+``/``-`` body lines, excluding the
    ``+++``/``---`` file headers."""
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )


def rejection_histogram(rejections: Sequence[BatchRejection]) -> str:
    """The compact ``REASON xN`` histogram, most frequent first (ties alphabetical)."""
    counts = Counter(r.reason for r in rejections)
    if not counts:
        return "(none)"
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{reason.upper()} x{n}" for reason, n in ordered)


def _run_git(clone: Path, args: Sequence[str], runner: Runner) -> subprocess.CompletedProcess[str]:
    return runner(
        ["git", "-C", str(clone), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def add_worktree(
    clone: Path, base_commit: str, dest: Path, *, runner: Runner = subprocess.run
) -> None:
    """Detached checkout of ``base_commit`` at ``dest``; raises `CheckoutFailedError`
    (the recorded skip, not a crash) when the commit is missing from the clone."""
    completed = _run_git(clone, ["worktree", "add", "--detach", str(dest), base_commit], runner)
    if completed.returncode != 0:
        raise CheckoutFailedError(
            f"git worktree add {base_commit[:12]} in {clone} failed "
            f"(exit {completed.returncode}): {completed.stderr.strip()}"
        )


def remove_worktree(clone: Path, dest: Path, *, runner: Runner = subprocess.run) -> None:
    """Force-remove ``dest`` from the clone's worktree set, then prune. The rmtree
    backstop covers a dir git no longer tracks (a previous crashed run)."""
    _run_git(clone, ["worktree", "remove", "--force", str(dest)], runner)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    _run_git(clone, ["worktree", "prune"], runner)


def stale_bundle_worktrees(clone: Path, *, runner: Runner = subprocess.run) -> tuple[str, ...]:
    """Any ``bundle-asm-`` worktree paths the clone still lists -- the post-run
    cleanliness check (must be empty)."""
    completed = _run_git(clone, ["worktree", "list", "--porcelain"], runner)
    if completed.returncode != 0:
        raise RuntimeError(f"git worktree list in {clone} failed: {completed.stderr.strip()}")
    return tuple(
        line.removeprefix("worktree ")
        for line in completed.stdout.splitlines()
        if line.startswith("worktree ") and _WORKTREE_PREFIX in line
    )


def process_candidate(
    record: Mapping[str, Any],
    *,
    corpus: Sequence[Mapping[str, Any]],
    rig_repos: Mapping[str, Path] = DEFAULT_RIG_REPOS,
    bundles_dir: Path,
    worktree_root: Path = DEFAULT_WORKTREE_ROOT,
    runner: Runner = subprocess.run,
) -> Admission | BatchRejection:
    """Checkout -> replay -> admission for ONE candidate. The worktree is removed in
    ``finally`` -- no path leaves it behind. ``corpus`` is ALL store records so
    SHARED_TRACE, the LOO exclusion set, and issue-leg resolution see everything."""
    work_id = str(record["work_id"])
    rig = str(record["rig"])
    clone = rig_repos.get(rig)
    if clone is None:
        return BatchRejection(
            work_id=work_id,
            rig=rig,
            reason=NO_RIG_CLONE,
            detail=f"rig {rig!r} has no local clone (known rigs: {sorted(rig_repos)})",
        )
    base_commit = base_commit_of(record)
    if base_commit is None:
        return BatchRejection(
            work_id=work_id,
            rig=rig,
            reason=NO_BASE_COMMIT,
            detail="no base_commit anchor on outcome or provenance",
        )
    trace = record.get("trace")
    trace_path = trace.get("jsonl_path") if isinstance(trace, Mapping) else None
    if not trace_path:
        return BatchRejection(
            work_id=work_id, rig=rig, reason=NO_TRACE, detail="record carries no trace.jsonl_path"
        )

    worktree = worktree_root / f"{_WORKTREE_PREFIX}{work_id}"
    if worktree.exists():  # a previous crashed run's leftover -- clear before checkout
        remove_worktree(clone, worktree, runner=runner)
    try:
        add_worktree(clone, base_commit, worktree, runner=runner)
    except CheckoutFailedError as exc:
        # A failed `worktree add` can leave a partly-created dest dir git never
        # registered -- invisible to the worktree-list sweep, so clear it here.
        if worktree.exists():
            remove_worktree(clone, worktree, runner=runner)
        return BatchRejection(work_id=work_id, rig=rig, reason=CHECKOUT_FAILED, detail=str(exc))
    try:
        stream = Path(str(trace_path)).read_text(encoding="utf-8")
        # Parse ONCE: the same calls feed work-dir inference and the replay.
        calls = parse_mutation_calls(stream)
        work_dir = effective_work_dir(record_work_dir(record, str(clone)), calls)
        replay = replay_calls(calls, checkout_dir=worktree, work_dir=work_dir, runner=runner)
        result = assemble_bundle(record, replay, corpus=corpus)
    finally:
        remove_worktree(clone, worktree, runner=runner)

    if isinstance(result, Rejection):
        return BatchRejection(
            work_id=result.work_id, rig=rig, reason=result.reason.value, detail=result.detail
        )
    return _serialize_admission(result, bundles_dir)


def _serialize_admission(bundle: TaskBundle, bundles_dir: Path) -> Admission:
    bundles_dir.mkdir(parents=True, exist_ok=True)
    out = bundles_dir / f"{bundle.work_id}.json"
    out.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")
    diffs = bundle.output.diff_by_file()
    return Admission(
        work_id=bundle.work_id,
        rig=bundle.rig,
        adjusted_rate=bundle.output.adjusted_replay_success_rate,
        diff_files=len(diffs),
        diff_lines=sum(diff_line_count(d) for d in diffs.values()),
        bundle_path=str(out),
    )


def render_report(
    *,
    store: Path,
    ranking_path: Path,
    candidates: Sequence[str],
    admissions: Sequence[Admission],
    rejections: Sequence[BatchRejection],
    pool_size: int,
    corpus_size: int | None = None,
) -> str:
    """The batch report markdown: methodology, admitted facts table, rejection
    histogram + per-candidate details, and the mem-75t.7.6 gate-readiness line."""
    today = datetime.date.today().isoformat()
    n_admitted = len(admissions)
    verdict = "GO" if n_admitted >= GATE_TARGET else "NO-GO"
    lines = [
        "# mem-75t.7.2 — First real bundle batch",
        "",
        f"Generated by `memory-bench/scripts/assemble_batch.py` on {today}.",
        "",
        f"- **Candidates**: top {len(candidates)} of the SELECT ranking"
        f" (`{ranking_path}`) with a non-zero mutation signal, drawn from the"
        f" {pool_size}-record bundle-eligible pool.",
        f"- **Corpus for admission**: ALL {corpus_size if corpus_size is not None else pool_size}"
        f" store records from `{store}` (read-only) — SHARED_TRACE detection, the LOO"
        " exclusion set, and issue-leg resolution see the whole store.",
        "- **Issue leg**: workflow-formula records (`metadata['gc.var.issue']`) source"
        " `issue_title`/`issue_body` from the referenced issue bead (recorded as"
        " `issue_work_id`); an unresolvable ref is a typed `ISSUE_REF_UNRESOLVED`"
        " rejection.",
        "- **Checkout**: detached git worktree of the rig clone at the record's"
        " `base_commit`; a missing commit is a recorded `CHECKOUT_FAILED` skip.",
        "- **Replay work_dir**: `effective_work_dir` (majority-prefix inference over"
        " the trace's own mutation paths, record-anchored — the mem-75t.7.1"
        " contract).",
        "- **Admitted bundles**: serialized to `.mem/bundles/<work_id>.json`" " (gitignored).",
        "",
        f"## Admitted ({n_admitted})",
        "",
    ]
    if admissions:
        lines += [
            "| work_id | rig | adjusted replay rate | gold-diff files | gold-diff lines |",
            "|---|---|---:|---:|---:|",
        ]
        lines += [
            f"| {a.work_id} | {a.rig} | {a.adjusted_rate:.2f} | {a.diff_files} | {a.diff_lines} |"
            for a in admissions
        ]
    else:
        lines.append("_None admitted._")
    lines += [
        "",
        f"## Rejections ({len(rejections)})",
        "",
        f"Histogram: **{rejection_histogram(rejections)}**",
        "",
    ]
    if rejections:
        lines += ["| work_id | rig | reason | detail |", "|---|---|---|---|"]
        for r in rejections:
            detail = r.detail.replace("|", "\\|").replace("\n", " ")
            if len(detail) > 160:
                detail = detail[:157] + "..."
            lines.append(f"| {r.work_id} | {r.rig} | {r.reason.upper()} | {detail} |")
    lines += [
        "",
        "## Gate readiness (mem-75t.7.6)",
        "",
        f"**{n_admitted} admitted bundles** vs the ~{GATE_TARGET}-bundle probe target"
        f" — **{verdict}**.",
        "",
    ]
    return "\n".join(lines)


def run_batch(
    *,
    store: Path,
    ranking_path: Path,
    bundles_dir: Path,
    report_out: Path,
    limit: int,
    rig_repos: Mapping[str, Path] = DEFAULT_RIG_REPOS,
    worktree_root: Path = DEFAULT_WORKTREE_ROOT,
    runner: Runner = subprocess.run,
) -> tuple[list[Admission], list[BatchRejection]]:
    """The batch loop, with the exit sweep: after all candidates, every used clone is
    swept for leftover ``bundle-asm-`` worktrees and verified clean -- a leftover
    after the sweep raises."""
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))["ranking"]
    candidates = top_candidates(ranking, limit)
    rig_by_ranked_id = {str(e["work_id"]): str(e.get("rig", "<unknown>")) for e in ranking}
    pool = load_pool(store)
    corpus = load_corpus(store)
    by_id = {str(r["work_id"]): r for r in pool}

    admissions: list[Admission] = []
    rejections: list[BatchRejection] = []
    used_clones: set[Path] = set()
    try:
        for work_id in candidates:
            record = by_id.get(work_id)
            if record is None:
                # The ranking artifact predates the current store build -- a ranked
                # work_id outside today's eligible pool is a recorded skip.
                rejections.append(
                    BatchRejection(
                        work_id=work_id,
                        rig=rig_by_ranked_id.get(work_id, "<unknown>"),
                        reason=STALE_RANKING,
                        detail=(
                            "ranked work_id absent from the current bundle-eligible "
                            "pool (stale select-ranking.json vs rebuilt store)"
                        ),
                    )
                )
                print(f"REJECT {work_id}  {STALE_RANKING}")
                continue
            clone = rig_repos.get(str(record["rig"]))
            if clone is not None:
                used_clones.add(clone)
            outcome = process_candidate(
                record,
                corpus=corpus,
                rig_repos=rig_repos,
                bundles_dir=bundles_dir,
                worktree_root=worktree_root,
                runner=runner,
            )
            if isinstance(outcome, Admission):
                admissions.append(outcome)
                print(
                    f"ADMIT  {work_id}  rate={outcome.adjusted_rate:.2f} "
                    f"files={outcome.diff_files}"
                )
            else:
                rejections.append(outcome)
                print(f"REJECT {work_id}  {outcome.reason}")
    finally:
        for clone in sorted(used_clones):
            for stale in stale_bundle_worktrees(clone, runner=runner):
                remove_worktree(clone, Path(stale), runner=runner)
            remaining = stale_bundle_worktrees(clone, runner=runner)
            if remaining:
                raise RuntimeError(f"bundle worktrees left in {clone} after sweep: {remaining}")

    report_out.write_text(
        render_report(
            store=store,
            ranking_path=ranking_path,
            candidates=candidates,
            admissions=admissions,
            rejections=rejections,
            pool_size=len(pool),
            corpus_size=len(corpus),
        ),
        encoding="utf-8",
    )
    return admissions, rejections


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--ranking", type=Path, default=DEFAULT_RANKING)
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES_DIR)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()

    admissions, rejections = run_batch(
        store=args.store,
        ranking_path=args.ranking,
        bundles_dir=args.bundles_dir,
        report_out=args.report_out,
        limit=args.limit,
    )
    print(
        f"\nadmitted={len(admissions)} rejected={len(rejections)} "
        f"histogram: {rejection_histogram(rejections)}"
    )
    print(f"report -> {args.report_out}")


if __name__ == "__main__":
    main()
