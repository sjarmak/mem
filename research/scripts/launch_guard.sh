#!/usr/bin/env bash
# launch_guard.sh — contended-box launch gate (PRD amendment A6 / R6).
#
# Refuses to launch a training/RL run on the shared RTX 5090 box unless the
# machine has enough free RAM/swap headroom AND the GPU is not already busy
# beyond a budget (other workloads may share this box and GPU). On the way it:
#
#   1. Bakes ALL cache/temp dirs (HF_HOME, TRANSFORMERS_CACHE, HF_DATASETS_CACHE,
#      TMPDIR, PIP_CACHE_DIR) into ONE tracked run dir so nothing scatters into
#      the 64 GB ~/.cache (premortem Theme D). These are EXPORTED for the wrapped
#      command so the run inherits them — that is the whole point; a shell-only
#      export would not survive into a detached launcher.
#   2. Snapshots reproducibility provenance into <results>/provenance.json:
#      lockfile hash, git SHA (+dirty flag), and the (12,0) Blackwell capability
#      check (R1). If the capability check does not print (12, 0), the launch is
#      refused — a run on the wrong torch/CUDA build is a silent science-killer.
#   3. Only then exec's the wrapped command. If any gate fails, it exits non-zero
#      and does NOT start the run.
#
# This script itself does NOT touch the GPU for training and does NOT install
# anything. The (12,0) check is a single read-only torch import; treat the whole
# script as approval-gated only because it is the thing that *starts* a GPU run.
#
# Usage:
#   research/scripts/launch_guard.sh \
#     --results-dir "$HOME"/runs/<run> \
#     --lockfile   research/env/requirements.lock \
#     [--min-ram-gib 8] [--min-swap-gib 4] [--gpu-budget-mib 4096] \
#     [--nas-root /mnt/ml] [--min-nas-gib 20] [--combined] \
#     [--repo /path/to/repo] \
#     -- <command to launch...>
#
# --combined: declare a Track-A + Track-B run sharing this box; raises the RAM
#   floor to COMBINED_MIN_RAM_GIB and prints what to free on refusal.
# Cache routing: HF model hub + pip + TMPDIR stay on local NVMe (fast/IOPS);
#   HF_DATASETS_CACHE and the checkpoint archive (MEM_CKPT_ARCHIVE) go to the NAS.
#
# Exit codes:
#   0  wrapped command launched (this script exec's into it)
#   1  a launch gate failed (RAM/swap/GPU/capability) — run NOT started
#   2  bad invocation

set -eo pipefail

# ---- defaults -------------------------------------------------------------
MIN_RAM_GIB=8
MIN_SWAP_GIB=4
GPU_BUDGET_MIB=4096        # refuse if GPU already using more than this (idle baseline ~3.7 GB)
NAS_ROOT="/mnt/ml"         # NFS NAS (48 TB free): datasets + checkpoint archive land here
MIN_NAS_GIB=20             # refuse if the NAS has less than this free
COMBINED=0                 # --combined: a Track-A + Track-B run shares this box; raise the RAM floor
RESULTS_DIR=""
LOCKFILE=""
REPO=""
CMD=()

# When both tracks run together, host RAM is the documented OOM risk (swap is
# usually exhausted). Enforce a higher floor and tell the operator what to free.
COMBINED_MIN_RAM_GIB=24
RAM_RELIEF_HINT="free RAM first: stop or pause any idle heavy local services (databases, vector stores, background agent workers) before launching a combined run; leave shared services other tooling depends on running."

die() { echo "launch_guard: $*" >&2; exit 1; }
refuse() { echo "launch_guard: REFUSE: $*" >&2; exit 1; }

# ---- arg parse ------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --results-dir)   RESULTS_DIR="$2"; shift 2 ;;
    --lockfile)      LOCKFILE="$2"; shift 2 ;;
    --repo)          REPO="$2"; shift 2 ;;
    --min-ram-gib)   MIN_RAM_GIB="$2"; shift 2 ;;
    --min-swap-gib)  MIN_SWAP_GIB="$2"; shift 2 ;;
    --gpu-budget-mib) GPU_BUDGET_MIB="$2"; shift 2 ;;
    --nas-root)      NAS_ROOT="$2"; shift 2 ;;
    --min-nas-gib)   MIN_NAS_GIB="$2"; shift 2 ;;
    --combined)      COMBINED=1; shift ;;
    --)              shift; CMD=("$@"); break ;;
    *)               echo "launch_guard: unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$RESULTS_DIR" ]] || { echo "launch_guard: --results-dir is required" >&2; exit 2; }
