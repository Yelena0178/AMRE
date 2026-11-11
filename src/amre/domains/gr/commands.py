"""Inspect and run the bounded GR pack from the CLI."""

import argparse
import json
from pathlib import Path

from amre.domains.gr.pack import GRDomainPack
from amre.domains.gr.service import GRComputations
from amre.research.storage import read_yaml


def add_gr_commands(commands: argparse._SubParsersAction) -> None:
    pack = commands.add_parser("gr", help="run scoped general-relativity computations")
    actions = pack.add_subparsers(dest="action", required=True)
    actions.add_parser("info")
    run = actions.add_parser("run")
    run.add_argument("tool", choices=tuple(GRDomainPack.available_tools))
    run.add_argument("input", type=Path)
    run.add_argument("--problem")
    run.add_argument("--assertion")
    run.add_argument("--timeout", type=float, default=12)
    actions.add_parser("show").add_argument("computation_id")
    replay = actions.add_parser("replay")
    replay.add_argument("computation_id")
    replay.add_argument("--timeout", type=float, default=12)


def run_gr_command(args: argparse.Namespace, root: Path) -> int:
    if args.action == "info":
        pack = GRDomainPack()
        print(
            json.dumps(
                {
                    "domain": pack.domain_name,
                    "definitions": pack.definitions,
                    "conventions_schema": pack.conventions.model_json_schema(),
                    "tools": [
                        tool.model_dump(mode="json") for tool in pack.available_tools.values()
                    ],
                    "consistency_checks": pack.consistency_checks,
                    "benchmarks": pack.benchmarks,
                    "reasoning_guidelines": pack.reasoning_guidelines,
                },
                indent=2,
            )
        )
        return 0
    computations = GRComputations(root)
    if args.action == "run":
        artifact = computations.run(
            args.tool,
            read_yaml(args.input),
            problem_id=args.problem,
            assertion_id=args.assertion,
            timeout=args.timeout,
        )
    else:
        artifact = computations.show(args.computation_id)
        if args.action == "replay":
            artifact = computations.run(artifact.tool, artifact.request, timeout=args.timeout)
    print(artifact.model_dump_json(indent=2))
    return 0 if artifact.result.status == "COMPLETED" or args.action == "show" else 1
