"""Assemble the mem-rj2mg result package: analysis.json + manifest.json.

What the seal in manifest.json does and does not establish, stated once so no
reader has to infer it. It establishes that the six files in this directory are
the bytes that were sealed, that the preregistration is the document locked
before any classification ran, and that the result-bearing numbers are the ones
published at RESULTS_COMMIT. It does not attest an author: someone with commit
rights who updates the constants below, the document, and history together
produces a package that verifies. The trust root for that case is git history
and code review, not this script.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys

GIT_TIMEOUT_S = 60

PREREG_SHA = "3ffc83e06bc22216e135ca2b2a3b24b8278b99ba1bb1310c1dcae7890e4aeb35"

# The preregistration was locked at this commit, before any classification ran.
# It was committed under PREREG_LOCKED_PATH and moved to PREREG_PATH by
# mem-vn4ek, which published fixtures/bdp/ as the tree handed to BDP and kept the
# preregistration out of it as internal study detail. Same bytes, two locations;
# verify_preregistration checks both before anything is sealed.
PREREG_LOCKED_COMMIT = "2beb545e59b46c10bc0bf2a591d833d9c3c99444"
PREREG_LOCKED_PATH = "memory-bench/fixtures/bdp/selector-expressibility-preregistration.json"
PREREG_PATH = "memory-bench/results/bdp/selector-expressibility-preregistration.json"

ANALYSIS_PATH = "memory-bench/results/bdp/selector-expressibility/analysis.json"

# The keys of analysis.json this script does NOT author. They come in on the
# scratch input and they are the result. Without a pin on them the packager would
# accept any numbers and then mint the digests that "verify" them, so the seal
# would prove only that it sealed whatever it was handed. RESULTS_SHA is the
# digest of their canonical form as published at RESULTS_COMMIT; changing a
# published number therefore requires editing a constant here, which is a visible
# diff in review rather than a silent re-seal.
RESULT_KEYS = (
    "population",
    "label_counts_deduped",
    "gates",
    "sensitivity_excluding_bd_ready",
    "C1_share",
    "free_text_invocations",
)
RESULTS_SHA = "4cb80b8f9cc1d83caa072fbf4074441dde37c2f43b4590d3caa1c997ff4457b7"
RESULTS_COMMIT = "fa446506fd07daea260eef260f7fc096bb67ac17"

# The exact contents of a sealed package. report.md is written by hand and never
# emitted here, so a regeneration into an empty directory would otherwise produce
# a valid five-file seal over an incomplete package. Anything outside this set
# (an editor backup, a __pycache__ entry, a stray archive) would be hashed into
# the manifest and shipped, so the set is closed in both directions.
HAND_AUTHORED = ("report.md",)
COPIED = ("extract.py", "classify.py", "g3.py", "package.py")
GENERATED = ("analysis.json", "manifest.json")
SEALED = frozenset(HAND_AUTHORED + COPIED + GENERATED)

# Shapes that must not reach a published artifact. Format-anchored and
# deliberately over-broad: a false positive costs one edit, a miss ships. This
# covers POSIX home and system paths, Windows and UNC paths, shell home
# variables, and email addresses. It does not cover semantic disclosure (query
# literals, bead bodies, model text) — those are excluded by construction
# upstream, not detected here, and no regex could establish them.
LEAK = re.compile(r"""(?xi)
    (?: /(?:home|Users|root|srv)/[^\s"'\\]+ )   # POSIX home and system roots
  | (?: [A-Z]:\\(?:Users|Documents)\\[^\s"']+ ) # Windows drive paths
  | (?: \\\\[A-Za-z0-9._-]+\\[^\s"']+ )         # UNC shares
  | (?: (?<![A-Za-z0-9_])~[A-Za-z0-9_-]*/ )     # tilde home expansion
  | (?: \$\{?HOME\}?/ )                         # home environment variable
  | (?: (?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+ )
    """)


class PackageIntegrityError(Exception):
    """A precondition for sealing does not hold. main() turns this into an exit.

    Deliberately not SystemExit: verify_* are imported and called by tests, and a
    BaseException would slip past an ordinary `except Exception` in any caller.
    """


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def json_pointer(parts: tuple[str, ...]) -> str:
    return "".join("/" + p.replace("~", "~0").replace("/", "~1") for p in parts) or "/"


def leak_locations(node: object, parts: tuple[str, ...] = ()) -> list[str]:
    """Every RFC 6901 pointer under `node` whose key or string value leaks.

    Pointers rather than leaf names: two leaks sharing a leaf name are two
    entries, a leak inside a list keeps its index, and a publisher can find the
    one that is reported. Keys are scanned as well as values, because a path used
    as a JSON key is as published as one used as a value.
    """
    found: list[str] = []
    if isinstance(node, str):
        if LEAK.search(node):
            found.append(json_pointer(parts))
    elif isinstance(node, dict):
        for key, value in node.items():
            if LEAK.search(key):
                found.append(json_pointer((*parts, key)))
            found.extend(leak_locations(value, (*parts, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(leak_locations(value, (*parts, str(index))))
    return found


def _git(args: list[str], cwd: pathlib.Path, what: str) -> bytes:
    """Run one git command, or raise with the reason. Never a silent verdict.

    A non-zero exit, a missing binary, or a hang is a fault: the caller learns it
    could not check, never that the check failed. git's own stderr is carried
    through, so a shallow clone is distinguishable from a wrong commit.
    GIT_NO_REPLACE_OBJECTS defeats a replacement ref, and the executable is
    resolved rather than taken from PATH at call time.
    """
    exe = shutil.which("git")
    if exe is None:
        raise PackageIntegrityError(f"could not {what}: no git executable on PATH")
    try:
        done = subprocess.run(
            [exe, *args],
            cwd=cwd,
            capture_output=True,
            check=True,
            timeout=GIT_TIMEOUT_S,
            env={"GIT_NO_REPLACE_OBJECTS": "1", "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
        )
    except subprocess.TimeoutExpired as exc:
        raise PackageIntegrityError(
            f"could not {what}: git timed out after {GIT_TIMEOUT_S}s"
        ) from exc
    except OSError as exc:
        raise PackageIntegrityError(f"could not {what}: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip() or f"exit {exc.returncode}"
        raise PackageIntegrityError(f"could not {what}: {detail}") from exc
    return done.stdout


def repo_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve().parent
    raw = _git(["rev-parse", "--show-toplevel"], here, "locate the repository root")
    out = raw.decode().strip()
    if not out:
        raise PackageIntegrityError("could not locate the repository root: git returned nothing")
    return pathlib.Path(out)


def verify_preregistration(root: pathlib.Path) -> None:
    """Refuse to seal unless the locked preregistration is intact at both paths.

    A locator that has drifted is recoverable; a preregistration whose bytes have
    changed since the lock is not, and re-sealing around one would launder the
    change into a fresh manifest.
    """
    current = root / PREREG_PATH
    if not current.is_file():
        raise PackageIntegrityError(f"preregistration not found at {PREREG_PATH}")
    found = sha256(current)
    if found != PREREG_SHA:
        raise PackageIntegrityError(
            f"preregistration at {PREREG_PATH} hashes {found}, expected {PREREG_SHA}"
        )
    locked = _git(
        ["show", f"{PREREG_LOCKED_COMMIT}:{PREREG_LOCKED_PATH}"],
        root,
        f"read the locked preregistration from {PREREG_LOCKED_COMMIT}:{PREREG_LOCKED_PATH}",
    )
    locked_sha = hashlib.sha256(locked).hexdigest()
    if locked_sha != PREREG_SHA:
        raise PackageIntegrityError(
            f"locked preregistration at {PREREG_LOCKED_COMMIT} hashes {locked_sha}, "
            f"expected {PREREG_SHA}"
        )


def verify_results(root: pathlib.Path, analysis: dict[str, object]) -> None:
    """Refuse to seal numbers other than the ones published at RESULTS_COMMIT.

    Checked on both sides, like the preregistration: the incoming analysis must
    hash to RESULTS_SHA, and the blob at RESULTS_COMMIT must hash to it too, so
    the constant cannot be retuned on its own to admit a different result.
    """
    missing = [k for k in RESULT_KEYS if k not in analysis]
    if missing:
        raise PackageIntegrityError(f"analysis.json is missing result keys: {', '.join(missing)}")
    found = canonical_digest({k: analysis[k] for k in RESULT_KEYS})
    if found != RESULTS_SHA:
        raise PackageIntegrityError(
            f"result keys hash {found}, expected {RESULTS_SHA}. Re-running the "
            f"packager cannot change a published number; update RESULTS_SHA and "
            f"RESULTS_COMMIT deliberately if the analysis was legitimately redone."
        )
    published = json.loads(
        _git(
            ["show", f"{RESULTS_COMMIT}:{ANALYSIS_PATH}"],
            root,
            f"read the published results from {RESULTS_COMMIT}:{ANALYSIS_PATH}",
        )
    )
    published_missing = [k for k in RESULT_KEYS if k not in published]
    if published_missing:
        raise PackageIntegrityError(
            f"analysis.json at {RESULTS_COMMIT} is missing result keys: "
            f"{', '.join(published_missing)}"
        )
    published_sha = canonical_digest({k: published[k] for k in RESULT_KEYS})
    if published_sha != RESULTS_SHA:
        raise PackageIntegrityError(
            f"published results at {RESULTS_COMMIT} hash {published_sha}, expected {RESULTS_SHA}"
        )


def verify_sealed_contents(out: pathlib.Path) -> None:
    """The output directory must be exactly the sealed set, no more and no less.

    Called after the copies land and before the manifest is written, so
    manifest.json is permitted but not yet required.
    """
    required = SEALED - {"manifest.json"}
    present = {p.name for p in out.iterdir() if p.is_file()}
    missing = sorted(required - present)
    if missing:
        raise PackageIntegrityError(
            f"refusing to seal an incomplete package: {', '.join(missing)} missing from {out}. "
            f"{', '.join(HAND_AUTHORED)} is hand-authored and must be placed before packaging."
        )
    extra = sorted(present - SEALED)
    if extra:
        raise PackageIntegrityError(
            f"refusing to seal unexpected files: {', '.join(extra)} in {out}"
        )


def verify_no_leaks(out: pathlib.Path) -> None:
    """No sealed file may carry a local path or an address."""
    for name in sorted(SEALED - {"manifest.json"}):
        text = (out / name).read_text(errors="replace")
        hits = sorted({m.group(0) for m in LEAK.finditer(text)})
        if hits:
            raise PackageIntegrityError(f"refusing to seal {name}: it carries {hits}")


def main() -> None:
    out = pathlib.Path(sys.argv[1])
    scratch = (
        pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(__file__).resolve().parent
    )
    root = repo_root()
    verify_preregistration(root)
    out.mkdir(parents=True, exist_ok=True)
    analysis = json.loads((scratch / "analysis.json").read_text())
    verify_results(root, analysis)

    analysis["preregistration"] = {
        "path": PREREG_PATH,
        "sha256": PREREG_SHA,
        "locked_commit": PREREG_LOCKED_COMMIT,
        "locked_at_path": PREREG_LOCKED_PATH,
        "path_note": (
            "The locked bytes were committed at locked_at_path and moved to path "
            "by mem-vn4ek. sha256 is the digest of the same bytes at both "
            "locations, and package.py verifies both before it seals."
        ),
        "not_redacted": {
            "locations": leak_locations(json.loads((root / PREREG_PATH).read_text())),
            "what": (
                "RFC 6901 pointers into the locked preregistration whose key or "
                "string value carries a local path or an address, computed from the "
                "document at seal time rather than asserted here, so the disclosure "
                "cannot drift from what the document holds."
            ),
            "why": (
                "Disclosed rather than removed. The document's value is being "
                "unaltered after locking, and a redacted copy would not carry the "
                "sha256 above. The package's own files are scanned and carry none, "
                "so a publisher who ships the preregistration alongside them is "
                "shipping those strings knowingly. Commit 49efed7 stripped one to "
                "pass a path scan and silently invalidated the digest of a locked "
                "document; mem-vn4ek restored the locked bytes."
            ),
        },
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

    (out / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")

    for name in COPIED:
        (out / name).write_bytes((scratch / name).read_bytes())

    verify_sealed_contents(out)
    verify_no_leaks(out)

    manifest = {
        "bead": "mem-rj2mg",
        "preregistration_sha256": PREREG_SHA,
        "files": {
            p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
            for p in sorted(out.iterdir())
            if p.is_file() and p.name != "manifest.json"
        },
        "privacy": (
            "Predicate shapes, taxonomy labels, counts and fractions only. No query "
            "literals, free-text arguments, bead titles or bodies, file paths, model "
            "text, or identities. The intermediate holding command text stays in a "
            "session scratchpad and is not part of this package."
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    try:
        main()
    except PackageIntegrityError as error:
        raise SystemExit(str(error)) from error