[[ ${#CMD[@]} -gt 0 ]] || { echo "launch_guard: a -- <command> is required" >&2; exit 2; }
[[ -n "$REPO" ]] || REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || echo "")"

mkdir -p "$RESULTS_DIR"

# ---- 1. bake cache/temp dirs: HOT stays local, COLD/large -> NAS ----------
# Local NVMe is the scarce filesystem (97% full). Route by access pattern:
#   local  : HF model hub cache (loaded once into VRAM — fast NVMe), pip cache,
#            TMPDIR scratch (high IOPS; NFS locking is unsafe here)
#   NAS    : datasets (sequential reads, NFS-tolerable), checkpoint ARCHIVE
# The NAS must actually be mounted before we route anything there — silently
# writing "NAS" data onto local would defeat the whole point.
if ! mountpoint -q "$(df -P "$NAS_ROOT" 2>/dev/null | awk 'NR==2{print $6}')" 2>/dev/null \
   && [[ ! -d "$NAS_ROOT" ]]; then
  refuse "NAS root $NAS_ROOT is not available (mount the NFS share or pass --nas-root)"
fi
nas_avail_gib=$(df -PBG "$NAS_ROOT" 2>/dev/null | awk 'NR==2{gsub("G","",$4);print $4+0}')
if [[ -z "$nas_avail_gib" ]]; then
  refuse "cannot stat NAS free space at $NAS_ROOT"
fi
if awk -v a="$nas_avail_gib" -v m="$MIN_NAS_GIB" 'BEGIN { exit !(a < m) }'; then
  refuse "NAS free ${nas_avail_gib} GiB < required ${MIN_NAS_GIB} GiB at $NAS_ROOT"
fi

CACHE_ROOT="$RESULTS_DIR/cache"           # local NVMe
TMP_ROOT="$RESULTS_DIR/tmp"               # local NVMe
NAS_DATASETS="$NAS_ROOT/datasets"
NAS_CKPT_ARCHIVE="$NAS_ROOT/checkpoints/$(basename "$RESULTS_DIR")"
mkdir -p "$CACHE_ROOT/hf" "$CACHE_ROOT/pip" "$TMP_ROOT" \
         "$NAS_DATASETS" "$NAS_CKPT_ARCHIVE"

# HF model/hub cache local (fast load); datasets cache on the NAS.
export HF_HOME="$CACHE_ROOT/hf"
export HF_HUB_CACHE="$CACHE_ROOT/hf/hub"
export HF_DATASETS_CACHE="$NAS_DATASETS"
export PIP_CACHE_DIR="$CACHE_ROOT/pip"
export TMPDIR="$TMP_ROOT"
# Consumed by the training scripts: where to rsync sealed checkpoints for archival.
export MEM_CKPT_ARCHIVE="$NAS_CKPT_ARCHIVE"

echo "launch_guard: local cache=$CACHE_ROOT tmp=$TMP_ROOT | NAS datasets=$NAS_DATASETS archive=$NAS_CKPT_ARCHIVE (free ${nas_avail_gib} GiB)" >&2

# ---- 2. RAM / swap gate ---------------------------------------------------
# A combined Track-A + Track-B run keeps both a training dataloader AND Harbor
# container test processes resident, so raise the floor (the documented OOM risk).
if [[ "$COMBINED" -eq 1 ]]; then
  if awk -v c="$MIN_RAM_GIB" -v d="$COMBINED_MIN_RAM_GIB" 'BEGIN { exit !(c < d) }'; then
    MIN_RAM_GIB="$COMBINED_MIN_RAM_GIB"
  fi
  echo "launch_guard: --combined: RAM floor raised to ${MIN_RAM_GIB} GiB (A+B share this box)" >&2
fi

# Free RAM = MemAvailable; free swap = SwapFree. Both in kB in /proc/meminfo.
mem_avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
swap_free_kb=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)
swap_total_kb=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)

mem_avail_gib=$(awk -v k="$mem_avail_kb" 'BEGIN { printf "%.2f", k/1024/1024 }')
swap_free_gib=$(awk -v k="$swap_free_kb" 'BEGIN { printf "%.2f", k/1024/1024 }')

if awk -v a="$mem_avail_gib" -v m="$MIN_RAM_GIB" 'BEGIN { exit !(a < m) }'; then
  refuse "free RAM ${mem_avail_gib} GiB < required ${MIN_RAM_GIB} GiB — ${RAM_RELIEF_HINT}"
fi

