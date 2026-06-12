#!/usr/bin/env python3
"""mem-75t.4 driver: build the MERGED session<->bead join + archive transcripts.

Productionizes the mem-75t.9 spike into the events-primary merged join:

1. gc events (PRIMARY) — `membench.events_join` over
   `/home/ds/gas-city/.gc/events.jsonl` + its gz archives: (bead, session)
   pairs at event granularity, plus the gc-session -> Claude-session-UUID map
   (`session_key`) that resolves transcripts in ONE PASS (replaces the ~11 s
   per-session `gc session logs` shelling).
2. content scan — the extended `membench.session_join` scanner (bd inputs +
   gc prime / gc hook --claim OUTPUTS) over the transcript corpus, or a
   pre-built `--content-join` artifact.
3. dolt assignee history — READ-ONLY client queries against the running city
   server (never `bd dolt start`), reusing the mem-75t.9 driver helpers.

The three sources merge via `membench.merge_join` (event actor-sequence wins;
content evidence overrides bare assignee), then every bead-linked transcript is
gzip-archived to durable storage (`membench.transcript_archive`) before the
~6-week rolling corpus window prunes it.

Output artifact (default `/home/ds/projects/mem/.mem/merged-session-bead-join.json`):
`{generated_at, params, stats, archive, beads: {work_id: [session entries]}}` —
the TS store build consumes it via `mem build-store --session-join <path>`.

ZFC: pure plumbing — read-only inputs, structural parsing, arithmetic stats.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membench.events_join import collect_events_join, event_paths
from membench.merge_join import MergedBead, merge_bead_sessions, merged_stats
from membench.session_join import derive_prefixes, load_store_work_ids
from membench.transcript_archive import archive_transcripts


def _load_sibling(name: str) -> Any:
    """Load a sibling driver script as a module (the test-suite idiom)."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_session_join_driver = _load_sibling("build_session_join")

DEFAULT_EVENTS_DIR = "/home/ds/gas-city/.gc"
DEFAULT_STORE = "/home/ds/projects/mem/.mem/store.db"
DEFAULT_OUT = "/home/ds/projects/mem/.mem/merged-session-bead-join.json"
DEFAULT_ARCHIVE = "/home/ds/projects/mem/.mem/transcript-archive"


def uuid_to_path_map(files: Sequence[Path]) -> dict[str, str]:
    """Claude session UUID -> top-level transcript path. The filename stem IS
    the session UUID for top-level transcripts; subagent sidecars (under a
    `subagents/` dir) share the parent's sessionId and are excluded."""
    mapping: dict[str, str] = {}
    for path in files:
        if "subagents" in path.parts:
            continue
        mapping[path.stem] = str(path)
    return mapping


def load_content_rows(artifact: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(artifact).read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"--content-join artifact {artifact} has no rows[]")
    return rows


