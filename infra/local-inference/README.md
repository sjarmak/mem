# Local dual-engine inference harness (vLLM + SGLang)

A local-first rig for hands-on experimentation with **batching, streaming metrics,
token-level prediction, KV-cache pressure, and throughput** on a single NVIDIA GPU —
wired into the `membench` harness so you can replay real memory-harness prompt
distributions against two engines and compare.

Both engines speak the **OpenAI-compatible** API behind the harness's existing
paid-host-fenced convention (`membench/grading/judge.py`,
`membench/memory_systems/local_stack.py`), so nothing here introduces a new client
contract — `membench/engines/` just measures what these servers expose.

## Why two engines

Your prompts are retrieval-augmented: a large, stable memory prefix + a short varying
tail. The single highest-leverage lever is therefore **prefix KV-cache reuse**, not
raw batching tricks. So this is an A/B, not a single setup:

- **vLLM** — PagedAttention + APC (`--enable-prefix-caching`), the throughput and
  observability baseline with the richest Prometheus metrics.
- **SGLang** — RadixAttention does tree-structured prefix sharing across requests,
  purpose-built for the many-trials-share-a-memory-prefix shape.

`membench/engines/metrics_scrape.py` maps both engines' differently-named gauges onto
one shape so the sweep compares them directly.

## Hardware note (RTX 5090 / Blackwell, sm_120)

- Engine images must ship **CUDA 12.8+**. Pin `VLLM_IMAGE` / `SGLANG_IMAGE` to a tag
  that has it (see `.env.example`); plain `latest` usually works but pin once verified.
- 32 GB hosts ~7–14B at bf16, ~32B with **FP8/AWQ/GPTQ**. Blackwell has strong FP8
  (and FP4) support — treat **quantization as an explicit throughput axis**, not an
  afterthought (free KV headroom → higher concurrency before preemption).
- One 32 GB GPU cannot fully host two bf16 8B models at once. Either A/B the engines
  **sequentially** (default, via compose profiles) or drop to a **3B** and cap both
  `*_MEM_*` knobs to ~0.45 to run them side-by-side.

## Prerequisites

- NVIDIA driver + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  (`docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi` must work).
- `cp .env.example .env` and set `HUGGING_FACE_HUB_TOKEN` for gated checkpoints.

## Run it

```bash
cd infra/local-inference
cp .env.example .env            # set your HF token + model

# 1) observability (always-on): Prometheus :9090, Grafana :3000, DCGM :9400
docker compose up -d

# 2) bring up ONE engine (the realistic single-GPU A/B):
docker compose --profile vllm up -d
#    wait for health, then drive load from memory-bench/ (see below)
docker compose --profile vllm down
docker compose --profile sglang up -d
```

Grafana (anonymous admin) auto-loads the **"Local Inference — Engines + GPU"**
dashboard: KV-cache usage, prefix-cache hit rate, running/waiting queue, TTFT
p50/p90, preemptions, and GPU util/mem/power from DCGM.

## Drive the experiment

From `memory-bench/` with an engine up:

```bash
# Concurrency sweep → latency/throughput/KV frontier, one JSON row per (engine, c):
uv run python scripts/engine_throughput_sweep.py --engine vllm \
    --concurrency 1,4,16,32 --groups 1 --prompts-per-group 64 \
    --out .mem/engine_sweep.jsonl

# The prefix-cache experiment that actually matters here — maximal sharing
# (groups=1, every request after the first should hit the cache) vs none (groups=N):
uv run python scripts/engine_throughput_sweep.py --engine vllm \
    --groups 1  --prompts-per-group 64 --out .mem/shared.jsonl     # high hit rate
uv run python scripts/engine_throughput_sweep.py --engine vllm \
    --groups 64 --prompts-per-group 1  --out .mem/unshared.jsonl   # ~0 hit rate
# compare prefix_cache_hit_rate_after + ttft_p50_s between the two.

# Replay a real prompt distribution instead of the synthetic probe:
uv run python scripts/engine_throughput_sweep.py --engine both \
    --prompts-file my_prompts.jsonl --out .mem/replay.jsonl

# Token-level prediction: add --logprobs to capture per-token logprobs in the client.
```

Suggested loop: pick a model → sweep concurrency on each engine → read the frontier +
Grafana KV/preemption traces → change one knob (`--enable-prefix-caching`,
`--max-num-batched-tokens`/chunked-prefill, `--quantization=fp8`, `--gpu-memory-utilization`)
→ re-sweep. Change one axis at a time.

## Phase 2 — k8s (do it second, not first)

k8s adds nothing to the single-GPU *iteration* loop and a lot of friction; stand it up
only when you're testing the **scaling design itself** — autoscaling on queue depth and
KV-cache-aware request routing across replicas. The concrete path:

1. `k3s` or `kind` + the **NVIDIA GPU Operator** (device plugin, drivers, DCGM as a
   DaemonSet — reuses the same DCGM metrics this stack already scrapes).
2. Deploy the engines via **KServe** or the **vllm-project/production-stack** Helm
   chart (autoscaling + prefix-cache-aware routing built in).
3. Point Prometheus at the in-cluster ServiceMonitors; the same
   `membench/engines/metrics_scrape.py` mapping works unchanged against pod `/metrics`.

That's a separate experiment from "throughput of one engine" — keep them distinct.
