#!/usr/bin/env python3
"""Per-step VRAM-creep watchdog (PRD amendment A6 / R6; protects the R7/R10 GRPO colocate leg).

The colocate leg serves a frozen generator under vLLM and trains a <=3B GRPO
searcher on the SAME 32 GB card (premortem #4). The documented killer is a slow,
monotonic step-over-step VRAM climb (fragmentation, KV-cache growth, leaked
activations) that ends in a hard OOM and loses the run. A hard OOM is unrecoverable;
a checkpoint-then-abort *before* the ceiling is recoverable.

This watchdog:
  - records used VRAM after each training step (caller calls ``observe(step)``),
  - appends a JSONL log line per step (per-step VRAM logging requirement),
  - detects a *sustained monotonic* climb (not single-step jitter) over a window,
  - when the climb is sustained AND used VRAM is within an abort margin of total,
    it requests a checkpoint via a caller-supplied callback, then signals abort.

It deliberately does NOT call ``torch.cuda.empty_cache`` or try to "fix" the leak —
that would mask the bug the premortem says to surface. It reports and aborts cleanly.

Reading VRAM: prefers ``pynvml`` if importable (no torch dependency, sees the whole
device incl. the colocated vLLM server), falls back to parsing ``nvidia-smi``. Both
report *device-wide* used memory, which is what matters on a shared card.

This module reads GPU memory counters only — it launches no GPU compute. Importing
or running it on a box with a GPU touches nvml/nvidia-smi (read-only); on a box
without a GPU it raises a clear error. Treat as approval-gated only insofar as it
is wired into a GPU run.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

MIB = 1024 * 1024


def _read_vram_mib_pynvml() -> tuple[int, int] | None:
    """(used_mib, total_mib) via pynvml, or None if pynvml is unavailable."""
    try:
        import pynvml  # type: ignore
    except Exception:
        return None
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return int(info.used // MIB), int(info.total // MIB)
    except Exception:
        return None
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def _read_vram_mib_smi() -> tuple[int, int]:
    """(used_mib, total_mib) via nvidia-smi. Raises if nvidia-smi is missing/fails."""
    out = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    # Single card assumed; take the first line.
    first = out.splitlines()[0]
    used_s, total_s = (x.strip() for x in first.split(","))
    return int(used_s), int(total_s)


def read_vram_mib() -> tuple[int, int]:
    """Device-wide (used_mib, total_mib). pynvml first, else nvidia-smi."""
    via_nvml = _read_vram_mib_pynvml()
    if via_nvml is not None:
        return via_nvml
    return _read_vram_mib_smi()


@dataclasses.dataclass(frozen=True)
class WatchdogConfig:
    log_path: Path
    # Sustained-climb window: require strictly increasing used-VRAM across this many
    # consecutive observations before treating it as a real leak (filters jitter).
    window: int = 5
    # Per-step climb must average at least this many MiB across the window to count.
    min_climb_mib_per_step: float = 64.0
    # Abort once used VRAM reaches (total - this margin); leaves room to checkpoint.
    abort_margin_mib: int = 2048


class VramWatchdog:
    """Stateful per-step monitor. Immutable config; the mutable history is internal."""

    def __init__(self, config: WatchdogConfig) -> None:
        self._cfg = config
        self._used_history: list[int] = []
        self._total_mib: int | None = None
        self._cfg.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _is_sustained_climb(self) -> bool:
        hist = self._used_history[-self._cfg.window :]
        if len(hist) < self._cfg.window:
            return False
        # Strictly monotonic across the window.
        if not all(b > a for a, b in zip(hist, hist[1:])):
            return False
        avg_climb = (hist[-1] - hist[0]) / (len(hist) - 1)
        return avg_climb >= self._cfg.min_climb_mib_per_step

    def _near_ceiling(self, used_mib: int, total_mib: int) -> bool:
        return used_mib >= (total_mib - self._cfg.abort_margin_mib)

    def observe(
        self,
        step: int,
        on_abort_checkpoint: Callable[[], None] | None = None,
    ) -> bool:
        """Record VRAM for ``step``; return True if the run should ABORT now.

        On an abort decision, calls ``on_abort_checkpoint`` (if given) to persist a
        recoverable checkpoint BEFORE returning True. The caller is responsible for
        actually stopping the loop on a True return.
        """
        used_mib, total_mib = read_vram_mib()
        self._total_mib = total_mib
        self._used_history.append(used_mib)

        sustained = self._is_sustained_climb()
        near = self._near_ceiling(used_mib, total_mib)
        abort = sustained and near

        record = {
            "step": step,
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "used_mib": used_mib,
            "total_mib": total_mib,
            "free_mib": total_mib - used_mib,
            "sustained_climb": sustained,
            "near_ceiling": near,
            "abort": abort,
        }
        with self._cfg.log_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")

        if abort:
            # Surface loudly; do NOT try to free/mask. Checkpoint then signal stop.
            msg = (
                f"vram_watchdog: ABORT at step {step}: used {used_mib}/{total_mib} MiB "
                f"with sustained monotonic climb over {self._cfg.window} steps "
                f"(within {self._cfg.abort_margin_mib} MiB of ceiling). "
                f"Checkpointing then aborting to avoid hard OOM."
            )
            print(msg, flush=True)
            if on_abort_checkpoint is not None:
                on_abort_checkpoint()
        return abort


def _demo(argv: list[str] | None = None) -> int:
    """Offline self-test of the climb logic with a synthetic VRAM series (no GPU).

    Verifies that a sustained climb near the ceiling triggers abort while jitter
    below the ceiling does not. Does not call read_vram_mib().
    """
    import argparse
    import tempfile

    p = argparse.ArgumentParser(description="vram_watchdog self-test (no GPU)")
    p.add_argument("--log", type=Path, default=None)
    args = p.parse_args(argv)
    log = args.log or Path(tempfile.mkstemp(prefix="vram_wd_", suffix=".jsonl")[1])

    cfg = WatchdogConfig(
        log_path=log, window=4, min_climb_mib_per_step=64, abort_margin_mib=2048
    )
    wd = VramWatchdog(cfg)
    total = 32768

    # Drive the climb-detection logic directly with a synthetic series so the demo
    # needs no GPU. Jitter below ceiling -> no abort; sustained climb to near
    # ceiling -> abort.
    jitter = [10000, 9800, 10200, 9900, 10100]
    for u in jitter:
        wd._used_history.append(u)
        assert not (wd._is_sustained_climb() and wd._near_ceiling(u, total))

    wd._used_history.clear()
    climb = [29000, 29600, 30200, 30900, 31200]  # monotonic, ending near 32768
    aborted = False
    for u in climb:
        wd._used_history.append(u)
        if wd._is_sustained_climb() and wd._near_ceiling(u, total):
            aborted = True
    assert aborted, "expected sustained near-ceiling climb to trigger abort"

    print(json.dumps({"log": str(log), "abort_triggered": aborted}, indent=2))
    print("OK: climb detection + ceiling-margin abort verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
