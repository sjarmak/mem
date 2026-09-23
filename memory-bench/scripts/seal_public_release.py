#!/usr/bin/env python3
"""Seal the published tree, or check that it is still sealed.

    PYTHONPATH=. python3 scripts/seal_public_release.py            # write public/SHA256SUMS
    PYTHONPATH=. python3 scripts/seal_public_release.py --check    # verify, exit 1 on drift

The export writes the corpus and ``data/SHA256SUMS``; this is the step that seals the
whole release, including the validator a downloader is told to run and the schema it
reads. It refuses a tree carrying anything that is not published source, so a build
artefact left under ``public/`` fails the seal rather than shipping inside it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from membench.public_seal import SEAL_FILE, verify_seal, write_seal

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "public"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=str(DEFAULT_ROOT))
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the existing seal instead of writing one",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"no published tree at {root}", file=sys.stderr)
        return 2

    if args.check:
        problems = verify_seal(root)
        for problem in problems:
            print(f"  {problem}")
        print(f"{root}/{SEAL_FILE}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
        return 1 if problems else 0

    try:
        target = write_seal(root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    lines = target.read_text(encoding="utf-8").splitlines()
    print(f"wrote {target} over {len(lines)} published file(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
