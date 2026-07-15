"""`ours` — retrieval-v1 (mem-di8) as a harness arm (integrated condition C).

This arm does not reimplement retrieval. It delegates to the retrieval-v1 surface
already shipped in TypeScript (`src/retrieve`, contract D6-D10) through the
`mem retrieve --json` CLI — the single substrate (no second store), consuming the
append-only `lessons` payload (D9, never re-distilled). The boundary and the store
handle are supplied by the harness; the arm has no discretion over them, and the
harness re-checks the arm's output against its LOO-bounded set
(`validity.assert_no_leak`). That is how "no arm touches the raw store directly"
holds even though retrieval physically reads the shared sidecar.

`ours` is **replay-only** (Decision 8): it runs over the work-audit graph for a
query work `B`, not over the convention-sequence fixture (which carries no errors
and no WorkRecords). Calling it from the id-based sequence runner is a
configuration error and raises.

**Trigger labeling (mem-tnyo).** In replay, the default query is built by
`queryFromRecord` from the held record's OWN stored trace errors -- failures the
fresh agent has not yet produced, i.e. an ORACLE trigger, not the deployed
failure-triggered flow. That is made explicit: `OursMemory.trigger == "oracle"`.
The separable control `OursIssueTriggerMemory` (`trigger == "issue-text"`) forms
the query WITHOUT the trace errors (`mem retrieve --no-trace-query`: title /
task-type text only -- the fields available at dispatch time), so the
trigger-information contribution is measurable on its own.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from membench.mem_cli import run_mem_json
from membench.memory_systems.base import (
    MemorySystem,
    RetrievalRequest,
    RetrieveResult,
)
from membench.runtime import StepContext
from membench.schemas.bundle import TaskBundle
from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation

# CLI scope spellings (retrieve.ts SCOPES), keyed by the internal scope value.
_CLI_SCOPE = {"cross_rig": "cross-rig", "same_rig_temporal": "same-rig"}

# The realistic dual-track scope (D7) -- same-rig prior work, temporally bounded.
RETRIEVAL_SCOPE = "same_rig_temporal"


@dataclass(frozen=True)
class OursQuery:
    """The minimal call the runner needs: replay a closed work under one scope.
    `work_id` is resolved to its full query context (errors, boundary) inside
    retrieval-v1 via `queryFromRecord` — the P2.2 replay path. With
    `no_trace_query` the CLI forms the query WITHOUT the record's stored trace
    errors (title/task-type text only — the mem-tnyo issue-text trigger)."""

    work_id: str
    scope: str
    store_path: str
    limit: int | None = None
    no_trace_query: bool = False


# A runner returns retrieval-v1's `RetrievalResult` (the CLI `--json` envelope's
# `data`). Injectable so the arm is testable without a built CLI or a real store.
RetrieveRunner = Callable[[OursQuery], dict[str, Any]]


def _render_payload(item: dict[str, Any]) -> str:
    """Render one retrieved item as the injected memory text: the citation plus
    the consumed (not rewritten) lesson payloads, canonically serialized so the
    injected-context volume (Decision-10 precision guard) is deterministic."""
    citation = item.get("citation", {})
    lessons = item.get("lessons", [])
    return json.dumps(
        {"citation": citation, "lessons": lessons},
        sort_keys=True,
        ensure_ascii=False,
    )


def _default_runner(mem_bin: str) -> RetrieveRunner:
    """Shell out to `mem retrieve <work_id> --scope ... --store ...` through the
    shared seam (`mem_cli.run_mem_json`: timeout, missing-binary and
    malformed-envelope context). A failed retrieval always raises — it is never
    silently treated as "no memory"."""

    def run(query: OursQuery) -> dict[str, Any]:
        argv = [
            mem_bin,
            "retrieve",
            query.work_id,
            "--scope",
            _CLI_SCOPE[query.scope],
            "--store",
            query.store_path,
        ]
        if query.limit is not None:
            argv += ["--limit", str(query.limit)]
        if query.no_trace_query:
            argv.append("--no-trace-query")
        return run_mem_json(argv)

    return run


def resolve_payloads(
    bundles: Sequence[TaskBundle],
    *,
    store_path: Path,
    runner: RetrieveRunner,
    no_trace_query: bool = False,
) -> dict[str, dict[str, str]]:
    """work_id -> (source work_id -> rendered citation+lessons payload) via the
    ours ARM's own retrieval runner, so the injected text is exactly what the arm
    would inject. Items without lessons are dropped -- the arm's information
    content is the lesson payload (D9); a bare citation carries none. Every item
    is checked against the bundle's LOO exclusion set (D6): retrieval-v1 is
    contracted to enforce that boundary, but a leak here would hand the agent its
    own work record, so the caller re-asserts rather than assumes.

    ``no_trace_query`` resolves the mem-tnyo issue-text-trigger payloads instead:
    the query is formed WITHOUT the held record's stored trace errors (title /
    task-type text only -- `mem retrieve --no-trace-query`)."""
    payloads: dict[str, dict[str, str]] = {}
    for bundle in bundles:
        result = runner(
            OursQuery(
                work_id=bundle.work_id,
                scope=RETRIEVAL_SCOPE,
                store_path=str(store_path),
                no_trace_query=no_trace_query,
            )
        )
        items = [item for item in result.get("items", []) if item.get("lessons")]
        leaked = sorted({item["work_id"] for item in items} & set(bundle.loo_excluded_work_ids))
        if leaked:
            raise RuntimeError(
                f"{bundle.work_id}: retrieval returned LOO-excluded work id(s) {leaked} -- "
                "the D6 boundary is broken; refusing to inject"
            )
        payloads[bundle.work_id] = {item["work_id"]: _render_payload(item) for item in items}
    return payloads


class OursMemory(MemorySystem):
    name = "ours"
    backend = MemoryBackend.KG
    uses_scope = True
    # How the replay query is formed (mem-tnyo condition metadata): "oracle" —
    # from the held record's OWN stored trace errors (queryFromRecord), an
    # information source the fresh agent does not have before failing. The
    # issue-text control overrides this in its subclass.
    trigger: str = "oracle"
    # The post-task write/reflect interface is append-only to a per-run scratch
    # store (D14, mem-lvp) — never this LOO-bounded corpus. Out of scope here.
    supports_write = False

    def __init__(
        self,
        store_path: str | Path | None = None,
        *,
        runner: RetrieveRunner | None = None,
        mem_bin: str | None = None,
        limit: int | None = None,
    ) -> None:
        self._store_path = str(store_path) if store_path is not None else None
        self._limit = limit
        # Either an injected runner (tests) or the subprocess default. The default
        # needs the `mem` binary path; resolve it lazily so constructing an arm
        # with an injected runner never depends on a built CLI.
        self._runner = runner
        self._mem_bin = mem_bin

    def reset(self, trial_id: str) -> None:
        return None

    def _resolve_runner(self) -> RetrieveRunner:
        if self._runner is not None:
            return self._runner
        if self._mem_bin is None:
            raise ValueError(
                "OursMemory needs either an injected `runner` or a `mem_bin` path "
                "to the retrieval-v1 CLI."
            )
        self._runner = _default_runner(self._mem_bin)
        return self._runner

    def retrieve(self, request: RetrievalRequest, ctx: StepContext) -> RetrieveResult:
        if request.query_work is None or request.scope is None:
            raise ValueError(
                "`ours` is failure-triggered/replay-only: it needs request.query_work "
                "+ request.scope. It does not serve the id-based sequence runner."
            )
        if self._store_path is None:
            raise ValueError("OursMemory needs a store_path (the harness LOO-bounded store).")
        if request.scope not in _CLI_SCOPE:
            raise ValueError(
                f"unknown retrieval scope {request.scope!r}; expected one of {sorted(_CLI_SCOPE)}"
            )

        result = self._resolve_runner()(
            OursQuery(
                work_id=request.query_work.work_id,
                scope=request.scope,
                store_path=self._store_path,
                limit=self._limit,
                no_trace_query=self.trigger == "issue-text",
            )
        )
        items = result.get("items", [])
        payloads = {item["work_id"]: _render_payload(item) for item in items}

        event = MemoryEvent(
            event_id=ctx.clock.event_id(),
            trial_id=ctx.trial_id,
            session_id=ctx.session_id,
            step_id=ctx.step_id,
            timestamp=ctx.clock.timestamp(),
            concrete_tool=f"mem retrieve --scope {_CLI_SCOPE[request.scope]}",
            normalized_operation=MemoryOperation.SEARCH,
            backend=self.backend,
            query=request.query_work.work_id,
            retrieved_ids=list(payloads),
            latency_ms=ctx.clock.latency_ms(),
            success=True,
        )
        return RetrieveResult(
            payloads=payloads,
            event=event,
            total_matched=int(result.get("total_matched", len(items))),
            near_duplicate_top=bool(result.get("near_duplicate_top", False)),
            fts_truncated=bool(result.get("fts_truncated", False)),
        )

    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent:
        raise NotImplementedError(
            "`ours` retrieval arm does not write; the post-task write/reflect "
            "interface (append-only scratch store, D14) lands in mem-lvp."
        )


class OursIssueTriggerMemory(OursMemory):
    """The mem-tnyo separable trigger control: identical retrieval surface, leak
    guards, and payload rendering as `ours`, but the query is formed WITHOUT the
    held record's stored trace errors (`mem retrieve --no-trace-query`) — from
    the issue/bead text only (title + task-type, the fields available at
    dispatch time). Condition metadata records `trigger = "issue-text"`."""

    name = "ours-issue-trigger"
    trigger = "issue-text"