# Swap-exhausted is the documented default state of this box. If a swap device
# exists, enforce the floor; if there is genuinely no swap configured, warn but
# do not block on it (RAM gate above is then the real protection).
if [[ "${swap_total_kb:-0}" -gt 0 ]]; then
  if awk -v s="$swap_free_gib" -v m="$MIN_SWAP_GIB" 'BEGIN { exit !(s < m) }'; then
    refuse "free swap ${swap_free_gib} GiB < required ${MIN_SWAP_GIB} GiB (box swap is often exhausted) — ${RAM_RELIEF_HINT}"
  fi
else
  echo "launch_guard: WARNING no swap device configured; relying on RAM gate only" >&2
fi
echo "launch_guard: RAM ${mem_avail_gib} GiB / swap ${swap_free_gib} GiB OK" >&2

# ---- 3. GPU busy gate -----------------------------------------------------
if command -v nvidia-smi >/dev/null 2>&1; then
  # Sum used memory across all visible GPUs (single-card box, but be safe).
  gpu_used_mib=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits \
                  | awk '{ s += $1 } END { print s+0 }')
  if [[ "$gpu_used_mib" -gt "$GPU_BUDGET_MIB" ]]; then
    refuse "GPU already using ${gpu_used_mib} MiB > budget ${GPU_BUDGET_MIB} MiB (another workload may be using the GPU)"
  fi
  echo "launch_guard: GPU used ${gpu_used_mib} MiB <= budget ${GPU_BUDGET_MIB} MiB OK" >&2
else
  refuse "nvidia-smi not found; cannot verify GPU is free — refusing rather than guessing"
fi

# ---- 4. provenance snapshot (lockfile hash + git SHA + capability check) ---
PROV="$RESULTS_DIR/provenance.json"

lock_hash="absent"
if [[ -n "$LOCKFILE" && -f "$LOCKFILE" ]]; then
  lock_hash=$(sha256sum "$LOCKFILE" | awk '{print $1}')
elif [[ -n "$LOCKFILE" ]]; then
  refuse "lockfile $LOCKFILE not found (R1 requires a pinned lockfile)"
fi

git_sha="unknown"
git_dirty="unknown"
if [[ -n "$REPO" ]] && git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  git_sha=$(git -C "$REPO" rev-parse HEAD)
  if [[ -n "$(git -C "$REPO" status --porcelain)" ]]; then
    git_dirty="true"
  else
    git_dirty="false"
  fi
fi

# (12,0) Blackwell capability check (R1). Read-only torch import; refuse if not (12, 0).
# Prefer the env's `python` (the locked Blackwell venv), fall back to `python3`.
if command -v python >/dev/null 2>&1; then PY=python; else PY=python3; fi
cap_raw="$("$PY" -c 'import torch; print("%d %d" % torch.cuda.get_device_capability())' 2>/dev/null || echo "")"
if [[ "$cap_raw" != "12 0" ]]; then
  refuse "capability check failed: expected (12, 0), got '${cap_raw:-<import-failed>}' — wrong torch/CUDA build"
fi
echo "launch_guard: device capability (12, 0) confirmed" >&2

# Write provenance JSON (no jq dependency; this is a fixed, controlled shape).
{
  printf '{\n'
  printf '  "lockfile": "%s",\n' "${LOCKFILE:-}"
  printf '  "lockfile_sha256": "%s",\n' "$lock_hash"
  printf '  "git_repo": "%s",\n' "${REPO:-}"
  printf '  "git_sha": "%s",\n' "$git_sha"
  printf '  "git_dirty": "%s",\n' "$git_dirty"
  printf '  "device_capability": "12.0",\n'
  printf '  "ram_avail_gib": "%s",\n' "$mem_avail_gib"
  printf '  "swap_free_gib": "%s",\n' "$swap_free_gib"
  printf '  "gpu_used_mib_at_launch": "%s",\n' "${gpu_used_mib:-unknown}"
  printf '  "hf_home": "%s",\n' "$HF_HOME"
  printf '  "hf_datasets_cache": "%s",\n' "$HF_DATASETS_CACHE"
  printf '  "nas_ckpt_archive": "%s",\n' "$MEM_CKPT_ARCHIVE"
  printf '  "nas_free_gib": "%s",\n' "$nas_avail_gib"
  printf '  "combined_run": "%s",\n' "$COMBINED"
  printf '  "tmpdir": "%s",\n' "$TMPDIR"
  printf '  "launched_at_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '}\n'
} > "$PROV"
echo "launch_guard: provenance -> $PROV" >&2

# ---- 5. launch ------------------------------------------------------------
echo "launch_guard: all gates passed; launching: ${CMD[*]}" >&2
exec "${CMD[@]}"
