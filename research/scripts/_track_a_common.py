#!/usr/bin/env python3
"""Shared helpers for the Track-A external-data SFT reranker pipeline.

This module is imported by ``baseline_ladder.py``, ``sft_reranker_train.py``, and
``eval_reranker.py`` so that the metric math, the Blackwell capability guard, the
HF-cache redirection, and the read-only mem-store access pattern are defined ONCE
and shared verbatim. Sharing the metric code is what makes the pre-registered bar
(R3 baseline) and the trained-model score (R4/eval) directly comparable -- they
literally call the same nDCG@10 / MRR / Recall@k functions on the same eval items.

NOTHING in this module touches the GPU at import time, downloads anything, or
installs anything. The capability guard and the model/dataset loaders are
functions that fail LOUDLY (with the cu126-wheel-trap hint) only when actually
called inside the approved SFT/vLLM container.

Internal requirement IDs referenced below: R3 (baseline ladder) / R4 (reranker
SFT) / R5 (leak-safe split) / A4 (CI margin).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

# --------------------------------------------------------------------------- #
# Read-only mem locations. Env-overridable so this is not pinned to one checkout;
# defaults resolve relative to the repo root (this file is at
# <repo>/research/scripts/_track_a_common.py). Set MEM_STORE / MEM_GRID_SUMMARY /
# MEMBENCH_ROOT when the .mem store lives outside the repo tree (e.g. a worktree).
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(os.environ.get("MEM_REPO_ROOT", Path(__file__).resolve().parents[2]))
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
STORE_PATH = Path(os.environ.get("MEM_STORE", REPO_ROOT / ".mem" / "store.db"))
GRID_SUMMARY = Path(
    os.environ.get("MEM_GRID_SUMMARY", REPO_ROOT / ".mem" / "grid" / "summary.json")
)
MEMBENCH_ROOT = Path(os.environ.get("MEMBENCH_ROOT", REPO_ROOT / "memory-bench"))

BASELINE_RESULTS = RESULTS_DIR / "baseline_ladder.json"
RERANKER_RESULTS = RESULTS_DIR / "reranker_eval.json"

# The Blackwell (RTX 5090) compute capability the locked cu129 stack must report.
BLACKWELL_CAPABILITY = (12, 0)

# Pre-registered eval choices (PRD R3/R4/A4).
EVAL_CHOICES = ("bright", "beir", "mem-heldout")
DEFAULT_K = 10

# The cu126 silent-wheel trap hint -- surfaced on any capability mismatch so the
# operator is pointed straight at the documented footgun, not left guessing.
CU126_TRAP_HINT = (
    "Blackwell sm_120 capability check FAILED. The usual cause is the cu126 "
    "silent-wheel trap: a bare `pip install torch` (or a transitive dep) resolved "
    "a cu126 wheel that imports fine but has NO sm_120 kernels. Reinstall torch "
    "from the cu129 index URL per research/env/requirements.lock "
    "(torch==2.11.0+cu129) and re-run, inside the mem-rl-sft image. "
    "See research/env/README.md 'Known footguns' #1."
)


# --------------------------------------------------------------------------- #
# GPU capability guard. Call FIRST in any script before importing torch-heavy
# trainers / loading a model. Real assertion, real error -- no silent fallback.
# --------------------------------------------------------------------------- #
def assert_blackwell() -> None:
    """Assert the active torch sees an sm_120 (12, 0) device, or fail loudly.

    Raises RuntimeError with the cu126-trap hint on any of: torch missing, CUDA
    unavailable, or capability != (12, 0). Never downgrades to CPU silently -- a
    run on the wrong build is a silent science-killer (PRD A6 / launch_guard.sh).
    """
    try:
        import torch
    except Exception as exc:  # noqa: BLE001 -- want the full hint regardless of cause
        raise RuntimeError(
            f"torch import failed ({type(exc).__name__}: {exc}). {CU126_TRAP_HINT}"
        ) from exc

    if not torch.cuda.is_available():
        raise RuntimeError(
            "torch.cuda.is_available() is False -- no usable GPU in this process. "
            + CU126_TRAP_HINT
        )

    cap = torch.cuda.get_device_capability()
    if tuple(cap) != BLACKWELL_CAPABILITY:
        raise RuntimeError(
            f"device capability {tuple(cap)} != required {BLACKWELL_CAPABILITY}. "
            + CU126_TRAP_HINT
        )


def redirect_hf_caches(run_dir: Path) -> dict[str, str]:
    """Point all HF/torch caches at a run-scoped dir (mirrors launch_guard.sh).

    Returns the env mapping it set, for logging into provenance. This keeps model
    + dataset downloads off the 64 GB ~/.cache and the 72 GB host root (PRD A6).
    Idempotent: existing values are NOT overwritten so launch_guard.sh's exported
    env wins when this runs underneath it.
    """
    cache_root = run_dir / "cache"
    mapping = {
        "HF_HOME": str(cache_root / "hf"),
        "TRANSFORMERS_CACHE": str(cache_root / "transformers"),
        "HF_DATASETS_CACHE": str(cache_root / "datasets"),
        "PIP_CACHE_DIR": str(cache_root / "pip"),
        "TMPDIR": str(run_dir / "tmp"),
    }
    applied: dict[str, str] = {}
    for key, val in mapping.items():
        existing = os.environ.get(key)
        if existing:
            applied[key] = existing
            continue
        Path(val).mkdir(parents=True, exist_ok=True)
        os.environ[key] = val
        applied[key] = val
    return applied


# --------------------------------------------------------------------------- #
# Eval-item schema. ONE shape consumed by baseline + trained eval so the metric
# call sites are identical.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Candidate:
    """A single ranking candidate for a query."""

    doc_id: str
    text: str


@dataclass(frozen=True)
class EvalItem:
    """One ranking problem: a query, its candidate pool, and the gold ids.

    ``relevant_ids`` is the set of doc_ids that count as correct (the qrels gold
    for BRIGHT/BEIR; the leak-safe required reads for mem-heldout). ``meta`` carries
    provenance (repo, source split) used for honest per-stratum reporting (A4).
    """

    query_id: str
    query: str
    candidates: list[Candidate]
    relevant_ids: list[str]
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Metric math (SHARED -- baseline and trained eval both call these). Binary
# relevance, identical to membench.metrics.scorers.score_retrieval's ranking
# definitions (nDCG keyed on ordered ids, ideal = all-relevant-at-top).
# --------------------------------------------------------------------------- #
def _dcg(relevances: Sequence[int]) -> float:
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances))


def ndcg_at_k(
    ranked_ids: Sequence[str], relevant_ids: set[str], k: int = DEFAULT_K
) -> float:
    top = ranked_ids[:k]
    gains = [1 if d in relevant_ids else 0 for d in top]
    ideal = [1] * min(len(relevant_ids), k)
    idcg = _dcg(ideal)
    return (_dcg(gains) / idcg) if idcg else 0.0


def mrr(ranked_ids: Sequence[str], relevant_ids: set[str]) -> float:
    for i, d in enumerate(ranked_ids):
        if d in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def recall_at_k(
    ranked_ids: Sequence[str], relevant_ids: set[str], k: int = DEFAULT_K
) -> float:
    if not relevant_ids:
        return 0.0
    top = set(ranked_ids[:k])
    return len(top & relevant_ids) / len(relevant_ids)


def per_item_metrics(
    ranked_ids: Sequence[str], relevant_ids: Sequence[str], k: int = DEFAULT_K
) -> dict[str, float]:
    """All headline metrics for one item, keyed identically everywhere."""
    rel = set(relevant_ids)
    return {
        f"ndcg@{k}": ndcg_at_k(ranked_ids, rel, k),
        "mrr": mrr(ranked_ids, rel),
        f"recall@{k}": recall_at_k(ranked_ids, rel, k),
    }


def aggregate_metrics(per_item: list[dict[str, float]]) -> dict[str, float]:
    """Mean over items. Empty -> all zeros (honest, not a crash)."""
    if not per_item:
        return {}
    keys = per_item[0].keys()
    return {key: sum(d[key] for d in per_item) / len(per_item) for key in keys}


# --------------------------------------------------------------------------- #
# A4 statistic: per-item paired delta + bootstrap 95% CI; verdict = CI_low > 0.
# Used by eval_reranker.py to compare trained vs the pre-registered baseline.
# Pure arithmetic -- deterministic given the seed.
# --------------------------------------------------------------------------- #
def bootstrap_paired_ci(
    deltas: Sequence[float],
    n_boot: int = 10000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict[str, float]:
    """Bootstrap CI of the MEAN of paired deltas.

    deltas[i] = trained_metric_i - baseline_metric_i for the SAME item i. Returns
    mean and the (alpha/2, 1-alpha/2) percentile CI of the resampled means. With
    <2 items the CI is undefined: we force ci_low=-inf so the pre-registered
    'CI lower bound > 0' rule CANNOT pass on a single item (a single-repo / single
    -item pool must never fake a win -- A4 honesty). ``undefined`` flags this.
    """
    import random

    n = len(deltas)
    if n == 0:
        return {
            "n": 0,
            "mean": 0.0,
            "ci_low": float("-inf"),
            "ci_high": float("inf"),
            "undefined": True,
        }
    mean = sum(deltas) / n
    if n < 2:
        return {
            "n": n,
            "mean": mean,
            "ci_low": float("-inf"),
            "ci_high": float("inf"),
            "undefined": True,
        }

    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(n_boot):
        sample = [deltas[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo_idx = int((alpha / 2) * n_boot)
    hi_idx = min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    return {
        "n": n,
        "mean": mean,
        "ci_low": means[lo_idx],
        "ci_high": means[hi_idx],
    }


def a4_verdict(
    deltas: Sequence[float], metric: str, **boot_kwargs: Any
) -> dict[str, Any]:
    """Pre-registered margin rule: PASS iff bootstrap CI lower bound > 0.

    The verdict is evaluated on the raw (possibly -inf) bound so an undefined CI
    (n<2) cannot pass; the bounds are then sanitized to JSON-safe values (None for
    +/-inf) so write_results' allow_nan=False guard stays happy.
    """
    ci = bootstrap_paired_ci(deltas, **boot_kwargs)
    verdict_pass = bool(ci["ci_low"] > 0.0)
    safe = dict(ci)
    for bound in ("ci_low", "ci_high"):
        if not math.isfinite(safe[bound]):
            safe[bound] = None
    return {
        "metric": metric,
        "rule": "CI lower bound > 0 (pre-registered, A4)",
        **safe,
        "verdict_pass": verdict_pass,
    }


# --------------------------------------------------------------------------- #
# Read-only mem store access (open the sidecar immutable, SELECT only).
# --------------------------------------------------------------------------- #
def open_store_ro(path: Path = STORE_PATH) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"mem store not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def held_out_work_ids() -> list[str]:
    """The graded held-out sound-oracle pool (grid/summary.json per_bundle)."""
    if not GRID_SUMMARY.exists():
        raise FileNotFoundError(f"held-out grid summary not found: {GRID_SUMMARY}")
    summary = json.loads(GRID_SUMMARY.read_text())
    return [b["work_id"] for b in summary.get("per_bundle", [])]


def write_results(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False: a NaN/inf leaking into a results file would be invalid JSON
    # and silently un-loadable downstream -- fail loudly instead.
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def add_membench_to_path() -> None:
    if str(MEMBENCH_ROOT) not in sys.path:
        sys.path.insert(0, str(MEMBENCH_ROOT))


# --------------------------------------------------------------------------- #
# Approved-download guard. External datasets/models are fetched at RUNTIME inside
# the approved container; outside it (or without the opt-in env) we refuse rather
# than silently hit the network on a 97%-full box.
# --------------------------------------------------------------------------- #
APPROVED_DOWNLOAD_ENV = "TRACK_A_ALLOW_DOWNLOAD"


def require_download_approval(what: str) -> None:
    """Gate a network fetch behind an explicit opt-in env var.

    The morning operator sets TRACK_A_ALLOW_DOWNLOAD=1 INSIDE the approved
    container after the disk/approval gate. Absent it, we raise -- never a stub,
    a real guard around the real loader call that follows.
    """
    if os.environ.get(APPROVED_DOWNLOAD_ENV) != "1":
        raise RuntimeError(
            f"Refusing to download {what!r}: set {APPROVED_DOWNLOAD_ENV}=1 only "
            "inside the approved mem-rl-sft container after the disk gate (the box "
            "is at 97% disk; an unguarded fetch can fill the root). "
            "See research/scripts/README_track_a.md approval gates."
        )


def run_scored(fn: Callable[[], dict[str, Any]], out_path: Path) -> dict[str, Any]:
    """Run a measurement, persist its JSON, echo to stdout. Shared CLI tail."""
    result = fn()
    write_results(out_path, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"\n[track-a] wrote {out_path}", file=sys.stderr)
    return result
