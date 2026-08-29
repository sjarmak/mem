"""G3: enumeration cost of the C1 residue.

For each label-containment query, the BDP-expressible superset is that query with
the label predicate removed. This reports the shape distribution of those
supersets.

Usage: python g3.py <invocations.jsonl>
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import classify as taxonomy

SURVIVING = {"E1", "E2", "E4"}


def surviving_predicates(argv: list[str]) -> list[str]:
    """Flags that survive into a BDP selector, forming the superset predicate."""
    keep: list[str] = []
    rest = taxonomy.normalize(argv)[1]
    i = 0
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("-"):
            i += 1
            continue
        name = tok.partition("=")[0]
        if taxonomy.FLAG_LABEL.get(name) in SURVIVING:
            keep.append(name)
        takes_value = (
            "=" not in tok
            and name not in taxonomy.VALUELESS
            and i + 1 < len(rest)
            and not rest[i + 1].startswith("-")
        )
        i += 2 if takes_value else 1
    return keep


def main(inv_path: str) -> None:
    shapes: collections.Counter[tuple[str, tuple[str, ...]]] = collections.Counter()
    # Every other statistic in this study dedups exact repeats within a session.
    # G3 must use the same population or its share is not comparable to C1_share.
    seen: set[tuple[str, str]] = set()
    with open(inv_path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if taxonomy.is_excluded_origin(str(rec.get("cwd") or "")):
                continue
            argv = taxonomy.strip_shell(rec["argv"])
            if any(taxonomy.PLACEHOLDER.search(t) for t in argv) or "--help" in argv:
                continue
            sub, labels, _free_text, _pos = taxonomy.classify(argv)
            if sub not in taxonomy.PREREG_READ or "C1" not in labels:
                continue
            key = (str(rec["session"]), " ".join(argv))
            if key in seen:
                continue
            seen.add(key)
            shapes[(sub, tuple(sorted(set(surviving_predicates(argv)))))] += 1

    total = sum(shapes.values())
    bare = sum(n for (_sub, keep), n in shapes.items() if not keep)
    print(f"{len(shapes)} distinct superset shapes behind C1 queries\n")
    for (sub, keep), n in shapes.most_common(15):
        desc = " ".join(keep) if keep else "(no other predicate: whole collection)"
        print(f"  {n:5d}  bd {sub} {desc}")
    print(f"\ntotal C1 invocations {total}")
    print(f"with NO other predicate: {bare} ({bare / total:.1%})")


if __name__ == "__main__":
    main(sys.argv[1])
