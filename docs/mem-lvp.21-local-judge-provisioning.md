# mem-lvp.21 — local OSS judge provisioning (setup notes)

Stands up the `LocalStackComparativeJudge` backend (`membench/bbon/local_stack_judge.py`)
the §12.6 action-impact run (mem-lvp.19) consumes. **Userspace** install — no sudo, no
systemd, no root. Host already had the NVIDIA driver (RTX 5090) + `zstd`.

## What was installed

- **Ollama 0.30.10**, userspace, extracted to `~/.local`:
  ```bash
  curl -fsSL https://github.com/ollama/ollama/releases/download/v0.30.10/ollama-linux-amd64.tar.zst -o ~/ollama.tar.zst
  tar --use-compress-program=unzstd -xf ~/ollama.tar.zst -C ~/.local && rm ~/ollama.tar.zst
  ```
  Binary: `~/.local/bin/ollama`; CUDA runners under `~/.local/lib/ollama`.
- **Daemon** (background, logs to `~/.ollama-serve.log`):
  ```bash
  PATH="$HOME/.local/bin:$PATH" nohup ollama serve > ~/.ollama-serve.log 2>&1 &
  ```
  Confirmed GPU pickup: `library=CUDA ... NVIDIA GeForce RTX 5090 ... available 26.4 GiB`.
- **Models pulled** (`~/.ollama/models`):
  - `nomic-embed-text` — the embedding model `LocalModelStack` preflights.
  - `llama3.1:8b` — the 8B+ instruct chat model (the module's named OSI-leaning fallback).

## Env pins for the run (lvp.19)

`LocalModelStack.from_env` resolves these; defaults work except the chat model pin:

| Var | Value | Note |
|---|---|---|
| `MEMBENCH_OLLAMA_BASE_URL` | `http://localhost:11434` | default — omit |
| `MEMBENCH_LOCAL_CHAT_MODEL` | `llama3.1:8b` | **set this** (default is bare `llama3`) |
| `MEMBENCH_LOCAL_EMBED_MODEL` | `nomic-embed-text` | default — omit |

## Model-license note (§4.5 publication gate)

- `llama3.1:8b` (Llama Community License) is **not OSI-approved** → a run pinned to it is
  publication-gated, exactly as `local_stack_judge.py` documents (a publication-time
  concern, not runtime — the judge records the model in telemetry, doesn't block).
- **Strict-OSI swap:** `qwen2.5` (Apache-2.0) via `MEMBENCH_LOCAL_CHAT_MODEL=qwen2.5:7b`
  (7B) or `qwen2.5:14b` (≥8B, ~9 GB) — pull then re-point, no code change.
- **§4.5 preferred Nemotron** (NVIDIA Open Model License, non-OSI): the 70B can't fit
  (~43 GB / >32 GB VRAM); only `nemotron-mini` (4B) fits but is <8B. Treat Nemotron as a
  later GPU-budget decision; the OSI-clean run is the publication-safe default.

## Verify (the deliverable)

```python
from membench.bbon.local_stack_judge import LocalStackComparativeJudge
LocalStackComparativeJudge(stack=LocalModelStack.from_env({"MEMBENCH_LOCAL_CHAT_MODEL": "llama3.1:8b"})).preflight()
# raises LocalStackUnavailableError if the daemon is down or a model is unpulled; returns None on success.
```

## Operational caveats

- **Not persistent across reboot.** The daemon is a userspace `nohup` process, not a
  service. Re-run `ollama serve` after a reboot before any run.
- **Disk.** The shared root volume is volatile (swung 16→44 GB free during this work).
  The 27 GB `~/.cache/huggingface` is the reclaim reserve if a larger model is pinned;
  did NOT need to free anything for this install (~5 GB footprint).
