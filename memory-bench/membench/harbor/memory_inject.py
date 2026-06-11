"""Per-rung memory CONTENT injection into an ablation task dir (mem-apg.3d).

mem-apg.2's `WorkRecordLadderAdapter` emits the per-rung information SPEC -- which
memory each rung MAY access -- but writes no memory content (architect-H4 gap). This
module closes it: given a rung's resolved memory payloads, it writes the actual
agent-readable memory file into the task dir.

The payloads themselves are resolved by the driver (`grid.run_grid`), which calls
the `ours` arm under the task's LOO boundary (D6) and supplies the oracle's
ground-truth prior. This module is pure mechanism (ZFC): it writes IO and runs the
leak guards; it makes no retrieval and no semantic judgment.

Two guards run before any write, so a leak aborts with nothing on disk:

- `assert_no_outcome_leak` (the mem-apg.1 task-construction guard) -- no high-entropy
  outcome identifier (pr / commit_sha / base_commit) may reach agent-readable text.
- the `oracle`-only H3 self-leak guard -- the oracle payload must NOT contain the
  held-out bead's OWN `trace_error` signature (full or relaxed), because that
  signature is exactly the answer the deterministic scorer (mem-apg.3a) checks.
  Leaking it would let the agent trivially "avoid" the failure it was handed.

`builtin` / `ours+builtin` depend on the agent's opaque built-in memory (the paid
Harbor path owned by mem-whi) and are DEFERRED -- injecting them is not this bead's
job, so they raise `DeferredRungError` rather than silently writing nothing.
"""

from collections.abc import Iterable, Sequence
from pathlib import Path

from membench.grading import (
    TraceErrorRef,
    assert_no_outcome_leak,
    relaxed_signature,
)

# Where a rung's memory content lands inside its task dir. A single Markdown file
# under a `memory/` area, mirroring the agent's own MEMORY.md convention so the
# injected prior reads the same as a built-in memory would.
MEMORY_FILENAME = "memory/MEMORY.md"

# Rungs whose memory is the agent's opaque built-in store -- owned by mem-whi's
# paid Harbor path, not constructible here.
DEFERRED_RUNGS = ("builtin", "ours+builtin")

# Rungs this module can inject today (the runnable subset of the ladder).
RUNNABLE_RUNGS = ("none", "ours", "oracle")

# The full ladder vocabulary (grading/ablation.py's DEFAULT_RUNGS draws from it).
KNOWN_RUNGS = RUNNABLE_RUNGS + DEFERRED_RUNGS


def validate_rungs(rungs: Sequence[str]) -> tuple[str, ...]:
    """Validate a rung list against the ladder vocabulary BEFORE any execution.

    Raises ValueError naming every unknown rung -- a typo'd ladder must fail at
    config/load time, not mid-sweep after earlier rungs have already burned agent
    runs. Returns the deferred subset (in ladder order) so the caller can surface
    what will be skipped instead of discovering it from a thinner result."""
    unknown = [rung for rung in rungs if rung not in KNOWN_RUNGS]
    if unknown:
        raise ValueError(
            f"unknown ablation rung(s) {unknown!r}; known rungs: {list(KNOWN_RUNGS)!r}"
        )
    return tuple(rung for rung in rungs if rung in DEFERRED_RUNGS)


class DeferredRungError(ValueError):
    """Raised when a rung's memory is owned by another bead (builtin / ours+builtin).

    Distinct from an unknown-rung error so the driver can SKIP these rungs
    deliberately instead of treating them as a config typo."""


class OracleSelfLeakError(AssertionError):
    """Raised when the oracle payload contains the held-out bead's own trace_error
    signature (full or relaxed) -- the answer the scorer checks (architect H3).

    A validity bug that must fail the run loudly, never be silently stripped."""

    def __init__(self, offenders: list[str]) -> None:
        self.offenders = offenders
        detail = ", ".join(repr(sig) for sig in offenders)
        super().__init__(
            f"oracle payload leaks the held bead's own trace_error signature: {detail}"
        )


