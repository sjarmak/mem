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
        "c1_invocations": 342,
        "shape_distribution": {
            "bd list --status": 301,
            "bd list (no other predicate)": 30,
            "bd ready (no other predicate)": 5,
            "bd list --assignee": 2,
            "bd ready --exclude-type --metadata-field --unassigned": 2,
            "bd count --status": 1,
            "bd list --priority --status": 1,
        },
        "retains_a_narrowing_predicate": {"count": 307, "share": 0.8977},
        "present_day_store_proxy": {
            "store": "mem project bead store",
            "superset_size_status_open": 107,
            "superset_size_whole_collection": 1708,
            "caveat": (
                "One store at one time, and it is the analyst's own project. The "
                "corpus spans 118 distinct working directories whose collection "
                "sizes are not measured here. This supports a claim about "
                "collections of this scale (low thousands of resources), not a "
                "general property of limit and cursor."
            ),
        },
    }

    analysis["deviations_from_preregistration"] = [
        {
            "item": "corpus resolution",
            "registered": "resolve session ids via gc session logs from the rig root",
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
                "not run. The mechanical rule assigns N1 to every positional "
                "argument on `bd search` that is not a generated bd id. A model "
                "pass could only move arguments OUT of N1, so G1 as computed is an "
                "upper bound on the text-search residue rather than an estimate of "
                "it, and it still lands below the profile-adequate threshold."
            ),
            "direction": "conservative against the study's own null result",
        },
        {
            "item": "G3 statistic",
            "registered": "median and p90 superset size a C1 query must enumerate",
            "actual": (
                "superset SHAPES counted exactly, with their sizes measured against "
                "one present-day store as a proxy. Historical result sizes are not "
                "recoverable from transcripts: the size a query returned on the day "
                "it ran is not recorded anywhere, and stores have changed since."
            ),
            "direction": (
                "descriptive either way; G3 carries no threshold, but the "
                "substituted statistic answers 'what must be enumerated' by shape "
                "rather than by measured size"
            ),
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

    analysis["defects_found_by_independent_review"] = {
        "note": (
            "Two independent reviewers audited the classifier and the method after "
            "the first result was committed. Every defect below is fixed in the "
            "scripts published here, and the numbers in this file are post-fix."
        ),
        "changed_a_published_number": [
            {
                "defect": (
                    "the id pattern matched any hyphenated lowercase token, so a "
                    "free-text topic query on `bd search` scored as an identity "
                    "fetch (E0) instead of text (N1)"
                ),
                "effect": (
                    "88 positionals across 799 deduped `bd search` invocations "
                    "moved from E0 to N1. G1 0.0105 -> 0.0114 session-averaged, "
                    "0.0464 -> 0.0508 per-invocation. Verdict unchanged."
                ),
                "why_it_mattered": (
                    "it undercounted the text residue, which is the one direction "
                    "the writeup claimed was impossible"
                ),
            },
            {
                "defect": (
                    "g3.py counted raw rows while every other statistic deduped "
                    "exact repeats within a session"
                ),
                "effect": (
                    "C1 invocations 577 -> 342, matching label_counts_deduped; "
                    "retains-a-narrowing-predicate 91.9% -> 89.8%; bare 47 -> 35. "
                    "Shape count and conclusion unchanged."
                ),
                "why_it_mattered": ("analysis.json reported 342 and 577 for the same quantity"),
            },
            {
                "defect": (
                    "E3 fired whenever two labels co-occurred, including "
                    "combinations containing an inexpressible one such as E1+N1"
                ),
                "effect": "E3 3820 -> 39. No gate reads E3.",
                "why_it_mattered": (
                    "the taxonomy defines E3 as a boolean combination of "
                    "EXPRESSIBLE predicates; the loose rule inflated the "
                    "expressible side with queries the Selector cannot serve"
                ),
            },
        ],
        "zero_measured_impact_on_this_corpus": [
            "`bd search --query TEXT` captured its value but never got an N1 label; "
            "no invocation in the corpus used that form (all 810 use the positional)",
            "-s means --status on list/search but --sort on `bd ready`, which the "
            "flat flag table could not express; no corpus row hit the collision",
            "ten subcommand-local boolean flags were absent from the valueless set, "
            "so each could have swallowed the token after it as a fake value",
            "clustered short flags (-qv) parse as one unknown flag; the corpus "
            "contains no clustered short flag on a read subcommand",
            "--spec-id was mapped but does not exist in the CLI (dead entry, removed)",
        ],
        "known_and_unfixed": [
            "extract.py can build a fake argv from prose that merely contains the "
            "token bd. About 12 of 31,133 rows show this shape; 2 survived into the "
            "counted population, both as ordinary list --status shapes whose label "
            "is unaffected. No filter was added, because a prose detector here "
            "would be a semantic heuristic in a layer that is mechanical by design.",
        ],
    }

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
