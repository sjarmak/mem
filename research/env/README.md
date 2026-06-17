# Blackwell sm_120 training env — build & smoke-test runbook

Environment scaffolding for the RTX 5090 (Blackwell, compute capability **(12, 0)**)
fine-tuning / RL stack. This directory is **files only** — nothing here has been
built, installed, or run. Everything that touches the GPU, the network, or a
container build is an **approval-gated morning action** (see bottom).

Grounding: the env requirements (R1/R2) and the two-image split + contended-box
ops amendments (A1/A6) — the split SFT/vLLM images and the digest-pin discipline
below exist to dodge the documented Blackwell sm_120 toolchain footguns.

## Files

| File | Purpose |
|------|---------|
| `requirements.lock` | Single SoT of the known-good sm_120 pin set (torch 2.11+cu129 / bitsandbytes 0.49.2 / Unsloth / TRL 0.23.x), with the known-broken issue citations. |
| `Dockerfile.sft` | The **stable** SFT-only image: Unsloth QLoRA, no vLLM, no flash-attn. Carries the project spine (R4). |
| `Dockerfile.vllm` | The **expendable** image: adds vLLM (+flash-attn) on the same pins for the R7/R10 GRPO behavior track. |
| `smoke_test.py` | The R2 real-kernel smoke test (capability + real matmul + 1 QLoRA step + adapter save/reload + VRAM/disk deltas). |
| `README.md` | This runbook. |

## Why two images (PRD amendment A1)

The env is a digest-pinned container **split in two**:

- **`Dockerfile.sft` is stable.** Unsloth QLoRA SFT needs neither vLLM nor
  flash-attn — the two most fragile sm_120 components. Keeping them out means the
  primary track (R4) ships even if the RL env never stabilizes.
- **`Dockerfile.vllm` is expendable.** It carries only the behavior track
  (R7 s3-style searcher; R10 contingency). Whether vLLM sm_120 colocate is stable
  *today on this box* is an open question, resolved by the R2.5/A2 colocation +
  2-hour soak gate **before** R7 consumes the runway. If it's unstable, R7 falls
  back to offline-sequential and nothing else is blocked.

## Morning commands (run ONLY after approval)

These download wheels, compile sm_120 kernels (slow, disk-hungry), pull a model,
and run a GPU workload. **Do not run any of them tonight.**

```bash
# 0. Pre-prune the existing ~64 GB ~/.cache BEFORE building (A6 / footgun #4).
#    Source compiles + HF downloads will otherwise collide with it on the 72 GB root.
#    (Inspect first; only prune what you own.)
du -sh ~/.cache/* 2>/dev/null | sort -h | tail
#    e.g.: rm -rf ~/.cache/pip ~/.cache/huggingface/datasets   # if safe to drop

# 1. Resolve + commit the real base-image digest, then paste it into Dockerfile.sft
#    (replace REPLACE_WITH_RESOLVED_DIGEST). Digest-pin discipline: never build
#    against a moving tag.
docker buildx imagetools inspect pytorch/pytorch:2.11.0-cuda12.9-cudnn9-devel

# 2. Build the STABLE SFT image. Use a SEPARATE, WIPEABLE buildx cache volume so
#    source compiles do NOT fill the host root (footgun #3).
docker buildx create --name memcache --driver docker-container 2>/dev/null || true
docker buildx build \
  --builder memcache \
  --cache-to   type=local,dest=/var/tmp/mem-buildcache,mode=max \
  --cache-from type=local,src=/var/tmp/mem-buildcache \
  -f Dockerfile.sft -t mem-rl-sft:latest --load .

# 3. Capture the built SFT image digest and paste into Dockerfile.vllm
#    (replace REPLACE_WITH_LOCAL_SFT_IMAGE_DIGEST).
docker images --digests mem-rl-sft

# 4. Build the EXPENDABLE vLLM image (FROM the pinned SFT image). flash-attn's
#    sm_120 build is the slow one — same wipeable cache volume.
docker buildx build \
  --builder memcache \
  --cache-to   type=local,dest=/var/tmp/mem-buildcache,mode=max \
  --cache-from type=local,src=/var/tmp/mem-buildcache \
  -f Dockerfile.vllm -t mem-rl-vllm:latest --load .

# 5. Run the R2 smoke test inside the STABLE image. Mount a WIPEABLE named volume
#    for the HF cache + adapter out (NOT the host root). --gpus all for the 5090.
docker volume create mem-cache 2>/dev/null || true
docker run --rm --gpus all \
  -v mem-cache:/cache \
  -e SMOKE_OUT=/cache/.smoke_out \
  mem-rl-sft:latest \
  python /workspace/smoke_test.py
#    Expect: "[smoke:PASS] all R2 checks passed", exit 0, and a smoke_result.json
#    with the VRAM + disk deltas. COMMIT that artifact under research/ (R2 (d)).

# 6. (R2.5/A2, separate gate) colocation + soak on the EXPENDABLE vLLM image —
#    do this only after step 5 passes; it resolves the [OPEN] vLLM-sm_120 question.
```

