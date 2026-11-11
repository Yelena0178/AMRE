"""Research literature import, discovery, and promotion commands."""

import argparse
import json
from pathlib import Path

from amre.errors import ResearchError
from amre.literature.catalog import LiteratureLibrary
from amre.literature.discovery import CandidateQueue
from amre.literature.models import (
    CandidateDraft,
    PaperImport,
    PaperRecord,
    ProviderName,
    TheoremDraft,
)
from amre.literature.providers import SearchRequest
from amre.research.models import Budget
from amre.research.storage import read_yaml, validate
from amre.research.store import read_statement_file


def add_literature_commands(commands: argparse._SubParsersAction) -> None:
    literature = commands.add_parser("literature", help="query and record bibliographic sources")
    actions = literature.add_subparsers(dest="action", required=True)
    search = actions.add_parser("search")
    lookup = search.add_mutually_exclusive_group(required=True)
    lookup.add_argument("--query")
    lookup.add_argument("--doi")
    lookup.add_argument("--arxiv-id")
    search.add_argument(
        "--provider", type=ProviderName, choices=list(ProviderName), default=ProviderName.OFFLINE
    )
    search.add_argument("--online", action="store_true")
    search.add_argument("--limit", type=int, default=5)
    ingest = actions.add_parser("import")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--statement", type=Path, help="import an extracted theorem")
    ingest.add_argument(
        "--source-excerpt", type=Path, help="check an authorized local source excerpt"
    )
    ingest.add_argument("--checked-by", help="person who verified the source location")
    show = actions.add_parser("show")
    show.add_argument("id")
    link = actions.add_parser("link")
    link.add_argument("subject_id")
    link.add_argument("source_id")
    link.add_argument("--scope", required=True)

    discover = commands.add_parser("discover", help="record a precisely scoped candidate topic")
    discover.add_argument("--metadata", type=Path, required=True)
    discover.add_argument("--statement", type=Path, required=True)
    candidates = commands.add_parser("candidates", help="inspect or promote candidate topics")
    queue = candidates.add_subparsers(dest="action", required=True)
    listing = queue.add_parser("list")
    listing.add_argument("--field")
    listing.add_argument("--program")
    listing.add_argument("--parent")
    promote = queue.add_parser("promote")
    promote.add_argument("candidate_id")
    promote.add_argument("--author", required=True)
    promote.add_argument("--budget", type=Path, help="YAML with max_tokens and timeout_seconds")


def _import(args, library: LiteratureLibrary):
    raw = read_yaml(args.file)
    if args.statement is None:
        if args.source_excerpt or args.checked_by:
            raise ResearchError("Source review applies to an extracted theorem")
        papers = validate(PaperImport, raw, "paper metadata").papers
        return [item.model_dump(mode="json") for item in library.import_papers(papers)]
    if bool(args.source_excerpt) != bool(args.checked_by):
        raise ResearchError("Source confirmation requires both --source-excerpt and --checked-by")
    review = None
    if args.source_excerpt:
        try:
            with args.source_excerpt.open("rb") as file:
                excerpt = file.read(16385)
            if len(excerpt) > 16384:
                raise ValueError("large source")
            review = (args.checked_by, excerpt.decode("utf-8-sig").replace("\r\n", "\n"))
        except (OSError, ValueError):
            raise ResearchError(
                "Source excerpt must be readable UTF-8 text of at most 16 KiB"
            ) from None
    draft = validate(TheoremDraft, raw, "theorem metadata")
    return library.import_theorem(draft, read_statement_file(args.statement), source_review=review)


def run_literature_command(args: argparse.Namespace, root: Path) -> int:
    if args.command == "literature":
        library = LiteratureLibrary(root)
        if args.action == "search":
            request = validate(
                SearchRequest,
                dict(query=args.query, doi=args.doi, arxiv_id=args.arxiv_id, limit=args.limit),
                "literature search",
            )
            result = library.search(request, args.provider, online=args.online)
        elif args.action == "import":
            result = _import(args, library)
        elif args.action == "link":
            result = library.link(args.subject_id, args.source_id, args.scope)
        else:
            catalog = library.read()
            result = next(
                (
                    item
                    for group, field in (
                        (catalog.papers, "paper_id"),
                        (catalog.theorems, "theorem_id"),
                        (catalog.citations, "citation_id"),
                    )
                    for item in group
                    if getattr(item, field) == args.id
                ),
                None,
            )
            if result is None:
                raise ResearchError(f"Unknown literature record: {args.id}")
            if isinstance(result, PaperRecord):
                result = {
                    **result.model_dump(mode="json"),
                    "version_fingerprints": [item.fingerprint() for item in result.metadata],
                }
    else:
        queue = CandidateQueue(root)
        if args.command == "discover":
            draft = validate(CandidateDraft, read_yaml(args.metadata), "candidate metadata")
            result = queue.discover(draft, read_statement_file(args.statement))
        elif args.action == "promote":
            budget = (
                validate(Budget, read_yaml(args.budget), "candidate budget")
                if args.budget
                else None
            )
            result = queue.promote(args.candidate_id, args.author, budget=budget)
        else:
            result = [
                item.model_dump(mode="json")
                for item in queue.list(field=args.field, program=args.program, parent=args.parent)
            ]
    print(
        result.model_dump_json(indent=2)
        if hasattr(result, "model_dump_json")
        else json.dumps(result, ensure_ascii=True, indent=2)
    )
    return 1 if getattr(result, "diagnostic", None) and not result.papers else 0
