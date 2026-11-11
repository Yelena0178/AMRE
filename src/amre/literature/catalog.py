"""Maintain a versioned literature catalogue in the research workspace transaction."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from amre.errors import ResearchError
from amre.literature.models import (
    CitationRecord,
    LiteratureCatalog,
    PaperMetadata,
    PaperRecord,
    ProviderName,
    SourceReview,
    SourceStatus,
    TheoremDraft,
    TheoremRecord,
)
from amre.literature.providers import OfflineFixtureProvider, PublicMetadataProvider, SearchRequest
from amre.research.graph import GraphDocument, GraphEdge, GraphNode, NodeKind
from amre.research.models import StatementRef
from amre.research.storage import validate
from amre.research.store import ResearchStore


def _find(records, field: str, identity: str):
    for item in records:
        if getattr(item, field) == identity:
            return item
    raise ResearchError(f"Unknown {field}: {identity}")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\0".join(parts).encode()).hexdigest()
    return f"{prefix}-{digest[:24]}"


def _with_node(graph: GraphDocument, node: GraphNode) -> GraphDocument:
    current = next((item for item in graph.nodes if item.id == node.id), None)
    if current and current.kind != NodeKind.LITERATURE_REFERENCE:
        raise ResearchError(f"Literature ID conflicts with existing graph node: {node.id}")
    nodes = tuple(item for item in graph.nodes if item.id != node.id)
    return graph.model_copy(update={"nodes": (*nodes, node)})


def _with_edges(graph: GraphDocument, *edges: GraphEdge) -> GraphDocument:
    return graph.model_copy(update={"edges": tuple(dict.fromkeys((*graph.edges, *edges)))})


def _paper_node(paper: PaperRecord) -> GraphNode:
    recent = paper.metadata[-1]
    sources = "\n".join(
        f"- {item.provider} {item.version or 'version unspecified'} · {item.source_url}"
        for item in paper.metadata[-5:]
    )
    fixture = "Synthetic fixture\n" if recent.fixture else ""
    authors = ", ".join(recent.authors) or "Authors unspecified"
    return GraphNode(
        id=paper.paper_id,
        kind=NodeKind.LITERATURE_REFERENCE,
        label=recent.title,
        citation=(
            f"{paper.status} · {recent.title}\n"
            f"{authors} · {recent.year or 'Year unknown'}\n"
            f"DOI: {recent.doi or 'Unknown'} · arXiv: {recent.arxiv_id or 'Unknown'}\n"
            f"{fixture}{sources}\n"
            "Metadata revisions: " + ", ".join(item.fingerprint() for item in paper.metadata)
        ),
    )


def _merge(papers: tuple[PaperRecord, ...], metadata: PaperMetadata):
    matches = [
        paper
        for paper in papers
        if any(
            item.fixture == metadata.fixture
            and (
                metadata.doi
                and item.doi == metadata.doi
                or metadata.arxiv_id
                and item.arxiv_id == metadata.arxiv_id
                or item.identity() == metadata.identity()
            )
            for item in paper.metadata
        )
    ]
    if len(matches) > 1:
        raise ResearchError("Source identifiers match different papers; reconcile before import")
    if matches:
        previous = matches[0]
        if metadata.identity() in {item.identity() for item in previous.metadata}:
            return papers, previous
        replacement = validate(
            PaperRecord,
            previous.model_copy(update={"metadata": (*previous.metadata, metadata)}),
            "paper merge",
        )
        return tuple(replacement if item == previous else item for item in papers), replacement
    key = metadata.doi or metadata.arxiv_id or metadata.fingerprint()
    paper = PaperRecord(
        paper_id=_stable_id("Paper", str(metadata.fixture), key), metadata=(metadata,)
    )
    return (*papers, paper), paper


class LiteratureLibrary:
    def __init__(self, root: Path):
        self.store = ResearchStore(root)
        self.root = self.store.root

    def read(self) -> LiteratureCatalog:
        return self.store.read().literature

    def search(self, request: SearchRequest, provider=ProviderName.OFFLINE, *, online=False):
        if provider != ProviderName.OFFLINE:
            return PublicMetadataProvider(self.root, provider, online=online).search(request)
        records = tuple(item for paper in self.read().papers for item in reversed(paper.metadata))
        return OfflineFixtureProvider(records).search(request)

    def import_papers(self, records: tuple[PaperMetadata, ...]) -> tuple[PaperRecord, ...]:
        with self.store._writing():
            document, before = self.store.snapshot()
            catalog = document.literature
            papers, graph = catalog.papers, document.graph
            ids = set()
            for record in records:
                merged, paper = _merge(papers, record)
                ids.add(paper.paper_id)
                if merged != papers:
                    papers = merged
                    graph = _with_node(graph, _paper_node(paper))
            if papers != catalog.papers:
                self.store._commit(
                    document,
                    before,
                    document.model_copy(
                        update={
                            "literature": catalog.model_copy(update={"papers": papers}),
                            "graph": graph,
                        }
                    ),
                    {},
                )
            return tuple(item for item in papers if item.paper_id in ids)

    def import_theorem(self, draft: TheoremDraft, statement: str, *, source_review=None):
        reference = StatementRef.from_text(statement)
        review = None
        if source_review:
            checked_by, original = source_review
            if len(original.encode()) > 16384:
                raise ResearchError("Use a scoped source excerpt of at most 16 KiB")
            if statement.strip() not in original or not statement.strip():
                raise ResearchError("The authorized source excerpt does not contain the statement")
            review = SourceReview(
                checked_by=checked_by,
                reviewed_at=datetime.now(UTC),
                source_digest=sha256(original.encode()).hexdigest(),
            )
        record = TheoremRecord(
            **draft.model_dump(),
            statement=reference,
            source_status=SourceStatus.SOURCE_CONFIRMED
            if review
            else SourceStatus.EXTRACTED_UNCHECKED,
            review=review,
        )
        with self.store._writing():
            document, before = self.store.snapshot()
            catalog = document.literature
            paper = _find(catalog.papers, "paper_id", record.paper_id)
            version = next(
                (item for item in paper.metadata if item.fingerprint() == record.paper_version),
                None,
            )
            if version is None:
                raise ResearchError("Unknown paper metadata version")
            for assumption in record.assumptions:
                if before.node(assumption).kind != NodeKind.ASSUMPTION:
                    raise ResearchError(f"Expected a registered assumption: {assumption}")
            old = next(
                (item for item in catalog.theorems if item.theorem_id == record.theorem_id), None
            )
            if old:
                old_fields = old.model_dump(exclude={"review", "source_status"})
                if old_fields != record.model_dump(exclude={"review", "source_status"}):
                    raise ResearchError(
                        "Use a new theorem ID for a different statement or source version"
                    )
                if old.review or review is None:
                    return old
            elif any(item.id == record.theorem_id for item in before.document.nodes):
                raise ResearchError(f"Theorem ID is already used: {record.theorem_id}")
            locator = record.locator
            citation = (
                f"{record.source_status} · {record.label}\n"
                f"{version.source_url} · {version.version or 'Version unspecified'}\n"
                f"Section {locator.section or '—'}, page {locator.page or '—'}, "
                f"item {locator.number or '—'} · {locator.scope}"
            )
            node = GraphNode(
                id=record.theorem_id,
                kind=NodeKind.LITERATURE_REFERENCE,
                label=record.label,
                statement=reference,
                citation=citation,
            )
            graph = _with_edges(
                _with_node(document.graph, node),
                GraphEdge(source=node.id, target=paper.paper_id, relation="CITES"),
                *(
                    GraphEdge(source=node.id, target=item, relation="USES_ASSUMPTION")
                    for item in record.assumptions
                ),
            )
            theorems = tuple(item for item in catalog.theorems if item.theorem_id != node.id)
            self.store._commit(
                document,
                before,
                document.model_copy(
                    update={
                        "graph": graph,
                        "literature": catalog.model_copy(update={"theorems": (*theorems, record)}),
                    }
                ),
                {reference.path: statement},
            )
            return record

    def link(self, subject_id: str, source_id: str, scope: str) -> CitationRecord:
        with self.store._writing():
            document, before = self.store.snapshot()
            before.node(subject_id)
            record = make_citation(document.literature, subject_id, source_id, scope)
            if record in document.literature.citations:
                return record
            catalog, graph = add_citations(document.literature, document.graph, record)
            self.store._commit(
                document,
                before,
                document.model_copy(update={"literature": catalog, "graph": graph}),
                {},
            )
            return record


def make_citation(
    catalog: LiteratureCatalog, subject: str, source: str, scope: str
) -> CitationRecord:
    theorem = next((item for item in catalog.theorems if item.theorem_id == source), None)
    paper = _find(catalog.papers, "paper_id", theorem.paper_id if theorem else source)
    return CitationRecord(
        citation_id=_stable_id("Cite", subject, source, scope.strip()),
        subject_id=subject,
        paper_id=paper.paper_id,
        theorem_id=theorem.theorem_id if theorem else None,
        scope=scope,
    )


def add_citations(catalog: LiteratureCatalog, graph: GraphDocument, *items: CitationRecord):
    edges = tuple(
        GraphEdge(source=item.subject_id, target=item.theorem_id or item.paper_id, relation="CITES")
        for item in items
    )
    citations = tuple(dict.fromkeys((*catalog.citations, *items)))
    return catalog.model_copy(update={"citations": citations}), _with_edges(graph, *edges)
