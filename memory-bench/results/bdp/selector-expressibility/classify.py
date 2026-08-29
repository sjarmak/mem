"""Pass 2: classify each bd read invocation against the BDP Selector taxonomy.

Mechanical only. Every label below is derived from the bd CLI grammar
(`bd --help`, `bd list --help`, `bd search --help`, `bd query --help`), which
makes the predicate deterministic.

Taxonomy and gates are fixed by the preregistration
(memory-bench/fixtures/bdp/selector-expressibility-preregistration.json,
sha256 3ffc83e06bc22216e135ca2b2a3b24b8278b99ba1bb1310c1dcae7890e4aeb35).
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

Row = dict[str, Any]  # a parsed JSON record; fields are checked at use
Predicate = Callable[[Row], bool]

PREREG_READ = {
    "list",
    "show",
    "ready",
    "query",
    "search",
    "dep",
    "count",
    "stats",
    "blocked",
}
# read-shaped subcommands NOT in the locked population; sensitivity reporting only
UNREGISTERED_READ = {
    "comments",
    "memories",
    "recall",
    "children",
    "graph",
    "history",
    "stale",
    "status",
    "state",
    "duplicates",
    "find-duplicates",
    "epic",
    "types",
    "statuses",
    "provenance",
}
DEP_READ_VERBS = {"list", "tree", "show", "graph", "why", "path", "cycles", ""}

GLOBAL_VALUE_FLAGS = {
    "--actor",
    "--database",
    "--db",
    "-C",
    "--directory",
    "--dolt-auto-commit",
    "--mem-profile",
    "--rig",
}

FLAG_LABEL: dict[str, str] = {}


def _add(label: str, *flags: str) -> None:
    for f in flags:
        FLAG_LABEL[f] = label


# E1 exact equality or set membership on a field
_add(
    "E1",
    "--status",
    "-s",
    "--type",
    "-t",
    "--priority",
    "-p",
    "--assignee",
    "-a",
    "--id",
    "--parent",
    "--external-ref",
    "--metadata-field",
    "--mol-type",
    "--wisp-type",
    "--exclude-type",
    "--mol",
    "--spec-id",
)
# E2 ordered comparison
_add(
    "E2",
    "--priority-min",
    "--priority-max",
    "--created-after",
    "--created-before",
    "--updated-after",
    "--updated-before",
    "--closed-after",
    "--closed-before",
    "--due-after",
    "--due-before",
    "--defer-after",
    "--defer-before",
    "--overdue",
)
# E4 existence test
_add(
    "E4",
    "--no-assignee",
    "--unassigned",
    "--no-labels",
    "--empty-description",
    "--has-metadata-key",
    "--deferred",
    "--pinned",
    "--no-pinned",
    "--no-parent",
)
# C1 array containment. A singular path compared to a JSON literal cannot express
# "this array contains X", and nested filters are excluded: superset + client filter.
_add("C1", "--label", "-l", "--label-any", "--exclude-label")
# N1 substring / prefix / glob text matching
_add(
    "N1",
    "--title",
    "--title-contains",
    "--desc-contains",
    "--notes-contains",
    "--external-contains",
    "--spec",
    "--label-pattern",
    "--search",
)
# N2 regular expression
_add("N2", "--label-regex")
# N3 traversal or join across resources
_add("N3", "--ready", "--deps", "--blocked-by", "--blocks", "--related", "--direction")
# N5 ordering / top-k. --limit is deliberately NOT here: BDP collection retrieval
# accepts `limit`. --offset is, because BDP pages by cursor over a snapshot.
_add("N5", "--sort", "--reverse", "-r", "--offset")
# N6 projection / nested-value selection
_add("N6", "--format", "--brief", "--skip-labels")

SUBCOMMAND_LABEL = {
    "ready": "N3",
    "blocked": "N3",
    "dep": "N3",
    "count": "N4",
    "stats": "N4",
}

VALUELESS = {
    "--overdue",
    "--no-assignee",
    "--unassigned",
    "--no-labels",
    "--empty-description",
    "--deferred",
    "--pinned",
    "--no-pinned",
    "--no-parent",
    "--ready",
    "--reverse",
    "-r",
    "--brief",
    "--skip-labels",
    "--json",
    "--all",
    "--flat",
    "--pretty",
    "--long",
    "--tree",
    "--no-color",
    "--no-pager",
    "--quiet",
    "-q",
    "--verbose",
    "-v",
    "--readonly",
    "--sandbox",
    "--global",
    "--explain",
    "--gated",
    "--help",
    "-h",
    "--include-gates",
    "--include-infra",
    "--include-templates",
    "--include-ephemeral",
    "--include-deferred",
    "--comments",
    "--watch",
    "-w",
}

REDIRECT = re.compile(r"^\d*(>>|>|<)&?\d*$")
PLACEHOLDER = re.compile(r"^<.+>$|^\$|\{\{|^\.\.\.$|^%s$")
ID_LIKE = re.compile(r"^[a-z][a-z0-9]*(?:-[0-9a-z.]+)+$", re.IGNORECASE)
QUERY_CMP = re.compile(r"<=|>=|!=|=|<|>")
QUERY_BOOL = re.compile(r"\b(AND|OR|NOT)\b", re.IGNORECASE)
TEXT_QUERY_FIELDS = {"title", "description", "notes"}


def strip_shell(argv: list[str]) -> list[str]:
    """Drop redirection operators and their targets."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if REDIRECT.match(tok):
            # a bare operator consumes its target; `2>&1` carries its own
            if tok.endswith(("&1", "&2")) or "&" in tok:
                i += 1
            else:
                i += 2
            continue
        out.append(tok)
        i += 1
    return out


