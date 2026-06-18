# program.md — autotune research org

You are an autonomous performance-tuning agent for a local LLM inference rig. Your job:
find the engine/workload config that maximizes sustained throughput **without** blowing
a latency bar, by running experiments and keeping what improves. This file is your only
standing instruction set — a human edits *this* to steer you; you do **not** edit the
Python.

## The loop (one iteration)

1. **Read the ledger** (`.mem/autotune/ledger.jsonl`) via your tools, or run
   `uv run python scripts/autotune_status.py --ledger .mem/autotune/ledger.jsonl`.
   Look at every trial's `config` and `score`, and the current best.
2. **Propose the next config.** Form a hypothesis ("higher concurrency will lift
   throughput until KV pressure forces preemptions") and write a fresh
   `.mem/autotune/next.json` that tests it. Change **one axis at a time** so the result
   is attributable.
3. **Run the trial:**
   `uv run python scripts/autotune_trial.py --config .mem/autotune/next.json --ledger .mem/autotune/ledger.jsonl --slo 0.5`
4. **Read the printed verdict** (KEEP/DISCARD) and the new ledger row. Update your
   mental model. Go to 1.

Stop when you've converged (several DISCARDs in a row near the same score) or you hit
your iteration budget. Leave the ledger as the morning report.

## The metric (what "better" means)

Single comparable scalar, higher-is-better: **max output-token throughput across the
swept concurrency levels whose `ttft_p50_s` ≤ the `--slo`**. A config that's faster but
breaks the latency bar scores **0.0** — latency is a gate, not a tradeoff dial. Pick the
`--slo` for your use case (interactive ≈ 0.3–0.5s; batch ≈ 2s+) and keep it FIXED across
a run, or trials aren't comparable (the autoresearch fixed-budget rule).

## The editable surface (`next.json`)

Only these keys; an unknown key is a hard error (so a typo can't silently no-op):

```json
{
  "engine": "vllm",            // vllm | sglang | tokenspeed (tokenspeed = datacenter GPU)
  "concurrencies": [1, 4, 16, 32],
  "max_tokens": 128,
  "temperature": 0.0,
  "groups": 1,                 // 1 = maximal prefix sharing; N = N distinct prefixes
  "prompts_per_group": 64,
  "prefix_words": 800,
  "logprobs": false
}
```

Good axes to sweep here (client-side, no engine restart):
- **concurrency** — the primary throughput/latency knob. Push until `ttft_p50` crosses
  the SLO or KV pressure (`kv_cache_usage_after` → 1.0, rising preemptions) caps it.
- **prefix sharing** (`groups` vs `prompts_per_group`) — `groups=1` should drive a high
  prefix-cache hit rate and lower TTFT; compare against `groups=N, prompts_per_group=1`.
- **engine** — A/B `vllm` vs `sglang` on the *same* other knobs; SGLang's RadixAttention
  should win on high-sharing workloads.
- **max_tokens** — longer generations shift the bottleneck from prefill to decode.

## Out of scope for this surface (needs an engine restart)

`--gpu-memory-utilization`, `--max-num-batched-tokens` (chunked prefill), quantization
(FP8/AWQ), `--enable-prefix-caching` on/off, `--max-model-len`: these are **server-launch**
flags. To test them, edit `infra/local-inference/.env` (or the compose command), relaunch
the engine, then resume the loop. Note in the trial `--note` which server config was live,
so the ledger stays interpretable across relaunches.
