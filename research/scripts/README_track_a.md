# Track A — External-data SFT reasoning-reranker pipeline

The **primary track** of the fine-tuning/RL exploration (baseline ladder → reranker
SFT → eval). Distil a small reasoning-aware reranker via Unsloth QLoRA SFT on
**external public data**, then prove it beats an off-the-shelf baseline by a
**pre-registered** margin on a leak-safe held-out eval.

> Env setup: [`../env/README.md`](../env/README.md). The R*/A* labels below are
> internal requirement IDs (baseline ladder = R3, reranker SFT = R4, leak-safe
> split = R5, CI margin = A4, contended-box ops = A6).

## Anti-circularity (READ FIRST)

**SFT training data is EXTERNAL ONLY.** We never mine relevance labels from mem's
gold traces — that would train the model to reproduce the eval oracle, i.e. the
leak the whole project exists to prevent (anti-circularity guard, R4 / A3).

- `sft_reranker_train.py` enforces this in code: `assert_external_only()` allows
  only the `EXTERNAL_DATASETS` allow-list (Rank1 traces / BRIGHT train). There is
  **no code path** in the trainer that reads `.mem/store.db` or the grid summary.
- mem is used **only as a leak-safe EVAL environment** — read-only,
  `mode=ro&immutable=1` — in `baseline_ladder.py` and `eval_reranker.py`.

## Files

| File | Role |
|------|------|
| `_track_a_common.py` | Shared: Blackwell `(12,0)` guard (cu126-trap hint), HF-cache redirect, the **one** metric implementation (nDCG@10 / MRR / Recall@k), the A4 bootstrap-CI statistic, read-only mem-store access, download-approval gate. |
| `baseline_ladder.py` | **R3** — ladder rungs on `--eval {bright,beir,mem-heldout}`: `cross_encoder` (bge-reranker-v2-m3), `bi_encoder` (e5/bge), and **`rank1`** (the RELEASED `jhu-clsp/rank1-*` reasoning reranker — the "do we even need to train?" rung). Writes the **pre-registered bar** `../results/baseline_ladder.json`. |
| `sft_reranker_train.py` | **R4** — Unsloth QLoRA SFT of a small reranker on external distilled reasoning traces. Saves **LoRA adapters only**, `save_total_limit=1`, grad checkpointing, logs VRAM + disk deltas. |
| `eval_reranker.py` | Scores a trained adapter on the **same** eval/metrics, computes the **A4** paired-delta bootstrap CI vs the frozen bar, verdict = CI lower bound > 0. Writes `../results/reranker_eval.json`. |

## Run order & approval gates

Each step is marked with the approval gate it trips. **Nothing here runs
tonight** — author-only until the containers exist. Step 1 (rank1) runs in the
`mem-rl-vllm` image; steps 1b–3 run in the `mem-rl-sft` image. Wrap every GPU
step in `launch_guard.sh` (RAM/swap/GPU + NAS routing + provenance gate, A6).

```
0. BUILD the images                            [GATE: image build — network + container]
   See ../env/README.md "Morning commands". mem-rl-sft:latest for steps 2-3;
   mem-rl-vllm:latest for the rank1 check in step 1 (rank1 is vLLM-served).
   Re-run the (12,0) capability check after build (cu126-trap).

1. DECISIVE CHECK — released Rank1 reranker    [GATE: model download + GPU run; vLLM image]
   "Do we even need to train?" Run the SHIPPED rank1 model on our eval FIRST.
   Inside mem-rl-vllm, after the disk gate: export TRACK_A_ALLOW_DOWNLOAD=1
     python baseline_ladder.py --eval mem-heldout --systems rank1 --max-candidates 50
     python baseline_ladder.py --eval bright --task biology --systems rank1 --max-candidates 50
   Decision:
     - rank1 >> bge on mem-heldout  -> reasoning-reranking helps AND the released
       model already delivers it. Training Track-A only earns its keep if it BEATS
       rank1. Often: SKIP step 2, ship the released model as the reranker.
     - rank1 ~= bge                 -> reasoning-reranking doesn't transfer to our
       domain; revisit whether a reranker is the right lever at all.
   (rank1 default = jhu-clsp/rank1-7b; use --rank1-model jhu-clsp/rank1-32b-awq for
    the strongest rung that still fits 32 GB.)

1b. BASELINE LADDER  (freeze the bar)          [GATE: model + dataset download (GPU)]
   Inside the container, set the download opt-in AFTER the disk gate:
     export TRACK_A_ALLOW_DOWNLOAD=1
   python baseline_ladder.py --eval mem-heldout
   python baseline_ladder.py --eval beir  --task scifact
   python baseline_ladder.py --eval bright --task biology
   -> ../results/baseline_ladder.json   (the PRE-REGISTERED bar; freeze it)

2. SFT TRAIN  (external data only)             [GATE: model + dataset download + GPU run]
   research/scripts/launch_guard.sh \
     --results-dir ~/runs/track-a-sft \
     --lockfile   ../env/requirements.lock \
     -- python sft_reranker_train.py \
          --model Qwen/Qwen2.5-1.5B-Instruct --dataset rank1
   -> ~/runs/track-a-sft/adapter/   (LoRA adapter only) + train_summary.json

3. EVAL TRAINED ADAPTER vs the bar             [GATE: model download + GPU run]
   python eval_reranker.py --eval mem-heldout \
          --adapter-dir ~/runs/track-a-sft/adapter
   (repeat for --eval beir / bright on the SAME --eval used in step 1)
   -> ../results/reranker_eval.json
```

### Approval-gate legend

- **image build** — downloads cu129 wheels + Unsloth/bnb, compiles; forbidden
  overnight (see env README).
- **model + dataset download** — pulls a base model and an external corpus from
  HF; guarded in-code by `require_download_approval()` (opt in with
  `TRACK_A_ALLOW_DOWNLOAD=1` **inside** the container, after the disk gate, because
  the box is at 97% disk).
- **GPU run** — real training/inference on the shared RTX 5090; gate through
  `launch_guard.sh` so a busy GPU / low RAM refuses the launch.

## Pre-registered margin rule (A4)

The win is **`CI lower bound > 0`** on the per-item paired delta
(`trained − baseline`), bootstrapped (10k resamples, fixed seed). `eval_reranker.py`
computes this for nDCG@10, MRR, and Recall@k; `headline_verdict_pass` keys on
nDCG@10. A CI that straddles 0 is **not** a win. A pool with `n<2` items yields an
**undefined** CI (`ci_low=None`) that can never pass — a single-item / single-repo
pool cannot fake a win.

## Honest N (A4)

The mem held-out pool is **single-repo today**. Both eval
scripts report `n_bundles` **and** `n_distinct_repos`; a mem-heldout PASS is
**provisional** until ≥2 independent-repo held-out families exist. Treat the BEIR /
BRIGHT legs as the cross-domain corroboration in the meantime.

## Budget discipline (R6 / A6)

- LoRA-only checkpoints, `save_total_limit=1`, `optim="adamw_8bit"`, 4-bit base,
  gradient checkpointing — target ≤24 GB VRAM.
- All HF/torch caches redirected off the 72 GB host root via `redirect_hf_caches()`
  (and `launch_guard.sh`'s exported env wins when it wraps the run).
- `train_summary.json` records the nvidia-smi VRAM delta and the `df` disk delta.
