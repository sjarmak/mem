#!/usr/bin/env python3
"""PRD R3 -- baseline ladder: the pre-registered bar the trained model must beat.

Computes nDCG@10 (+ MRR + Recall@k) for OFF-THE-SHELF retrieval baselines on a
chosen eval set, and writes research/results/baseline_ladder.json. These numbers
are frozen BEFORE any training run; eval_reranker.py compares the trained adapter
against this exact file with the A4 CI-margin rule.

Baselines / ladder rungs (PRD R3):
  (a) cross-encoder rerank  -- BAAI/bge-reranker-v2-m3 scores each (query, cand)
      pair and re-orders the candidate pool.
  (b) bi-encoder dense      -- e5 / bge dense embeddings; cosine over the same pool.
  (c) rank1 reasoning rerank -- the RELEASED jhu-clsp/rank1-* model (Qwen2.5 already
      fine-tuned on jhu-clsp/rank1-training-data). This is the decisive "do we even
      need to train?" rung: if the released reasoning reranker already beats bge on
      OUR eval, Track-A training only earns its keep by beating rank1 itself. Run
      this FIRST (`--systems rank1 --eval mem-heldout`) before any GPU training.
      Generative: writes a reasoning chain then a true/false verdict -> needs vLLM
      (the vLLM image, not the SFT-only one) and is far costlier per pair than (a)/(b),
      so keep --max-candidates modest for it.

Eval sets (--eval):
  bright      -- BRIGHT reasoning-intensive retrieval (StackExchange etc).
  beir        -- a BEIR task (default scifact); standard IR qrels.
  mem-heldout -- mem's leak-safe held-out sound-oracle pool, loaded READ-ONLY from
                 the .mem store (immutable open, SELECT only).

GPU/model/dataset use is gated: assert_blackwell() runs before any model load, and
every external fetch is wrapped in require_download_approval(). AUTHOR-ONLY until
the SFT container exists -- nothing here runs tonight.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _track_a_common import (  # noqa: E402
    BASELINE_RESULTS,
    DEFAULT_K,
    EVAL_CHOICES,
    Candidate,
    EvalItem,
    add_membench_to_path,
    aggregate_metrics,
    assert_blackwell,
    held_out_work_ids,
    open_store_ro,
    per_item_metrics,
    redirect_hf_caches,
    require_download_approval,
    run_scored,
)

# Off-the-shelf defaults (PRD R3). Overridable so the operator can swap the exact
# HF id without editing code.
DEFAULT_CROSS_ENCODER = "BAAI/bge-reranker-v2-m3"
DEFAULT_BI_ENCODER = "intfloat/e5-large-v2"
DEFAULT_BEIR_TASK = "scifact"
DEFAULT_BRIGHT_TASK = "biology"

# Released Rank1 reasoning reranker (verified on HF 2026-06-16). Qwen2.5 base,
# fine-tuned on jhu-clsp/rank1-training-data. Pick by VRAM (all fit the 32 GB card
# for INFERENCE): fp16 0.5b/1.5b/3b/7b, fp16 14b is tight (~28 GB); for the 24b/32b
# use the AWQ 4-bit variants (jhu-clsp/rank1-32b-awq ~18 GB, rank1-14b-awq ~8 GB).
DEFAULT_RANK1 = "jhu-clsp/rank1-7b"

# Model-card inference template (https://huggingface.co/jhu-clsp/rank1-7b).
RANK1_PROMPT = (
    "Determine if the following passage is relevant to the query. "
    "Answer only with 'true' or 'false'.\n"
    "Query: {query}\n"
    "Passage: {document}\n"
    "<think>"
)


# --------------------------------------------------------------------------- #
# Eval-set loaders. Each returns list[EvalItem]. External loaders are guarded by
# require_download_approval(); mem-heldout is local + read-only.
# --------------------------------------------------------------------------- #
def load_bright(task: str, max_candidates: int) -> list[EvalItem]:
    """Load a BRIGHT split via HF datasets (xlangai/BRIGHT).

    BRIGHT ships, per task config, a 'documents' corpus and 'examples' with a gold
    id list. We build one EvalItem per query whose candidate pool is the corpus
    (capped at max_candidates for the cross-encoder pass).
    """
    require_download_approval(f"BRIGHT task {task!r} (xlangai/BRIGHT)")
    from datasets import load_dataset

    corpus = load_dataset("xlangai/BRIGHT", "documents", split=task)
    examples = load_dataset("xlangai/BRIGHT", "examples", split=task)

    cand_pool = [
        Candidate(doc_id=str(r["id"]), text=str(r["content"]))
        for r in corpus.select(range(min(len(corpus), max_candidates)))
    ]
    items: list[EvalItem] = []
    for ex in examples:
        gold = [str(g) for g in ex.get("gold_ids", []) or []]
        items.append(
            EvalItem(
                query_id=str(ex["id"]),
                query=str(ex["query"]),
                candidates=cand_pool,
                relevant_ids=gold,
                meta={"eval": "bright", "task": task},
            )
        )
    return items


def load_beir(task: str, max_candidates: int) -> list[EvalItem]:
    """Load a BEIR task via HF (BeIR/<task> + qrels).

    BEIR is distributed as corpus / queries / qrels. We pair each query with its
    judged-relevant docs and a corpus-derived candidate pool (capped).
    """
    require_download_approval(f"BEIR task {task!r} (BeIR/{task})")
    from datasets import load_dataset

    corpus = load_dataset(f"BeIR/{task}", "corpus", split="corpus")
    queries = load_dataset(f"BeIR/{task}", "queries", split="queries")
    qrels = load_dataset(f"BeIR/{task}-qrels", split="test")

    rel_by_query: dict[str, list[str]] = {}
    for r in qrels:
        if int(r["score"]) > 0:
            rel_by_query.setdefault(str(r["query-id"]), []).append(str(r["corpus-id"]))

    cand_pool = [
        Candidate(
            doc_id=str(r["_id"]),
            text=f"{r.get('title', '')} {r.get('text', '')}".strip(),
        )
        for r in corpus.select(range(min(len(corpus), max_candidates)))
    ]
    qtext = {str(q["_id"]): str(q["text"]) for q in queries}

    items: list[EvalItem] = []
    for qid, gold in rel_by_query.items():
        if qid not in qtext:
            continue
        items.append(
            EvalItem(
                query_id=qid,
                query=qtext[qid],
                candidates=cand_pool,
                relevant_ids=gold,
                meta={"eval": "beir", "task": task},
            )
        )
    return items


def load_mem_heldout(max_candidates: int) -> list[EvalItem]:
    """Build leak-safe eval items from mem's held-out sound-oracle pool.

    READ-ONLY: opens the store with mode=ro&immutable=1. For each held-out work_id
    the query is the work's title/failure context; the gold relevant id is the
    work's own failureSignature-bearing trace_errors row(s). The candidate pool is
    the union of trace_error rows across the held-out pool (so the eval is a real
    'find the right error record' ranking). This uses mem strictly as a leak-safe
    EVAL environment -- it does NOT mine training labels (anti-circularity guard).
    """
    add_membench_to_path()
    work_ids = held_out_work_ids()
    conn = open_store_ro()
    try:
        # Candidate pool: trace_error rows across the held-out pool (id + text).
        candidates: list[Candidate] = []
        gold_by_work: dict[str, list[str]] = {}
        query_by_work: dict[str, str] = {}
        repo_by_work: dict[str, str] = {}

        ph = ",".join("?" * len(work_ids))
        for row in conn.execute(
            f"SELECT work_id, title, repo FROM work_records WHERE work_id IN ({ph})",
            work_ids,
        ).fetchall():
            query_by_work[row["work_id"]] = row["title"] or row["work_id"]
            repo_by_work[row["work_id"]] = row["repo"] or "unknown"

        for row in conn.execute(
            "SELECT id, work_id, error_class, signature, file, message "
            f"FROM trace_errors WHERE work_id IN ({ph})",
            work_ids,
        ).fetchall():
            doc_id = f"err-{row['id']}"
            text = " ".join(
                str(x)
                for x in (
                    row["error_class"],
                    row["signature"],
                    row["file"],
                    row["message"],
                )
                if x
            )
            candidates.append(Candidate(doc_id=doc_id, text=text))
            gold_by_work.setdefault(row["work_id"], []).append(doc_id)
    finally:
        conn.close()

    cand_pool = candidates[:max_candidates] if max_candidates else candidates
    pool_ids = {c.doc_id for c in cand_pool}

    items: list[EvalItem] = []
    for wid in work_ids:
        gold = [g for g in gold_by_work.get(wid, []) if g in pool_ids]
        if not gold:
            # No gold candidate survives in the pool -> not a scorable item.
            continue
        items.append(
            EvalItem(
                query_id=wid,
                query=query_by_work.get(wid, wid),
                candidates=cand_pool,
                relevant_ids=gold,
                meta={"eval": "mem-heldout", "repo": repo_by_work.get(wid, "unknown")},
            )
        )
    return items


def load_eval_set(
    eval_name: str, task: str | None, max_candidates: int
) -> list[EvalItem]:
    if eval_name == "bright":
        return load_bright(task or DEFAULT_BRIGHT_TASK, max_candidates)
    if eval_name == "beir":
        return load_beir(task or DEFAULT_BEIR_TASK, max_candidates)
    if eval_name == "mem-heldout":
        return load_mem_heldout(max_candidates)
    raise ValueError(f"unknown --eval {eval_name!r}; choose from {EVAL_CHOICES}")


# --------------------------------------------------------------------------- #
# Rankers. Both load a model (GPU) -> guarded by assert_blackwell + download
# approval. Each returns, per item, the ranked candidate doc_ids.
# --------------------------------------------------------------------------- #
def rank_cross_encoder(items: list[EvalItem], model_id: str) -> dict[str, list[str]]:
    """bge-reranker-v2-m3 cross-encoder: score every (query, candidate) pair."""
    require_download_approval(f"cross-encoder {model_id!r}")
    from sentence_transformers import CrossEncoder

    ce = CrossEncoder(model_id, max_length=512)
    ranked: dict[str, list[str]] = {}
    for item in items:
        pairs = [(item.query, c.text) for c in item.candidates]
        scores = ce.predict(pairs, convert_to_numpy=True)
        order = sorted(range(len(item.candidates)), key=lambda i: -float(scores[i]))
        ranked[item.query_id] = [item.candidates[i].doc_id for i in order]
    return ranked


def rank_bi_encoder(items: list[EvalItem], model_id: str) -> dict[str, list[str]]:
    """e5/bge bi-encoder: cosine between query and candidate embeddings.

    Candidate embeddings are computed once (the pool is shared across queries),
    then each query is scored against the cached matrix.
    """
    require_download_approval(f"bi-encoder {model_id!r}")
    import numpy as np
    from sentence_transformers import SentenceTransformer

    is_e5 = "e5" in model_id.lower()
    enc = SentenceTransformer(model_id)

    # Shared pool -> embed candidate docs once (key off the first item's pool).
    pool = items[0].candidates if items else []
    doc_prefix = "passage: " if is_e5 else ""
    doc_emb = enc.encode(
        [doc_prefix + c.text for c in pool],
        convert_to_numpy=True,
        normalize_embeddings=True,
        batch_size=64,
    )
    doc_ids = [c.doc_id for c in pool]

    q_prefix = "query: " if is_e5 else ""
    ranked: dict[str, list[str]] = {}
    for item in items:
        q_emb = enc.encode(
            q_prefix + item.query, convert_to_numpy=True, normalize_embeddings=True
        )
        sims = doc_emb @ q_emb
        order = np.argsort(-sims)
        ranked[item.query_id] = [doc_ids[i] for i in order]
    return ranked


def rank_rank1(
    items: list[EvalItem], model_id: str, max_gen_tokens: int = 8192
) -> dict[str, list[str]]:
    """Released Rank1 reasoning reranker -- the decisive 'do we need to train?' rung.

    Faithful to the jhu-clsp/rank1 model card, run as TWO passes so the relevance
    probability is read at an unambiguous position (the token right after the
    reasoning closes), independent of stop-string bookkeeping:

      pass 1: reasoning = generate(prompt + '<think>' ... , stop='</think>')
      pass 2: one constrained step on (prompt + reasoning + '</think>'); relevance =
              softmax over the ' true' / ' false' next-token logprobs
              = exp(lp_true) / (exp(lp_true) + exp(lp_false))         [model-card formula]

    vLLM is the rank1-recommended runtime, so this needs the vLLM image. It generates
    a full reasoning chain per (query, candidate) pair -- O(items * pool) long
    generations -- so keep the candidate pool small (mem-heldout is cheap; cap BRIGHT).
    """
    require_download_approval(f"rank1 reasoning reranker {model_id!r} (vLLM)")
    import math

    from transformers import AutoTokenizer
    from vllm import LLM, SamplingParams

    tok = AutoTokenizer.from_pretrained(model_id)

    def _single_token_id(s: str) -> int:
        ids = tok(s, add_special_tokens=False).input_ids
        if len(ids) != 1:
            raise RuntimeError(
                f"rank1 scoring expects {s!r} to be a single token for this tokenizer, "
                f"got {ids}; adjust the true/false extraction for base model {model_id!r}."
            )
        return ids[0]

    true_id, false_id = _single_token_id(" true"), _single_token_id(" false")

    llm = LLM(model=model_id, dtype="float16", max_model_len=16000)

    prompts: list[str] = []
    index: list[tuple[str, str]] = []
    for item in items:
        for cand in item.candidates:
            prompts.append(RANK1_PROMPT.format(query=item.query, document=cand.text))
            index.append((item.query_id, cand.doc_id))

    # pass 1 -- reasoning chains (deterministic).
    think = llm.generate(
        prompts,
        SamplingParams(temperature=0.0, max_tokens=max_gen_tokens, stop=["</think>"]),
    )
    closed = [p + o.outputs[0].text + "</think>" for p, o in zip(prompts, think)]

    # pass 2 -- one step; read P(true)/P(false) from the next-token logprobs.
    decide = llm.generate(
        closed, SamplingParams(temperature=0.0, max_tokens=1, logprobs=20)
    )

    scores: dict[str, dict[str, float]] = {}
    for (qid, doc_id), out in zip(index, decide):
        lp = out.outputs[0].logprobs[0] if out.outputs[0].logprobs else {}
        t = math.exp(lp[true_id].logprob) if true_id in lp else 0.0
        f = math.exp(lp[false_id].logprob) if false_id in lp else 0.0
        scores.setdefault(qid, {})[doc_id] = (t / (t + f)) if (t + f) > 0 else 0.0

    ranked: dict[str, list[str]] = {}
    for item in items:
        s = scores.get(item.query_id, {})
        order = sorted(item.candidates, key=lambda c: -s.get(c.doc_id, 0.0))
        ranked[item.query_id] = [c.doc_id for c in order]
    return ranked


# --------------------------------------------------------------------------- #
# Scoring: apply shared metric math to a ranking.
# --------------------------------------------------------------------------- #
def score_system(
    items: list[EvalItem], ranked: dict[str, list[str]], k: int
) -> dict[str, Any]:
    per_item = []
    per_item_detail = []
    for item in items:
        ids = ranked.get(item.query_id, [])
        m = per_item_metrics(ids, item.relevant_ids, k)
        per_item.append(m)
        per_item_detail.append({"query_id": item.query_id, **m, **item.meta})
    return {
        "aggregate": aggregate_metrics(per_item),
        "n_items": len(items),
        "per_item": per_item_detail,
    }


def distinct_repos(items: list[EvalItem]) -> list[str]:
    return sorted({str(i.meta.get("repo")) for i in items if i.meta.get("repo")})


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--eval", choices=EVAL_CHOICES, default="mem-heldout", help="eval set"
    )
    ap.add_argument(
        "--task", default=None, help="sub-task for bright/beir (e.g. scifact, biology)"
    )
    ap.add_argument(
        "--cross-encoder", default=DEFAULT_CROSS_ENCODER, help="cross-encoder HF id"
    )
    ap.add_argument("--bi-encoder", default=DEFAULT_BI_ENCODER, help="bi-encoder HF id")
    ap.add_argument(
        "--rank1-model", default=DEFAULT_RANK1, help="released rank1 reranker HF id"
    )
    ap.add_argument("--k", type=int, default=DEFAULT_K, help="cutoff k for nDCG/Recall")
    ap.add_argument(
        "--max-candidates", type=int, default=200, help="candidate pool cap per query"
    )
    ap.add_argument(
        "--run-dir",
        default=str(Path.home() / "runs" / "track-a-baseline"),
        help="run-scoped dir for HF caches (kept off host root)",
    )
    ap.add_argument("--out", default=str(BASELINE_RESULTS), help="results JSON path")
    ap.add_argument(
        "--systems",
        default="cross_encoder,bi_encoder",
        help="comma list of ladder rungs to run: cross_encoder, bi_encoder, rank1. "
        "Recommended FIRST run: '--systems rank1 --eval mem-heldout' (released "
        "reasoning reranker -> the 'do we need to train?' check).",
    )
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    redirect_hf_caches(run_dir)
    assert_blackwell()  # fail loudly before any model load (cu126-trap hint)

    items = load_eval_set(args.eval, args.task, args.max_candidates)
    if not items:
        raise RuntimeError(
            f"eval set {args.eval!r} produced 0 scorable items -- refusing to "
            "emit an empty pre-registered bar."
        )

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    results: dict[str, Any] = {}
    if "cross_encoder" in systems:
        ranked = rank_cross_encoder(items, args.cross_encoder)
        results[f"cross_encoder:{args.cross_encoder}"] = score_system(
            items, ranked, args.k
        )
    if "bi_encoder" in systems:
        ranked = rank_bi_encoder(items, args.bi_encoder)
        results[f"bi_encoder:{args.bi_encoder}"] = score_system(items, ranked, args.k)
    if "rank1" in systems:
        ranked = rank_rank1(items, args.rank1_model)
        results[f"rank1:{args.rank1_model}"] = score_system(items, ranked, args.k)

    payload: dict[str, Any] = {
        "track": "A",
        "requirement": "R3 baseline ladder (pre-registered bar)",
        "eval": args.eval,
        "task": args.task,
        "k": args.k,
        "n_items": len(items),
        "n_distinct_repos": len(distinct_repos(items)),
        "distinct_repos": distinct_repos(items),
        "systems": results,
        "note": (
            "These metrics are the pre-registered bar. eval_reranker.py compares a "
            "trained adapter against THIS file using the A4 rule (CI lower bound > 0). "
            "For mem-heldout n_distinct_repos is reported honestly (A4: pool is "
            "single-repo today -> n~1 in repos)."
        ),
    }
    run_scored(lambda: payload, Path(args.out))
    return payload


if __name__ == "__main__":
    main()
