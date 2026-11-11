"""CLI for portfolio inspection, bounded dispatch, and bottleneck derivation."""

import argparse
import json
from pathlib import Path

from amre.errors import ResearchError
from amre.execution.queue import load_program
from amre.research.storage import read_yaml
from amre.research.store import read_statement_file
from amre.scheduling.derivation import derive_problem
from amre.scheduling.service import PortfolioScheduler


def _selection(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--problem")
    group.add_argument("--program")


def add_scheduling_commands(commands: argparse._SubParsersAction) -> None:
    routes = commands.add_parser("routes", help="inspect mathematical research branches")
    route_actions = routes.add_subparsers(dest="action", required=True)
    route_actions.add_parser("list").add_argument("problem_id")
    show = route_actions.add_parser("show")
    show.add_argument("problem_id")
    show.add_argument("route_id")

    portfolio = commands.add_parser("portfolio", help="inspect and extend the research portfolio")
    portfolio_actions = portfolio.add_subparsers(dest="action", required=True)
    _selection(portfolio_actions.add_parser("status"))
    derive = portfolio_actions.add_parser("derive")
    derive.add_argument("--problem", required=True)
    derive.add_argument("--obstacle", required=True)
    derive.add_argument("--route", required=True)
    derive.add_argument("--metadata", required=True, type=Path)
    derive.add_argument("--statement", required=True, type=Path)

    scheduler = commands.add_parser("scheduler", help="explain, simulate, or run serial scheduling")
    schedule_actions = scheduler.add_subparsers(dest="action", required=True)
    explain = schedule_actions.add_parser("explain")
    _selection(explain, required=False)
    explain.add_argument("--decision")
    explain.add_argument("--seed", type=int, default=0)
    for name in ("simulate", "run"):
        action = schedule_actions.add_parser(name)
        _selection(action)
        action.add_argument("--seed", type=int, default=0)
        action.add_argument("--steps", type=int, default=1)
        if name == "simulate":
            action.add_argument("--assumed-ms", type=int, default=1000)


def _display(decision) -> dict:
    data = decision.model_dump(mode="json")
    for entry, candidate in zip(
        data["inputs"]["candidates"], decision.inputs.candidates, strict=True
    ):
        entry["score_total"] = candidate.score.total
    return data


def run_scheduling_command(args: argparse.Namespace, root: Path) -> int:
    scheduler = PortfolioScheduler(root)
    if args.command == "routes":
        decision = scheduler.explain(args.problem_id, None, 0)
        rows = _display(decision)["inputs"]["candidates"]
        if args.action == "show":
            rows = [item for item in rows if item["branch"]["route_id"] == args.route_id]
            if not rows:
                raise ResearchError("Route is outside the problem portfolio")
            output = rows[0]
        else:
            output = rows
    elif args.command == "portfolio" and args.action == "derive":
        child = derive_problem(
            root,
            args.problem,
            args.obstacle,
            args.route,
            read_yaml(args.metadata),
            read_statement_file(args.statement),
        )
        output = {"problem_id": child, "parent": args.problem, "route": args.route}
    else:
        program = load_program(root, args.program) if args.program else None
        if args.action == "explain" and args.decision:
            if args.problem or args.program:
                raise ResearchError("Stored decision replay takes only --decision")
            output = _display(scheduler.decision(args.decision))
        elif args.action in {"explain", "status"}:
            decision = scheduler.explain(args.problem, program, getattr(args, "seed", 0))
            output = _display(decision)
            if args.action == "status":
                output["journal"] = scheduler.journal(decision.inputs.scope).model_dump(mode="json")
        elif args.action == "simulate":
            output = [
                _display(item)
                for item in scheduler.simulate(
                    args.problem,
                    program,
                    args.seed,
                    args.steps,
                    args.assumed_ms,
                )
            ]
        else:
            reports = scheduler.run(args.problem, program, args.seed, args.steps)
            output = [
                {"decision": _display(decision), "result": result.model_dump(mode="json")}
                for decision, result in reports
            ]
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0
