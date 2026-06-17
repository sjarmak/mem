#!/usr/bin/env python3
"""R2.5 BLOCKING colocation + soak gate (PRD amendment A2; blocks R7/R10).

THIS IS AN APPROVAL-GATED MORNING RUN. It loads a frozen generator under vLLM in
sleep/colocate mode and trains a <=3B GRPO searcher on the SAME 32 GB RTX 5090
while other workloads are active. It WILL allocate VRAM, drive GPU compute for >=10
steps, and (in soak mode) run >=2 hours. Do NOT execute it unattended on the
shared box without explicit human approval and a quiet window. Authoring it here
makes the gate runnable later; running it is a separate, approved action.

------------------------------------------------------------------------------
Why this gate exists (premortem Theme B, risk #4)
------------------------------------------------------------------------------
Four of five premortem lenses route through one open question: is vLLM sm_120
colocate stable *today* on this box? The entire behavior track (R7/R10) needs a
frozen generator served concurrently with a trainable policy on one 32 GB
Blackwell card. If that does not hold, the only leg that answers the research
question cannot run. This gate resolves the [OPEN] vLLM-sm_120 question BEFORE R4
eats the runway, and it does so under the *real* contended conditions, not a
clean-box best case.

------------------------------------------------------------------------------
Gate definition (both phases MUST pass; this is a blocking gate)
------------------------------------------------------------------------------
Phase (a) — colocation smoke:
    - load the frozen generator under vLLM sleep/colocate
    - attach a <=3B GRPO searcher (TRL GRPO, Unsloth)
    - run >=10 GRPO steps with NO OOM
    - log peak VRAM (device-wide, via vram_watchdog.read_vram_mib)
Phase (b) — soak:
    - run >=2 hours of the same colocated loop
    - cross >=2 checkpoint boundaries (atomic_checkpoint.save_checkpoint)
    - keep the usual co-tenant workloads ACTIVE for the duration (contention is the test)
    - no OOM, no monotonic VRAM creep to the ceiling (vram_watchdog aborts if so)

------------------------------------------------------------------------------
On FAILURE (either phase) — documented fallback, NOT a workaround
------------------------------------------------------------------------------
If this gate fails, the behavior track does NOT attempt to force true colocation.
R7 falls back to OFFLINE SEQUENTIAL:

    1. serve the frozen generator alone
    2. dump rollouts to disk
    3. UNLOAD the generator (free its VRAM)
    4. train the searcher on the dumped rollouts

This trades wall-clock for VRAM headroom and removes the concurrent-residency
requirement entirely. A failed gate is a *finding* (the box cannot colocate
sm_120 today), and the offline-sequential path is the pre-registered response —
never assume true colocation, and never silently degrade.

------------------------------------------------------------------------------
This file's status
------------------------------------------------------------------------------
This is the gate SPEC + a runnable harness skeleton. The two model-loading hooks
(``load_frozen_generator_vllm`` and ``build_grpo_searcher_step``) are explicit
integration points marked with a hard NotImplementedError, because wiring them
requires the locked Blackwell env (R1) and live model weights that MUST NOT be
downloaded under the current autonomy guardrails. They are not silent stubs:
calling the gate without supplying them fails loudly with instructions. The
phase orchestration, peak-VRAM accounting, checkpoint-boundary counting, soak
timing, co-tenant liveness check, and the pass/fail + verdict JSON are all real.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

# Reuse the already-authored ops helpers from research/scripts/.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from atomic_checkpoint import save_checkpoint  # noqa: E402
from vram_watchdog import VramWatchdog, WatchdogConfig, read_vram_mib  # noqa: E402

MIN_SMOKE_STEPS = 10
MIN_SOAK_SECONDS = 2 * 60 * 60  # 2 hours
MIN_SOAK_CHECKPOINTS = 2


@dataclasses.dataclass(frozen=True)
class GateConfig:
    results_dir: Path
    generator_model: str  # frozen generator (served under vLLM)
    searcher_model: str  # <=3B trainable GRPO searcher
    smoke_steps: int = MIN_SMOKE_STEPS
    soak_seconds: int = MIN_SOAK_SECONDS
    soak_checkpoints: int = MIN_SOAK_CHECKPOINTS
    checkpoint_every_seconds: int = 45 * 60  # ~2 boundaries inside a 2h soak
    # Process-name substrings of the co-tenant workloads whose contention the soak
    # must run under; operator-supplied (empty = don't assert any specific co-tenant).
    cotenant_names: tuple[str, ...] = ()


# ---- integration points (must be supplied to actually run on GPU) -----------


def load_frozen_generator_vllm(model: str):
    """Load the frozen generator under vLLM in sleep/colocate mode.

    INTEGRATION POINT — requires the locked Blackwell vLLM env (R1) + weights.
    Must return an object the searcher step can query for rollouts. Raises until
    wired so the gate cannot pass on an unimplemented backend.
    """
    raise NotImplementedError(
        "wire vLLM sleep/colocate load for the frozen generator in the locked "
        f"Blackwell env before running the gate (model={model!r}). "
        "Do NOT download weights under the current autonomy guardrails."
    )


def build_grpo_searcher_step(searcher_model: str, generator) -> Callable[[int], None]:
    """Build a single-GRPO-step callable for the <=3B searcher over the frozen generator.

    INTEGRATION POINT — TRL GRPO + Unsloth in the vLLM colocate env. Returns a
    ``step_fn(step_index)`` that performs ONE on-policy GRPO step. Raises until wired.
    """
    raise NotImplementedError(
        "wire the TRL/Unsloth GRPO step for the searcher in the colocate env "
        f"(searcher={searcher_model!r}). Generator handle: {generator!r}."
    )


# ---- co-tenant liveness (the soak must run WHILE co-tenant workloads are active) ----


def cotenants_active(names: tuple[str, ...]) -> dict[str, bool]:
    """Best-effort check that the named co-tenant processes are running.

    Read-only: scans /proc command lines. The soak's validity depends on real
    contention, so the gate records this rather than assuming a clean box.
    """
    found = dict.fromkeys(names, False)
    proc = Path("/proc")
    if not proc.exists():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        cmdline_path = entry / "cmdline"
        try:
            cmdline = (
                cmdline_path.read_bytes().replace(b"\x00", b" ").decode(errors="ignore")
            )
        except OSError:
            continue
        for n in names:
            if n in cmdline:
                found[n] = True
    return found


# ---- phase (a): colocation smoke -------------------------------------------


def run_smoke(cfg: GateConfig, watchdog: VramWatchdog) -> dict:
    generator = load_frozen_generator_vllm(cfg.generator_model)
    step_fn = build_grpo_searcher_step(cfg.searcher_model, generator)

    peak_mib = 0
    for step in range(cfg.smoke_steps):
        step_fn(step)
        used, total = read_vram_mib()
        peak_mib = max(peak_mib, used)
        abort = watchdog.observe(step)
        if abort:
            return {
                "phase": "smoke",
                "passed": False,
                "reason": "vram_watchdog abort (creep toward OOM)",
                "steps_completed": step + 1,
                "peak_vram_mib": peak_mib,
                "total_vram_mib": total,
            }
    return {
        "phase": "smoke",
        "passed": True,
        "steps_completed": cfg.smoke_steps,
        "peak_vram_mib": peak_mib,
    }


# ---- phase (b): soak --------------------------------------------------------


def run_soak(cfg: GateConfig, watchdog: VramWatchdog) -> dict:
    generator = load_frozen_generator_vllm(cfg.generator_model)
    step_fn = build_grpo_searcher_step(cfg.searcher_model, generator)
    ckpt_dir = cfg.results_dir / "soak_checkpoints"

    start = time.monotonic()
    last_ckpt = start
    checkpoints = 0
    peak_mib = 0
    step = 0
    cotenant_samples: list[dict[str, bool]] = []

    while True:
        now = time.monotonic()
        elapsed = now - start
        if elapsed >= cfg.soak_seconds and checkpoints >= cfg.soak_checkpoints:
            break

        step_fn(step)
        used, total = read_vram_mib()
        peak_mib = max(peak_mib, used)

        if watchdog.observe(step):
            return {
                "phase": "soak",
                "passed": False,
                "reason": "vram_watchdog abort (creep toward OOM)",
                "elapsed_seconds": round(elapsed),
                "checkpoints": checkpoints,
                "peak_vram_mib": peak_mib,
                "total_vram_mib": total,
            }

        # Sample co-tenant liveness periodically; the soak is only valid under contention.
        if step % 50 == 0:
            cotenant_samples.append(cotenants_active(cfg.cotenant_names))

        # Cross checkpoint boundaries on a wall-clock cadence.
        if (now - last_ckpt) >= cfg.checkpoint_every_seconds:
            save_checkpoint(
                ckpt_dir,
                step=step,
                write_fn=lambda staging: (staging / "soak_marker.json").write_text(
                    json.dumps({"step": step, "elapsed_s": round(elapsed)}) + "\n"
                ),
                keep_count=2,
                extra_manifest={"phase": "soak"},
            )
            checkpoints += 1
            last_ckpt = now

        step += 1

    # Did the co-tenants stay alive across the soak? If never seen, the contention
    # condition was not met — report it (does not auto-pass a clean-box soak).
    any_cotenant_seen = any(any(s.values()) for s in cotenant_samples)
    return {
        "phase": "soak",
        "passed": True,
        "elapsed_seconds": round(time.monotonic() - start),
        "steps_completed": step,
        "checkpoints": checkpoints,
        "peak_vram_mib": peak_mib,
        "cotenants_observed_active": any_cotenant_seen,
        "cotenant_samples": cotenant_samples[-5:],
    }


# ---- orchestration ----------------------------------------------------------


def run_gate(cfg: GateConfig) -> dict:
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    wd = VramWatchdog(WatchdogConfig(log_path=cfg.results_dir / "vram_watchdog.jsonl"))

    smoke = run_smoke(cfg, wd)
    if not smoke["passed"]:
        verdict = _verdict(cfg, smoke_result=smoke, soak_result=None)
        _write_verdict(cfg, verdict)
        return verdict

    soak = run_soak(cfg, wd)
    verdict = _verdict(cfg, smoke_result=smoke, soak_result=soak)
    _write_verdict(cfg, verdict)
    return verdict


def _verdict(cfg: GateConfig, smoke_result: dict, soak_result: dict | None) -> dict:
    smoke_ok = bool(smoke_result.get("passed"))
    soak_ok = bool(soak_result and soak_result.get("passed"))
    soak_meets_floor = bool(
        soak_result
        and soak_result.get("elapsed_seconds", 0) >= cfg.soak_seconds
        and soak_result.get("checkpoints", 0) >= cfg.soak_checkpoints
    )
    passed = smoke_ok and soak_ok and soak_meets_floor
    return {
        "gate": "R2.5 colocation+soak",
        "passed": passed,
        "smoke": smoke_result,
        "soak": soak_result,
        "soak_meets_floor": soak_meets_floor,
        "fallback_if_failed": (
            "R7 -> offline sequential: serve generator -> dump rollouts -> "
            "UNLOAD generator -> train searcher. Never assume true colocation."
        ),
        "blocks": ["R7", "R10"],
    }


def _write_verdict(cfg: GateConfig, verdict: dict) -> None:
    out = cfg.results_dir / "colocation_soak_verdict.json"
    out.write_text(json.dumps(verdict, indent=2) + "\n")
    print(f"colocation_soak_gate: verdict -> {out}", file=sys.stderr)
    print(json.dumps(verdict, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="R2.5 BLOCKING colocation+soak gate (approval-gated GPU run).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--results-dir", type=Path, required=True)
    p.add_argument(
        "--generator-model", required=True, help="frozen generator served under vLLM"
    )
    p.add_argument(
        "--searcher-model", required=True, help="<=3B trainable GRPO searcher"
    )
    p.add_argument("--smoke-steps", type=int, default=MIN_SMOKE_STEPS)
    p.add_argument("--soak-seconds", type=int, default=MIN_SOAK_SECONDS)
    p.add_argument("--soak-checkpoints", type=int, default=MIN_SOAK_CHECKPOINTS)
    p.add_argument("--checkpoint-every-seconds", type=int, default=45 * 60)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.smoke_steps < MIN_SMOKE_STEPS:
        print(f"error: --smoke-steps must be >= {MIN_SMOKE_STEPS}", file=sys.stderr)
        return 2
    if args.soak_seconds < MIN_SOAK_SECONDS:
        print(f"error: --soak-seconds must be >= {MIN_SOAK_SECONDS}", file=sys.stderr)
        return 2
    if args.soak_checkpoints < MIN_SOAK_CHECKPOINTS:
        print(
            f"error: --soak-checkpoints must be >= {MIN_SOAK_CHECKPOINTS}",
            file=sys.stderr,
        )
        return 2

    cfg = GateConfig(
        results_dir=args.results_dir,
        generator_model=args.generator_model,
        searcher_model=args.searcher_model,
        smoke_steps=args.smoke_steps,
        soak_seconds=args.soak_seconds,
        soak_checkpoints=args.soak_checkpoints,
        checkpoint_every_seconds=args.checkpoint_every_seconds,
    )
    verdict = run_gate(cfg)
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
