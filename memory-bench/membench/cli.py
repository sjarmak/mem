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
from membench.beads_ordering.density_linkage import (
    load_density_linkage_manifest,
    write_density_linkage_manifest,
)
from membench.beads_ordering.density_linkage_agent import (
    run_density_linkage_agent_shard,
)
from membench.beads_ordering.density_linkage_evidence import (
    collect_density_linkage_oracle,
    write_density_linkage_oracle,
)
from membench.beads_ordering.followup_corpus import (
    load_followup_corpora,
    write_followup_corpora,
)
from membench.beads_ordering.followup_evidence import (
    collect_oracle_evidence,
    write_agent_followup_evidence,
    write_oracle_evidence,
)
from membench.beads_ordering.models import (
    BM25FConfig,
    ExperimentMode,
    FrozenCorpus,
    OrderingArm,
    OrderingRunResult,
    OrderingTask,
    TaskSplit,
)
from membench.beads_ordering.mutation import (
    benchmark_rank_scaling,
    run_mutation_experiment,
    write_mutation_experiment,
    write_rank_scaling,
)
from membench.beads_ordering.ranked_searching import enrich_with_ranked_searching
from membench.beads_ordering.report import write_report
from membench.beads_ordering.runner import (
    ARMS,
    CONTROL_ARMS,
    PAGE_SIZES,
    corpus_digest,
    file_sha256,
    git_diff_sha256,
    git_dirty,
    git_sha,
    run_agent_cell,
    seed_beads_workspace,
    task_workspace,
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
_DEFAULT_ORDERING_FOLLOWUP_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "beads_ordering" / "followup"
)
_DEFAULT_ORDERING_FOLLOWUP_PREREGISTRATION = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "beads_ordering"
    / "structural-followup-preregistration.json"
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


def _repeat_indices(*, start: int, count: int) -> range:
    if start < 0:
        raise ValueError("repeat start must be non-negative")
    if count <= 0:
        raise ValueError("repeat count must be positive")
    return range(start, start + count)


def _select_ordering_tasks(
    corpus: FrozenCorpus, *, task_ids_raw: str, split_raw: str
) -> list[OrderingTask]:
    selected_ids = {part.strip() for part in task_ids_raw.split(",") if part.strip()}
    known_ids = {task.task_id for task in corpus.tasks}
    unknown = sorted(selected_ids - known_ids)
    if unknown:
        raise SystemExit(f"unknown task IDs: {', '.join(unknown)}")
    split = None if split_raw == "all" else TaskSplit(split_raw)
    selected = [
        task
        for task in corpus.tasks
        if (not selected_ids or task.task_id in selected_ids)
        and (split is None or task.split is split)
    ]
    if selected_ids and len(selected) != len(selected_ids):
        mismatched = sorted(selected_ids - {task.task_id for task in selected})
        raise SystemExit(
            f"task ID selection does not match task split {split_raw}: {', '.join(mismatched)}"
        )
    if not selected:
        raise SystemExit(f"no ordering tasks match task split {split_raw}")
    return selected


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


def _cmd_beads_ordering_followup_freeze(args: argparse.Namespace) -> int:
    manifest = write_followup_corpora(
        Path(args.out),
        artifact_repo=Path(args.structural_order_source).expanduser().resolve(),
        seed=args.seed,
        overwrite=args.overwrite,
    )
    print(
        "wrote structural follow-up suite: "
        f"{args.out} ({manifest['family_count']} families, "
        f"{manifest['heldout_task_count']} held-out tasks)"
    )
    return 0


def _cmd_beads_ordering_density_linkage_freeze(args: argparse.Namespace) -> int:
    manifest = write_density_linkage_manifest(
        Path(args.fixture_dir).resolve(),
        Path(args.out).resolve(),
        preregistration=Path(args.preregistration).resolve(),
        overwrite=args.overwrite,
    )
    print(
        "wrote density/linkage manifest: "
        f"{args.out} ({manifest['base_task_count']} base tasks, "
        f"{manifest['variant_count']} variants)"
    )
    return 0


