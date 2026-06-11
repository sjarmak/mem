#!/usr/bin/env python3
"""mem-75t.10 audit: quantify alias self-pair contamination of within-task
recurrence, and certify the UUID-deduped (clean) number for mem-apg.

An alias self-pair is two session entries on the SAME Claude conversation reached
through two filesystem namespaces (same UUID stem, different transcript path).
The pre-fix population keyed dedup on PATH, so such a pair survived — and pairing
a transcript with itself GUARANTEES signature recurrence. This script builds each
session view ONCE, then reports recurrence two ways over the identical view set:

- BEFORE: path-keyed population, no alias guard (the shipped-then-flagged path).
- AFTER:  UUID-keyed population + the alias guard (mem-75t.10 fix).

The delta is exactly the alias contamination. Read-only over store + transcripts.

PRECONDITION — one-shot forensic tool. This audit is valid ONLY against the
PRE-FIX merged-join artifact (the one built before merge_join._collapse_aliases
landed; it still carries the alias duplicate entries). Run against a rebuilt
(fixed) artifact, BEFORE == AFTER because the aliases are already gone — that is
expected, not a contradiction. The measured result is captured in
`.mem/alias-recurrence-audit.json` and the mem-75t.9 findings doc; this script
exists to reproduce that measurement, not as an ongoing regression check.
"""

from __future__ import annotations

import json
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membench.cross_session import (
    BeadCrossSession,
    SessionView,
    aggregate_metrics,
    baseline_signatures,
    bead_cross_session,
    build_session_view,
    pair_metrics,
)
from membench.harbor.base_rate_spike import make_cli_extractor
from membench.session_join import session_uuid

ART = "/home/ds/projects/mem/.mem/merged-session-bead-join.json"
MEM_BIN = "/home/ds/projects/mem/bin/mem"
OUT = "/home/ds/projects/mem/.mem/alias-recurrence-audit.json"
MIN_SESSIONS = 2


def population(beads: dict, *, key_by_uuid: bool) -> dict[str, list[dict]]:
    """Per bead, its non-suspect resolved entries. The dedup key and the row's
    `session_id` are kept BYTE-FOR-BYTE identical to the two production paths so
    the audit faithfully reproduces both computations (not an approximation):

    - BEFORE = the pre-fix `select_merged_population`: dedup key = raw path.
    - AFTER  = the fixed `select_merged_population`: key = `session_key or
      session_uuid(path) or path` (compute_cross_session.py).

    `session_id` mirrors production's `gc_session_id or session_key or path` in
    BOTH so the time-sort tiebreaker and the alias-pair lookup match production."""
    out: dict[str, list[dict]] = {}
    for wid, entries in beads.items():
        rows: dict[str, dict] = {}
        for e in entries:
            path = e.get("transcript_path")
            if e.get("suspect") or not path:
                continue
            sk = e.get("session_key")
            uuid = session_uuid(str(path))
            dedup_key = (sk or uuid or str(path)) if key_by_uuid else str(path)
            rows.setdefault(
                dedup_key,
                {
                    "transcript_path": path,
                    "start": e.get("t_first"),
                    "end": e.get("t_last"),
                    "session_id": e.get("gc_session_id") or sk or str(path),
                },
            )
        if len(rows) >= MIN_SESSIONS:
            out[wid] = list(rows.values())
    return out


def unguarded_bead(
    work_id: str, views: list[SessionView], *, exclude=frozenset()
) -> BeadCrossSession:
    """bead_cross_session WITHOUT the alias guard — the pre-fix pairing."""
    ordered = tuple(sorted(views, key=lambda v: (v.start is None, v.start or "", v.session_id)))
    pairs = tuple(pair_metrics(a, b, exclude=exclude) for a, b in pairwise(ordered))
    return BeadCrossSession(
        work_id=work_id,
        sessions=ordered,
        pairs=pairs,
        total_turns=0,
        total_tool_calls=0,
        total_input_tokens=None,
        total_output_tokens=None,
    )


def recurrence_block(beads: list[BeadCrossSession]) -> dict:
    # `beads` arrive already alias-resolved (BEFORE: path-keyed pop, no aliases
    # within a bead by construction of the pre-fix code; AFTER: guarded). The
    # `unguarded_bead` rebuild here only re-applies the baseline exclusion to the
    # SAME sessions — it is NOT re-introducing the alias guard's absence, and the
    # baseline is derived per-population on purpose (using the contaminated
    # baseline on the clean population would import contamination).
    raw = aggregate_metrics(beads)
    baseline = baseline_signatures(beads, min_beads=3)
    filtered = [unguarded_bead(b.work_id, list(b.sessions), exclude=baseline) for b in beads]
    filt = aggregate_metrics(filtered)
    return {
        "n_beads": raw["n_beads"],
        "raw_eligible": raw["recurrence_eligible_pairs"],
        "raw_recurrent": raw["recurrent_pairs"],
        "raw_pair_rate": raw["pair_recurrence_rate"],
        "filtered_eligible": filt["recurrence_eligible_pairs"],
        "filtered_recurrent": filt["recurrent_pairs"],
        "filtered_pair_rate": filt["pair_recurrence_rate"],
        "filtered_bead_eligible": filt["beads_with_eligible_pair"],
        "filtered_bead_recurrent": filt["beads_with_recurrence"],
        "filtered_bead_rate": filt["bead_recurrence_rate"],
    }


def main() -> int:
    beads_art = json.loads(Path(ART).read_text(encoding="utf-8"))["beads"]
    extractor = make_cli_extractor(MEM_BIN)
    cache: dict[str, SessionView] = {}

    def view_for(row: dict) -> SessionView | None:
        path = row["transcript_path"]
        if path not in cache:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            cache[path] = build_session_view(
                session_id=row["session_id"],
                transcript_path=path,
                stream_text=text,
                extractor=extractor,
                start=row["start"],
                end=row["end"],
            )
        return cache[path]

    # BEFORE population is a superset; build views once over it, derive AFTER from the same cache.
    before_pop = population(beads_art, key_by_uuid=False)
    after_pop = population(beads_art, key_by_uuid=True)

    def build(pop, *, guarded: bool) -> list[BeadCrossSession]:
        out = []
        for wid, rows in sorted(pop.items()):
            views = [v for v in (view_for(r) for r in rows) if v is not None]
            if len(views) < MIN_SESSIONS:
                continue
            out.append(bead_cross_session(wid, views) if guarded else unguarded_bead(wid, views))
        return out

    before = build(before_pop, guarded=False)
    after = build(after_pop, guarded=True)

    # Count alias self-pairs directly in the BEFORE eligible set.
    alias_eligible = alias_recurrent = 0
    for b in before:
        for p in b.pairs:
            same_session = session_uuid(_path(b, p.prev_session_id)) == session_uuid(
                _path(b, p.next_session_id)
            )
            if same_session and p.recurrence is not None:
                alias_eligible += 1
                if p.recurrence:
                    alias_recurrent += 1

    report = {
        "before": recurrence_block(before),
        "after": recurrence_block(after),
        "alias_self_pairs_in_before_eligible": alias_eligible,
        "alias_self_pairs_recurrent": alias_recurrent,
    }
    Path(OUT).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def _path(bead: BeadCrossSession, session_id: str) -> str:
    for v in bead.sessions:
        if v.session_id == session_id:
            return v.transcript_path
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
