"""Driver for the lexical-miss recall experiment (mem-lbuvd).

Materializes both task classes, runs all three generators over each, and writes
`analysis.json`. Every threshold it applies comes from
`fixtures/lexical_recall/preregistration.json`, which was locked before this
script produced a number.

Run from `memory-bench/`:

    BEADS_BIN=~/.local/bin/bd-memory-ordering-5877 \
    uv run python results/lexical_recall/run.py \
      --workspace-root ../.mem/lexical-recall-workspaces \
      --out results/lexical_recall/analysis.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from membench.beads_ordering.models import FrozenCorpus, MemoryFixture
from membench.beads_ordering.runner import seed_beads_workspace
from membench.lexical_recall import corpus as miss_corpus
from membench.lexical_recall.generators import (
    CandidateGenerator,
    EmbeddingGenerator,
    Fts5Generator,
    LiteralGenerator,
    OllamaEmbedder,
)
from membench.lexical_recall.models import Generator, TaskRecall
from membench.lexical_recall.runner import (
    control_specs,
    gate_verdicts,
    miss_specs,
    recall_summary,
    run_class,
)
from membench.memory_systems.local_stack import LocalModelStack

EMBEDDING_REPEATS = 3


def _binary_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ollama_provenance(base_url: str, model: str) -> dict[str, str]:
    """Pin the served model. A recall number from a dense arm means nothing without
    the exact weights that produced it."""

    completed = subprocess.run(
        ["curl", "-s", "--max-time", "10", f"{base_url}/api/tags"],
        text=True,
        capture_output=True,
        check=False,
    )
    digest = "unknown"
    if completed.returncode == 0:
        try:
            for entry in json.loads(completed.stdout).get("models", []):
                if str(entry.get("name", "")).split(":")[0] == model.split(":")[0]:
                    digest = str(entry.get("digest", "unknown"))
                    break
        except json.JSONDecodeError:
            digest = "unknown"
    return {"model": model, "digest": digest, "base_url_scheme": "local"}


def _build_generators(
    *,
    memories: Sequence[MemoryFixture],
    corpus_size: int,
    workspace: Path,
    beads_bin: str,
    embedder: OllamaEmbedder,
) -> dict[Generator, CandidateGenerator]:
    # The ordering corpus is nested by prefix, so a task at corpus_size 50 must be
    # answered against the first 50 Memories and nothing else.
    prefix = list(memories)[:corpus_size]
    return {
        Generator.LITERAL: LiteralGenerator(beads_bin=beads_bin, workspace=workspace),
        Generator.FTS: Fts5Generator(memories=prefix, corpus_size=corpus_size),
        Generator.EMBEDDING: EmbeddingGenerator(
            memories=prefix,
            corpus_size=corpus_size,
            embedder=embedder,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--ordering-fixture", default="fixtures/beads_ordering/corpus.json")
    parser.add_argument("--beads-bin", default=os.environ.get("BEADS_BIN", "bd"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    started = time.time()
    root = Path(args.workspace_root)
    stack = LocalModelStack.from_env()
    embedder = OllamaEmbedder(stack.ollama_base_url, stack.ollama_embedding_model)

    miss = miss_corpus.build_frozen_corpus()
    miss_size = len(miss.memories)
    miss_workspace = root / f"lexmiss-{miss_size}"
    seed_beads_workspace(
        corpus=miss_corpus.as_seedable_corpus(miss),
        corpus_size=miss_size,
        beads_bin=args.beads_bin,
        workspace=miss_workspace,
    )
    miss_generators = _build_generators(
        memories=miss.memories,
        corpus_size=miss_size,
        workspace=miss_workspace,
        beads_bin=args.beads_bin,
        embedder=embedder,
    )

    ordering = FrozenCorpus.model_validate_json(
        Path(args.ordering_fixture).read_text(encoding="utf-8")
    )
    control = control_specs(ordering.tasks)
    sizes = sorted({spec.corpus_size for spec in control})
    control_generators: dict[int, dict[Generator, CandidateGenerator]] = {}
    for size in sizes:
        workspace = root / f"ordering-{size}"
        seed_beads_workspace(
            corpus=ordering,
            corpus_size=size,
            beads_bin=args.beads_bin,
            workspace=workspace,
        )
        control_generators[size] = _build_generators(
            memories=ordering.memories,
            corpus_size=size,
            workspace=workspace,
            beads_bin=args.beads_bin,
            embedder=embedder,
        )

    rows: list[TaskRecall] = list(run_class(miss_specs(miss), lambda _size: miss_generators))
    rows.extend(run_class(control, lambda size: control_generators[size]))

    # The preregistration asks for 3 repeats of the embedding arm to detect
    # nondeterminism in the served model. Document vectors are cached, so a repeat
    # re-embeds only the query; that is the part that could drift.
    miss_spec_list = miss_specs(miss)
    repeat_rankings: list[dict[str, tuple[str, ...]]] = []
    embedding_arm = miss_generators[Generator.EMBEDDING]
    for _ in range(EMBEDDING_REPEATS):
        repeat_rankings.append(
            {spec.task_id: embedding_arm.rank(spec.query)[:20] for spec in miss_spec_list}
        )
    repeats_agree = all(ranking == repeat_rankings[0] for ranking in repeat_rankings)

    payload = {
        "bead": "mem-lbuvd",
        "preregistration_sha256": hashlib.sha256(
            Path("fixtures/lexical_recall/preregistration.json").read_bytes()
        ).hexdigest(),
        "corpus": {
            "lexical_miss": {
                "memories": miss_size,
                "tasks": len(miss.tasks),
                "seed": miss.seed,
                # The corpus is generated, not stored, so the digest is what pins
                # it. It is taken over the same stored-value strings Beads imports.
                "digest": miss_corpus.corpus_digest(miss),
            },
            "lexical_hit_control": {
                "memories": len(ordering.memories),
                "tasks": len(control),
                "corpus_sizes": sizes,
            },
        },
        "provenance": {
            "beads_bin_sha256": _binary_sha256(args.beads_bin),
            "embedding": _ollama_provenance(stack.ollama_base_url, stack.ollama_embedding_model),
            "fts": {"engine": "sqlite fts5", "tokenizer": "porter unicode61", "rank": "bm25"},
        },
        "recall": recall_summary(rows),
        "gates": gate_verdicts(rows),
        "embedding_repeats": {
            "n": EMBEDDING_REPEATS,
            "top_20_rankings_identical": repeats_agree,
        },
        "elapsed_seconds": round(time.time() - started, 1),
    }
    Path(args.out).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["gates"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
