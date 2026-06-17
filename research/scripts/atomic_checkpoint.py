#!/usr/bin/env python3
"""Atomic, resumable checkpoint helper (PRD amendment A6 / R6).

The premortem (#2, Theme D) names the precise failure: a multi-hour run on a
near-full, swap-exhausted, shared box dies mid-checkpoint and corrupts the only
checkpoint it had, because the trainer deleted the old one *before* the new one
was fully written. ``save_total_limit=1`` alone is exactly this trap — it keeps
one checkpoint and that one can be the half-written one.

This helper enforces the safe ordering:

    1. write the new checkpoint into a sibling ``*.incomplete`` tempdir
    2. fsync the files and the directory entry (durability across power/OOM kills)
    3. atomically ``os.replace`` the tempdir into its final name
    4. ONLY THEN prune older checkpoints, always keeping ``keep_count`` (default 2:
       last-good + current). The current write is never the one pruned.

Because every committed checkpoint is whole, "resume" is just "load the newest
committed checkpoint". A run interrupted during step 1/2 leaves a ``*.incomplete``
dir that is ignored on resume and garbage-collected on the next save.

The actual *contents* of a checkpoint are the caller's responsibility — this helper
is checkpoint-format-agnostic. The caller passes a ``write_fn(staging_dir)`` that
must persist EVERYTHING needed to resume: model/adapter weights, optimizer state,
LR-scheduler state, and RNG state (python/numpy/torch/cuda). A checkpoint missing
optimizer/scheduler/RNG state is not resumable and defeats the purpose.

This module performs no GPU work and imports no ML libraries; it is pure
filesystem orchestration and is safe to import/run anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

INCOMPLETE_SUFFIX = ".incomplete"
MANIFEST_NAME = "checkpoint_manifest.json"
STEP_PREFIX = "step-"


def _fsync_dir(path: Path) -> None:
    """fsync a directory so its renamed/created entries are durable."""
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    """fsync every regular file under root, then the directories bottom-up."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            fp = Path(dirpath) / name
            try:
                fd = os.open(str(fp), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                # A file that cannot be opened for fsync is a write that did not
                # land; surface it rather than silently shipping a partial ckpt.
                raise
    for dirpath, _dirs, _files in os.walk(root, topdown=False):
        _fsync_dir(Path(dirpath))


def _checkpoint_dirs(base_dir: Path) -> list[Path]:
    """Committed checkpoint dirs (``step-<n>``), newest step last. Ignores tempdirs."""
    if not base_dir.exists():
        return []
    out: list[tuple[int, Path]] = []
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name.endswith(INCOMPLETE_SUFFIX):
            continue
        if not child.name.startswith(STEP_PREFIX):
            continue
        try:
            step = int(child.name[len(STEP_PREFIX) :])
        except ValueError:
            continue
        out.append((step, child))
    out.sort(key=lambda t: t[0])
    return [p for _s, p in out]


def gc_incomplete(base_dir: Path) -> list[Path]:
    """Remove leftover ``*.incomplete`` tempdirs from interrupted saves. Returns removed paths."""
    removed: list[Path] = []
    if not base_dir.exists():
        return removed
    for child in base_dir.iterdir():
        if child.is_dir() and child.name.endswith(INCOMPLETE_SUFFIX):
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child)
    return removed