def beads_to_json(merged: dict[str, MergedBead]) -> dict[str, list[dict[str, Any]]]:
    return {
        work_id: [entry.to_json(i + 1) for i, entry in enumerate(bead.entries)]
        for work_id, bead in sorted(merged.items())
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--events-dir", default=DEFAULT_EVENTS_DIR)
    parser.add_argument("--roots", nargs="*", default=list(_session_join_driver.DEFAULT_ROOTS))
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--content-join",
        default=None,
        help="reuse rows[] from a prior content-scan artifact instead of rescanning the corpus",
    )
    parser.add_argument("--skip-dolt", action="store_true")
    parser.add_argument("--dolt-host", default=_session_join_driver.DOLT_HOST)
    parser.add_argument("--dolt-port", type=int, default=_session_join_driver.DOLT_PORT)
    parser.add_argument("--archive-dir", default=DEFAULT_ARCHIVE)
    parser.add_argument("--skip-archive", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="debug: scan only N transcripts")
    args = parser.parse_args(argv)

    store_ids = load_store_work_ids(args.store)
    prefixes = derive_prefixes(store_ids)
    print(f"store: {len(store_ids)} work_ids, {len(prefixes)} prefixes")

    # --- transcript corpus walk (uuid map always; scan unless --content-join)
    files = _session_join_driver.iter_transcripts(args.roots)
    if args.limit is not None:
        files = files[: args.limit]
    uuid_map = uuid_to_path_map(files)
    print(f"corpus: {len(files)} transcripts, {len(uuid_map)} top-level session uuids")

    if args.content_join:
        content_rows = load_content_rows(args.content_join)
        print(f"content scan: {len(content_rows)} rows reused from {args.content_join}")
    else:
        t0 = time.monotonic()
        content_rows, unreadable = _session_join_driver.scan_corpus(files, prefixes, store_ids)
        print(
            f"content scan: {len(content_rows)} rows in {time.monotonic() - t0:.1f}s "
            f"({unreadable} unreadable)"
        )

    # --- events (PRIMARY)
    t0 = time.monotonic()
    events = collect_events_join(event_paths(args.events_dir))
    print(
        f"events: {len(events.pairs)} (bead, session) pairs, "
        f"{len(events.session_keys)} session keys in {time.monotonic() - t0:.1f}s"
    )
    if events.n_malformed_lines:
        print(f"  WARNING: {events.n_malformed_lines} malformed event line(s) skipped")

    # --- dolt history (cross-check)
    dolt_sessions: dict[str, list[str]] = {}
    dolt_gaps: dict[str, str] = {}
    if not args.skip_dolt:
        rigs = _session_join_driver.load_store_rigs(args.store)
        raw_history, dolt_gaps = _session_join_driver.read_dolt_history(
            rigs,
            host=args.dolt_host,
            port=args.dolt_port,
            timeout_s=_session_join_driver.DOLT_TIMEOUT_S,
        )
        dolt_sessions = {k: list(v) for k, v in raw_history.items()}
        print(f"dolt history: {len(dolt_sessions)} beads, {len(dolt_gaps)} rig gaps")

    # --- store assignee links
    assignee_links = dict(_session_join_driver.load_store_assignee_links(args.store))

    # --- merge
    merged = merge_bead_sessions(
        event_pairs=events.pairs,
        session_keys=events.session_keys,
        content_rows=content_rows,
        dolt_sessions=dolt_sessions,
        assignee_links=assignee_links,
        uuid_to_path=uuid_map,
        store_ids=store_ids,
    )
    stats = merged_stats(merged)
    print(
        f"merged: {stats['beads']} beads, {stats['multi_session_beads']} multi-session, "
        f"{stats['suspect_assignee_entries']} suspect assignee links"
    )

    # --- archival (before the rolling window prunes anything we just linked)
    archive_report: dict[str, int] | None = None
    if not args.skip_archive:
        linked_paths = {
            entry.transcript_path
            for bead in merged.values()
            for entry in bead.entries
            if entry.transcript_path
        }
        report = archive_transcripts(sorted(linked_paths), args.archive_dir)
        archive_report = report.to_json()
        print(f"archive: {archive_report}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "params": {
            "events_dir": args.events_dir,
            "roots": list(args.roots),
            "store": args.store,
            "content_join": args.content_join,
            "skip_dolt": args.skip_dolt,
            "archive_dir": None if args.skip_archive else args.archive_dir,
        },
        "events": {
            "pairs": len(events.pairs),
            "session_keys": len(events.session_keys),
            "bead_events": events.n_bead_events,
            "malformed_lines": events.n_malformed_lines,
        },
        "dolt_rig_gaps": dolt_gaps,
        "stats": stats,
        "archive": archive_report,
        # Full gc-session -> transcript-path resolver map (every session whose
        # session_key resolves on disk, joined or not): build-store uses it as
        # the primary session resolver so `gc session logs` shelling (~11 s per
        # session) only runs for sessions the events stream never keyed.
        "session_paths": {
            gc_id: uuid_map[key]
            for gc_id, key in sorted(events.session_keys.items())
            if key in uuid_map
        },
        "beads": beads_to_json(merged),
    }
    out_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