def normalize(argv: list[str]) -> tuple[str, list[str]]:
    """Return (subcommand, remaining args), leading global flags stripped."""
    i = 1
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-"):
            break
        if "=" in tok:
            i += 1
            continue
        if tok in GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        i += 1
    if i >= len(argv):
        return "", []
    return argv[i], argv[i + 1 :]


def classify_query_expr(expr: str) -> set[str]:
    labels: set[str] = set()
    for field, op in re.findall(r"([a-z_]+)\s*(<=|>=|!=|=|<|>)", expr, re.IGNORECASE):
        f = field.lower()
        if f in TEXT_QUERY_FIELDS:
            labels.add("N1")  # bd query documents these as "contains"
        elif op in {"<", "<=", ">", ">="}:
            labels.add("E2")
        elif f == "label":
            labels.add("C1")
        else:
            labels.add("E1")
    if QUERY_BOOL.search(expr):
        labels.add("E3")
    return labels


def classify(argv: list[str]) -> tuple[str, set[str], list[str], list[str]]:
    sub, rest = normalize(argv)
    labels: set[str] = set()
    free_text: list[str] = []
    positionals: list[str] = []

    if sub in SUBCOMMAND_LABEL:
        labels.add(SUBCOMMAND_LABEL[sub])

    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("-") and len(tok) > 1:
            name, eq, inline = tok.partition("=")
            label = FLAG_LABEL.get(name)
            if label:
                labels.add(label)
            if name in {"--query", "-q"}:
                if inline:
                    free_text.append(inline)
                elif i + 1 < len(rest):
                    free_text.append(rest[i + 1])
            if (
                not eq
                and name not in VALUELESS
                and i + 1 < len(rest)
                and not rest[i + 1].startswith("-")
            ):
                i += 2
                continue
            i += 1
            continue
        positionals.append(tok)
        i += 1

    if sub == "show":
        ids = [p for p in positionals if ID_LIKE.match(p)]
        if not ids:
            return sub, set(), [], positionals
        labels.add("E0" if len(ids) == 1 else "E1")
    elif sub == "search":
        for p in positionals:
            if ID_LIKE.match(p):
                labels.add("E0")
            else:
                labels.add("N1")
                free_text.append(p)
    elif sub == "query":
        for p in positionals:
            if QUERY_CMP.search(p):
                labels |= classify_query_expr(p)
            else:
                labels.add("N1")
                free_text.append(p)

    predicate = {x for x in labels if x not in {"N5", "N6", "E3"}}
    if len(predicate) >= 2:
        labels.add("E3")
    if not labels:
        labels.add("E_NONE")
    return sub, labels, free_text, positionals


