"""Explicit preparation, response import and opt-in bounded model generation."""

import argparse
import json
import sys
from pathlib import Path

from amre.errors import ResearchError
from amre.execution.adapters import CodexCLIAdapter
from amre.intake.models import IntakeLimits
from amre.intake.service import IntakeStore, read_bounded, source_text


def add_intake_commands(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("intake", help="prepare source-attributed research drafts")
    actions = parser.add_subparsers(dest="action", required=True)
    importing = actions.add_parser("import", help="archive local UTF-8 materials")
    importing.add_argument("--draft", type=Path, required=True)
    importing.add_argument("files", type=Path, nargs="+")
    prepare = actions.add_parser("prepare", help="export a model request without launching a model")
    prepare.add_argument("--draft", type=Path, required=True)
    instruction = prepare.add_mutually_exclusive_group(required=True)
    instruction.add_argument("--request", help="natural-language request or revision")
    instruction.add_argument("--request-file", type=Path)
    prepare.add_argument("--timeout", type=int, default=120)
    prepare.add_argument("--max-tokens", type=int, default=8000)
    prepare.add_argument("--max-context-bytes", type=int, default=262144)
    prepare.add_argument("--max-output-bytes", type=int, default=262144)
    accept = actions.add_parser("accept", help="import externally produced DraftContent JSON")
    accept.add_argument("--draft", type=Path, required=True)
    accept.add_argument("--request-id", required=True)
    accept.add_argument("--response", type=Path, required=True)
    accept.add_argument("--model", required=True, help="declared producer; not proof verification")
    generate = actions.add_parser("generate", help="explicitly launch the core Codex adapter")
    generate.add_argument("--draft", type=Path, required=True)
    generate.add_argument("--request-id", required=True)
    generate.add_argument("--adapter", choices=["codex"], required=True)
    generate.add_argument("--live", action="store_true")
    show = actions.add_parser("show", help="validate and display a published draft version")
    show.add_argument("--draft", type=Path, required=True)
    show.add_argument("--version", type=int)


def run_intake_command(args, root: Path, executable: str) -> int:
    # Piped Windows Python may otherwise select GBK and fail on mathematical symbols.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    store = IntakeStore(args.draft, workspace=root)
    if args.action == "import":
        print(
            json.dumps(
                [s.model_dump() for s in store.import_materials(args.files)],
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.action == "prepare":
        from amre.research.storage import validate

        limits = validate(
            IntakeLimits,
            {
                "timeout_seconds": args.timeout,
                "max_tokens": args.max_tokens,
                "max_context_bytes": args.max_context_bytes,
                "max_output_bytes": args.max_output_bytes,
            },
            "intake limits",
        )
        text = (
            args.request
            if args.request is not None
            else source_text(read_bounded(args.request_file, 524288))
        )
        request = store.prepare(text, limits)
        print(
            json.dumps(
                {
                    "request_id": request.request_id,
                    "prompt": str(store.path(f"requests/{request.request_id}.md")),
                    "base_version": request.base_version,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.action == "accept":
        if not args.model.strip():
            raise ResearchError("Provide the declared response producer")
        print(store.accept(args.request_id, args.response, model=args.model))
    elif args.action == "generate":
        adapter = CodexCLIAdapter(executable, live=args.live)
        print(store.generate(args.request_id, adapter))
    else:
        print(json.dumps(store.load_version(args.version), ensure_ascii=False, indent=2))
    return 0
