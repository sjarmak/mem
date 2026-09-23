#!/usr/bin/env python3
"""Run the frozen capture instruction over the off-episode packets and score what got written.

The question this buys an answer to: the campaign saw an agent assert Python version history
its evidence did not establish, six times out of seven. Is that what writing a durable record
does in general, or was it that one packet's CPython subject inviting version talk? Three
packets from unrelated technologies, whose artifacts state no version at all, separate the
two readings.

    # free, proves the whole path end to end
    scripts/run_offepisode_capture.py --dry-run --out /var/tmp/oe-dry
    scripts/run_offepisode_capture.py --dry-run --simulate-intrusion --out /var/tmp/oe-dry2

    # paid
    scripts/run_offepisode_capture.py --fire --model claude-sonnet-4-5 \\
        --repeats 2 --out results/offepisode-capture-YYYYMMDD

`--fire` is required for a paid run and does nothing else: no flag combination should let a
session be bought by a driver that was invoked to look at something.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from membench.fidelity.offepisode_corpus import OffEpisodePacket, load_all  # noqa: E402
from membench.fidelity.offepisode_session import (  # noqa: E402
    CaptureSession,
    load_capture_instruction,
    run_capture_session,
    summarize,
)
from membench.runner.headless_agent import (  # noqa: E402
    ENV_OAUTH,
    REFUSE_API_KEY_SET,
    REFUSE_UNPINNED_MODEL,
    a_paid_run_carries_the_metered_api_key,
    a_paid_run_needs_a_model,
    assistant_event,
    result_event,
    serialize_stream,
)
from membench.runner.sandbox import (  # noqa: E402
    SandboxContaminationError,
    assert_no_bead_store_above,
)
from membench.runner.tool_surface import MemoryToolSurface, harness_call  # noqa: E402

PACKET_ROOT = REPO / "tests" / "fixtures" / "offepisode_packets"
DEFAULT_TIMEOUT_S = 600.0


def simulated_runner(
    surface: MemoryToolSurface, packet: OffEpisodePacket, *, intruding: bool
) -> object:
    """A stand-in for `claude -p` that writes one of the packet's own authored records.

    It really drives the shim, so the readback, the inventory parse and the scorer are all
    exercised against a store that actually holds something. The record it writes is
    pre-authored packet content -- the supported record or its lure, which differ by exactly
    one line -- and not text shaped to the scorer. That is the difference between proving the
    path works and proving nothing: a simulator fitted to what the scorer matches satisfies
    every assertion by construction.

    The version-token probe is the one used, named rather than taken positionally: the
    scorer this exercises counts declared betraying tokens, and the token-free probe declares
    none, so writing it would exercise the path against a record the scorer cannot see."""
    probe = packet.probe("version-token")
    record = probe.lure if intruding else probe.supported
    key = f"{packet.packet_id}-capture"

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        harness_call(surface, ["memories", "--json"])
        harness_call(surface, ["remember", record, "--key", key])
        stream = serialize_stream(
            [
                assistant_event(
                    [("Bash", {"command": "bd memories --json", "description": "check"})]
                ),
                assistant_event(
                    [
                        (
                            "Bash",
                            {
                                "command": f'bd remember "..." --key "{key}"',
                                "description": "capture",
                            },
                        )
                    ]
                ),
                result_event("captured"),
            ]
        )
        return subprocess.CompletedProcess(list(argv), 0, stream, "")

    return run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="output directory (refused if it exists)"
    )
    parser.add_argument("--fire", action="store_true", help="authorize a PAID run")
    parser.add_argument("--dry-run", action="store_true", help="simulate the agent, spend nothing")
    parser.add_argument(
        "--simulate-intrusion",
        action="store_true",
        help="dry-run only: write the packet's lure record instead of its supported one",
    )
    parser.add_argument("--repeats", type=int, default=2, help="sessions per packet")
    parser.add_argument("--model", default="", help="model to pin (required to fire)")
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--packet-root", type=Path, default=PACKET_ROOT)
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="where sandboxes and stores are minted; must have no .beads above it",
    )
    parser.add_argument(
        "--packet", action="append", default=None, help="restrict to these packet ids (repeatable)"
    )
    args = parser.parse_args(argv)

    if args.dry_run == args.fire:
        print("pass exactly one of --fire (paid) or --dry-run (free).", file=sys.stderr)
        return 2
    if args.simulate_intrusion and not args.dry_run:
        print(
            "--simulate-intrusion is a dry-run affordance; it cannot shape a paid session.",
            file=sys.stderr,
        )
        return 2
    if args.repeats < 1:
        print("--repeats must be at least 1.", file=sys.stderr)
        return 2

    # Model gate first: it fires regardless of the token, so the other order prints a
    # go-command telling a human to run something that would immediately refuse for a
    # different reason.
    if a_paid_run_needs_a_model(args.model, dry_run=args.dry_run):
        print(REFUSE_UNPINNED_MODEL)
        return 2
    # Before the token gate: a set key is a misconfiguration regardless of the token, so
    # refusing here keeps the token message below from telling a human to re-run in a still
    # contaminated environment.
    if a_paid_run_carries_the_metered_api_key(dry_run=args.dry_run):
        print(REFUSE_API_KEY_SET)
        return 2
    if not args.dry_run and not os.environ.get(ENV_OAUTH):
        print(
            f"REFUSING to spend: {ENV_OAUTH} is unset, and it is the only auth channel that\n"
            "  survives the CLAUDE_CONFIG_DIR pin this surface applies -- the session would\n"
            "  abort at /login having measured nothing. Run `claude setup-token`, export the\n"
            "  token, and re-run; or pass --dry-run to prove the wiring for free."
        )
        return 2

    if args.out.exists():
        print(
            f"{args.out} already exists; a run is spent once and never repurchased.",
            file=sys.stderr,
        )
        return 2

    packets = load_all(args.packet_root)
    if args.packet:
        wanted = set(args.packet)
        unknown = sorted(wanted - {p.packet_id for p in packets})
        if unknown:
            print(f"unknown packet id(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        packets = [p for p in packets if p.packet_id in wanted]
    if not packets:
        print(f"no packets under {args.packet_root}", file=sys.stderr)
        return 2

    instruction = load_capture_instruction(args.packet_root)

    with tempfile.TemporaryDirectory(prefix="offepisode-run-", dir=args.work_root) as raw_root:
        work_root = Path(raw_root).resolve()
        try:
            assert_no_bead_store_above(work_root)
        except SandboxContaminationError as exc:
            print(str(exc), file=sys.stderr)
            print(
                "\n  Pass --work-root <dir> pointing somewhere with no .beads in any parent\n"
                "  (this machine has one at $HOME, /tmp and ~/projects; /var/tmp is clean).",
                file=sys.stderr,
            )
            return 2

        mode = "DRY-RUN (simulated agent, no tokens)" if args.dry_run else "PAID real claude -p"
        print(f"off-episode capture: {mode}; {len(packets)} packet(s) x {args.repeats} repeat(s)")

        args.out.mkdir(parents=True)
        rows = []
        for packet in packets:
            for repeat in range(1, args.repeats + 1):
                session = CaptureSession(packet_id=packet.packet_id, repeat=repeat)
                # Built per session because the simulator drives the session's own shim, which
                # does not exist until the session mints it. A dry run therefore hands the
                # runner a factory, and the session calls it once the surface is up.
                make_runner = (
                    (
                        lambda surface, _p=packet: simulated_runner(
                            surface, _p, intruding=args.simulate_intrusion
                        )
                    )
                    if args.dry_run
                    else (lambda _surface: subprocess.run)
                )
                row = run_capture_session(
                    packet,
                    instruction=instruction,
                    session=session,
                    out=args.out / session.name,
                    work_root=work_root,
                    model=args.model,
                    timeout_s=args.timeout_s,
                    make_runner=make_runner,
                    paid=not args.dry_run,
                )
                rows.append(row)
                intrusion = row["intrusion"]
                print(
                    f"  {session.name}: {'wrote' if row['wrote_anything'] else 'WROTE NOTHING'} "
                    f"({intrusion['records']} record(s)), "
                    f"declared-lure hits {intrusion['declared_lures_hit_count']}, "
                    f"version-lure hits {intrusion['version_lures_hit_count']}"
                )

    summary = summarize(rows)
    summary["mode"] = "dry-run" if args.dry_run else "paid"
    summary["model"] = args.model
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
