"""membench CLI — run one sequence under 3 conditions, or emit Harbor tasks.

  membench run-sequence <fixture.json> [--out DIR] [--fs-dir DIR]
  membench gen-tasks    <fixture.json> --out DIR [--overwrite]

`run-sequence` exercises the full skeleton pipeline in-process with the
deterministic reference agent (no Docker / no paid API) and writes the comparison
report + per-trial OTel spans + ATIF exports. `gen-tasks` emits Harbor task dirs
for a real `harbor run` (paid Claude path).
"""

import argparse
import hashlib
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from membench.beads_ordering.corpus import build_frozen_corpus
from membench.beads_ordering.models import (
    BM25FConfig,
    ExperimentMode,
    FrozenCorpus,
    OrderingArm,
    OrderingRunResult,
)
from membench.beads_ordering.ranked_searching import enrich_with_ranked_searching
from membench.beads_ordering.report import write_report
from membench.beads_ordering.runner import (
    ARMS,
    PAGE_SIZES,
    corpus_digest,
    file_sha256,
    git_dirty,
    git_sha,
    run_agent_cell,
    seed_beads_workspace,
    validate_paid_run,
    validate_rank_truth,
    write_raw_results,
)
from membench.corpus import load_corpus, load_query_work
from membench.dataset import load_sequence
from membench.harbor.adapter import SequenceAdapter
from membench.harbor.env_recon import DEFAULT_RIG_REPOS
from membench.harbor.ftp_curate import (
    DEFAULT_BASE_IMAGE,
    curate_rig,
    load_linked_commits,
    rig_report,
)
from membench.mem_cli import run_mem_json
from membench.memory_systems import build_memory_system
from membench.memory_systems.base import MemorySystem
from membench.replay import run_replay
from membench.report import arm_vector
from membench.report.comparison import build_comparison
from membench.runner.conditions import run_sequence
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.memory_event import MemoryBackend
from membench.telemetry.atif import trace_to_atif
from membench.telemetry.otel_spans import replay_to_spans, trace_to_spans

# memory-bench/membench/cli.py -> memory-bench -> repo root -> bin/mem.
_DEFAULT_MEM_BIN = Path(__file__).resolve().parents[2] / "bin" / "mem"
_DEFAULT_ORDERING_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "beads_ordering" / "corpus.json"
)


def _default_experiment(dataset_id: str) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="skeleton-exp",
        agent=AgentConfig(agent_config_id="scripted-ref", runtime="scripted"),
        memory=MemoryConfig(
            memory_config_id="filesystem",
            system="filesystem",
            storage_backends=[MemoryBackend.FILESYSTEM],
            retrieval_strategy="exact_by_id",
        ),
        dataset_id=dataset_id,
    )


