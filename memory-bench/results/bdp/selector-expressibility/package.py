"""Assemble the mem-rj2mg result package: analysis.json + manifest.json."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

OUT = pathlib.Path(sys.argv[1])
SCRATCH = (
    pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(__file__).resolve().parent
)

PREREG_SHA = "3ffc83e06bc22216e135ca2b2a3b24b8278b99ba1bb1310c1dcae7890e4aeb35"


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    analysis = json.loads((SCRATCH / "analysis.json").read_text())

    analysis["preregistration"] = {
        "path": "memory-bench/fixtures/bdp/selector-expressibility-preregistration.json",
        "sha256": PREREG_SHA,
        "locked_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip(),
    }

    analysis["G3_enumeration_cost"] = {
        "method": (
            "For each C1 (label-containment) query the BDP-expressible superset is "
            "that query with the label predicate removed. Historical result sizes "
            "cannot be recovered, so superset SHAPES are counted exactly and their "
            "sizes are measured against one present-day store as a proxy."
        ),
        "distinct_superset_shapes": 7,
        "c1_invocations": 577,
        "shape_distribution": {
            "bd list --status": 523,
            "bd list (no other predicate)": 36,
            "bd ready (no other predicate)": 11,
            "bd list --assignee": 2,
            "bd ready --exclude-type --metadata-field --unassigned": 2,
            "bd count --status": 2,
            "bd list --priority --status": 1,
        },
        "retains_a_narrowing_predicate": {"count": 530, "share": 0.9186},
        "present_day_store_proxy": {
            "store": "mem project bead store",
            "superset_size_status_open": 107,
            "superset_size_whole_collection": 1708,
            "caveat": (
                "one store at one time; queries in the corpus ran against several "
                "rigs whose collection sizes differ"
            ),
        },
    }

    analysis["deviations_from_preregistration"] = [
        {
            "item": "corpus resolution",
            "registered": "resolve session ids through gc session logs, run from the orchestrator rig root",
            "actual": (
                "enumerated transcript JSONL directly from the on-disk agent project "
                "directories (9,172 files, 6.8 GB)"
            ),
            "direction": "larger denominator than the registered procedure would yield",
        },
        {
            "item": "model classification of free-text intent",
            "registered": "a batched model pass over free-text search arguments",
            "actual": (
                "not run. The mechanical rule assigns N1 to EVERY non-ID-like "
                "positional argument, which is the assumption most favorable to the "
                "'a search predicate is needed' conclusion. G1 is therefore an upper "
                "bound on the text-search residue, and it still lands below the "
                "profile-adequate threshold. A model pass could only lower it."
            ),
            "direction": "conservative against the study's own null result",
        },
        {
            "item": "E_NONE reporting bucket",
            "registered": "taxonomy E0-E4, C1, N1-N6",
            "actual": (
                "queries carrying no predicate at all are reported as E_NONE rather "
                "than folded into E1. They remain expressible (the empty selector "
                "matches the whole collection) and no gate keys on this split."
            ),
            "direction": "reporting granularity only; no gate affected",
        },
    ]

    analysis["classifier_defects_found_and_fixed"] = [
        "bd dep add/remove counted as reads (dep is a read subcommand only for "
        "list/tree/show/graph/why/path/cycles)",
        "the id pattern rejected multi-hyphen ids such as agent-diagnostics-4w02, "
        "sending 1,489 identity fetches to 'unparseable'",
        "--help invocations were classified as retrieval queries (612 of them)",
        "shell redirection tokens (2>&1) were read as argv placeholders, which at one "
        "point discarded 27,939 of 31,133 rows",
        "--search, --unassigned and --mol were absent from the flag table",
    ]

    (OUT / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")

    for name in ("extract.py", "classify.py", "g3.py", "package.py"):
        (OUT / name).write_bytes((SCRATCH / name).read_bytes())

    manifest = {
        "bead": "mem-rj2mg",
        "preregistration_sha256": PREREG_SHA,
        "files": {
            p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
            for p in sorted(OUT.iterdir())
            if p.is_file() and p.name != "manifest.json"
        },
        "privacy": (
            "Predicate shapes, taxonomy labels, counts and fractions only. No query "
            "literals, free-text arguments, bead titles or bodies, file paths, model "
            "text, or identities. The intermediate holding command text stays in a "
            "session scratchpad and is not part of this package."
        ),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