def _cmd_beads_ordering_density_linkage_materialize(args: argparse.Namespace) -> int:
    variants = load_density_linkage_manifest(
        Path(args.fixture_dir).resolve(), Path(args.manifest).resolve()
    )
    try:
        variant = variants[args.variant_id]
    except KeyError as exc:
        raise SystemExit(f"unknown density/linkage variant: {args.variant_id}") from exc
    out = Path(args.out).resolve()
    if out.exists() and not args.overwrite:
        raise SystemExit(f"{out} exists; pass --overwrite to replace it")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(variant.corpus.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"wrote density/linkage fixture: {out} ({args.variant_id})")
    return 0


def _cmd_beads_ordering_density_linkage_oracle(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir).resolve()
    manifest_path = Path(args.manifest).resolve()
    variants = load_density_linkage_manifest(fixture_dir, manifest_path)
    arms = _parse_ordering_arms(args.arms)
    page_sizes = _parse_page_sizes(args.page_sizes)
    beads_bin = _require_beads_bin(args.beads_bin)
    bm25f = BM25FConfig(
        key_weight=args.bm25f_key_weight,
        alias_weight=args.bm25f_alias_weight,
        title_weight=args.bm25f_title_weight,
        body_weight=args.bm25f_body_weight,
        k1=args.bm25f_k1,
        b=args.bm25f_b,
    )
    rows = collect_density_linkage_oracle(
        variants=variants,
        workspace_root=Path(args.workspace_root).resolve(),
        beads_bin=beads_bin,
        arms=arms,
        page_sizes=page_sizes,
        bm25f=bm25f,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mem_repo = Path(__file__).resolve().parents[2]
    beads_repo = Path(args.beads_repo).expanduser().resolve()
    package = Path(__file__).resolve().parent / "beads_ordering"
    provenance: dict[str, object] = {
        "mem_git_sha": git_sha(mem_repo),
        "mem_git_dirty": git_dirty(mem_repo),
        "mem_git_diff_sha256": git_diff_sha256(mem_repo),
        "beads_git_sha": git_sha(beads_repo),
        "beads_git_dirty": git_dirty(beads_repo),
        "beads_git_diff_sha256": git_diff_sha256(beads_repo),
        "beads_bin": beads_bin,
        "beads_bin_sha256": file_sha256(Path(beads_bin)),
        "structural_order_source_git_sha": manifest["structural_order_source_git_sha"],
        "base_fixture_manifest_sha256": file_sha256(fixture_dir / "manifest.json"),
        "density_linkage_manifest_sha256": file_sha256(manifest_path),
        "preregistration_sha256": manifest["preregistration_sha256"],
        "density_linkage_source_sha256": _source_bundle_sha256(
            [
                package / "density_linkage.py",
                package / "density_linkage_evidence.py",
                package / "models.py",
                Path(__file__).resolve(),
            ]
        ),
        "arms": [arm.value for arm in arms],
        "page_sizes": [str(page_size) for page_size in page_sizes],
        "bm25f": bm25f.model_dump(),
    }
    evidence_manifest = write_density_linkage_oracle(rows, Path(args.out), provenance=provenance)
    print(f"wrote density/linkage oracle: {args.out} " f"({evidence_manifest['row_count']} cells)")
    return 0


def _cmd_beads_ordering_density_linkage_run(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir).resolve()
    manifest_path = Path(args.manifest).resolve()
    variants = load_density_linkage_manifest(fixture_dir, manifest_path)
    arms = _parse_ordering_arms(args.arms)
    page_sizes = _parse_page_sizes(args.page_sizes)
    try:
        modes = tuple(
            ExperimentMode(value.strip()) for value in args.modes.split(",") if value.strip()
        )
    except ValueError as exc:
        raise SystemExit("modes must be comma-separated experiment modes") from exc
    if not modes:
        raise SystemExit("at least one experiment mode is required")
    beads_bin = _require_beads_bin(args.beads_bin)
    bm25f = BM25FConfig(
        key_weight=args.bm25f_key_weight,
        alias_weight=args.bm25f_alias_weight,
        title_weight=args.bm25f_title_weight,
        body_weight=args.bm25f_body_weight,
        k1=args.bm25f_k1,
        b=args.bm25f_b,
    )
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    credentials = (
        Path(args.claude_credentials).expanduser().resolve() if args.claude_credentials else None
    )
    suite_provenance: dict[str, object] = {
        "base_fixture_manifest_sha256": file_sha256(fixture_dir / "manifest.json"),
        "density_linkage_manifest_sha256": file_sha256(manifest_path),
        "preregistration_sha256": raw_manifest["preregistration_sha256"],
        "agent_sharding_amendment_sha256": file_sha256(
            Path(args.agent_sharding_amendment).resolve()
        ),
    }
    manifest = run_density_linkage_agent_shard(
        variants=variants,
        workspace_root=Path(args.workspace_root).resolve(),
        beads_bin=beads_bin,
        beads_repo=Path(args.beads_repo).expanduser().resolve(),
        mem_repo=Path(__file__).resolve().parents[2],
        arms=arms,
        page_sizes=page_sizes,
        modes=modes,
        repeats=args.repeats,
        repeat_start=args.repeat_start,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        order_seed=args.order_seed,
        bm25f=bm25f,
        model=args.model,
        claude_credentials=credentials,
        max_tool_calls=args.max_tool_calls,
        out=Path(args.out).resolve(),
        suite_provenance=suite_provenance,
    )
    print(
        f"completed density/linkage agent shard {args.shard_index}/{args.shard_count}: "
        f"{manifest['planned_cell_count']} cells"
    )
    return 0


def _parse_corpus_sizes(raw: str) -> tuple[int, ...]:
    try:
        sizes = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise SystemExit("corpus sizes must be comma-separated positive integers") from exc
    if not sizes or any(size < 1 for size in sizes):
        raise SystemExit("corpus sizes must be comma-separated positive integers")
    return tuple(dict.fromkeys(sizes))


def _source_bundle_sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _cmd_beads_ordering_followup_mutations(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir).resolve()
    corpora = load_followup_corpora(fixture_dir)
    sizes = _parse_corpus_sizes(args.sizes)
    rows = run_mutation_experiment(
        corpora,
        sizes=sizes,
        event_count=args.event_count,
        seed=args.seed,
        page_size=args.page_size,
    )
    mem_repo = Path(__file__).resolve().parents[2]
    beads_repo = Path(args.beads_repo).expanduser().resolve()
    beads_bin = Path(_require_beads_bin(args.beads_bin))
    source_shas = {corpus.structural_order_source_git_sha for corpus in corpora.values()}
    if len(source_shas) != 1 or not next(iter(source_shas)):
        raise SystemExit("follow-up fixtures do not share one structural-order source SHA")
    preregistration = Path(args.preregistration).resolve()
    ordering_package = Path(__file__).resolve().parent / "beads_ordering"
    provenance: dict[str, object] = {
        "mem_git_sha": git_sha(mem_repo),
        "mem_git_dirty": git_dirty(mem_repo),
        "mem_git_diff_sha256": git_diff_sha256(mem_repo),
        "beads_git_sha": git_sha(beads_repo),
        "beads_git_dirty": git_dirty(beads_repo),
        "beads_git_diff_sha256": git_diff_sha256(beads_repo),
        "beads_bin": str(beads_bin),
        "beads_bin_sha256": file_sha256(beads_bin),
        "structural_order_source_git_sha": next(iter(source_shas)),
        "fixture_manifest_sha256": file_sha256(fixture_dir / "manifest.json"),
        "preregistration_sha256": file_sha256(preregistration),
        "followup_source_sha256": _source_bundle_sha256(
            [
                ordering_package / "followup_corpus.py",
                ordering_package / "models.py",
                ordering_package / "mutation.py",
                Path(__file__).resolve(),
                preregistration,
            ]
        ),
    }
    manifest = write_mutation_experiment(
        rows,
        Path(args.out),
        provenance=provenance,
        seed=args.seed,
        event_count=args.event_count,
    )
    print(f"wrote mutation replay: {args.out} " f"({manifest['row_count']} task-policy snapshots)")
    return 0


def _cmd_beads_ordering_followup_rank_scaling(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir).resolve()
    corpora = load_followup_corpora(fixture_dir)
    sizes = _parse_corpus_sizes(args.sizes)
    rows = benchmark_rank_scaling(
        corpora,
        sizes=sizes,
        repeats=args.repeats,
    )
    mem_repo = Path(__file__).resolve().parents[2]
    beads_repo = Path(args.beads_repo).expanduser().resolve()
    beads_bin = Path(_require_beads_bin(args.beads_bin))
    source_shas = {corpus.structural_order_source_git_sha for corpus in corpora.values()}
    if len(source_shas) != 1 or not next(iter(source_shas)):
        raise SystemExit("follow-up fixtures do not share one structural-order source SHA")
    provenance: dict[str, object] = {
        "mem_git_sha": git_sha(mem_repo),
        "mem_git_dirty": git_dirty(mem_repo),
        "mem_git_diff_sha256": git_diff_sha256(mem_repo),
        "beads_git_sha": git_sha(beads_repo),
        "beads_git_dirty": git_dirty(beads_repo),
        "beads_git_diff_sha256": git_diff_sha256(beads_repo),
        "beads_bin": str(beads_bin),
        "beads_bin_sha256": file_sha256(beads_bin),
        "structural_order_source_git_sha": next(iter(source_shas)),
        "fixture_manifest_sha256": file_sha256(fixture_dir / "manifest.json"),
        "followup_source_sha256": _source_bundle_sha256(
            [
                Path(__file__).resolve().parent / "beads_ordering" / "followup_corpus.py",
                Path(__file__).resolve().parent / "beads_ordering" / "models.py",
                Path(__file__).resolve().parent / "beads_ordering" / "mutation.py",
                Path(__file__).resolve(),
            ]
        ),
    }
    manifest = write_rank_scaling(rows, Path(args.out), provenance=provenance)
    print(f"wrote rank scaling: {args.out} ({manifest['row_count']} measurements)")
    return 0


def _cmd_beads_ordering_followup_oracle(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir).resolve()
    validation_dir = Path(args.validation_dir).resolve()
    workspace_root = Path(args.workspace_root).resolve()
    corpora = load_followup_corpora(fixture_dir)
    arms = _parse_ordering_arms(args.arms)
    page_sizes = _parse_page_sizes(args.page_sizes)
    beads_bin = _require_beads_bin(args.beads_bin)
    bm25f = BM25FConfig(
        key_weight=args.bm25f_key_weight,
        alias_weight=args.bm25f_alias_weight,
        title_weight=args.bm25f_title_weight,
        body_weight=args.bm25f_body_weight,
        k1=args.bm25f_k1,
        b=args.bm25f_b,
    )
    rows = collect_oracle_evidence(
        corpora=corpora,
        validation_dir=validation_dir,
        workspace_root=workspace_root,
        beads_bin=beads_bin,
        arms=arms,
        page_sizes=page_sizes,
        bm25f=bm25f,
    )
    mem_repo = Path(__file__).resolve().parents[2]
    beads_repo = Path(args.beads_repo).expanduser().resolve()
    source_shas = {corpus.structural_order_source_git_sha for corpus in corpora.values()}
    if len(source_shas) != 1 or not next(iter(source_shas)):
        raise SystemExit("follow-up fixtures do not share one structural-order source SHA")
    validation_paths = sorted(validation_dir.glob("*.json"))
    provenance: dict[str, object] = {
        "mem_git_sha": git_sha(mem_repo),
        "mem_git_dirty": git_dirty(mem_repo),
        "mem_git_diff_sha256": git_diff_sha256(mem_repo),
        "beads_git_sha": git_sha(beads_repo),
        "beads_git_dirty": git_dirty(beads_repo),
        "beads_git_diff_sha256": git_diff_sha256(beads_repo),
        "beads_bin": beads_bin,
        "beads_bin_sha256": file_sha256(Path(beads_bin)),
        "structural_order_source_git_sha": next(iter(source_shas)),
        "fixture_manifest_sha256": file_sha256(fixture_dir / "manifest.json"),
        "validation_bundle_sha256": _source_bundle_sha256(validation_paths),
        "arms": [arm.value for arm in arms],
        "page_sizes": [str(value) for value in page_sizes],
        "bm25f": bm25f.model_dump(),
        "agent_outcomes": "collected separately in the authenticated follow-up agent grid",
    }
    manifest = write_oracle_evidence(rows, Path(args.out), provenance=provenance)
    print(f"wrote follow-up oracle evidence: {args.out} ({manifest['row_count']} cells)")
    return 0


def _cmd_beads_ordering_followup_agent_analyze(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir).resolve()
    corpora = load_followup_corpora(fixture_dir)
    rows = [
        OrderingRunResult.model_validate_json(line)
        for raw_path in args.raw
        for line in Path(raw_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len({row.run_id for row in rows}) != len(rows):
        raise SystemExit("follow-up agent inputs contain duplicate run IDs")
    task_metadata = {
        task.task_id: {
            "graph_family": task.graph_family,
            "failure_case": task.failure_case,
        }
        for corpus in corpora.values()
        for task in corpus.tasks
    }
    provenance: dict[str, object] = {
        "fixture_manifest_sha256": file_sha256(fixture_dir / "manifest.json"),
        "mem_git_shas": sorted({row.mem_git_sha for row in rows}),
        "mem_git_diff_sha256s": sorted({row.mem_git_diff_sha256 for row in rows}),
        "beads_git_shas": sorted({row.beads_git_sha for row in rows}),
        "beads_git_diff_sha256s": sorted({row.beads_git_diff_sha256 for row in rows}),
        "beads_bin_sha256s": sorted({row.beads_bin_sha256 for row in rows}),
        "structural_order_source_git_shas": sorted(
            {row.structural_order_source_git_sha for row in rows}
        ),
        "agent_models": sorted({row.agent_model for row in rows}),
        "agent_cli_versions": sorted({row.agent_cli_version for row in rows}),
    }
    manifest = write_agent_followup_evidence(
        rows,
        task_metadata,
        Path(args.out),
        provenance=provenance,
    )
    print(
        f"wrote follow-up agent analysis: {args.out} "
        f"({manifest['observation_count']} observations, {manifest['cell_count']} cells)"
    )
    return 0


def _validated_ordering_inputs(
    args: argparse.Namespace,
    *,
    arms: list[OrderingArm],
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
    selected_tasks = _select_ordering_tasks(
        corpus,
        task_ids_raw=args.task_ids,
        split_raw=args.task_split,
    )
    selected_ids = {task.task_id for task in selected_tasks}
    task_scoped = any(arm in CONTROL_ARMS for arm in arms)
    for task in selected_tasks:
        workspace = task_workspace(workspace_root, task, task_scoped=task_scoped)
        seed_beads_workspace(
            corpus=corpus,
            corpus_size=task.corpus_size,
            beads_bin=beads_bin,
            workspace=workspace,
            control_task_id=task.task_id if task_scoped else None,
        )
        ranks = validate_rank_truth(
            task=task,
            workspace=workspace,
            beads_bin=beads_bin,
            bm25f=bm25f,
            arms=arms,
        )
        truth[task.task_id] = {
            "total_matched": ranks.total_matched,
            "candidate_digest": ranks.candidate_digest,
            "ranks": {arm.value: dict(memory_ranks) for arm, memory_ranks in ranks.ranks.items()},
        }
    if set(truth) != selected_ids:
        missing = sorted(selected_ids - set(truth))
        raise SystemExit(f"unknown task IDs: {', '.join(missing)}")
    return corpus, beads_bin, workspace_root, bm25f, truth


def _cmd_beads_ordering_validate(args: argparse.Namespace) -> int:
    arms = _parse_ordering_arms(args.arms)
    corpus, beads_bin, _, bm25f, truth = _validated_ordering_inputs(args, arms=arms)
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
    arms = _parse_ordering_arms(args.arms)
    corpus, beads_bin, workspace_root, bm25f, truth_payload = _validated_ordering_inputs(
        args, arms=arms
    )
    claude_credentials = (
        Path(args.claude_credentials).expanduser().resolve() if args.claude_credentials else None
    )
    model, cli_version = validate_paid_run(args.model, claude_credentials=claude_credentials)
    page_sizes = _parse_page_sizes(args.page_sizes)
    mode = ExperimentMode(args.mode)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    mem_repo = Path(__file__).resolve().parents[2]
    beads_repo = Path(args.beads_repo).expanduser().resolve()
    mem_sha = git_sha(mem_repo)
    mem_dirty = git_dirty(mem_repo)
    mem_diff_digest = git_diff_sha256(mem_repo)
    beads_sha = git_sha(beads_repo)
    beads_dirty = git_dirty(beads_repo)
    beads_diff_digest = git_diff_sha256(beads_repo)
    beads_binary_digest = file_sha256(Path(beads_bin))
    selected_tasks = [task for task in corpus.tasks if task.task_id in truth_payload]
    task_scoped = any(arm in CONTROL_ARMS for arm in arms)
    cells = [
        (task, arm, page_size, repeat)
        for task in selected_tasks
        for arm in arms
        for page_size in page_sizes
        for repeat in _repeat_indices(start=args.repeat_start, count=args.repeats)
    ]
    random.Random(args.order_seed).shuffle(cells)
    prompt_digest = hashlib.sha256(b"beads-ordering-agent-protocol-v5").hexdigest()
    manifest = {
        "schema_version": 3,
        "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "mem_git_sha": mem_sha,
        "mem_git_dirty": mem_dirty,
        "mem_git_diff_sha256": mem_diff_digest,
        "beads_git_sha": beads_sha,
        "beads_git_dirty": beads_dirty,
        "beads_git_diff_sha256": beads_diff_digest,
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
            "control-automatic": (
                "task-scoped materialization of the automatic reverse-PageRank order"
            ),
            "control-semantic": (
                "frozen pin, boost, neutral, demote bands over automatic order; stable within band"
            ),
            "control-strategy": (
                "frozen operator-selected query-independent strategy for the graph family"
            ),
            "control-raw": (
                "frozen explicit numeric ranks first, then automatic order, then canonical ID"
            ),
        },
        "page_sizes": [str(value) for value in page_sizes],
        "arms": [arm.value for arm in arms],
        "mode": mode.value,
        "repeats": args.repeats,
        "repeat_start": args.repeat_start,
        "order_seed": args.order_seed,
        "max_tool_calls": args.max_tool_calls,
        "task_split": args.task_split,
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
                or row.mem_git_diff_sha256 != mem_diff_digest
                or row.beads_git_sha != beads_sha
                or row.beads_git_dirty != beads_dirty
                or row.beads_git_diff_sha256 != beads_diff_digest
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
                workspace=task_workspace(workspace_root, task, task_scoped=task_scoped),
                beads_bin=beads_bin,
                bm25f=bm25f,
                rank_truth=rank_truth,
                model=model,
                cli_version=cli_version,
                mem_sha=mem_sha,
                mem_dirty=mem_dirty,
                mem_git_diff_sha256=mem_diff_digest,
                beads_sha=beads_sha,
                beads_dirty=beads_dirty,
                beads_git_diff_sha256=beads_diff_digest,
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
    parser.add_argument(
        "--task-split",
        choices=["all", *(split.value for split in TaskSplit)],
        default="all",
        help="restrict validation/run cells to one frozen task split",
    )
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

    p_followup_freeze = sub.add_parser(
        "beads-ordering-followup-freeze",
        help="write independently varied structural-ordering follow-up corpora",
    )
    p_followup_freeze.add_argument("--out", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR))
    p_followup_freeze.add_argument("--seed", type=int, default=5878)
    p_followup_freeze.add_argument(
        "--structural-order-source",
        required=True,
        help="pinned structural-order source checkout used to materialize graph priors",
    )
    p_followup_freeze.add_argument("--overwrite", action="store_true")
    p_followup_freeze.set_defaults(func=_cmd_beads_ordering_followup_freeze)

    p_density_linkage_freeze = sub.add_parser(
        "beads-ordering-density-linkage-freeze",
        help="freeze candidate-density and reference-linkage variant recipes",
    )
    p_density_linkage_freeze.add_argument(
        "--fixture-dir", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR)
    )
    p_density_linkage_freeze.add_argument(
        "--preregistration",
        default=str(
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "beads_ordering"
            / "density-linkage-preregistration.json"
        ),
    )
    p_density_linkage_freeze.add_argument("--out", required=True)
    p_density_linkage_freeze.add_argument("--overwrite", action="store_true")
    p_density_linkage_freeze.set_defaults(func=_cmd_beads_ordering_density_linkage_freeze)

    p_density_linkage_materialize = sub.add_parser(
        "beads-ordering-density-linkage-materialize",
        help="materialize one frozen density/linkage variant for the existing runner",
    )
    p_density_linkage_materialize.add_argument(
        "--fixture-dir", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR)
    )
    p_density_linkage_materialize.add_argument("--manifest", required=True)
    p_density_linkage_materialize.add_argument("--variant-id", required=True)
    p_density_linkage_materialize.add_argument("--out", required=True)
    p_density_linkage_materialize.add_argument("--overwrite", action="store_true")
    p_density_linkage_materialize.set_defaults(func=_cmd_beads_ordering_density_linkage_materialize)

    p_density_linkage_oracle = sub.add_parser(
        "beads-ordering-density-linkage-oracle",
        help="collect deterministic ordering and navigation evidence for density/linkage variants",
    )
    p_density_linkage_oracle.add_argument(
        "--fixture-dir", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR)
    )
    p_density_linkage_oracle.add_argument("--manifest", required=True)
    p_density_linkage_oracle.add_argument("--workspace-root", required=True)
    p_density_linkage_oracle.add_argument("--beads-repo", required=True)
    p_density_linkage_oracle.add_argument("--beads-bin", default=os.environ.get("BEADS_BIN"))
    p_density_linkage_oracle.add_argument("--arms", default="key,pagerank,bm25f,control-semantic")
    p_density_linkage_oracle.add_argument("--page-sizes", default="5,all")
    p_density_linkage_oracle.add_argument("--out", required=True)
    _add_bm25f_flags(p_density_linkage_oracle)
    p_density_linkage_oracle.set_defaults(func=_cmd_beads_ordering_density_linkage_oracle)

    p_density_linkage_run = sub.add_parser(
        "beads-ordering-density-linkage-run",
        help="run a resumable authenticated agent shard over density/linkage variants",
    )
    p_density_linkage_run.add_argument("--fixture-dir", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR))
    p_density_linkage_run.add_argument("--manifest", required=True)
    p_density_linkage_run.add_argument(
        "--agent-sharding-amendment",
        default=str(
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "beads_ordering"
            / "density-linkage-agent-sharding-amendment.json"
        ),
    )
    p_density_linkage_run.add_argument("--workspace-root", required=True)
    p_density_linkage_run.add_argument("--beads-repo", required=True)
    p_density_linkage_run.add_argument("--beads-bin", default=os.environ.get("BEADS_BIN"))
    p_density_linkage_run.add_argument("--model", required=True)
    p_density_linkage_run.add_argument("--claude-credentials", default="")
    p_density_linkage_run.add_argument("--arms", default="key,pagerank,bm25f,control-semantic")
    p_density_linkage_run.add_argument("--page-sizes", default="5,all")
    p_density_linkage_run.add_argument("--modes", default="search-only,navigation")
    p_density_linkage_run.add_argument("--repeats", type=int, default=1)
    p_density_linkage_run.add_argument("--repeat-start", type=int, default=0)
    p_density_linkage_run.add_argument("--shard-index", type=int, required=True)
    p_density_linkage_run.add_argument("--shard-count", type=int, required=True)
    p_density_linkage_run.add_argument("--order-seed", type=int, default=5879)
    p_density_linkage_run.add_argument("--max-tool-calls", type=int, default=12)
    p_density_linkage_run.add_argument("--out", required=True)
    _add_bm25f_flags(p_density_linkage_run)
    p_density_linkage_run.set_defaults(func=_cmd_beads_ordering_density_linkage_run)

    p_followup_mutations = sub.add_parser(
        "beads-ordering-followup-mutations",
        help="replay structural-rank mutations and refresh policies",
    )
    p_followup_mutations.add_argument("--fixture-dir", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR))
    p_followup_mutations.add_argument(
        "--preregistration", default=str(_DEFAULT_ORDERING_FOLLOWUP_PREREGISTRATION)
    )
    p_followup_mutations.add_argument("--beads-repo", required=True)
    p_followup_mutations.add_argument("--beads-bin", default=os.environ.get("BEADS_BIN"))
    p_followup_mutations.add_argument("--sizes", default="50,100,500")
    p_followup_mutations.add_argument("--event-count", type=int, default=40)
    p_followup_mutations.add_argument("--page-size", type=int, default=10)
    p_followup_mutations.add_argument("--seed", type=int, default=5878)
    p_followup_mutations.add_argument("--out", required=True)
    p_followup_mutations.set_defaults(func=_cmd_beads_ordering_followup_mutations)

    p_followup_scaling = sub.add_parser(
        "beads-ordering-followup-rank-scaling",
        help="measure structural rank compute cost across corpus sizes",
    )
    p_followup_scaling.add_argument("--fixture-dir", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR))
    p_followup_scaling.add_argument("--beads-repo", required=True)
    p_followup_scaling.add_argument("--beads-bin", default=os.environ.get("BEADS_BIN"))
    p_followup_scaling.add_argument("--sizes", default="50,100,500,2000,10000")
    p_followup_scaling.add_argument("--repeats", type=int, default=3)
    p_followup_scaling.add_argument("--out", required=True)
    p_followup_scaling.set_defaults(func=_cmd_beads_ordering_followup_rank_scaling)

    p_followup_oracle = sub.add_parser(
        "beads-ordering-followup-oracle",
        help="collect deterministic rank and reference-navigation follow-up evidence",
    )
    p_followup_oracle.add_argument("--fixture-dir", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR))
    p_followup_oracle.add_argument("--validation-dir", required=True)
    p_followup_oracle.add_argument("--workspace-root", required=True)
    p_followup_oracle.add_argument("--beads-repo", required=True)
    p_followup_oracle.add_argument("--beads-bin", default=os.environ.get("BEADS_BIN"))
    p_followup_oracle.add_argument(
        "--arms",
        default=",".join((*[arm.value for arm in ARMS], *[arm.value for arm in CONTROL_ARMS])),
    )
    p_followup_oracle.add_argument("--page-sizes", default="5,10,20,all")
    p_followup_oracle.add_argument("--out", required=True)
    _add_bm25f_flags(p_followup_oracle)
    p_followup_oracle.set_defaults(func=_cmd_beads_ordering_followup_oracle)

    p_followup_agent_analysis = sub.add_parser(
        "beads-ordering-followup-agent-analyze",
        help="average targeted repeats and analyze the follow-up agent grid",
    )
    p_followup_agent_analysis.add_argument(
        "--fixture-dir", default=str(_DEFAULT_ORDERING_FOLLOWUP_DIR)
    )
    p_followup_agent_analysis.add_argument("--raw", required=True, nargs="+")
    p_followup_agent_analysis.add_argument("--out", required=True)
    p_followup_agent_analysis.set_defaults(func=_cmd_beads_ordering_followup_agent_analyze)

    p_validate = sub.add_parser(
        "beads-ordering-validate", help="seed Beads and verify fixed candidate-set parity"
    )
    _add_ordering_input_flags(p_validate)
    p_validate.add_argument("--arms", default=",".join(arm.value for arm in ARMS))
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
    p_ordering_run.add_argument(
        "--repeat-start",
        type=int,
        default=0,
        help="first repeat index (useful for preregistered targeted repeats)",
    )
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