def main(inv_path: str, out_path: str) -> None:
    per_session: dict[str, list[Row]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    raw_counts: Counter[str] = Counter()
    unregistered: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    rows: list[Row] = []

    with open(inv_path, encoding="utf-8") as fh:
        raw_lines = fh.readlines()

    for line in raw_lines:
        r = json.loads(line)
        argv = strip_shell(r["argv"])
        if any(PLACEHOLDER.search(t) for t in argv):
            skipped["placeholder_or_template"] += 1
            continue
        if "--help" in argv or "-h" in argv:
            skipped["help_invocation"] += 1
            continue
        sub, labels, free_text, _positionals = classify(argv)
        if sub in UNREGISTERED_READ:
            unregistered[sub] += 1
        if sub not in PREREG_READ:
            continue
        if sub == "dep":
            verb = next((t for t in normalize(argv)[1] if not t.startswith("-")), "")
            if verb not in DEP_READ_VERBS:
                skipped["dep_write_verb"] += 1
                continue
        if not labels:
            skipped["unparseable"] += 1
            continue
        raw_counts[sub] += 1
        key = (r["session"], " ".join(argv))
        if key in seen:
            continue
        seen.add(key)
        row = {
            "session": r["session"],
            "sub": sub,
            "labels": sorted(labels),
            "n_free_text": len(free_text),
            "free_text": free_text,
        }
        rows.append(row)
        per_session[r["session"]].append(row)

    with open(out_path, "w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")

    def always(_it: Row) -> bool:
        return True

    def session_frac(pred: Predicate, keep: Predicate = always) -> tuple[float, int]:
        vals: list[float] = []
        for items in per_session.values():
            sel = [it for it in items if keep(it)]
            if sel:
                vals.append(sum(1 for it in sel if pred(it)) / len(sel))
        return (statistics.fmean(vals) if vals else 0.0), len(vals)

    def is_g1(it: Row) -> bool:
        return "N1" in it["labels"] or "N2" in it["labels"]

    def is_g2(it: Row) -> bool:
        return "N3" in it["labels"]

    def not_ready(it: Row) -> bool:
        return bool(it["sub"] != "ready")

    def has_c1(it: Row) -> bool:
        return "C1" in it["labels"]

    g1, n1s = session_frac(is_g1)
    g2, n2s = session_frac(is_g2)
    g1_nr, _ = session_frac(is_g1, not_ready)
    g2_nr, _ = session_frac(is_g2, not_ready)

    label_counts: Counter[str] = Counter()
    for row in rows:
        for lab in row["labels"]:
            label_counts[lab] += 1

    n = len(rows)

    def verdict(v: float) -> str:
        if v >= 0.20:
            return "argues_for_capability"
        return "profile_adequate" if v <= 0.10 else "inconclusive"

    print(
        json.dumps(
            {
                "population": {
                    "transcripts_scanned": 9172,
                    "raw_read_invocations": sum(raw_counts.values()),
                    "deduped_invocations": n,
                    "sessions": len(per_session),
                    "by_subcommand_raw": dict(raw_counts.most_common()),
                    "unregistered_read_shaped_subcommands": dict(unregistered.most_common()),
                    "skipped": dict(skipped),
                },
                "label_counts_deduped": dict(label_counts.most_common()),
                "gates": {
                    "G1_search_predicate": {
                        "session_averaged": round(g1, 4),
                        "per_invocation": round(sum(1 for r in rows if is_g1(r)) / n, 4),
                        "n_sessions": n1s,
                        "verdict": verdict(g1),
                    },
                    "G2_traversal": {
                        "session_averaged": round(g2, 4),
                        "per_invocation": round(sum(1 for r in rows if is_g2(r)) / n, 4),
                        "n_sessions": n2s,
                        "verdict": verdict(g2),
                    },
                },
                "sensitivity_excluding_bd_ready": {
                    "G1_session_averaged": round(g1_nr, 4),
                    "G2_session_averaged": round(g2_nr, 4),
                },
                "C1_share": {
                    "session_averaged": round(session_frac(has_c1)[0], 4),
                    "per_invocation": round(sum(1 for r in rows if has_c1(r)) / n, 4),
                },
                "free_text_invocations": sum(1 for r in rows if r["n_free_text"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