def _assert_no_oracle_self_leak(payload: str, held_errors: Sequence[TraceErrorRef]) -> None:
    """Fail loud if `payload` contains any held error's full or relaxed signature.

    Both keys are scanned because the scorer's avoid-axis keys on the RELAXED
    signature (`relaxed_signature`), so leaking only the relaxed key still hands
    the agent the answer. Case-insensitive, mirroring `assert_no_outcome_leak`."""
    haystack = payload.lower()
    offenders: list[str] = []
    seen: set[str] = set()
    for error in held_errors:
        for signature in (error.signature, relaxed_signature(error)):
            if signature not in seen and signature.lower() in haystack:
                seen.add(signature)
                offenders.append(signature)
    if offenders:
        raise OracleSelfLeakError(offenders)


def _render(payloads: dict[str, str]) -> str:
    """Render id -> payload as the agent-readable memory file. One section per
    prior, keyed by its source id so the agent can cite it. An empty mapping
    yields an explicit 'no relevant prior memory' body -- the rung was tried."""
    if not payloads:
        return "# Prior-session memory\n\nNo relevant prior memory was retrieved.\n"
    parts = ["# Prior-session memory", ""]
    for source_id, content in payloads.items():
        parts += [f"## {source_id}", "", content, ""]
    return "\n".join(parts)


def _write(task_dir: Path, text: str) -> Path:
    target = task_dir / MEMORY_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def inject_context(
    task_dir: str | Path,
    payloads: dict[str, str],
    *,
    outcome_labels: Iterable[str] = (),
) -> Path:
    """Render + leak-check + write context payloads as the task's memory file.

    The injection MECHANISM shared by `inject_rung_memory`'s content-bearing rungs
    and the mem-75t.7.6 probe gate (`harbor.probe_gate`), which injects the cheap
    oracle-rung context (the gold-diff file list) through the same render + guard +
    write path. The leak guard runs over the RENDERED text, so a label smuggled in
    via a payload key fails just like one in a payload body."""
    text = _render(payloads)
    assert_no_outcome_leak(text, list(outcome_labels))
    return _write(Path(task_dir), text)


def inject_rung_memory(
    task_dir: str | Path,
    rung: str,
    *,
    held_errors: Sequence[TraceErrorRef],
    ours_payloads: dict[str, str] | None = None,
    oracle_payload: str | None = None,
    outcome_labels: Iterable[str] = (),
) -> Path | None:
    """Write `rung`'s memory content into `task_dir`; return the file written.

    - `none` -> writes nothing, returns None (the stateless control).
    - `ours` -> writes the distilled retrieval payloads (`ours_payloads`).
    - `oracle` -> writes `oracle_payload`, guarded by the H3 self-leak check.
    - `builtin` / `ours+builtin` -> `DeferredRungError` (owned by mem-whi).

    All payloads are leak-checked for outcome labels before any write; the oracle
    rung additionally runs the self-leak guard. `held_errors` is required and must
    be non-empty (the held-out set is 'beads with >=1 trace_error' by
    construction) so the oracle guard always has the answer to protect."""
    held = list(held_errors)
    if not held:
        raise ValueError("inject_rung_memory needs at least one held error (oracle guard input)")

    task_path = Path(task_dir)
    labels = list(outcome_labels)

    if rung == "none":
        return None

    if rung == "ours":
        return inject_context(task_path, ours_payloads or {}, outcome_labels=labels)

    if rung == "oracle":
        if oracle_payload is None:
            raise ValueError("oracle rung needs an oracle_payload")
        _assert_no_oracle_self_leak(oracle_payload, held)
        return inject_context(task_path, {"oracle": oracle_payload}, outcome_labels=labels)

    if rung in DEFERRED_RUNGS:
        raise DeferredRungError(
            f"rung {rung!r} depends on the agent's built-in memory (paid Harbor path, "
            "mem-whi) and is not injected here"
        )

    raise ValueError(f"unknown ablation rung {rung!r}")
