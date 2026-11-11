"""CLI commands for research contracts."""

import argparse
from pathlib import Path

from amre.errors import ResearchError
from amre.research.graph import GraphDocument
from amre.research.storage import read_yaml, validate, yaml_text
from amre.research.store import ResearchStore, read_statement_file


def add_problem_commands(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="problem_command", required=True)
    create = commands.add_parser("create", help="create a contract from YAML metadata and LaTeX")
    create.add_argument("metadata", type=Path)
    create.add_argument("--statement", type=Path, required=True)
    create.add_argument("--graph", type=Path, help="replace the graph in the same transaction")
    commands.add_parser("list", help="list contracts with claim and review status")
    show = commands.add_parser("show", help="show contract metadata and its LaTeX statement")
    show.add_argument("problem_id")
    check = commands.add_parser("validate", help="validate the research workspace and references")
    check.add_argument("problem_id", nargs="?")
    update = commands.add_parser("update", help="patch contract metadata or replace its statement")
    update.add_argument("problem_id")
    update.add_argument("patch", nargs="?", type=Path)
    update.add_argument("--statement", type=Path)
    update.add_argument("--graph", type=Path, help="replace the graph in the same transaction")


def _graph_argument(path: Path | None) -> GraphDocument | None:
    if path is None:
        return None
    return validate(GraphDocument, read_yaml(path), "research graph")


def run_problem_command(args: argparse.Namespace, workspace_root: Path) -> int:
    store = ResearchStore(workspace_root)
    command = args.problem_command
    if command == "create":
        contract = store.create(
            read_yaml(args.metadata),
            read_statement_file(args.statement),
            graph=_graph_argument(args.graph),
        )
        print(f"Created {contract.problem_id}")
        return 0
    if command == "update":
        if args.patch is None and args.statement is None and args.graph is None:
            raise ResearchError("Provide a metadata patch, a LaTeX file, or a graph")
        patch = read_yaml(args.patch) if args.patch is not None else {}
        statement = read_statement_file(args.statement) if args.statement is not None else None
        contract = store.update(
            args.problem_id, patch or {}, statement=statement, graph=_graph_argument(args.graph)
        )
        print(f"Updated {contract.problem_id}")
        return 0

    document, graph = store.snapshot()
    if command == "list":
        for contract in sorted(document.contracts, key=lambda item: item.problem_id):
            review = graph.review(contract.problem_id)
            print(
                f"{contract.problem_id}\t{contract.research_status}\t"
                f"{contract.claim_status}\t{review.status}"
            )
        return 0
    if args.problem_id is not None:
        contract = document.contract(args.problem_id)
    if command == "show":
        fields = contract.model_dump(mode="json")
        fields["review"] = graph.review(contract.problem_id).model_dump(mode="json")
        print(yaml_text(fields), end="")
        print("\n--- LaTeX ---")
        print(store.read_statement(contract.statement), end="")
        return 0
    print(
        f"Valid research workspace ({len(document.contracts)} contracts, "
        f"{len(graph.document.nodes)} nodes, {len(graph.document.edges)} edges)"
    )
    return 0
