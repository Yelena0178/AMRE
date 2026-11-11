"""Offline benchmarking and versioned strategy management commands."""

import argparse
import json
from pathlib import Path

from amre.benchmarks.service import run_benchmark
from amre.research.storage import read_yaml, validate
from amre.strategy.config import StrategyProfile, activation_log, active_profile, load_version
from amre.strategy.service import StrategyRegistry


def add_benchmark_commands(commands: argparse._SubParsersAction) -> None:
    benchmark = commands.add_parser("benchmark", help="run bounded offline workflow comparisons")
    actions = benchmark.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run")
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--seed", type=int, default=19)
    strategy = commands.add_parser("strategy", help="evaluate and activate versioned strategies")
    actions = strategy.add_subparsers(dest="action", required=True)
    actions.add_parser("propose").add_argument("profile", type=Path)
    recommend = actions.add_parser("recommend")
    recommend.add_argument("trajectory", type=Path)
    recommend.add_argument("--baseline")
    actions.add_parser("show").add_argument("version_id", nargs="?")
    compare = actions.add_parser("compare")
    compare.add_argument("before")
    compare.add_argument("after")
    evaluate = actions.add_parser("evaluate")
    evaluate.add_argument("version_id")
    evaluate.add_argument("--baseline")
    evaluate.add_argument("--suite", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--seed", type=int, default=19)
    apply = actions.add_parser("apply")
    apply.add_argument("version_id")
    apply.add_argument("--evaluation", required=True)
    actions.add_parser("rollback")


def run_benchmark_command(args: argparse.Namespace, root: Path) -> int:
    status = 0
    if args.command == "benchmark":
        value = run_benchmark(args.suite, args.output, args.seed)
        status = (
            0
            if all(
                all(case.checked.values())
                and all(workflow.completed for workflow in case.workflows)
                for case in value.cases
            )
            else 1
        )
    else:
        registry = StrategyRegistry(root)
        if args.action == "propose":
            value = registry.propose(
                validate(
                    StrategyProfile,
                    read_yaml(args.profile),
                    "strategy profile",
                )
            )
        elif args.action == "recommend":
            base = load_version(root, args.baseline) if args.baseline else active_profile(root)
            value = registry.recommend(
                args.trajectory, base.profile if base else StrategyProfile(name="Default")
            )
        elif args.action == "show":
            value = load_version(root, args.version_id) if args.version_id else activation_log(root)
        elif args.action == "compare":
            value = registry.compare(args.before, args.after)
        elif args.action == "evaluate":
            active = active_profile(root)
            baseline = args.baseline or (active.version_id if active else None)
            value = registry.evaluate(
                args.version_id, args.suite, args.output, args.seed, baseline=baseline
            )
            status = 0 if value.eligible else 1
        elif args.action == "apply":
            value = registry.apply(args.version_id, args.evaluation)
        else:
            value = registry.rollback()
    print(
        value.model_dump_json(indent=2)
        if hasattr(value, "model_dump_json")
        else json.dumps(value, ensure_ascii=True, indent=2)
    )
    return status
