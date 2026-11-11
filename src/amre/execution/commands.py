"""CLI for safe offline dispatch, explicit live dispatch, and job control."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from amre.errors import ResearchError
from amre.execution.adapters import CodexCLIAdapter, MockModelAdapter, ModelAdapter
from amre.execution.deep import DeepResearchRunner
from amre.execution.failure_memory import FailureMemory, recommendations
from amre.execution.models import AdapterName, ExecutionJob, ExecutionState, ResearchWorkflow
from amre.execution.orchestrator import ResearchOrchestrator
from amre.execution.queue import BudgetManager, TaskQueue, load_policy, load_program
from amre.execution.skill_models import ReasoningMode
from amre.research.obstructions import ObstructionCategory
from amre.research.store import ResearchStore


def _adapter_options(parser: argparse.ArgumentParser, *, resume: bool = False) -> None:
    parser.add_argument(
        "--adapter", choices=tuple(AdapterName), default=None if resume else AdapterName.MOCK
    )
    parser.add_argument("--live", action="store_true", help="authorize one real Codex dispatch")
    parser.add_argument("--mode", type=ReasoningMode, choices=tuple(ReasoningMode))
    parser.add_argument("--obstacle", help="use an exact open obstacle ID from the problem")


def add_execution_commands(commands: argparse._SubParsersAction) -> None:
    run = commands.add_parser("run", help="execute bounded deep research for each selected problem")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--problem")
    target.add_argument("--program")
    run.add_argument("--dry-run", action="store_true", help="show the plan without dispatch")
    run.add_argument(
        "--workflow",
        choices=tuple(ResearchWorkflow),
        help="use deep research (default) or an explicit short task",
    )
    _adapter_options(run)
    for name in ("pause", "resume", "stop"):
        control = commands.add_parser(name, help=f"{name} a research job")
        control.add_argument("--problem", required=True)
        control.add_argument("--program")
        if name == "resume":
            _adapter_options(control, resume=True)
    budget = commands.add_parser("budget", help="inspect the execution ledger")
    budget.add_subparsers(dest="action", required=True).add_parser("show")
    attempt = commands.add_parser("attempt", help="query published research attempts")
    queries = attempt.add_subparsers(dest="action", required=True)
    queries.add_parser("list").add_argument("problem_id")
    show = queries.add_parser("show")
    show.add_argument("problem_id")
    show.add_argument("attempt_id")
    obstacle = commands.add_parser("obstacle", help="inspect and resolve scoped obstacles")
    queries = obstacle.add_subparsers(dest="action", required=True)
    listing = queries.add_parser("list")
    listing.add_argument("problem_id")
    listing.add_argument("--query")
    for action in ("show", "recommend", "resolve"):
        command = queries.add_parser(action)
        command.add_argument("obstacle_id")
        if action == "resolve":
            command.add_argument("--evidence", required=True)
    failures = commands.add_parser("failure", help="query archived failures and obstacles")
    listing = failures.add_subparsers(dest="action", required=True).add_parser("list")
    listing.add_argument("problem_id")
    listing.add_argument("--obstacle")
    sessions = commands.add_parser("session", help="control a deep research session")
    actions = sessions.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start")
    start.add_argument("problem_id")
    _adapter_options(start)
    for action in ("inspect", "checkpoint", "pause", "resume", "stop"):
        command = actions.add_parser(action)
        command.add_argument("session_id")
        if action == "resume":
            _adapter_options(command, resume=True)


def _session_command(args: argparse.Namespace, root: Path, codex_executable: str) -> int:
    deep = DeepResearchRunner(root)
    if args.action == "start":
        job = deep.start(
            args.problem_id,
            _adapter(args, codex_executable),
            mode=args.mode,
            obstacle_id=args.obstacle,
        )
    elif args.action == "inspect":
        print(deep.inspect(args.session_id).model_dump_json())
        return 0
    elif args.action == "checkpoint":
        print(json.dumps({"checkpoint_id": deep.checkpoint(args.session_id)}))
        return 0
    elif args.action == "pause":
        job = deep.pause(args.session_id)
    elif args.action == "stop":
        job = deep.stop(args.session_id)
    else:
        snapshot = deep.inspect(args.session_id)
        ledger = deep.runner.queue.read()
        reservation = next(
            item for item in ledger.reservations if item.attempt_id == snapshot.attempt_id
        )
        job = ledger.jobs[
            ExecutionJob(problem_id=snapshot.problem_id, program=reservation.program).key
        ]
        if job.deep_session_id != args.session_id:
            raise ResearchError("A newer job owns this problem")
        program = load_program(root, reservation.program) if reservation.program else None
        adapter = _resume_adapter(args, codex_executable, job, reservation)
        job = deep.resume(
            snapshot.problem_id,
            adapter,
            program=program,
            mode=args.mode,
            obstacle_id=args.obstacle,
            expected_session_id=args.session_id,
        )
    print(job.model_dump_json())
    return 1 if job.state in {ExecutionState.BLOCKED, ExecutionState.FAILED} else 0


def _query(args: argparse.Namespace, root: Path) -> object:
    memory = FailureMemory(root)
    if args.command == "failure":
        return memory.attempts(args.problem_id, failures=True, obstacle_id=args.obstacle)
    if args.command == "attempt":
        rows = memory.attempts(args.problem_id)
        if args.action == "list":
            return rows
        for row in rows:
            if row["attempt_id"] == args.attempt_id:
                return row
        raise ResearchError(f"No published attempt: {args.attempt_id}")
    if args.action == "list":
        return (
            memory.search(args.problem_id, args.query)
            if args.query is not None
            else memory.obstacles(args.problem_id)
        )
    if args.action == "resolve":
        return memory.resolve(args.obstacle_id, args.evidence)
    item = memory.obstacle(args.obstacle_id)
    if args.action == "recommend":
        item["recommendations"] = [
            asdict(advice) for advice in recommendations(ObstructionCategory(item["category"]))
        ]
    return item


def _adapter(args: argparse.Namespace, codex_executable: str) -> ModelAdapter:
    if args.adapter == AdapterName.CODEX:
        return CodexCLIAdapter(codex_executable, live=args.live)
    if args.live:
        raise ResearchError("--live requires --adapter codex")
    return MockModelAdapter()


def _resume_adapter(args, executable, job, reservation):
    if job and job.state in {
        ExecutionState.RUNNING,
        ExecutionState.COMPLETED,
        ExecutionState.CANCELLED,
    }:
        return None
    if reservation and reservation.adapter == AdapterName.CODEX and args.adapter is None:
        raise ResearchError(
            "Select --adapter codex --live to resume Codex or --adapter mock for offline recovery"
        )
    return _adapter(args, executable)


def budget_summary(root: Path) -> dict[str, object]:
    ledger = TaskQueue(root).read()
    items = ledger.reservations
    deep = tuple(item for item in items if item.deep_session_id)
    elapsed = BudgetManager.used_ms(items)
    deep_elapsed = BudgetManager.used_ms(deep)

    def total(field: str) -> int | str:
        values = [getattr(item.usage, field) for item in items]
        return "UNKNOWN" if any(value is None for value in values) else sum(values)

    return {
        "policy": load_policy(root).model_dump(mode="json"),
        "attempts": len(items),
        "time_ms": elapsed,
        "deep_time_ms": deep_elapsed,
        "deep_time_percent": 100 * deep_elapsed / elapsed if elapsed else 0,
        "tokens": total("tokens"),
        "cost_microusd": total("cost_microusd"),
        "tool_calls": total("tool_calls"),
        "reservations": [item.model_dump(mode="json") for item in items],
    }


def run_execution_command(args: argparse.Namespace, root: Path, codex_executable: str) -> int:
    if args.command == "session":
        return _session_command(args, root, codex_executable)
    if args.command in {"attempt", "obstacle", "failure"}:
        print(json.dumps(_query(args, root), ensure_ascii=True, indent=2))
        return 0
    if args.command == "budget":
        print(json.dumps(budget_summary(root), indent=2))
        return 0
    program = load_program(root, args.program) if args.program is not None else None
    if args.command == "run":
        workflow = args.workflow or load_policy(root).workflow
        problems = program.problems if program else (args.problem,)
        document = ResearchStore(root).read()
        for problem in problems:
            document.contract(problem)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "program": args.program,
                        "problems": problems,
                        "adapter": args.adapter,
                        "workflow": workflow,
                        "dry_run": True,
                    },
                    indent=2,
                )
            )
            return 0
        if args.live and len(problems) > 1:
            raise ResearchError("Live dispatch requires a single problem")
        adapter = _adapter(args, codex_executable)
        if workflow == ResearchWorkflow.DEEP:
            start = DeepResearchRunner(root).start
        else:
            start = ResearchOrchestrator(root).run
        for problem in problems:
            job = start(
                problem, adapter, program=program, mode=args.mode, obstacle_id=args.obstacle
            )
            print(job.model_dump_json())
            if job.state != ExecutionState.COMPLETED:
                return 1
        return 0

    ResearchStore(root).load_contract(args.problem)
    if program is not None and args.problem not in program.problems:
        raise ResearchError("Problem does not belong to the selected program")
    if args.command == "resume":
        runner = ResearchOrchestrator(root)
        ledger = runner.queue.read()
        current = ledger.jobs.get(
            ExecutionJob(problem_id=args.problem, program=program.name if program else None).key
        )
        previous = next(
            (
                item
                for item in ledger.reservations
                if current and item.attempt_id == current.attempt_id
            ),
            None,
        )
        adapter = _resume_adapter(args, codex_executable, current, previous)
        job = runner.run(
            args.problem,
            adapter,
            program=program,
            resume=True,
            mode=args.mode,
            obstacle_id=args.obstacle,
        )
        print(job.model_dump_json())
        return 0 if job.state in {ExecutionState.COMPLETED, ExecutionState.PAUSED} else 1
    job = TaskQueue(root).control(
        ExecutionJob(problem_id=args.problem, program=program.name if program else None),
        ExecutionState.PAUSED if args.command == "pause" else ExecutionState.CANCELLED,
    )
    print(job.model_dump_json())
    return 0
