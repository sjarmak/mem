"""Export the four-class corpus as Jev need-gate cases (mem-xh9vb X1).

The gate input is fixed by the pre-registration: exactly what the agent sees at leg
start, which is the goal prompt plus the memory store's TITLES. Never bodies, never the
label.

Titles, not bodies, is the load-bearing part. A memory's body is the fact's VALUE, and the
partial class is defined by withholding one value from the prompt — so a store listing that
carried bodies would hand the model the very value whose absence it is being asked to
detect, and every partial case would be answerable by string comparison. Titles are the
authored SUBJECT of each fact, which is what a real index listing shows.

The store is synthesized from the necessary task's ``oracle_memory`` rather than read off a
live ``bd`` store, because a live store is filled by an establish-leg agent run and X1 is
offline by construction. The synthesized listing is the id-exact ceiling a perfect establish
leg would have recorded, and it is IDENTICAL across the four classes of a seed, which is what
the pre-registration requires of the gate input: the store must carry no trace of the label.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from membench.generators.enterprise_workflow import fact_subject, fact_value
from membench.metrics.scorers import states_value
from membench.runner.toolreq_corpus import four_class_tasks
from membench.runner.toolreq_realagent import (
    VARIANT_NECESSARY,
    VARIANT_NEEDS_MEMORY,
    ToolReqRealAgentTask,
    load_corpus_with_sequences,
)


class ExportError(RuntimeError):
    """The corpus cannot be exported as a label-safe case set."""


def store_titles(necessary: ToolReqRealAgentTask) -> list[str]:
    """The memory index listing for a seed: one authored subject per fact, sorted.

    Read off the NECESSARY task, so every class of the seed gets the same listing.
    """
    if necessary.variant != VARIANT_NECESSARY:
        raise ExportError(f"{necessary.work_id}: store must be read off the necessary task")
    titles = sorted({fact_subject(content) for content in necessary.oracle_memory.values()})
    if not titles:
        raise ExportError(f"{necessary.work_id}: necessary task has no facts, so no store")
    return titles


def _assert_no_body_leaks(titles: Sequence[str], necessary: ToolReqRealAgentTask) -> None:
    """A title that states a value is a body. The partial class dies on this.

    Matched with ``states_value``, the repo's word-anchored matcher, not ``in``. A bare
    substring test reads the value ``v2`` inside the subject ``the checkout_v2 feature flag
    state`` and refuses a listing that leaks nothing — the same anchoring bug the value
    rewriter in ``toolreq_realagent`` already guards against.
    """
    values = {fact_value(content) for content in necessary.oracle_memory.values()}
    for title in titles:
        for value in values:
            if value and states_value(title, value):
                raise ExportError(
                    f"{necessary.work_id}: store title {title!r} carries the value {value!r}; "
                    "the listing would hand the model the answer to the partial class"
                )


def build_cases(
    corpus_dir: Path, *, with_titles: bool = True
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(cases, labels)``, the model-facing rows and the never-shown sidecar."""
    _, necessary_tasks = load_corpus_with_sequences(corpus_dir)
    if not necessary_tasks:
        raise ExportError(f"{corpus_dir}: no tasks")
    titles_by_work = {}
    for task in necessary_tasks:
        titles = store_titles(task)
        _assert_no_body_leaks(titles, task)
        titles_by_work[task.work_id] = titles

    cases: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for task in four_class_tasks(necessary_tasks):
        titles = titles_by_work[task.work_id]
        sections = [{"memory_title": title} for title in titles] if with_titles else []
        cases.append(
            {
                "question_id": task.result_id,
                "state": {"question": task.goal_step.user_request, "sections": sections},
            }
        )
        labels.append(
            {
                "question_id": task.result_id,
                "work_id": task.work_id,
                "variant": task.variant,
                "needs_memory": VARIANT_NEEDS_MEMORY[task.variant],
            }
        )
    return cases, labels


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def export(corpus_dir: Path, out_dir: Path, *, with_titles: bool = True) -> dict[str, Any]:
    cases, labels = build_cases(corpus_dir, with_titles=with_titles)
    cases_path = out_dir / "cases.jsonl"
    labels_path = out_dir / "cases.labels.jsonl"
    _write_jsonl(cases_path, cases)
    _write_jsonl(labels_path, labels)
    by_variant: dict[str, int] = {}
    for row in labels:
        by_variant[row["variant"]] = by_variant.get(row["variant"], 0) + 1
    meta = {
        "corpus_dir": str(corpus_dir),
        "with_titles": with_titles,
        "cases": len(cases),
        "seeds": len({row["work_id"] for row in labels}),
        "by_variant": dict(sorted(by_variant.items())),
        "needs_memory_true": sum(row["needs_memory"] for row in labels),
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "labels_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
    }
    (out_dir / "cases.meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return meta


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--no-titles",
        action="store_true",
        help="secondary row: the prompt alone, with an empty store listing",
    )
    args = ap.parse_args(argv)
    meta = export(args.corpus, args.out, with_titles=not args.no_titles)
    print(json.dumps(meta, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
