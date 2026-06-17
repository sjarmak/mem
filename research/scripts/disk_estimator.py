#!/usr/bin/env python3
"""Pre-launch disk guard for single-RTX-5090 fine-tuning/RL runs (PRD amendment A6 / R6).

Projects the *peak* disk footprint of a training run and refuses to launch if that
peak would not fit in currently-free space. This matters on a near-full or shared
host: an existing large ``~/.cache`` and concurrent workloads are exactly what
"clean box" estimates ignore, and the run dies at a checkpoint-save mid-training.

Peak disk is modeled as the sum of components that are simultaneously on disk at the
worst moment of a run:

    peak = cache_baseline            # what ~/.cache already holds today (pre-prune target)
         + base_weights             # the model snapshot pulled into HF_HOME
         + datasets_raw             # downloaded raw dataset files
         + datasets_processed       # Arrow / tokenized memory-maps (often >= raw)
         + checkpoint_size * keep_count   # last-good + current (atomic_checkpoint.py keeps 2)
         + incomplete_temp          # *.incomplete partial-download / tmp write headroom

This is a *read-only / dry-run* tool: it stats existing paths, does arithmetic, and
either prints a green report (exit 0) or refuses (exit 1). It never deletes, downloads,
or writes anything outside the results dir it is pointed at (and even there, only a
JSON snapshot, and only when --emit is given).

Usage (all sizes in GiB unless suffixed):
    python disk_estimator.py \
        --base-weights 16 \
        --datasets-raw 4 --datasets-processed 6 \
        --checkpoint-size 1.2 --keep-count 2 \
        --incomplete-temp 8 \
        --target-dir "$HOME"/runs \
        [--cache-dir ~/.cache] [--assume-pruned] \
        [--emit "$HOME"/runs/<run>/disk_estimate.json]

Exit codes:
    0  projected peak fits in free space (with headroom margin)
    1  projected peak exceeds free space (or exceeds it once the safety margin is applied)
    2  bad invocation / unreadable target
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path

GIB = 1024**3

# Keep a safety margin so we do not fill the disk to the last byte. The kernel,
# logs, and any co-tenant workloads need slack; a run that lands at 99.9% full is
# a failed run waiting to happen.
DEFAULT_HEADROOM_GIB = 10.0


@dataclasses.dataclass(frozen=True)
class Components:
    """All sizes are bytes. Immutable: arithmetic returns new values, never mutates.

    The box has two filesystems in play: local NVMe (97% full, the scarce one) and
    the NFS NAS (/mnt, 48 TB free). Datasets are sequential-read and NAS-tolerable, so
    they can be routed to the NAS to spare local NVMe; the active model snapshot,
    checkpoints, and temp scratch stay local (fast load / high IOPS / NFS-locking).
    ``concurrent_local`` is the OTHER track's simultaneous local footprint, so a
    combined Track-A + Track-B run is checked against local free as one budget.
    """

    cache_baseline: int
    base_weights: int
    datasets_raw: int
    datasets_processed: int
    checkpoint_size: int
    keep_count: int
    incomplete_temp: int
    datasets_on_nas: bool = False
    concurrent_local: int = 0

    @property
    def checkpoints_total(self) -> int:
        return self.checkpoint_size * self.keep_count

    @property
    def datasets_total(self) -> int:
        return self.datasets_raw + self.datasets_processed

    @property
    def local_peak(self) -> int:
        """Peak bytes that land on local NVMe at the worst moment."""
        local = (
            self.cache_baseline
            + self.base_weights
            + self.checkpoints_total
            + self.incomplete_temp
            + self.concurrent_local
        )
        if not self.datasets_on_nas:
            local += self.datasets_total
        return local

    @property
    def nas_peak(self) -> int:
        """Peak bytes routed to the NAS (0 when datasets stay local)."""
        return self.datasets_total if self.datasets_on_nas else 0

    @property
    def peak(self) -> int:
        """Total footprint across both filesystems (back-compat / reporting)."""
        return self.local_peak + self.nas_peak - self.concurrent_local

    def as_report(self) -> dict[str, float]:
        return {
            "cache_baseline_gib": _g(self.cache_baseline),
            "base_weights_gib": _g(self.base_weights),
            "datasets_raw_gib": _g(self.datasets_raw),
            "datasets_processed_gib": _g(self.datasets_processed),
            "datasets_on_nas": self.datasets_on_nas,
            "checkpoint_size_gib": _g(self.checkpoint_size),
            "keep_count": self.keep_count,
            "checkpoints_total_gib": _g(self.checkpoints_total),
            "incomplete_temp_gib": _g(self.incomplete_temp),
            "concurrent_local_gib": _g(self.concurrent_local),
            "local_peak_gib": _g(self.local_peak),
            "nas_peak_gib": _g(self.nas_peak),
            "projected_peak_gib": _g(self.peak),
        }


def _g(num_bytes: int) -> float:
    """Bytes -> GiB, rounded for human-readable reports."""
    return round(num_bytes / GIB, 2)


def gib_to_bytes(value: float) -> int:
    return int(round(value * GIB))


def measure_dir_bytes(path: Path) -> int:
    """Sum on-disk apparent sizes under ``path``. Returns 0 if the path is absent.

    Read-only. Follows no symlinks out of the tree (uses lstat). Unreadable
    entries are skipped rather than crashing the guard — a guard that dies on a
    permission error is worse than one that under-counts a subtree and says so.
    """
    if not path.exists():
        return 0
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.lstat().st_size
            except OSError:
                # Skip vanished/locked files; this is a live shared box.
                continue
    return total


def free_bytes(target_dir: Path) -> int:
    """Free bytes on the filesystem backing ``target_dir`` (its nearest existing parent)."""
    probe = target_dir
    while not probe.exists():
        if probe.parent == probe:
            break
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    return usage.free


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Project peak disk for a run and refuse launch if it will not fit.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--base-weights",
        type=float,
        required=True,
        help="Size of the base model snapshot pulled into HF_HOME (GiB).",
    )
    p.add_argument(
        "--datasets-raw",
        type=float,
        default=0.0,
        help="Raw downloaded dataset files (GiB).",
    )
    p.add_argument(
        "--datasets-processed",
        type=float,
        default=0.0,
        help="Arrow / tokenized memory-mapped dataset artifacts (GiB). Often >= raw.",
    )
    p.add_argument(
        "--checkpoint-size",
        type=float,
        required=True,
        help="Size of ONE checkpoint (LoRA adapter + optimizer/scheduler/RNG state) (GiB).",
    )
    p.add_argument(
        "--keep-count",
        type=int,
        default=2,
        help="Checkpoints retained at once (atomic_checkpoint.py keeps last-good + current = 2).",
    )
    p.add_argument(
        "--incomplete-temp",
        type=float,
        default=8.0,
        help="Headroom for *.incomplete partial downloads + temp checkpoint dirs (GiB).",
    )
    p.add_argument(
        "--target-dir",
        type=Path,
        required=True,
        help="Directory the run will write to; its filesystem's free space is the budget.",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("~/.cache").expanduser(),
        help="Existing cache directory whose current size counts toward peak.",
    )
    p.add_argument(
        "--cache-baseline",
        type=float,
        default=None,
        help="Override measured cache size (GiB). If omitted, --cache-dir is measured live.",
    )
    p.add_argument(
        "--assume-pruned",
        action="store_true",
        help="Model ~/.cache as already pre-pruned to 0 (do this ONLY after actually pruning).",
    )
    p.add_argument(
        "--headroom",
        type=float,
        default=DEFAULT_HEADROOM_GIB,
        help="Safety margin kept free above the projected peak (GiB).",
    )
    p.add_argument(
        "--datasets-on-nas",
        action="store_true",
        help="Route datasets to the NAS (/mnt); checked against NAS free, not local NVMe.",
    )
    p.add_argument(
        "--nas-dir",
        type=Path,
        default=Path("/mnt/ml"),
        help="NAS directory datasets are routed to (its filesystem's free space is the NAS budget).",
    )
    p.add_argument(
        "--concurrent-local-gib",
        type=float,
        default=0.0,
        help="Other track's simultaneous LOCAL footprint (GiB) for a combined A+B budget "
        "(e.g. Track-B Harbor node_modules/worktrees while Track-A trains).",
    )
    p.add_argument(
        "--emit",
        type=Path,
        default=None,
        help="Optional path to write the estimate JSON snapshot into the run's results dir.",
    )
    return p


def resolve_cache_baseline(args: argparse.Namespace) -> int:
    if args.assume_pruned:
        return 0
    if args.cache_baseline is not None:
        return gib_to_bytes(args.cache_baseline)
    return measure_dir_bytes(args.cache_dir)


@dataclasses.dataclass(frozen=True)
class Verdict:
    """The two-filesystem result. ``fits`` requires BOTH local and (if used) NAS to fit."""

    comp: Components
    local_free: int
    nas_free: int
    fits: bool
    message: str


def evaluate(args: argparse.Namespace) -> Verdict:
    """Check the run's peak against BOTH filesystems. Pure aside from stat/walk."""
    comp = Components(
        cache_baseline=resolve_cache_baseline(args),
        base_weights=gib_to_bytes(args.base_weights),
        datasets_raw=gib_to_bytes(args.datasets_raw),
        datasets_processed=gib_to_bytes(args.datasets_processed),
        checkpoint_size=gib_to_bytes(args.checkpoint_size),
        keep_count=args.keep_count,
        incomplete_temp=gib_to_bytes(args.incomplete_temp),
        datasets_on_nas=args.datasets_on_nas,
        concurrent_local=gib_to_bytes(args.concurrent_local_gib),
    )
    headroom = gib_to_bytes(args.headroom)

    local_free = free_bytes(args.target_dir)
    local_required = comp.local_peak + headroom
    local_fits = local_required <= local_free

    nas_free = free_bytes(args.nas_dir) if comp.nas_peak > 0 else 0
    nas_required = comp.nas_peak + headroom if comp.nas_peak > 0 else 0
    nas_fits = comp.nas_peak == 0 or nas_required <= nas_free

    fits = local_fits and nas_fits

    parts: list[str] = []
    verb = "OK" if fits else "REFUSE"
    parts.append(
        f"LOCAL: peak {_g(comp.local_peak)} GiB + {_g(headroom)} headroom "
        f"= {_g(local_required)} GiB vs {_g(local_free)} GiB free "
        f"{'OK' if local_fits else 'EXCEEDS'}"
    )
    if comp.nas_peak > 0:
        parts.append(
            f"NAS: peak {_g(comp.nas_peak)} GiB + {_g(headroom)} headroom "
            f"= {_g(nas_required)} GiB vs {_g(nas_free)} GiB free "
            f"{'OK' if nas_fits else 'EXCEEDS'}"
        )
    hint = ""
    if not fits:
        hint = (
            " | free disk: pre-prune ~/.cache "
            f"(cache_baseline={_g(comp.cache_baseline)} GiB), reduce keep_count, "
            "or route datasets to NAS with --datasets-on-nas."
        )
    return Verdict(
        comp, local_free, nas_free, fits, f"{verb}: " + " || ".join(parts) + hint
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.keep_count < 1:
        print("error: --keep-count must be >= 1", file=sys.stderr)
        return 2
    if not args.target_dir.parent.exists() and not args.target_dir.exists():
        # Walk up to find a real filesystem; if none, the path is nonsense.
        if free_bytes(args.target_dir) == 0:
            print(
                f"error: cannot resolve a filesystem for --target-dir {args.target_dir}",
                file=sys.stderr,
            )
            return 2

    verdict = evaluate(args)

    report = verdict.comp.as_report()
    report["headroom_gib"] = round(args.headroom, 2)
    report["local_free_gib"] = _g(verdict.local_free)
    report["nas_free_gib"] = _g(verdict.nas_free)
    report["fits"] = verdict.fits
    report["target_dir"] = str(args.target_dir)
    report["nas_dir"] = str(args.nas_dir) if verdict.comp.nas_peak > 0 else None

    print(json.dumps(report, indent=2))
    print(verdict.message, file=sys.stderr)
    fits = verdict.fits

    if args.emit is not None:
        # Only write inside the run's results dir, only on explicit request.
        args.emit.parent.mkdir(parents=True, exist_ok=True)
        args.emit.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote estimate snapshot -> {args.emit}", file=sys.stderr)

    return 0 if fits else 1


if __name__ == "__main__":
    raise SystemExit(main())