After ANY install or build, **re-run the capability check** to confirm torch was
not silently bumped:

```bash
docker run --rm --gpus all mem-rl-sft:latest \
  python -c "import torch; print(torch.cuda.get_device_capability())"   # must print (12, 0)
```

## Known footguns

1. **The cu126 silent-wheel trap.** A bare `pip install torch` (or a transitive
   dep that pulls torch) can resolve a **cu126** wheel that imports cleanly — and
   may even make `get_device_capability()` print `(12, 0)` — while having **no
   sm_120 kernels**; the first real matmul/QLoRA op then dies or silently falls
   back. Both Dockerfiles install torch from the **cu129** index URL and assert
   `+cu129` at build time; `smoke_test.py` forces a real kernel to catch the rest.

2. **`TORCHDYNAMO_DISABLE=1` is the default.** Baked into both images. sm_120
   inductor/compile paths are unstable on this moving target; opt into compile
   **explicitly per run**, never implicitly.

3. **Use a separate, wipeable build-cache volume.** Source compiles (flash-attn,
   vLLM) generate gigabytes of intermediates that will fill the **72 GB host
   root**. The morning commands route buildx cache to `/var/tmp/mem-buildcache`
   and the runtime HF cache to a named `mem-cache` volume — both disposable.
   Mounting `/cache` over the host root is the whole point of the baked
   `HF_HOME`/`TMPDIR`/`PIP_CACHE_DIR` env (A6).

4. **Pre-prune `~/.cache` (≈64 GB) before building.** It competes with the build
   for root-fs space. Inspect with `du -sh ~/.cache/*`; drop only what you own.

5. **NEVER `pip install` / auto-update inside a working image.** Env drift is the
   #1 premortem risk. To change a pin: edit `requirements.lock`, rebuild, re-run
   the `(12, 0)` check. The running container is read-only by discipline.

## Digest-pin discipline

- Both Dockerfiles `FROM ...@sha256:<digest>`, never a floating tag. The
  placeholders (`REPLACE_WITH_RESOLVED_DIGEST`,
  `REPLACE_WITH_LOCAL_SFT_IMAGE_DIGEST`) are filled in steps 1 and 3 above and
  then **committed** — the toolchain must be byte-reproducible.
- `requirements.lock` is the **single SoT** for every Python pin; both images
  read from it (the vLLM image uses it as a `--constraint` so vLLM's resolver
  cannot bump torch).
- Per run (A6): snapshot the lockfile hash + git SHA + the `(12, 0)` capability
  output alongside results, so any number is traceable to an exact env.

## Approval-gated morning actions (DO NOT run tonight)

- **build `Dockerfile.sft`** — downloads cu129 wheels + Unsloth/bnb, compiles;
  network + container build (forbidden overnight).
- **build `Dockerfile.vllm`** — additionally compiles flash-attn + vLLM sm_120
  (slow, disk-heavy); network + container build (forbidden overnight).
- **run `smoke_test.py`** — pulls a ~0.5B model and runs a real GPU QLoRA step;
  model download + GPU workload (forbidden overnight).