def save_checkpoint(
    base_dir: Path,
    step: int,
    write_fn: Callable[[Path], None],
    keep_count: int = 2,
    extra_manifest: dict | None = None,
) -> Path:
    """Atomically write checkpoint for ``step`` and prune to ``keep_count`` newest.

    Args:
        base_dir: directory holding all ``step-<n>`` checkpoints for this run.
        step: global training step (used for ordering and the dir name).
        write_fn: callback that persists ALL resume state into the staging dir it
            is handed (weights/adapter + optimizer + scheduler + RNG). It must
            raise on any failure; a swallowed error here ships a corrupt ckpt.
        keep_count: how many newest committed checkpoints to retain (>=1; default
            2 = last-good + current). The just-written checkpoint is never pruned.
        extra_manifest: optional caller metadata merged into the manifest.

    Returns:
        Path to the committed checkpoint directory.
    """
    if keep_count < 1:
        raise ValueError("keep_count must be >= 1 (default 2 = last-good + current)")

    base_dir.mkdir(parents=True, exist_ok=True)

    # Sweep any half-written tempdirs from a previous crash before writing.
    gc_incomplete(base_dir)

    final_dir = base_dir / f"{STEP_PREFIX}{step}"
    staging_dir = base_dir / f"{STEP_PREFIX}{step}{INCOMPLETE_SUFFIX}"

    # If a stale staging dir for this exact step survived, clear it first.
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True)

    # 1. write everything into staging
    write_fn(staging_dir)

    # manifest is written last inside staging so its presence => caller finished
    manifest = {
        "step": step,
        "written_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "keep_count": keep_count,
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    (staging_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")

    # 2. fsync file contents + dir entries so the data is durable pre-rename
    _fsync_tree(staging_dir)

    # If final_dir already exists (re-save of same step), remove it first so the
    # rename target is clean. os.replace onto an existing non-empty dir fails.
    if final_dir.exists():
        shutil.rmtree(final_dir)

    # 3. atomic rename, then fsync the parent so the rename itself is durable
    os.replace(staging_dir, final_dir)
    _fsync_dir(base_dir)

    # 4. ONLY NOW prune older checkpoints, keeping the newest keep_count
    committed = _checkpoint_dirs(base_dir)
    if len(committed) > keep_count:
        for old in committed[:-keep_count]:
            shutil.rmtree(old, ignore_errors=True)
        _fsync_dir(base_dir)

    return final_dir


def latest_checkpoint(base_dir: Path) -> Path | None:
    """Newest committed checkpoint dir, or None. The resume entry point."""
    dirs = _checkpoint_dirs(base_dir)
    return dirs[-1] if dirs else None


def read_manifest(checkpoint_dir: Path) -> dict:
    """Load a checkpoint's manifest. Raises if absent (an incomplete checkpoint)."""
    mf = checkpoint_dir / MANIFEST_NAME
    if not mf.exists():
        raise FileNotFoundError(
            f"no {MANIFEST_NAME} in {checkpoint_dir}: checkpoint is incomplete/corrupt"
        )
    return json.loads(mf.read_text())


def _demo(argv: list[str] | None = None) -> int:
    """Tiny self-test: write 4 steps with keep_count=2, prove only the newest 2 survive.

    Filesystem-only; no GPU. Useful as a smoke check that ordering/pruning hold.
    """
    import argparse
    import tempfile

    p = argparse.ArgumentParser(
        description="atomic_checkpoint self-test (filesystem only)"
    )
    p.add_argument(
        "--dir", type=Path, default=None, help="base dir (default: a tempdir)"
    )
    args = p.parse_args(argv)

    base = args.dir or Path(tempfile.mkdtemp(prefix="atomic_ckpt_demo_"))

    def fake_write(staging: Path) -> None:
        # Stand-ins for the real resume payload.
        (staging / "adapter.bin").write_bytes(b"weights")
        (staging / "optimizer.pt").write_bytes(b"optim")
        (staging / "scheduler.pt").write_bytes(b"sched")
        (staging / "rng_state.pt").write_bytes(b"rng")

    for step in (10, 20, 30, 40):
        save_checkpoint(base, step, fake_write, keep_count=2)

    survivors = [d.name for d in _checkpoint_dirs(base)]
    print(json.dumps({"base_dir": str(base), "survivors": survivors}, indent=2))
    assert survivors == ["step-30", "step-40"], survivors
    latest = latest_checkpoint(base)
    assert latest is not None and latest.name == "step-40"
    print("OK: atomic ordering + prune-to-keep_count verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
