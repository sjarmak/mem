#!/usr/bin/env python3
"""Score a trained reranker adapter and compare against the pre-registered bar.

Loads base + LoRA adapter (from sft_reranker_train.py), reranks the SAME candidate
sets on the SAME eval sets as baseline_ladder.py, computes the SAME metrics
(shared via _track_a_common), and applies the A4 statistic:

    per-item paired delta (trained - baseline) -> bootstrap 95% CI ->
    verdict PASS iff CI lower bound > 0   (PRD A4, pre-registered margin rule).

For --eval mem-heldout it reports n_bundles AND n_distinct_repos HONESTLY (A4: the
held-out pool is single-repo today, so a 'win' there is provisional until >=2
independent-repo families exist). Writes research/results/reranker_eval.json.

The comparison reads research/results/baseline_ladder.json -- the bar must have
been frozen first. Per-item alignment is by query_id, so the paired delta is over
the identical items the baseline scored.

AUTHOR-ONLY tonight; assert_blackwell() + require_download_approval() gate all
GPU/network use.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_ladder as bl  # noqa: E402 -- reuse loaders + scoring verbatim
from _track_a_common import (  # noqa: E402
    BASELINE_RESULTS,
    DEFAULT_K,
    EVAL_CHOICES,
    RERANKER_RESULTS,
    EvalItem,
    a4_verdict,
    assert_blackwell,
    per_item_metrics,
    redirect_hf_caches,
    require_download_approval,
    run_scored,
)

DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
HEADLINE_METRIC = f"ndcg@{DEFAULT_K}"


def rank_trained(
    items: list[EvalItem], base_model: str, adapter_dir: str, max_seq_len: int
) -> dict[str, list[str]]:
    """Rerank each candidate pool with the trained QLoRA reranker.

    Pointwise: for each (query, candidate) the model emits a reasoning rationale +
    'Relevant: yes/no'; we score by the probability mass on the 'yes' token at the
    verdict position (a standard pointwise-LLM reranker readout), then order
    descending. Uses the same chat template the SFT used.
    """
    require_download_approval(f"base model {base_model!r} + adapter {adapter_dir!r}")
    import torch
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=max_seq_len,
        load_in_4bit=True,
        dtype=None,
    )
    # Attach the trained adapter, then switch to fast inference.
    model.load_adapter(adapter_dir)
    FastLanguageModel.for_inference(model)

    yes_id = tokenizer.encode("yes", add_special_tokens=False)[0]
    no_id = tokenizer.encode("no", add_special_tokens=False)[0]

    from sft_reranker_train import RERANK_SYSTEM_PROMPT

    ranked: dict[str, list[str]] = {}
    for item in items:
        scores: list[float] = []
        for cand in item.candidates:
            messages = [
                {"role": "system", "content": RERANK_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Query: {item.query}\n\nDocument: {cand.text}",
                },
                {"role": "assistant", "content": "Relevant:"},
            ]
            input_ids = tokenizer.apply_chat_template(
                messages, return_tensors="pt", continue_final_message=True
            ).to(model.device)
            with torch.no_grad():
                logits = model(input_ids).logits[0, -1]
            probs = torch.softmax(logits[[yes_id, no_id]], dim=-1)
            scores.append(float(probs[0]))  # P(yes)
        order = sorted(range(len(item.candidates)), key=lambda i: -scores[i])
        ranked[item.query_id] = [item.candidates[i].doc_id for i in order]
    return ranked


def load_baseline(
    path: Path, system_pref: str | None
) -> tuple[str, dict[str, dict[str, float]]]:
    """Load the pre-registered baseline and pick the system to compare against.

    Returns (system_name, {query_id: per_item_metrics}). Defaults to the
    strongest baseline by headline metric so the bar is the hardest one (a trained
    win against the weakest baseline would be a soft claim).
    """
    if not path.exists():
        raise FileNotFoundError(
            f"baseline {path} not found -- run baseline_ladder.py first (R3 bar must "
            "be frozen before comparison)."
        )
    data = json.loads(path.read_text())
    systems = data["systems"]
    if system_pref and system_pref in systems:
        chosen = system_pref
    else:
        chosen = max(
            systems, key=lambda s: systems[s]["aggregate"].get(HEADLINE_METRIC, 0.0)
        )
    by_query = {row["query_id"]: row for row in systems[chosen]["per_item"]}
    return chosen, by_query


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--eval", choices=EVAL_CHOICES, default="mem-heldout")
    ap.add_argument("--task", default=None, help="sub-task for bright/beir")
    ap.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    ap.add_argument("--adapter-dir", required=True, help="trained LoRA adapter dir")
    ap.add_argument("--max-seq-len", type=int, default=2048)
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--max-candidates", type=int, default=200)
    ap.add_argument(
        "--baseline", default=str(BASELINE_RESULTS), help="frozen R3 bar JSON"
    )
    ap.add_argument(
        "--baseline-system",
        default=None,
        help="which baseline system to compare vs (default: strongest)",
    )
    ap.add_argument("--run-dir", default=str(Path.home() / "runs" / "track-a-eval"))
    ap.add_argument("--out", default=str(RERANKER_RESULTS))
    ap.add_argument("--n-boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    redirect_hf_caches(Path(args.run_dir))
    assert_blackwell()

    # SAME items the baseline scored (same loader, same caps).
    items = bl.load_eval_set(args.eval, args.task, args.max_candidates)
    if not items:
        raise RuntimeError(f"eval set {args.eval!r} produced 0 scorable items.")

    baseline_system, base_by_query = load_baseline(
        Path(args.baseline), args.baseline_system
    )

    ranked = rank_trained(items, args.base_model, args.adapter_dir, args.max_seq_len)

    # Per-item trained metrics + paired deltas, aligned by query_id.
    trained_detail: list[dict[str, Any]] = []
    deltas_by_metric: dict[str, list[float]] = {}
    metric_keys = [f"ndcg@{args.k}", "mrr", f"recall@{args.k}"]
    for item in items:
        ids = ranked.get(item.query_id, [])
        tm = per_item_metrics(ids, item.relevant_ids, args.k)
        trained_detail.append({"query_id": item.query_id, **tm, **item.meta})
        base_row = base_by_query.get(item.query_id)
        if base_row is None:
            continue  # item not in the frozen bar -> not comparable
        for mk in metric_keys:
            deltas_by_metric.setdefault(mk, []).append(
                tm[mk] - float(base_row.get(mk, 0.0))
            )

    trained_agg = {
        mk: (
            sum(d[mk] for d in trained_detail) / len(trained_detail)
            if trained_detail
            else 0.0
        )
        for mk in metric_keys
    }

    verdicts = {
        mk: a4_verdict(
            deltas_by_metric.get(mk, []), mk, n_boot=args.n_boot, seed=args.seed
        )
        for mk in metric_keys
    }

    distinct_repos = bl.distinct_repos(items)
    payload: dict[str, Any] = {
        "track": "A",
        "requirement": "R4 trained-reranker eval vs R3 bar (A4 CI margin)",
        "eval": args.eval,
        "task": args.task,
        "k": args.k,
        "base_model": args.base_model,
        "adapter_dir": args.adapter_dir,
        "baseline_file": args.baseline,
        "baseline_system_compared": baseline_system,
        "n_items": len(items),
        "n_bundles": len(items) if args.eval == "mem-heldout" else None,
        "n_distinct_repos": len(distinct_repos),
        "distinct_repos": distinct_repos,
        "trained_aggregate": trained_agg,
        "a4_verdicts": verdicts,
        "headline_verdict_pass": bool(verdicts[HEADLINE_METRIC]["verdict_pass"]),
        "trained_per_item": trained_detail,
        "honesty_note": (
            "A4: for mem-heldout the pool is single-repo today (n~1 in repos); a "
            "PASS here is provisional until >=2 independent-repo held-out families "
            "exist. n_bundles and n_distinct_repos are reported above for that reason."
        ),
    }
    run_scored(lambda: payload, Path(args.out))
    return payload


if __name__ == "__main__":
    main()
