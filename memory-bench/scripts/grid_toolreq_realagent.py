#!/usr/bin/env python3
"""mem-rk41.3 / .3.1 — real-agent none/oracle/ours ceiling-at-scale over the
tool-requiring corpus.

The corpus-wide generalization of the mem-rk41.4 single-goal probe: it adapts every frozen
tool-requiring world (``membench.runner.toolreq_realagent``) onto the ``Write``-bridge and
runs the SAME arm loop + external scorer the probe uses (``realagent_probe.run_arm`` /
``score_goal_action``), across ``none`` (empty memory, a leak detector), ``oracle`` (the
id-exact ceiling), and ``ours`` (a genuine ``mem retrieve`` payload), under both
memory-trust channels. Values are OPAQUE on this path, so ``none`` can pass only on a
genuine leak — the verdict rule is the probe's, restored.

``ours`` (mem-rk41.3.1) seeds a fresh ``.mem/store.db`` from the SAME corpus, using
rk41.5's ``toolreq_bundle_adapter`` substrate (the shared value-free apply_config staleness
signature) plus lessons whose facts state each sequence's CURRENT opaque value — the SAME
opaque token space ``oracle`` surfaces. That shared value space is load-bearing: under a
different value-map, ``ours`` could never satisfy the scorer even if retrieval fired
correctly. Cross-task retrieval will generally NOT surface the queried task's own
sequence-unique opaque value, so ``ours`` scoring near ``none`` rather than near ``oracle``
is the expected, honest substrate finding, not a defect to work around.

This is the paid ceiling driver. It STAGES but never over-reaches:

* ``--dry-run`` runs the identical loop with a simulated memory-copying agent — no token,
  no ``claude`` — proving the arms separate end to end for FREE. Seeding the ``ours``
  store and resolving its payload are ALSO free (real ``mem`` CLI calls, no agent turn) and
  run on every invocation, dry-run or paid alike — only ``claude -p`` is spend-gated;
* a real run REFUSES to spend without ``CLAUDE_CODE_OAUTH_TOKEN`` and prints the exact
  ``scix-batch`` go-command + the run count / worst-case wall-clock, so the paid fire stays
  an explicit, cost-disclosed, per-action decision (Stephanie's call);
* per-task results persist to ``--out/<work_id>.json`` and are REUSED on re-run, so a
  token-expiry or OOM mid-sweep does not re-pay for finished tasks.

This file is the SHELL: argparse, repo-root path constants, and printing — nothing else. Every
measured step is a typed ``membench`` call it merely wires together: the grid core (arms, verdict,
fingerprints, and the resume cache whose every defect this file has shipped) is
``membench.runner.toolreq_grid``, and the ``ours``-store seeder + payload resolver are
``membench.runner.toolreq_realagent.seed_ours_store_and_resolve_payloads`` /
``membench.harbor.bundle_grid.resolve_payloads``. All of it is inside ``mypy --strict``,
which this file is not. Read the cache invariant in ``toolreq_grid``.

It does NOT run the ``builtin`` arm (mem-rk41.3.2): that needs a persistent native-memory
env, separately substantial.

    # FREE — prove the wiring over the whole corpus (still needs bin/mem built):
    uv run python scripts/grid_toolreq_realagent.py --corpus-dir fixtures/worlds-tool --dry-run

    # PAID — Stephanie's per-action go (wrap in scix-batch; the script prints the command):
    scix-batch -- env CLAUDE_CODE_OAUTH_TOKEN=... \
        uv run python scripts/grid_toolreq_realagent.py --corpus-dir fixtures/worlds-tool
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from membench.mem_cli import MemCliError
from membench.runner.headless_agent import (
    DEFAULT_TIMEOUT_S,
    REFUSE_UNPINNED_MODEL,
    HeadlessAgentError,
    a_paid_run_needs_a_model,
    resolve_model,
)
from membench.runner.toolreq_grid import (
    SUMMARY_NAME,
    remaining_tasks,
    run_corpus,
    worst_case_paid_call_count,
)
from membench.runner.toolreq_realagent import (
    ToolReqRealAgentTask,
    load_corpus_with_sequences,
    seed_ours_store_and_resolve_payloads,
)
from membench.schemas.sequence import BenchmarkSequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = PROJECT_ROOT / "memory-bench/fixtures/worlds-tool"
DEFAULT_OUT = PROJECT_ROOT / ".mem/toolreq-realagent"
DEFAULT_MEM_BIN = str(PROJECT_ROOT / "bin/mem")
ENV_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"

# The channel-non-uniform plan shape the disclosed paid-call count is FOR (see toolreq_grid): one
# string so the two spend disclosures below cannot describe the same fire two different ways.
_PAID_SHAPE = "none once + oracle,ours per channel"


# What a cache-derived count is conditional on, printed with the count and never without it. The
# fire is priced under an IDENTITY (resume_cache.RunIdentity), and the go-command below pins the
# half of it this script can pin — the model. (`--store` is pinned too, so the fire seeds where the
# operator meant; it is NOT an identity input, which is what licenses toolreq_grid.remaining_tasks
# pricing over a throwaway store at all.) The rest is named rather than pinned: an `npm -g` upgrade
# of the claude binary is a drift nobody performs on purpose (the argument is
# BaseRunIdentity.cli_version's), and a corpus regenerated under the same work_ids moves the seeded
# payload without moving anything visible from here.
_ASSUMES = (
    "  WHAT THAT COUNT ASSUMES: that the fire runs under the identity priced here. The command "
    "below pins --model for that reason. An upgraded `claude` (the binary is an identity field "
    "too) or a store seeded from a changed corpus re-measures cells counted as cached above, and "
    "spends up to the cold ceiling."
)


def _cannot_price(cause: str, out_dir: Path) -> str:
    """The disclosure's line for "this fire cannot be priced" — one shape for every diagnosis, so
    the fallback reads the same whichever half of the identity is missing.

    It says UPPER BOUND rather than a number, because that is what the cold ceiling below it then
    is: over-disclosing is the SAFE direction (the human is told the fire costs more than it does),
    and it is the direction this disclosure had for every resume before mem-u9nu2."""
    return (
        f"  COST OF THIS FIRE: UNKNOWN — {cause}. Pricing a resume means asking the cache what it "
        "would reuse, which needs the identity a paid cell is filed under: the claude binary and "
        "the seeded ours payload. Without both there is no honest answer, so the cold ceiling "
        f"below is an UPPER BOUND for a second reason — any task already cached in {out_dir} is "
        "reused and spends nothing."
    )


def _this_fire_costs(
    tasks: Sequence[ToolReqRealAgentTask],
    sequences: Sequence[BenchmarkSequence],
    repeats: int,
    out_dir: Path,
    model: str,
    mem_bin: str,
) -> str:
    """The disclosure's headline: what the fire being authorized ACTUALLY spends.

    The corpus ceiling is what a COLD ``--out`` spends. A resume measures only what is not already
    cached, so on a mostly-served ``--out`` that number describes work that will not be done, and
    the human authorizes real money against it (mem-u9nu2). ``remaining_tasks`` answers what this
    fire measures, and ``worst_case_paid_call_count`` — the same function the corpus ceiling is
    summed by — prices it. Still a CEILING per task (mem-fjfaf), now over the right tasks.

    NOT free of preconditions, unlike the builtin sibling's: this seeds a store and resolves
    payloads, because the payload rides in the cache identity and the miss set is unknowable without
    it (``toolreq_grid.remaining_tasks``). So the refuse path needs a built `mem` — no tokens, no
    agent, not instant. It does NOT touch the run's ``--store``: pricing is a read, and
    ``remaining_tasks`` seeds a throwaway precisely so this gate stays safe to run casually.

    The grid decides what remains; this decides what to SAY about it, including when it says there
    is no answer. TWO diagnoses are caught, narrowly and by type, and they are the two halves of the
    identity this fire would be priced under: ``HeadlessAgentError`` (the claude binary cannot be
    identified) and ``MemCliError`` (the `mem` CLI cannot seed the store, so the ours payload the
    identity carries cannot be resolved). Both mean the same thing to a human — the fire cannot be
    priced — so both print the cold ceiling, a strictly SAFE over-report, labeled as one and named.

    Each is reported by its MEANING and not by interpolating the exception, which for
    ``MemCliError`` is a subprocess dump that would bury the rest of this disclosure. Nothing is
    lost by that: a precondition of pricing the fire is a precondition of the fire, so an operator
    who fixes the cause and re-runs meets the same failure where it is reported in full — and one
    who fires anyway meets it before the first token is spent.

    Anything else propagates, in particular ``CachePlan.lookup``'s ``ValueError`` when an identity's
    fingerprint is not the plan's: that is a structural defect in the grid and must surface as
    itself, never as a cost estimate.

    ``_ASSUMES`` rides only with the branches that print a count; the UNKNOWN branch has none to
    caveat. ``resume_cache.pending_tasks`` argues the under-report direction that makes it
    load-bearing on the fully-cached branch too."""
    try:
        remaining = remaining_tasks(
            tasks,
            sequences,
            out_dir=out_dir,
            repeats=repeats,
            model=model,
            mem_bin=mem_bin,
            seed_fn=seed_ours_store_and_resolve_payloads,
        )
    except HeadlessAgentError as exc:
        return _cannot_price(f"the claude binary could not be identified ({exc})", out_dir)
    except MemCliError:
        return _cannot_price(
            f"the `mem` CLI at {mem_bin} could not seed the ours store — build it (npm run build "
            "at the repo root) and re-run; a fire would fail at the same point, before spending",
            out_dir,
        )
    if not remaining:
        return (
            f"  THIS FIRE SPENDS NOTHING: all {len(tasks)} task(s) are already cached in {out_dir} "
            "under this run's identity and would be REUSED. Re-read the existing results instead "
            f"of re-firing.\n{_ASSUMES}"
        )
    runs = worst_case_paid_call_count(remaining, repeats=repeats)
    hours = runs * DEFAULT_TIMEOUT_S / 3600.0
    cached = len(tasks) - len(remaining)
    return (
        f"  COST OF THIS FIRE: at most {runs} real `claude -p` run(s) over the {len(remaining)} of "
        f"{len(tasks)} task(s) that REMAIN; worst-case wall-clock ~{hours:.1f}h at the "
        f"{DEFAULT_TIMEOUT_S:.0f}s timeout.\n"
        f"    {cached} task(s) are already cached in {out_dir} and would be reused, spending "
        f"nothing.\n{_ASSUMES}"
    )


def _print_go_command(
    tasks: Sequence[ToolReqRealAgentTask],
    sequences: Sequence[BenchmarkSequence],
    repeats: int,
    out_dir: Path,
    corpus_dir: Path,
    model: str,
    store_path: Path,
    mem_bin: str,
) -> None:
    # DERIVED from the plan (worst_case_paid_call_count), never `n_tasks x arm x channel x repeat`:
    # `none` is measured ONCE per task (channel-invariant; mem-dg5fm), so a hardcoded
    # `len(ARMS) x len(CHANNELS)` would over-disclose the fire by `n_tasks x repeats` calls. The
    # grid is now channel-NON-uniform (RECALLED runs none+oracle+ours, TRUSTED runs oracle+ours),
    # so there is no honest single `x channel` factor to print — the total and shape ARE the gate.
    n_tasks = len(tasks)
    runs = worst_case_paid_call_count(tasks, repeats=repeats)
    # THE one rule, called — never a second copy of `model or MEMBENCH_AGENT_MODEL`
    # (headless_agent.resolve_model).
    # main() refuses an unpinned paid run BEFORE this prints, so this always names a model.
    resolved_model = resolve_model(model)
    print(
        f"REFUSING to spend: {ENV_OAUTH} is unset.\n"
        f"{_this_fire_costs(tasks, sequences, repeats, out_dir, model, mem_bin)}\n"
        f"  A COLD --out is at most {runs} real `claude -p` run(s) "
        f"({n_tasks} task x {repeats} repeat; {_PAID_SHAPE}). That is the whole corpus, not this "
        "fire.\n"
        f"  Per-task results persist to {out_dir} and are reused on re-run (resumable).\n"
        "  To fire (Stephanie's per-action go), source the token from an account home and "
        "wrap in scix-batch:\n\n"
        f"    scix-batch -- env {ENV_OAUTH}=... \\\n"
        f"        uv run python scripts/grid_toolreq_realagent.py --corpus-dir {corpus_dir} \\\n"
        f"            --out {out_dir} --repeats {repeats} --model {resolved_model} \\\n"
        f"            --store {store_path} --mem-bin {mem_bin}\n\n"
        "  Or prove the wiring for free first:\n"
        f"    uv run python scripts/grid_toolreq_realagent.py --corpus-dir {corpus_dir} --dry-run"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repeats", type=int, default=3, help="runs per (task, arm, channel)")
    parser.add_argument(
        "--model",
        default="",
        help="pins --model; else MEMBENCH_AGENT_MODEL; a paid run refuses if neither names one",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N tasks (smoke subset)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="simulate the agent; no token, no claude"
    )
    parser.add_argument("--mem-bin", default=DEFAULT_MEM_BIN, help="the built mem CLI binary")
    parser.add_argument(
        "--store", type=Path, default=None, help="ours store path (default <out>/store.db)"
    )
    args = parser.parse_args(argv)

    # `--repeats 0` runs zero agent turns per cell and persists six 0/0 rows, which
    # `task_verdict` then reads as a confident "KILL: oracle ceiling 0/0 — no separation" for a
    # task that was NEVER EVALUATED. Refuse it at the flag, with a message that says why;
    # `RunIdentity.repeats` / `CellOutcome.runs` (both >= 1) are the structural backstop.
    if args.repeats < 1:
        parser.error("--repeats must be >= 1; 0 evaluates nothing and fabricates a 0/0 verdict")

    corpus_dir = args.corpus_dir
    sequences, tasks = load_corpus_with_sequences(corpus_dir)
    if args.limit is not None:
        sequences = sequences[: args.limit]
        tasks = tasks[: args.limit]
    if not tasks:
        print(f"no tool-requiring tasks under {corpus_dir}", file=sys.stderr)
        return 1

    # The model gate comes FIRST, and not for tidiness: it fires regardless of the token, so the
    # other order printed a go-command telling the human to run a command that would immediately
    # refuse for a DIFFERENT reason. It is also what lets the disclosure exist at all — the model is
    # part of a paid cell's identity, so without one there is no identity to ask the cache what a
    # resume would reuse, and no honest way to price the fire (mem-u9nu2).
    if a_paid_run_needs_a_model(args.model, dry_run=args.dry_run):
        print(REFUSE_UNPINNED_MODEL)
        return 2

    # Resolved BEFORE the gate below, not after: the disclosure prices what a resume over THIS
    # store would reuse, and the ours payload it seeds rides in the cache identity. A store_path
    # computed after the gate would leave the disclosure pricing a different run than the fire.
    store_path = args.store if args.store is not None else args.out / "store.db"

    if not args.dry_run and not os.environ.get(ENV_OAUTH):
        _print_go_command(
            tasks,
            sequences,
            args.repeats,
            args.out,
            corpus_dir,
            args.model,
            store_path,
            args.mem_bin,
        )
        return 2

    mode = "DRY-RUN (simulated agent, no tokens)" if args.dry_run else "PAID real claude -p"
    print(
        f"toolreq real-agent sweep: {mode}; {len(tasks)} task(s) x {args.repeats} repeat; "
        f"up to {worst_case_paid_call_count(tasks, repeats=args.repeats)} `claude -p` call(s) "
        f"({_PAID_SHAPE})"
    )
    try:
        summary = run_corpus(
            tasks,
            sequences,
            out_dir=args.out,
            repeats=args.repeats,
            model=args.model,
            dry_run=args.dry_run,
            store_path=store_path,
            mem_bin=args.mem_bin,
            seed_fn=seed_ours_store_and_resolve_payloads,
        )
    except HeadlessAgentError as exc:
        # A rate-limited/flaky/timed-out `claude -p` mid-sweep gets a diagnosed halt, never a raw
        # traceback: finished tasks are already persisted, so resuming is cheap — but only if the
        # operator is told so. `run_corpus` also resolves the CLI version up front, so this arm
        # covers the halts that happen BEFORE any spend as well as the ones partway through.
        #
        # This message is byte-identical to the sibling driver's (`grid_toolreq_builtin.py`) by
        # hand, and nothing executable holds it that way: each driver's test pins the string on its
        # own, so an edit to one side stays green on the other. The precedent for fixing that is six
        # lines up in the import — `REFUSE_UNPINNED_MODEL` is the sibling paid-boundary string, and
        # it lives in `headless_agent` beside the error precisely so the drivers defer to one copy
        # rather than hand-copy it (mem-bzv2p). Doing the same to this one means editing a driver
        # this change never touched, and `mem-tx8pp` already reworks what these two grids share;
        # filed as mem-blagt rather than done drive-by. Until then this is a copy, not an invariant.
        print(
            f"SWEEP HALT: a real agent call failed mid-sweep ({exc}) — stopping instead "
            f"of spending into a broken link. Finished tasks are persisted under "
            f"{args.out} and are REUSED on re-run: re-run the same command to resume "
            "once the underlying failure (network, rate limit, CLI issue) is resolved.",
            file=sys.stderr,
        )
        return 3
    for record in summary["per_task"]:
        print(f"  {record['work_id']:<24} {record['verdict']}")
    summary_path = args.out / SUMMARY_NAME
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n{summary['separates_all_channels']}/{summary['n_tasks']} task(s) separate on both "
        f"channels; leaked={summary['leaked'] or 'none'}; summary -> {summary_path}"
    )
    if args.dry_run:
        print("(DRY-RUN proves arm wiring + scorer discriminate; real behaviour is the paid run.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
