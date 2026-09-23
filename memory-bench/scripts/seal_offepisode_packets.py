#!/usr/bin/env python3
"""Write each off-episode packet's artifact digests into its manifest (mem-xj9si).

The manifests carry a sha256 per artifact so `EvidencePacket.from_files` refuses
evidence that drifted since it was sealed. That is only worth anything if the
digests are recomputed deliberately, so this is a script rather than something
the loader does on the fly: editing an artifact makes the packet fail to load
until someone runs this, which is the prompt to re-check the authoring
invariants in tests/test_offepisode_packets.py.

    python scripts/seal_offepisode_packets.py
    python scripts/seal_offepisode_packets.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from membench.fidelity.offepisode_corpus import EVIDENCE_ARTIFACTS

DEFAULT_ROOT = Path("tests/fixtures/offepisode_packets")


def digests(directory: Path) -> dict[str, str]:
    """The sha256 of each evidence artifact in one packet directory."""
    return {
        name: hashlib.sha256((directory / "packet" / name).read_bytes()).hexdigest()
        for name in EVIDENCE_ARTIFACTS
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero instead of rewriting the manifests",
    )
    args = parser.parse_args()

    drifted: list[str] = []
    for directory in sorted(args.root.iterdir()):
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text())
        current = digests(directory)
        if manifest.get("artifacts") == current:
            print(f"{manifest['packet_id']}: sealed")
            continue
        drifted.append(manifest["packet_id"])
        if args.check:
            print(f"{manifest['packet_id']}: DRIFTED")
            continue
        manifest["artifacts"] = current
        manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
        print(f"{manifest['packet_id']}: resealed")

    if args.check and drifted:
        print(f"\n{len(drifted)} packet(s) need resealing: {', '.join(drifted)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
