"""`ours` — retrieval-v1 (mem-di8) as a harness arm (integrated condition C).

This arm does not reimplement retrieval. It delegates to the retrieval-v1 surface
already shipped in TypeScript (`src/retrieve`, contract D6–D10) through the
`mem retrieve --json` CLI — the single substrate (no second store), consuming the
append-only `lessons` payload (D9, never re-distilled). The boundary and the store
handle are supplied by the harness; the arm has no discretion over them, and the
harness re-checks the arm's output against its LOO-bounded set
(`validity.assert_no_leak`). That is how "no arm touches the raw store directly"
holds even though retrieval physically reads the shared sidecar.

`ours` is **failure-triggered and replay-only** (Decision 8): it runs over the
work-audit graph for a query work `B`, not over the convention-sequence fixture
(which carries no errors and no WorkRecords). Calling it from the id-based
sequence runner is a configuration error and raises.
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from membench.memory_systems.base import (
    MemorySystem,
    RetrievalRequest,
    RetrieveResult,
)
from membench.runtime import StepContext
from membench.schemas.memory_event import MemoryBackend, MemoryEvent, MemoryOperation

# CLI scope spellings (retrieve.ts SCOPES), keyed by the internal scope value.
_CLI_SCOPE = {"cross_rig": "cross-rig", "same_rig_temporal": "same-rig"}


@dataclass(frozen=True)
class OursQuery:
    """The minimal call the runner needs: replay a closed work under one scope.
    `work_id` is resolved to its full query context (errors, boundary) inside
    retrieval-v1 via `queryFromRecord` — the P2.2 replay path."""

    work_id: str
    scope: str
    store_path: str
    limit: int | None = None


# A runner returns retrieval-v1's `RetrievalResult` (the CLI `--json` envelope's
# `data`). Injectable so the arm is testable without a built CLI or a real store.
RetrieveRunner = Callable[[OursQuery], dict]


def _render_payload(item: dict) -> str:
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
    """Shell out to `mem retrieve <work_id> --scope ... --store ... --json`,
    parsing the success envelope. A non-zero exit or an error envelope raises —
    a failed retrieval is never silently treated as "no memory"."""

    def run(query: OursQuery) -> dict:
        cli_scope = _CLI_SCOPE[query.scope]
        argv = [
            mem_bin,
            "retrieve",
            query.work_id,
            "--scope",
            cli_scope,
            "--store",
            query.store_path,
            "--json",
        ]
        if query.limit is not None:
            argv += ["--limit", str(query.limit)]
        completed = subprocess.run(  # noqa: S603 - argv is fully constructed, no shell
            argv, capture_output=True, text=True, check=False
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"mem retrieve failed (exit {completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        envelope = json.loads(completed.stdout)
        if not envelope.get("ok", False):
            raise RuntimeError(f"mem retrieve error: {envelope.get('errors')}")
        return envelope["data"]

    return run


class OursMemory(MemorySystem):
    name = "ours"
    backend = MemoryBackend.KG
    uses_scope = True
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

    def reset(self, trial_id: str) -> None:  # noqa: D401 - stateless over the bounded store
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
        )

    def write(self, memory_id: str, content: str, ctx: StepContext) -> MemoryEvent:
        raise NotImplementedError(
            "`ours` retrieval arm does not write; the post-task write/reflect "
            "interface (append-only scratch store, D14) lands in mem-lvp."
        )
