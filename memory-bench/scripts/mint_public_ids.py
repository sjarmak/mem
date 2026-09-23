#!/usr/bin/env python3
"""Mint the published id space for a frozen corpus and write its private inverse.

Every id a published record can carry — memory ids, step ids, distractors, superseded
versions — is replaced at publication by a keyed alias. This script produces the map
that makes that possible, and it is the only place the map is produced.

    PYTHONPATH=. python3 scripts/mint_public_ids.py \
        --corpus fixtures/worlds-public-v1 --mint-seed "$MEMBENCH_MINT_SEED"

The output goes to ``fixtures/mint/<corpus>.mint.json``, inside this repo. It is the
whole inverse of the published id space: anyone holding it can label every published id
as gold, distractor or stale, which is precisely what a solver must not be able to do.
It never goes in the published tree and never goes in a release.

The seed has no default. A default seed is a published secret: the aliases would be
reproducible by anyone reading this file, and the brute-force attack the alias scheme
exists to defeat would work again against them.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from membench.generators.necessity_sweep import load_corpus_sequences
from membench.public_alias import (
    build_alias_map,
    mint_path,
    published_internal_ids,
    write_alias_map,
)

DEFAULT_CORPUS = "fixtures/worlds-public-v1"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=DEFAULT_CORPUS, help="frozen corpus dir to mint")
    ap.add_argument("--mint-seed", required=True, help="the secret this corpus is keyed by")
    ap.add_argument(
        "--out", default=None, help="mint path (default: fixtures/mint/<corpus>.mint.json)"
    )
    args = ap.parse_args(argv)

    corpus_dir = Path(args.corpus)
    corpus = corpus_dir.name
    candidates = load_corpus_sequences(corpus_dir)
    internal_ids: set[str] = set()
    for candidate in candidates:
        internal_ids.update(published_internal_ids(candidate.sequence))

    amap = build_alias_map(internal_ids, corpus=corpus, mint_seed=args.mint_seed)
    path = write_alias_map(amap, path=args.out)

    print(f"corpus            {corpus_dir}")
    print(f"sequences         {len(candidates)}")
    print(f"minted ids        {len(amap)}")
    print(f"wrote             {path}")
    print(f"default location  {mint_path(corpus)}")
    print("KEEP PRIVATE: this file is the inverse of every published id.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
