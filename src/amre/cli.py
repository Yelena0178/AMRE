"""AMRE's deterministic command-line entry point."""

import argparse
import logging
from importlib.metadata import version
from pathlib import Path

from amre.benchmarks.commands import add_benchmark_commands, run_benchmark_command
from amre.config import LoadedConfig, load_config
from amre.dashboard.commands import add_dashboard_commands, run_dashboard_command
from amre.demo import add_demo_command, run_demo_command
from amre.diagnostics import inspect_workspace, run_checks
from amre.domains.gr.commands import add_gr_commands, run_gr_command
from amre.errors import AmreError
from amre.execution.commands import add_execution_commands, run_execution_command
from amre.execution.queue import TaskQueue
from amre.literature.commands import add_literature_commands, run_literature_command
from amre.log import configure_logging
from amre.reports import add_report_commands, run_report_command
from amre.research.commands import add_problem_commands, run_problem_command
from amre.research.memory_commands import add_memory_commands, run_memory_command
from amre.scheduling.commands import add_scheduling_commands, run_scheduling_command

logger = logging.getLogger(__name__)


def _doctor(config: LoadedConfig) -> int:
    checks = run_checks(config.settings)
    for check in checks:
        if check.passed:
            result = "OK"
        elif check.required:
            result = "FAIL"
        else:
            result = "OPTIONAL"
        print(f"[{result}] {check.name}: {check.detail}")
    return int(any(check.required and not check.passed for check in checks))


def _status(config: LoadedConfig) -> int:
    workspace = inspect_workspace(config.settings.workspace_root)
    print(f"AMRE {version('amre')}")
    print("Runtime: deep research with portfolio scheduling and domain tools")
    print(f"Configuration: {config.source or '(no file)'}")
    print(f"Workspace: {workspace.root} ({'available' if workspace.is_directory else 'missing'})")
    print(f"Git worktree: {'detected' if workspace.in_worktree else 'unconfirmed'}")
    print(f"Log level: {config.settings.log_level}")
    if workspace.is_directory:
        for job in TaskQueue(workspace.root).read().jobs.values():
            print(
                f"Job {job.problem_id}: {job.state}"
                + (f" (requested {job.request})" if job.request else "")
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="amre", description="Autonomous Mathematical Research Engine"
    )
    parser.add_argument("--config", type=Path, help="path to a YAML settings file")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("version", help="show the installed AMRE version")
    commands.add_parser("doctor", help="check the local runtime and workspace")
    commands.add_parser("status", help="show the configuration and workspace")
    add_problem_commands(commands.add_parser("problem", help="manage research contracts"))
    add_memory_commands(commands)
    add_execution_commands(commands)
    add_scheduling_commands(commands)
    add_literature_commands(commands)
    add_gr_commands(commands)
    add_dashboard_commands(commands)
    add_report_commands(commands)
    add_benchmark_commands(commands)
    add_demo_command(commands)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "version":
        print(version("amre"))
        return 0

    try:
        config = load_config(args.config)
        configure_logging(config.settings.log_level)
        logger.debug("Loaded settings from %s", config.source or "defaults and environment")
        if args.command == "problem":
            return run_problem_command(args, config.settings.workspace_root)
        if args.command in {"checkpoint", "memory", "context"}:
            return run_memory_command(args, config.settings.workspace_root)
        if args.command in {"routes", "portfolio", "scheduler"}:
            return run_scheduling_command(args, config.settings.workspace_root)
        if args.command in {"literature", "discover", "candidates"}:
            return run_literature_command(args, config.settings.workspace_root)
        if args.command == "gr":
            return run_gr_command(args, config.settings.workspace_root)
        if args.command == "dashboard":
            return run_dashboard_command(args, config.settings.workspace_root)
        if args.command == "report":
            return run_report_command(args, config.settings.workspace_root)
        if args.command in {"benchmark", "strategy"}:
            return run_benchmark_command(args, config.settings.workspace_root)
        if args.command == "demo":
            return run_demo_command(args)
        if args.command in {
            "run",
            "pause",
            "resume",
            "stop",
            "budget",
            "attempt",
            "obstacle",
            "failure",
            "session",
        }:
            return run_execution_command(
                args,
                config.settings.workspace_root,
                config.settings.codex_executable,
            )
        return _doctor(config) if args.command == "doctor" else _status(config)
    except AmreError as exc:
        configure_logging()
        logger.error("%s", exc)
        return 2
    except OSError as exc:
        configure_logging()
        logger.error("Workspace I/O failed: %s", exc.strerror or type(exc).__name__)
        return 2
    except KeyboardInterrupt:
        return 130
