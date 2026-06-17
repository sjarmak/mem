#!/usr/bin/env python3
"""R2 real-kernel smoke test for the Blackwell sm_120 (RTX 5090) training env.

R2 acceptance: a script that
  (a) asserts torch.cuda.get_device_capability() == (12, 0),
  (b) runs ONE real CUDA matmul (a real kernel, not just an import),
  (c) QLoRA-fine-tunes a ~0.5B model for >=1 step without error,
  (d) saves AND reloads a LoRA adapter,
  (e) logs the nvidia-smi VRAM delta and the df -h delta,
  (f) exits 0 on success, 1 on any failure.

WHY a REAL kernel, not just the import check: the cu126 silent-wheel trap
(README footgun #1) can let `import torch` succeed and even make
get_device_capability() print (12, 0) while NO sm_120 kernels are present — the
first real matmul/QLoRA op is where it actually dies or silently falls back to
CPU. This script forces that failure into the open.

GUARDRAIL: this file is AUTHORED tonight but MUST NOT be run tonight. It needs
the built image + a small (~0.5B) model download + a real GPU workload, all of
which are forbidden under the overnight guardrails. It is an approval-gated
morning action (see README).

Run (morning, after image build + approval):
    python smoke_test.py
Optional env:
    SMOKE_MODEL   HF id of the ~0.5B base model (default Qwen/Qwen2.5-0.5B)
    SMOKE_OUT     output dir for the adapter (default ./.smoke_out, on a volume)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from typing import Optional

# Expected Blackwell compute capability (RTX 5090).
EXPECTED_CAPABILITY = (12, 0)
EXPECTED_TORCH_PREFIX = "2.11"
EXPECTED_TORCH_CUDA_TAG = "+cu129"

DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B"
DEFAULT_OUT = os.path.join(os.getcwd(), ".smoke_out")


def _log(section: str, msg: str) -> None:
    print(f"[smoke:{section}] {msg}", flush=True)


def _nvidia_smi_used_mib() -> Optional[int]:
    """Return total used VRAM in MiB via nvidia-smi, or None if unavailable.

    We shell out to nvidia-smi (rather than torch) so the delta reflects the
    whole device, catching leaks outside the process too (contended-box, A6).
    """
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        _log("nvidia-smi", f"unavailable: {exc}")
        return None
    # Sum across GPUs (single-GPU box, but be explicit).
    total = 0
    for line in out.stdout.strip().splitlines():
        line = line.strip()
        if line:
            total += int(line)
    return total


def _df_free_kib(path: str) -> Optional[int]:
    """Return free space in KiB on the filesystem backing `path`, or None."""
    try:
        st = os.statvfs(path)
    except OSError as exc:
        _log("df", f"statvfs failed for {path}: {exc}")
        return None
    return (st.f_bavail * st.f_frsize) // 1024


def _human_gib(kib: Optional[int]) -> str:
    if kib is None:
        return "n/a"
    return f"{kib / (1024 * 1024):.2f} GiB"


def check_capability() -> None:
    """(a) Assert the GPU is sm_120 and torch is the cu129 build."""
    import torch

    _log("cap", f"torch={torch.__version__}")
    if not torch.__version__.startswith(EXPECTED_TORCH_PREFIX):
        raise RuntimeError(
            f"torch {torch.__version__} is not {EXPECTED_TORCH_PREFIX}.x "
            "— it was silently bumped; abort."
        )
    if EXPECTED_TORCH_CUDA_TAG not in torch.__version__:
        raise RuntimeError(
            f"torch {torch.__version__} is not a {EXPECTED_TORCH_CUDA_TAG} build "
            "— cu126 silent-wheel trap; abort."
        )
    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is False — no usable GPU.")
    cap = torch.cuda.get_device_capability()
    _log("cap", f"device={torch.cuda.get_device_name(0)} capability={cap}")
    if cap != EXPECTED_CAPABILITY:
        raise RuntimeError(
            f"compute capability {cap} != expected {EXPECTED_CAPABILITY} "
            "— wrong GPU or wrong wheel; abort."
        )


def check_real_matmul() -> None:
    """(b) Run ONE real CUDA matmul to force an sm_120 kernel to execute."""
    import torch

    a = torch.randn(2048, 2048, device="cuda", dtype=torch.bfloat16)
    b = torch.randn(2048, 2048, device="cuda", dtype=torch.bfloat16)
    c = a @ b
    torch.cuda.synchronize()
    val = float(c.float().abs().mean().item())
    if not (val == val):  # NaN check
        raise RuntimeError("matmul produced NaN — kernel/dtype path broken.")
    _log("matmul", f"2048x2048 bf16 matmul OK (mean abs={val:.4f})")


def check_qlora_step(model_id: str, out_dir: str) -> None:
    """(c)+(d) One QLoRA training step on a ~0.5B model, then save+reload adapter.

    Uses Unsloth's FastLanguageModel (the SFT trainer per A1). One optimizer step
    on a single tiny batch is enough to exercise the 4-bit bitsandbytes path and
    the LoRA forward/backward — the failure surfaces here if bnb/Unsloth lack
    sm_120 kernels.
    """
    import torch
    from unsloth import FastLanguageModel

    _log("qlora", f"loading {model_id} in 4-bit (QLoRA)")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_id,
        max_seq_length=512,
        dtype=None,  # auto (bf16 on Blackwell)
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=8,
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
        ],
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=0,
    )

    # Build ONE tiny batch and take ONE real optimizer step.
    text = "mem smoke test: the quick brown fox retrieves the relevant document."
    enc = tokenizer(text, return_tensors="pt", max_length=64, truncation=True)
    input_ids = enc["input_ids"].to("cuda")
    attn = enc["attention_mask"].to("cuda")

    model.train()
    optim = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=1e-4
    )
    optim.zero_grad(set_to_none=True)
    out = model(input_ids=input_ids, attention_mask=attn, labels=input_ids)
    loss = out.loss
    if loss is None:
        raise RuntimeError("model returned no loss — labels path broken.")
    loss.backward()
    optim.step()
    torch.cuda.synchronize()
    loss_val = float(loss.item())
    _log("qlora", f"1 QLoRA step OK (loss={loss_val:.4f})")
    if not (loss_val == loss_val):
        raise RuntimeError("QLoRA step produced NaN loss — abort.")

    # (d) Save the LoRA adapter, then reload it into a fresh model instance.
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    _log("qlora", f"adapter saved to {out_dir}")

    # Reload: load base again, then attach the saved adapter via PEFT.
    from peft import PeftModel

    base, _ = FastLanguageModel.from_pretrained(
        model_name=model_id,
        max_seq_length=512,
        dtype=None,
        load_in_4bit=True,
    )
    reloaded = PeftModel.from_pretrained(base, out_dir)
    n_lora = sum(1 for name, _ in reloaded.named_parameters() if "lora" in name.lower())
    if n_lora == 0:
        raise RuntimeError("reloaded adapter has no LoRA params — save/load broken.")
    _log("qlora", f"adapter reloaded OK ({n_lora} LoRA tensors present)")


def main() -> int:
    model_id = os.environ.get("SMOKE_MODEL", DEFAULT_MODEL)
    out_dir = os.environ.get("SMOKE_OUT", DEFAULT_OUT)

    _log("start", f"model={model_id} out={out_dir}")
    vram_before = _nvidia_smi_used_mib()
    disk_before = _df_free_kib(os.path.dirname(out_dir) or os.getcwd())
    t0 = time.time()

    try:
        check_capability()
        check_real_matmul()
        check_qlora_step(model_id, out_dir)
    except Exception:  # noqa: BLE001 — top-level smoke harness, report and fail loud
        _log("FAIL", "smoke test raised:")
        traceback.print_exc()
        # Still emit the deltas so a failed run is diagnosable.
        _emit_deltas(vram_before, disk_before, out_dir, t0, ok=False)
        return 1

    _emit_deltas(vram_before, disk_before, out_dir, t0, ok=True)
    _log("PASS", "all R2 checks passed")
    return 0


def _emit_deltas(
    vram_before: Optional[int],
    disk_before: Optional[int],
    out_dir: str,
    t0: float,
    ok: bool,
) -> None:
    """(e) Log nvidia-smi VRAM delta and df free-space delta."""
    vram_after = _nvidia_smi_used_mib()
    disk_after = _df_free_kib(os.path.dirname(out_dir) or os.getcwd())
    vram_delta = (
        (vram_after - vram_before)
        if (vram_after is not None and vram_before is not None)
        else None
    )
    # df free went DOWN as we downloaded weights + wrote the adapter; report
    # consumed = before - after.
    disk_consumed_kib = (
        (disk_before - disk_after)
        if (disk_before is not None and disk_after is not None)
        else None
    )
    summary = {
        "ok": ok,
        "elapsed_s": round(time.time() - t0, 1),
        "vram_used_before_mib": vram_before,
        "vram_used_after_mib": vram_after,
        "vram_delta_mib": vram_delta,
        "disk_free_before": _human_gib(disk_before),
        "disk_free_after": _human_gib(disk_after),
        "disk_consumed": _human_gib(disk_consumed_kib),
    }
    _log("deltas", json.dumps(summary))
    # Also drop a machine-readable artifact for the R2 acceptance record.
    try:
        with open(os.path.join(os.getcwd(), "smoke_result.json"), "w") as fh:
            json.dump(summary, fh, indent=2)
    except OSError as exc:
        _log("deltas", f"could not write smoke_result.json: {exc}")


if __name__ == "__main__":
    sys.exit(main())
