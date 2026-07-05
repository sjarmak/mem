#!/usr/bin/env python3
"""Recall-ladder pilot smoke (mem-do8r): wire + score all four rungs, then HALT.

Adopts the Sakana (AIEWF 2026) recall ladder -- fix the model, vary ONLY the recall
arm across four rungs (``none`` / ``vector-rag`` / ``ours`` / ``oracle``) -- and proves
the harness threads every rung end-to-end on a TINY fixture pool with NO Docker, no
network, and no paid API. The single net-new wiring item over the prior 3-rung grid is
``vector-rag`` (the semantic arm); this script resolves each rung's payload set the way
the real driver will, then runs ``run_grid`` with a deterministic ``StubRunner``.

Per-rung payload resolution (each rung differs ONLY in recall policy):

- ``none``    -> nothing injected (the stateless control).
- ``vector-rag`` -> the REAL ``NemoEmbedMemory`` arm over a seeded, LOO-scoped fixture
  corpus, driven by a deterministic multi-hot fake embedder (no model, no GPU). Its
  ``write`` + ``retrieve`` rank priors by exact cosine, so the injected payloads are
  EARNED by retrieval, not hand-passed (plan-review HIGH #1).
- ``ours``    -> the REAL ``OursMemory`` arm with an INJECTED stub retrieval runner
  returning fixture ledger items (not the real, still-empty ``lessons`` store).
- ``oracle``  -> a curated gold-diff payload (guarded by the H3 self-leak check).

WHAT THIS DOES NOT SHOW (deliberately, per plan-review HIGH #2): a lift curve. The
``StubRunner`` ignores injected memory by design, so each rung's per-run trace is
fixture-authored to exercise the SCORING wiring, not earned by the agent reading the
prior. Rung-3 (``ours``) is also degenerate on the REAL grid until a ``mem
distill-lessons`` pass populates the ``lessons`` store (ADR lock #2) -- that gate stays
explicit and is NOT hidden by the stub. This smoke validates PLUMBING: four rungs
dispatch, inject through the right guard, and emit one comparable ``RewardRecord`` each.

ZFC: pure plumbing -- deterministic arithmetic, mechanical injection, no model judgment.

Usage (from memory-bench/):

    uv run python scripts/recall_ladder_smoke.py
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from membench.grading import RewardRecord, RunTrace, TraceErrorRef
from membench.harbor.grid import StubRunner, run_grid
from membench.memory_systems.base import RetrievalRequest
from membench.memory_systems.nemo_embed_system import (
    NemoEmbedMemory,
    _NemoEmbedClient,
)
from membench.memory_systems.ours_system import OursMemory, OursQuery
from membench.runtime import IdClock, StepContext
from membench.validity import QueryWork

RUNGS = ("none", "vector-rag", "ours", "oracle")


# --- deterministic fake embedder (no model, no GPU, no network) ------------------


def _tokens(text: str) -> set[str]:
    """Lowercased alphanumeric tokens -- the only text feature the fake embedder sees."""
    return set("".join(c.lower() if c.isalnum() else " " for c in text).split())


# Kept local (not imported from tests/test_nemo_embed_system.py's identical fake): a
# standalone smoke script should not depend on the test package, and a fixed
# bag-of-vocab definition has no behavior to drift. If a third caller appears, promote
# this to a shared testing helper (rule of three).
@dataclass(frozen=True)
class FakeEmbedder:
    """Multi-hot bag-of-vocab embedder: a text maps to a fixed-dimension vector with
    1.0 at each present vocab token. Cosine of two such vectors tracks token overlap,
    so retrieval ORDER is a known function of the fixture -- deterministic, and with no
    numpy or model. Query/document sides are symmetric (the real embedder owns any
    asymmetric instruction prompt; the seam does not)."""

    vocab: tuple[str, ...]

    def _vec(self, text: str) -> list[float]:
        present = _tokens(text)
        return [1.0 if term in present else 0.0 for term in self.vocab]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


# --- the tiny fixture pool (2 held-out tasks) ------------------------------------


@dataclass(frozen=True)
class LadderTask:
    """One held-out query work `B` plus every rung's fixture inputs. `held` is the
    single held-out failure the scorer grades against; `query` is the failure string
    the vector arm retrieves on; `corpus` is the seeded, LOO-scoped prior pool the
    vector arm ranks (id -> lesson text, exactly one relevant); `relevant_prior` is
    that one relevant id, which the vector arm MUST rank first (the retrieval-quality
    assertion -- and it is deliberately NOT the alphabetically-smallest corpus id, so a
    degenerate all-zero-score embedding, which would fall back to id-sorted order,
    provably fails the rank-1 check instead of silently passing); `ours_items` are the
    fixture ledger rows the stub `ours` runner returns; `oracle` is the curated
    gold-diff (must NOT carry the held signature -- the oracle H3 guard enforces it)."""

    work_id: str
    rig: str
    started: str
    title: str
    held: TraceErrorRef
    query: str
    corpus: tuple[tuple[str, str], ...]
    relevant_prior: str
    ours_items: tuple[Mapping[str, Any], ...]
    oracle: str


def _err(tool: str, file: str, line: int, error_class: str) -> TraceErrorRef:
    sig = f"{tool}:{file}:{line}:{error_class}"
    return TraceErrorRef(tool=tool, file=file, line=line, error_class=error_class, signature=sig)


TASKS: tuple[LadderTask, ...] = (
    LadderTask(
        work_id="smoke-parser",
        rig="mem",
        started="2026-02-01T00:00:00Z",
        title="Fix argument type mismatch in the parser",
        held=_err("tsc", "src/parser.ts", 42, "TS2345"),
        query="type error argument not assignable in the parser call",
        corpus=(
            ("prior-coerce", "coerce the argument to the expected numeric type before the call"),
            ("prior-import", "add the missing import for the config module"),
            ("prior-await", "await the promise before reading its result in the scheduler"),
        ),
        # prior-coerce ranks first on token overlap; alpha-first would be prior-await.
        relevant_prior="prior-coerce",
        ours_items=(
            {
                "work_id": "ledger-parser-7",
                "citation": {"work_id": "ledger-parser-7", "title": "prior argument coercion fix"},
                "lessons": ["ranked ledger: the argument needs a numeric coercion at the boundary"],
            },
        ),
        # No "parser.ts" / "TS2345" substring: the oracle self-leak guard rejects the
        # held full/relaxed signature, and a smoke that trips the guard is not a smoke.
        oracle="curated fix: cast the argument to the numeric type expected by the helper",
    ),
    LadderTask(
        work_id="smoke-import",
        rig="mem",
        started="2026-02-02T00:00:00Z",
        title="Fix unresolved import in the loader",
        held=_err("eslint", "src/loader.ts", 8, "import-no-unresolved"),
        query="import module not resolved missing in the loader",
        corpus=(
            (
                "prior-modimport",
                "add the missing import for the loader module at the top of the file",
            ),
            ("prior-coerce2", "coerce the argument to the expected numeric type"),
            ("prior-retry", "increase the retry backoff for the network client"),
        ),
        # prior-modimport ranks first on token overlap; alpha-first would be prior-coerce2.
        relevant_prior="prior-modimport",
        ours_items=(
            {
                "work_id": "ledger-import-3",
                "citation": {"work_id": "ledger-import-3", "title": "prior module-resolution fix"},
                "lessons": ["ranked ledger: register the module path in the resolution config"],
            },
        ),
        oracle="curated fix: add the absent module reference at the head of the source file",
    ),
)


def _vocab_for(task: LadderTask) -> tuple[str, ...]:
    """The union of tokens across the task's query + corpus, sorted for a stable
    embedding dimension. Every vector from one arm must share a dimension, so the
    vocab is fixed once per task."""
    terms: set[str] = set(_tokens(task.query))
    for _, content in task.corpus:
        terms |= _tokens(content)
    return tuple(sorted(terms))


# --- per-rung payload resolution (each rung, the way the real driver will) --------


def _resolve_vector_payloads(task: LadderTask) -> dict[str, str]:
    """Drive the REAL `NemoEmbedMemory` arm over the seeded corpus with the fake
    embedder: write every prior into the task's trial scope, then retrieve on the
    failure string. The returned payloads are the cosine-ranked subset -- earned by
    retrieval, so 'the arm wires' is a true statement, not a hand-passed dict.

    Asserts the arm actually ranked the RELEVANT prior first (payloads are dict-ordered
    by rank). Without this the smoke would accept any non-empty top-k, so a tokenization
    or query regression that collapsed every cosine score to 0.0 -- retrieval effectively
    empty -- would still print `vector-rag` as wired (the arm returns id-sorted priors on
    a tie). The relevant prior is not the alphabetically-smallest id, so that degenerate
    case reorders it off the top and this check fails loud."""
    arm = NemoEmbedMemory(_NemoEmbedClient(FakeEmbedder(_vocab_for(task))), top_k=2)
    ctx = StepContext(trial_id=task.work_id, session_id="smoke", step_id="write", clock=IdClock())
    for memory_id, content in task.corpus:
        arm.write(memory_id, content, ctx)
    payloads = dict(arm.retrieve(RetrievalRequest(query_text=task.query), ctx).payloads)
    ranked = list(payloads)
    if not ranked or ranked[0] != task.relevant_prior:
        raise AssertionError(
            f"{task.work_id}: vector arm did not rank {task.relevant_prior!r} first "
            f"(got {ranked!r}); semantic retrieval regressed or scored every prior equal."
        )
    return payloads


def _resolve_ours_payloads(task: LadderTask) -> dict[str, str]:
    """Drive the REAL `OursMemory` arm with an INJECTED stub runner returning the
    task's fixture ledger rows -- the same retrieve/render path the real CLI runner
    takes, without the (still-empty) `lessons` store or a built `mem` binary."""

    def runner(query: OursQuery) -> dict[str, Any]:
        assert query.work_id == task.work_id
        items = list(task.ours_items)
        return {"items": items, "total_matched": len(items)}

    arm = OursMemory(store_path=":smoke:", runner=runner)
    ctx = StepContext(trial_id=task.work_id, session_id="smoke", step_id="query", clock=IdClock())
    request = RetrievalRequest(
        query_work=QueryWork(work_id=task.work_id, rig=task.rig, started=task.started),
        scope="cross_rig",
    )
    return dict(arm.retrieve(request, ctx).payloads)


def _stub_runner(task: LadderTask) -> StubRunner:
    """Deterministic per-rung traces: `none` never reaches the held file (the floor);
    every memory rung reaches it and the failure stays absent (fixture-authored to
    exercise SCORING per rung, NOT a real lift -- the stub ignores injected memory)."""
    reached = RunTrace(errors=(), files_touched=frozenset({task.held.file}))
    floor = RunTrace(errors=(task.held,), files_touched=frozenset())
    return StubRunner({"none": floor, "vector-rag": reached, "ours": reached, "oracle": reached})


def _record_for(task: LadderTask) -> dict[str, Any]:
    """The minimal WorkRecord the ladder adapter needs (work_id / rig / title +
    one-level lifecycle)."""
    return {
        "work_id": task.work_id,
        "rig": task.rig,
        "title": task.title,
        "lifecycle": {
            "created": "2026-01-01T00:00:00Z",
            "started": task.started,
            "closed": "2026-02-10T00:00:00Z",
            "status": "closed",
        },
    }


# A decoy outcome-label set (a commit sha + a PR ref) passed to every rung so the
# leak guard runs with a NON-EMPTY label set -- otherwise `assert_no_outcome_leak`
# scans against nothing and the "inject through the right guard" claim is vacuous. No
# fixture payload contains these, so the guard passes; the negative case (a payload
# that DOES leak a label) is pinned in tests/test_run_grid_4arm_recall_ladder.py.
OUTCOME_LABELS = ("deadbeefcafe1234", "PR-9999")


def run_task(
    task: LadderTask,
    output_dir: Path,
    *,
    vector_payloads: dict[str, str],
    ours_payloads: dict[str, str],
) -> list[RewardRecord]:
    """Score every rung through `run_grid` on already-resolved payloads (resolved once
    by the caller, so the earned-by-retrieval payloads are audited exactly once)."""
    return run_grid(
        _record_for(task),
        output_dir / task.work_id,
        held_errors=[task.held],
        runner=_stub_runner(task),
        rungs=RUNGS,
        vector_payloads=vector_payloads,
        ours_payloads=ours_payloads,
        oracle_payload=task.oracle,
        outcome_labels=OUTCOME_LABELS,
    )


# --- reporting -------------------------------------------------------------------


def _print_table(rows: list[tuple[RewardRecord, int]]) -> None:
    header = (
        f"{'work_id':<14} {'rung':<11} {'reached':<8} {'resolved':<9} {'reward':<7} {'#prior':<6}"
    )
    print(header)
    print("-" * len(header))
    for rec, n_prior in rows:
        c = rec.components
        print(
            f"{rec.work_id:<14} {rec.rung:<11} "
            f"{c.path_reached!s:<8} {c.trace_error_resolved!s:<9} "
            f"{rec.reward:<7.3f} {n_prior:<6}"
        )


def main() -> None:
    print("recall-ladder pilot smoke (mem-do8r) -- 4 rungs: none / vector-rag / ours / oracle")
    print(
        "deterministic: fake embedder + stub ours runner + StubRunner (no Docker/model/paid API)\n"
    )

    rows: list[tuple[RewardRecord, int]] = []
    with TemporaryDirectory(prefix="recall-ladder-smoke-") as tmp:
        output_dir = Path(tmp)
        for task in TASKS:
            # Resolve each retrieval arm ONCE (each drives a real write+retrieve), then
            # reuse the ranked payloads for both scoring and the #prior report column.
            vector_payloads = _resolve_vector_payloads(task)
            ours_payloads = _resolve_ours_payloads(task)
            counts = {
                "none": 0,
                "vector-rag": len(vector_payloads),
                "ours": len(ours_payloads),
                "oracle": 1,
            }
            records = run_task(
                task,
                output_dir,
                vector_payloads=vector_payloads,
                ours_payloads=ours_payloads,
            )
            for rec in records:
                rows.append((rec, counts[rec.rung]))

    _print_table(rows)

    scored = {rec.rung for rec, _ in rows}
    assert scored == set(RUNGS), f"expected every rung scored, got {sorted(scored)}"
    print(
        f"\nOK: all {len(RUNGS)} rungs wired through run_grid over {len(TASKS)} fixture tasks; "
        f"{len(rows)} comparable RewardRecords emitted (one per work_id x rung)."
    )
    print(
        "HALT (mayor pilot scope 2026-07-05): PLUMBING validated, NOT a lift curve. The "
        "StubRunner ignores injected memory by design, and rung-3 `ours` stays degenerate on "
        "the real grid until a `mem distill-lessons` pass populates the lessons store (ADR "
        "lock #2). Do NOT run the full grid in this pass."
    )


if __name__ == "__main__":
    main()
