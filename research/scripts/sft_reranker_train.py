#!/usr/bin/env python3
"""PRD R4 -- distilled reasoning reranker, Unsloth QLoRA SFT on EXTERNAL data.

Fine-tunes a small reranker (default Qwen/Qwen2.5-1.5B-Instruct) with 4-bit QLoRA
via Unsloth on EXTERNAL distilled reasoning traces (Rank1-style / BRIGHT training
split): each example is (query, document, reasoning rationale) -> a relevance
label rendered as a chat completion. Standard SFT with TRL's SFTTrainer.

=============================== ANTI-CIRCULARITY ===============================
Anti-circularity guard (R4 / A3): training data is EXTERNAL ONLY. We NEVER mine
labels from mem's gold traces -- doing so would train the model to reproduce the
eval oracle (the leak the whole project exists to prevent). The dataset loaders
below touch ONLY public corpora (Rank1 traces / BRIGHT train). There is NO code
path in this file that reads the mem store (.mem/store.db) or the grid summary.
A runtime assertion (assert_external_only) guards the dataset id.
===============================================================================

Budget discipline (PRD R6): LoRA-only checkpoints, save_total_limit handling,
HF caches redirected off the host root, gradient checkpointing on, 4-bit base,
nvidia-smi + df deltas logged. Target <=24 GB VRAM.

AUTHOR-ONLY tonight. assert_blackwell() + require_download_approval() gate every
GPU/network action; nothing runs until inside the approved mem-rl-sft container.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _track_a_common import (  # noqa: E402
    assert_blackwell,
    redirect_hf_caches,
    require_download_approval,
)

DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
DEFAULT_MAX_SEQ_LEN = 2048
DEFAULT_LORA_R = 16

# EXTERNAL distilled-reasoning training sources. The allow-list is the
# anti-circularity firewall: only these public datasets may feed SFT.
EXTERNAL_DATASETS = {
    # Rank1-style distilled R1 reasoning traces for reranking.
    "rank1": "orionw/rank1-training-data",
    # BRIGHT reasoning-retrieval training examples (public).
    "bright-train": "xlangai/BRIGHT",
}

# A concise instruction that frames the pointwise relevance judgment with a
# reasoning rationale (Rank1 distillation target shape).
RERANK_SYSTEM_PROMPT = (
    "You are a relevance judge. Given a query and a document, reason briefly about "
    "whether the document is relevant to the query, then output a final verdict on "
    "its own line as 'Relevant: yes' or 'Relevant: no'."
)


def assert_external_only(dataset_key: str) -> str:
    """Resolve a dataset key to its HF id, asserting it is on the external allow-list.

    This is the executable form of the anti-circularity guard: only keys in the
    EXTERNAL_DATASETS allow-list (public HF corpora) resolve; anything else --
    including any attempt to point SFT at mem -- raises. The allow-list IS the
    firewall; there is no mem-store read path in this module to fence off.
    """
    if dataset_key not in EXTERNAL_DATASETS:
        raise ValueError(
            f"dataset {dataset_key!r} is not on the EXTERNAL allow-list "
            f"{sorted(EXTERNAL_DATASETS)}. Anti-circularity guard (PRD R4/A3): SFT "
            "may train ONLY on external public data, never on mem gold traces."
        )
    return EXTERNAL_DATASETS[dataset_key]


def _gpu_mem_used_mib() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return sum(int(x) for x in out.split())
    except Exception:
        return None


def _disk_free_gib(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024**3)


def build_dataset(dataset_key: str, max_examples: int, max_seq_len: int) -> Any:
    """Load an external reranking dataset and render it into chat-format SFT rows.

    Each row: messages = [system, user(query+doc), assistant(rationale + verdict)].
    The label ('Relevant: yes/no') is supervised; the rationale is the distilled
    reasoning the small model learns to emit (R11 evidence surface).
    """
    hf_id = assert_external_only(dataset_key)
    require_download_approval(f"external SFT dataset {dataset_key!r} ({hf_id})")
    from datasets import load_dataset

    if dataset_key == "rank1":
        raw = load_dataset(hf_id, split="train")
    else:  # bright-train
        raw = load_dataset(hf_id, "examples", split="biology")

    raw = raw.select(range(min(len(raw), max_examples)))

    def to_chat(ex: dict[str, Any]) -> dict[str, Any]:
        query = str(ex.get("query") or ex.get("question") or "")
        doc = str(ex.get("document") or ex.get("positive") or ex.get("content") or "")
        rationale = str(ex.get("reasoning") or ex.get("rationale") or "")
        label = ex.get("label", ex.get("relevant", 1))
        verdict = (
            "yes" if (str(label).lower() in {"1", "true", "yes", "relevant"}) else "no"
        )
        assistant = (rationale + "\n" if rationale else "") + f"Relevant: {verdict}"
        return {
            "messages": [
                {"role": "system", "content": RERANK_SYSTEM_PROMPT},
                {"role": "user", "content": f"Query: {query}\n\nDocument: {doc}"},
                {"role": "assistant", "content": assistant},
            ]
        }

    return raw.map(to_chat, remove_columns=raw.column_names)


def train(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    adapter_dir = run_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    redirect_hf_caches(run_dir)
    assert_blackwell()  # cu126-trap guard before any model/trainer import

    require_download_approval(f"base model {args.model!r}")
    from unsloth import FastLanguageModel  # noqa: PLC0415 -- import after caps guard
    from trl import SFTConfig, SFTTrainer  # noqa: PLC0415

    df_before = _disk_free_gib(run_dir)
    vram_before = _gpu_mem_used_mib()

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.model,
        max_seq_length=args.max_seq_len,
        load_in_4bit=True,  # QLoRA: 4-bit base -> fits <=24 GB (R6)
        dtype=None,  # let Unsloth pick bf16 on Blackwell
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        lora_alpha=args.lora_r * 2,
        lora_dropout=0.0,
        bias="none",
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        use_gradient_checkpointing="unsloth",  # R6: memory over speed
        random_state=args.seed,
    )

    dataset = build_dataset(args.dataset, args.max_examples, args.max_seq_len)

    sft_config = SFTConfig(
        output_dir=str(run_dir / "checkpoints"),
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        warmup_steps=args.warmup_steps,
        max_steps=args.max_steps,
        num_train_epochs=args.epochs if args.max_steps <= 0 else 1,
        learning_rate=args.lr,
        logging_steps=args.logging_steps,
        optim="adamw_8bit",  # 8-bit optimizer states -> VRAM headroom
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=args.seed,
        bf16=True,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=1,  # R6: keep only the last checkpoint on disk
        report_to="none",
        max_seq_length=args.max_seq_len,
        dataset_text_field=None,  # chat-format messages handled by the trainer
        gradient_checkpointing=True,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=sft_config,
    )

    t0 = time.time()
    train_result = trainer.train()
    elapsed_s = time.time() - t0

    # Save LoRA ADAPTERS ONLY (R6: never the merged full model -> disk budget).
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))

    vram_after = _gpu_mem_used_mib()
    df_after = _disk_free_gib(run_dir)

    summary: dict[str, Any] = {
        "track": "A",
        "requirement": "R4 reranker SFT (external data only)",
        "model": args.model,
        "dataset_key": args.dataset,
        "dataset_hf_id": EXTERNAL_DATASETS[args.dataset],
        "anti_circularity": "EXTERNAL ONLY; mem gold traces never read (PRD R4/A3)",
        "lora_r": args.lora_r,
        "max_seq_len": args.max_seq_len,
        "n_examples": len(dataset),
        "adapter_dir": str(adapter_dir),
        "elapsed_s": round(elapsed_s, 1),
        "train_loss": getattr(train_result, "training_loss", None),
        "vram_used_mib_before": vram_before,
        "vram_used_mib_after": vram_after,
        "vram_delta_mib": (
            vram_after - vram_before if (vram_after and vram_before) else None
        ),
        "disk_free_gib_before": round(df_before, 2),
        "disk_free_gib_after": round(df_after, 2),
        "disk_consumed_gib": round(df_before - df_after, 2),
    }
    (run_dir / "train_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--model", default=DEFAULT_MODEL, help="base reranker HF id (e.g. Qwen 1.5B/7B)"
    )
    ap.add_argument(
        "--dataset",
        choices=sorted(EXTERNAL_DATASETS),
        default="rank1",
        help="EXTERNAL training dataset (allow-listed; never mem)",
    )
    ap.add_argument("--max-seq-len", type=int, default=DEFAULT_MAX_SEQ_LEN)
    ap.add_argument("--lora-r", type=int, default=DEFAULT_LORA_R)
    ap.add_argument("--max-examples", type=int, default=20000, help="cap external rows")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument(
        "--max-steps", type=int, default=-1, help=">0 overrides epochs (use for smoke)"
    )
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--warmup-steps", type=int, default=10)
    ap.add_argument("--logging-steps", type=int, default=5)
    ap.add_argument("--save-steps", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--run-dir",
        default=str(Path.home() / "runs" / "track-a-sft"),
        help="run-scoped dir for adapter + HF caches (off host root)",
    )
    args = ap.parse_args(argv)
    return train(args)


if __name__ == "__main__":
    main()