def _cmd_run_sequence(args: argparse.Namespace) -> int:
    seq = load_sequence(args.fixture)
    experiment = _default_experiment(seq.sequence_id)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fs_dir = Path(args.fs_dir) if args.fs_dir else out / "memory_store"

    run = run_sequence(seq, experiment, fs_base_dir=fs_dir)

    traces_dir = out / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    for trial in run.trials:
        (traces_dir / f"{trial.trial_id}.trace.json").write_text(
            trial.trace.model_dump_json(indent=2), encoding="utf-8"
        )
        (traces_dir / f"{trial.trial_id}.otel.json").write_text(
            json.dumps(trace_to_spans(trial.trace), indent=2), encoding="utf-8"
        )
        (traces_dir / f"{trial.trial_id}.atif.json").write_text(
            json.dumps(trace_to_atif(trial.trace), indent=2), encoding="utf-8"
        )

    report = build_comparison(run)
    (out / "report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    md = report.to_markdown()
    (out / "report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


def _build_arm(name: str, store: str, mem_bin: str, limit: int | None) -> MemorySystem:
    """Construct one arm by name. `ours` takes the store + CLI binding; the rest
    go through the factory unchanged (so an unknown name raises the factory's
    wired-arm-set ValueError — never a silent skip)."""
    if name == "ours":
        return build_memory_system("ours", store_path=store, mem_bin=mem_bin, limit=limit)
    return build_memory_system(name)


def _cmd_replay(args: argparse.Namespace) -> int:
    mem_bin = args.mem_bin or str(_DEFAULT_MEM_BIN)
    corpus = load_corpus(args.store, mem_bin=mem_bin)
    # The caller names the query work; the harness never curates the eval target.
    query = load_query_work(args.store, args.work_id, mem_bin=mem_bin)
    arm_names = [n.strip() for n in args.arms.split(",") if n.strip()]
    arms = [_build_arm(n, args.store, mem_bin, args.limit) for n in arm_names]

    run = run_replay(query, corpus, arms)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "replay_report.json").write_text(
        json.dumps(arm_vector.to_dict(run), indent=2), encoding="utf-8"
    )
    (out / "replay_spans.json").write_text(
        json.dumps(replay_to_spans(run), indent=2), encoding="utf-8"
    )
    md = arm_vector.to_markdown(run)
    (out / "replay_report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


def _cmd_gen_tasks(args: argparse.Namespace) -> int:
    seq = load_sequence(args.fixture)
    adapter = SequenceAdapter(seq, args.out, overwrite=args.overwrite)
    created = adapter.run()
    for d in created:
        print(d)
    print(f"\n{len(created)} Harbor task dirs written to {args.out}")
    return 0


def _resolve_landing_commits(args: argparse.Namespace) -> list[str]:
    """The landing SHAs to curate, by precedence: an explicit ``--commits`` debug
    override; else a ``--linked-json`` file from ``mem link-outcomes``; else
    shelling that command live. The acceptance path uses neither override -- it
    derives from the store via ``mem link-outcomes`` so the result is not handed
    in (no validation theater)."""
    if args.commits:
        return [sha.strip() for sha in args.commits.split(",") if sha.strip()]

    if args.linked_json:
        payload = json.loads(Path(args.linked_json).read_text(encoding="utf-8"))
        # A hand-supplied file may carry the full `mem --json` {ok,data,errors}
        # envelope or an already-unwrapped body; tolerate both.
        inner = payload.get("data", payload) if isinstance(payload, dict) else {}
    else:
        if not args.store:
            raise SystemExit(
                "curate-ftp needs --store (to derive links) or --linked-json/--commits"
            )
        mem_bin = args.mem_bin or str(_DEFAULT_MEM_BIN)
        # run_mem_json owns the envelope unwrap plus the missing-binary / timeout /
        # non-zero-exit / malformed-stdout failure ladder (raises MemCliError).
        inner = run_mem_json([mem_bin, "link-outcomes", args.rig, "--store", args.store])

    linkages = frozenset(n.strip() for n in args.linkages.split(",") if n.strip())
    return load_linked_commits(inner, linkages=linkages)


def _cmd_curate_ftp(args: argparse.Namespace) -> int:
    clone = DEFAULT_RIG_REPOS.get(args.rig)
    if clone is None:
        raise SystemExit(f"rig '{args.rig}' has no checkout in DEFAULT_RIG_REPOS")

    landing_shas = _resolve_landing_commits(args)
    results = curate_rig(
        args.rig,
        landing_shas,
        clone,
        base_image=args.base_image,
        worktree_root=Path(args.worktree_root),
    )
    report = rig_report(args.rig, results)
    payload = json.dumps(report, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        summary = cast(dict[str, int], report["summary"])
        print(
            f"wrote {out}: {summary['commits']} commits, "
            f"{summary['ftp_tests']} ftp ({summary['behavioral']} behavioral)"
        )
    else:
        print(payload)
    return 0


def _load_ordering_fixture(path: str) -> FrozenCorpus:
    return FrozenCorpus.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _require_beads_bin(value: str | None) -> str:
    if not value:
        raise SystemExit("pass --beads-bin or set BEADS_BIN to the experimental bd binary")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise SystemExit(f"experimental bd binary is not executable: {path}")
    return str(path)


def _parse_page_sizes(raw: str) -> list[int | str]:
    values: list[int | str] = []
    for item in (part.strip() for part in raw.split(",")):
        if item == "all":
            values.append(item)
        else:
            try:
                parsed = int(item)
            except ValueError as exc:
                raise SystemExit("page sizes must be positive integers or 'all'") from exc
            if parsed < 1:
                raise SystemExit("page sizes must be positive integers or 'all'")
            values.append(parsed)
    if not values:
        raise SystemExit("at least one page size is required")
    return values


def _parse_ordering_arms(raw: str) -> list[OrderingArm]:
    try:
        arms = [OrderingArm(part.strip()) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        choices = ", ".join(arm.value for arm in OrderingArm)
        raise SystemExit(f"ordering arms must be one of: {choices}") from exc
    if not arms:
        raise SystemExit("at least one ordering arm is required")
    return arms


def _cmd_beads_ordering_freeze(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists() and not args.overwrite:
        raise SystemExit(f"{out} exists; pass --overwrite to replace it")
    corpus = build_frozen_corpus(seed=args.seed)
    corpus = enrich_with_ranked_searching(
        corpus,
        artifact_repo=Path(args.structural_order_source).expanduser().resolve(),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(corpus.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote frozen corpus: {out} ({len(corpus.memories)} Memories, {len(corpus.tasks)} tasks)"
    )
    return 0


def _validated_ordering_inputs(
    args: argparse.Namespace,
) -> tuple[FrozenCorpus, str, Path, BM25FConfig, dict[str, object]]:
    corpus = _load_ordering_fixture(args.fixture)
    beads_bin = _require_beads_bin(args.beads_bin)
    workspace_root = Path(args.workspace_root).resolve()
    bm25f = BM25FConfig(
        key_weight=args.bm25f_key_weight,
        alias_weight=args.bm25f_alias_weight,
        title_weight=args.bm25f_title_weight,
        body_weight=args.bm25f_body_weight,
        k1=args.bm25f_k1,
        b=args.bm25f_b,
    )
    truth: dict[str, object] = {}
    selected_ids = set(args.task_ids.split(",")) if args.task_ids else None
    for size in sorted({task.corpus_size for task in corpus.tasks}):
        tasks = [
            task
            for task in corpus.tasks
            if task.corpus_size == size and (selected_ids is None or task.task_id in selected_ids)
        ]
        if not tasks:
            continue
        workspace = workspace_root / f"corpus-{size}"
        seed_beads_workspace(
            corpus=corpus, corpus_size=size, beads_bin=beads_bin, workspace=workspace
        )
        for task in tasks:
            ranks = validate_rank_truth(
                task=task, workspace=workspace, beads_bin=beads_bin, bm25f=bm25f
            )
            truth[task.task_id] = {
                "total_matched": ranks.total_matched,
                "candidate_digest": ranks.candidate_digest,
                "ranks": {
                    arm.value: dict(memory_ranks) for arm, memory_ranks in ranks.ranks.items()
                },
            }
    if selected_ids is not None and set(truth) != selected_ids:
        missing = sorted(selected_ids - set(truth))
        raise SystemExit(f"unknown task IDs: {', '.join(missing)}")
    return corpus, beads_bin, workspace_root, bm25f, truth


def _cmd_beads_ordering_validate(args: argparse.Namespace) -> int:
    corpus, beads_bin, _, bm25f, truth = _validated_ordering_inputs(args)
    payload = {
        "schema_version": corpus.schema_version,
        "fixture_digest": corpus_digest(corpus),
        "beads_bin": beads_bin,
        "bm25f": bm25f.model_dump(),
        "tasks": truth,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"validated {len(truth)} frozen tasks: {out}")
    return 0


def _cmd_beads_ordering_run(args: argparse.Namespace) -> int:
    corpus, beads_bin, workspace_root, bm25f, truth_payload = _validated_ordering_inputs(args)
    claude_credentials = (
        Path(args.claude_credentials).expanduser().resolve() if args.claude_credentials else None
    )
    model, cli_version = validate_paid_run(args.model, claude_credentials=claude_credentials)
    arms = _parse_ordering_arms(args.arms)
    page_sizes = _parse_page_sizes(args.page_sizes)
    mode = ExperimentMode(args.mode)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    mem_repo = Path(__file__).resolve().parents[2]
    beads_repo = Path(args.beads_repo).expanduser().resolve()
    mem_sha = git_sha(mem_repo)
    mem_dirty = git_dirty(mem_repo)
    beads_sha = git_sha(beads_repo)
    beads_dirty = git_dirty(beads_repo)
    beads_binary_digest = file_sha256(Path(beads_bin))
    selected_tasks = [task for task in corpus.tasks if task.task_id in truth_payload]
    cells = [
        (task, arm, page_size, repeat)
        for task in selected_tasks
        for arm in arms
        for page_size in page_sizes
        for repeat in range(args.repeats)
    ]
    random.Random(args.order_seed).shuffle(cells)
    prompt_digest = hashlib.sha256(b"beads-ordering-agent-protocol-v3").hexdigest()
    manifest = {
        "schema_version": 2,
        "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "mem_git_sha": mem_sha,
        "mem_git_dirty": mem_dirty,
        "beads_git_sha": beads_sha,
        "beads_git_dirty": beads_dirty,
        "beads_bin": beads_bin,
        "beads_bin_sha256": beads_binary_digest,
        "structural_order_source_git_sha": corpus.structural_order_source_git_sha,
        "fixture": str(Path(args.fixture).resolve()),
        "fixture_digest": corpus_digest(corpus),
        "bm25f": bm25f.model_dump(),
        "ordering_conventions": {
            "key": "case-sensitive canonical Memory key ascending",
            "navigation": "authored navigation_rank ascending, then canonical Memory ID",
            "indegree": "global query-independent indegree descending, then canonical Memory ID",
            "outdegree": "global query-independent outdegree descending, then canonical Memory ID",
            "pagerank": "global query-independent PageRank descending, then canonical Memory ID",
            "reverse-pagerank": (
                "global query-independent PageRank on reversed edges descending, then "
                "canonical Memory ID"
            ),
            "hits-authority": (
                "global query-independent HITS authority descending, then canonical Memory ID"
            ),
            "hits-hub": "global query-independent HITS hub descending, then canonical Memory ID",
            "bm25f": "global BM25F score over the same literal-match set, then canonical Memory ID",
        },
        "page_sizes": [str(value) for value in page_sizes],
        "arms": [arm.value for arm in arms],
        "mode": mode.value,
        "repeats": args.repeats,
        "order_seed": args.order_seed,
        "max_tool_calls": args.max_tool_calls,
        "agent_model": model,
        "agent_cli_version": cli_version,
        "agent_settings": {"autoMemoryEnabled": False},
        "agent_auth": (
            "oauth-environment" if claude_credentials is None else "copied-oauth-credentials"
        ),
        "prompt_protocol_digest": prompt_digest,
        "tasks": truth_payload,
    }
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        comparable = {key: value for key, value in manifest.items() if key != "started_at"}
        previous_comparable = {key: value for key, value in previous.items() if key != "started_at"}
        if comparable != previous_comparable:
            raise SystemExit(f"{out} contains a run with a different manifest")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    rows: list[OrderingRunResult] = []
    for index, (task, arm, page_size, repeat) in enumerate(cells, start=1):
        run_id = f"{task.task_id}:{mode.value}:{arm.value}:p{page_size}:r{repeat}"
        result_path = out / "runs" / run_id.replace(":", "__") / "result.json"
        if result_path.exists():
            row = OrderingRunResult.model_validate_json(result_path.read_text(encoding="utf-8"))
            if (
                row.mem_git_sha != mem_sha
                or row.mem_git_dirty != mem_dirty
                or row.beads_git_sha != beads_sha
                or row.beads_git_dirty != beads_dirty
                or row.beads_bin_sha256 != beads_binary_digest
                or row.structural_order_source_git_sha != corpus.structural_order_source_git_sha
                or row.agent_model != model
                or row.agent_cli_version != cli_version
            ):
                raise SystemExit(f"cached run identity mismatch: {run_id}")
        else:
            raw_truth = truth_payload[task.task_id]
            if not isinstance(raw_truth, dict):
                raise SystemExit(f"invalid rank truth for {task.task_id}")
            from membench.beads_ordering.runner import RankTruth

            rank_truth = RankTruth(
                total_matched=int(raw_truth["total_matched"]),
                candidate_digest=str(raw_truth["candidate_digest"]),
                ranks={
                    OrderingArm(arm_name): {
                        str(memory_id): int(rank) for memory_id, rank in memory_ranks.items()
                    }
                    for arm_name, memory_ranks in raw_truth["ranks"].items()
                },
            )
            print(f"[{index}/{len(cells)}] {run_id}", flush=True)
            row = run_agent_cell(
                task=task,
                arm=arm,
                page_size=page_size,
                mode=mode,
                repeat=repeat,
                workspace=workspace_root / f"corpus-{task.corpus_size}",
                beads_bin=beads_bin,
                bm25f=bm25f,
                rank_truth=rank_truth,
                model=model,
                cli_version=cli_version,
                mem_sha=mem_sha,
                mem_dirty=mem_dirty,
                beads_sha=beads_sha,
                beads_dirty=beads_dirty,
                beads_bin_sha256=beads_binary_digest,
                structural_order_source_git_sha=corpus.structural_order_source_git_sha,
                artifacts_dir=out,
                claude_credentials=claude_credentials,
                max_tool_calls=args.max_tool_calls,
            )
        rows.append(row)
        write_raw_results(out / "raw-results.jsonl", rows)
    write_report(rows, out)
    print(f"wrote {len(rows)} runs and analysis to {out}")
    return 0


def _cmd_beads_ordering_analyze(args: argparse.Namespace) -> int:
    rows = [
        OrderingRunResult.model_validate_json(line)
        for raw_path in args.raw
        for line in Path(raw_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    write_report(rows, Path(args.out))
    print(f"analyzed {len(rows)} runs: {args.out}")
    return 0


def _add_bm25f_flags(parser: argparse.ArgumentParser) -> None:
    defaults = BM25FConfig()
    parser.add_argument("--bm25f-key-weight", type=float, default=defaults.key_weight)
    parser.add_argument("--bm25f-alias-weight", type=float, default=defaults.alias_weight)
    parser.add_argument("--bm25f-title-weight", type=float, default=defaults.title_weight)
    parser.add_argument("--bm25f-body-weight", type=float, default=defaults.body_weight)
    parser.add_argument("--bm25f-k1", type=float, default=defaults.k1)
    parser.add_argument("--bm25f-b", type=float, default=defaults.b)


def _add_ordering_input_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fixture", default=str(_DEFAULT_ORDERING_FIXTURE))
    parser.add_argument("--beads-bin", default=os.environ.get("BEADS_BIN"))
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--task-ids", default="", help="optional comma-separated task IDs")
    _add_bm25f_flags(parser)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="membench")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-sequence", help="run one sequence under 3 conditions")
    p_run.add_argument("fixture", help="path to a benchmark-sequence JSON fixture")
    p_run.add_argument("--out", default="reports", help="output dir (default: reports/)")
    p_run.add_argument("--fs-dir", default=None, help="filesystem-memory store dir")
    p_run.set_defaults(func=_cmd_run_sequence)

    p_replay = sub.add_parser(
        "replay", help="replay arms over a loaded P1.5 store under the LOO guard"
    )
    p_replay.add_argument("--store", required=True, help="path to the P1.5 SQLite store")
    p_replay.add_argument(
        "--work-id", required=True, dest="work_id", help="the query work to replay (caller-chosen)"
    )
    p_replay.add_argument(
        "--arms", default="none,ours", help="comma-separated arms (default: none,ours)"
    )
    p_replay.add_argument("--mem-bin", default=None, dest="mem_bin", help="path to the mem CLI")
    p_replay.add_argument(
        "--limit", type=int, default=None, help="max items the `ours` arm returns"
    )
    p_replay.add_argument("--out", default="reports", help="output dir (default: reports/)")
    p_replay.set_defaults(func=_cmd_replay)

    p_gen = sub.add_parser("gen-tasks", help="emit Harbor task dirs for `harbor run`")
    p_gen.add_argument("fixture", help="path to a benchmark-sequence JSON fixture")
    p_gen.add_argument("--out", required=True, help="output dir for task dirs")
    p_gen.add_argument("--overwrite", action="store_true")
    p_gen.set_defaults(func=_cmd_gen_tasks)

    p_ftp = sub.add_parser(
        "curate-ftp", help="curate a rig's fail-to-pass oracle from its landing commits"
    )
    p_ftp.add_argument("rig", help="rig name (must have a checkout in DEFAULT_RIG_REPOS)")
    p_ftp.add_argument("--store", default=None, help="P1.5 store to derive links from")
    p_ftp.add_argument(
        "--linked-json", default=None, dest="linked_json", help="a `mem link-outcomes` JSON file"
    )
    p_ftp.add_argument(
        "--commits",
        default=None,
        help="comma-separated landing SHAs (DEBUG override; bypasses derivation)",
    )
    p_ftp.add_argument(
        "--linkages",
        default="canonical",
        help="comma-separated linkage confidences to keep (default: canonical)",
    )
    p_ftp.add_argument("--base-image", default=DEFAULT_BASE_IMAGE, dest="base_image")
    p_ftp.add_argument("--worktree-root", default="/tmp", dest="worktree_root")
    p_ftp.add_argument("--mem-bin", default=None, dest="mem_bin", help="path to the mem CLI")
    p_ftp.add_argument("--out", default=None, help="write the oracle JSON here (else stdout)")
    p_ftp.set_defaults(func=_cmd_curate_ftp)

    p_freeze = sub.add_parser(
        "beads-ordering-freeze", help="write the frozen Beads ordering corpus and labels"
    )
    p_freeze.add_argument("--out", default=str(_DEFAULT_ORDERING_FIXTURE))
    p_freeze.add_argument("--seed", type=int, default=5877)
    p_freeze.add_argument(
        "--structural-order-source",
        required=True,
        help="checkout containing 29-ranked-searching used to materialize structural priors",
    )
    p_freeze.add_argument("--overwrite", action="store_true")
    p_freeze.set_defaults(func=_cmd_beads_ordering_freeze)

    p_validate = sub.add_parser(
        "beads-ordering-validate", help="seed Beads and verify fixed candidate-set parity"
    )
    _add_ordering_input_flags(p_validate)
    p_validate.add_argument("--out", required=True)
    p_validate.set_defaults(func=_cmd_beads_ordering_validate)

    p_ordering_run = sub.add_parser(
        "beads-ordering-run", help="run the ranked-pagination agent experiment"
    )
    _add_ordering_input_flags(p_ordering_run)
    p_ordering_run.add_argument("--beads-repo", required=True)
    p_ordering_run.add_argument("--model", required=True)
    p_ordering_run.add_argument(
        "--claude-credentials",
        default="",
        help="optional OAuth credentials file copied into each neutral agent config",
    )
    p_ordering_run.add_argument("--arms", default=",".join(arm.value for arm in ARMS))
    p_ordering_run.add_argument("--page-sizes", default=",".join(str(size) for size in PAGE_SIZES))
    p_ordering_run.add_argument(
        "--mode", choices=[mode.value for mode in ExperimentMode], default="search-only"
    )
    p_ordering_run.add_argument("--repeats", type=int, default=1)
    p_ordering_run.add_argument("--order-seed", type=int, default=5877)
    p_ordering_run.add_argument("--max-tool-calls", type=int, default=12)
    p_ordering_run.add_argument("--out", required=True)
    p_ordering_run.set_defaults(func=_cmd_beads_ordering_run)

    p_ordering_analysis = sub.add_parser(
        "beads-ordering-analyze", help="regenerate tables and plots from raw ordering runs"
    )
    p_ordering_analysis.add_argument("--raw", required=True, nargs="+")
    p_ordering_analysis.add_argument("--out", required=True)
    p_ordering_analysis.set_defaults(func=_cmd_beads_ordering_analyze)

    args = parser.parse_args(argv)
    return cast(int, args.func(args))


if __name__ == "__main__":
    sys.exit(main())
