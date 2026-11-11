"""Command-line entry points for checkpoints and research memory."""

import argparse
import json
import sys
from pathlib import Path

from amre.research.context import build_context
from amre.research.index import rebuild_index, summarize
from amre.research.memory import ResearchMemory
from amre.research.storage import read_yaml, yaml_text


def add_memory_commands(commands: argparse._SubParsersAction) -> None:
    checkpoint = commands.add_parser("checkpoint", help="save and restore research state")
    actions = checkpoint.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="save a checkpoint from research notes")
    create.add_argument("problem_id")
    create.add_argument(
        "notes", type=Path, help="YAML with next_research_action and optional notes"
    )
    for action in ("show", "restore"):
        command = actions.add_parser(action, help=f"{action} a checkpoint")
        command.add_argument("problem_id")
        command.add_argument("--checkpoint")

    memory = commands.add_parser("memory", help="inspect and index the research archive")
    actions = memory.add_subparsers(dest="action", required=True)
    actions.add_parser("inspect", help="validate research files and replay events")
    actions.add_parser("rebuild", help="rebuild the SQLite index from research files")
    record = actions.add_parser("record", help="append a research event from YAML")
    record.add_argument("event", type=Path)

    context = commands.add_parser("context", help="build a bounded research context")
    actions = context.add_subparsers(dest="action", required=True)
    build = actions.add_parser("build", help="assemble context from a restored checkpoint")
    build.add_argument("problem_id")
    build.add_argument("--checkpoint")
    build.add_argument("--max-bytes", type=int, default=16000, help="maximum UTF-8 bytes")


def run_memory_command(args: argparse.Namespace, workspace_root: Path) -> int:
    memory = ResearchMemory(workspace_root)
    if args.command == "memory":
        if args.action == "record":
            event = memory.append_event(read_yaml(args.event))
            print(event.event_id)
            return 0
        result = (
            rebuild_index(memory)
            if args.action == "rebuild"
            else summarize(memory, memory.inventory())
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "checkpoint" and args.action == "create":
        checkpoint = memory.create_checkpoint(args.problem_id, read_yaml(args.notes))
        print(checkpoint.checkpoint_id)
        return 0
    state = memory.restore(args.problem_id, args.checkpoint)
    if args.command == "context":
        context = build_context(state, max_bytes=args.max_bytes)
        # Preserve the counted UTF-8 payload, including newlines, under PowerShell.
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(context.text.encode("utf-8"))
        else:
            sys.stdout.write(context.text)
        if context.omitted:
            print(f"Omitted {len(context.omitted)} whole context blocks", file=sys.stderr)
    elif args.action == "show":
        print(yaml_text(state.checkpoint.model_dump(mode="json")), end="")
    else:
        print(
            json.dumps(
                {
                    "checkpoint": state.checkpoint.model_dump(mode="json"),
                    "workspace_matches": state.workspace_matches,
                    "statement": state.read_statement(state.checkpoint.contract.statement),
                    "failed_attempts": [event.event_id for event in state.failed_attempts],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0
