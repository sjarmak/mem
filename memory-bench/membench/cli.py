"""membench CLI — run one sequence under 3 conditions, or emit Harbor tasks.

  membench run-sequence <fixture.json> [--out DIR] [--fs-dir DIR]
  membench gen-tasks    <fixture.json> --out DIR [--overwrite]

`run-sequence` exercises the full skeleton pipeline in-process with the
deterministic reference agent (no Docker / no paid API) and writes the comparison
report + per-trial OTel spans + ATIF exports. `gen-tasks` emits Harbor task dirs
for a real `harbor run` (paid Claude path).
"""

import argparse
import json
import sys
from pathlib import Path

from membench.dataset import load_sequence
from membench.harbor.adapter import SequenceAdapter
from membench.report.comparison import build_comparison
from membench.runner.conditions import run_sequence
from membench.schemas.config import AgentConfig, ExperimentConfig, MemoryConfig
from membench.schemas.memory_event import MemoryBackend
from membench.telemetry.atif import trace_to_atif
from membench.telemetry.otel_spans import trace_to_spans


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


def _cmd_gen_tasks(args: argparse.Namespace) -> int:
    seq = load_sequence(args.fixture)
    adapter = SequenceAdapter(seq, args.out, overwrite=args.overwrite)
    created = adapter.run()
    for d in created:
        print(d)
    print(f"\n{len(created)} Harbor task dirs written to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="membench")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-sequence", help="run one sequence under 3 conditions")
    p_run.add_argument("fixture", help="path to a benchmark-sequence JSON fixture")
    p_run.add_argument("--out", default="reports", help="output dir (default: reports/)")
    p_run.add_argument("--fs-dir", default=None, help="filesystem-memory store dir")
    p_run.set_defaults(func=_cmd_run_sequence)

    p_gen = sub.add_parser("gen-tasks", help="emit Harbor task dirs for `harbor run`")
    p_gen.add_argument("fixture", help="path to a benchmark-sequence JSON fixture")
    p_gen.add_argument("--out", required=True, help="output dir for task dirs")
    p_gen.add_argument("--overwrite", action="store_true")
    p_gen.set_defaults(func=_cmd_gen_tasks)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
