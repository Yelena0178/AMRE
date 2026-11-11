"""Git-friendly research workspace and transactional update boundary."""

from collections.abc import Collection, Iterable, Iterator, Mapping
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import Field

from amre.errors import ResearchError, WorkspaceChanged
from amre.literature.models import LiteratureCatalog
from amre.research.graph import (
    ASSERTIONS,
    DEPENDENCY_RELATIONS,
    GraphDocument,
    GraphEdge,
    GraphNode,
    NodeKind,
    Relation,
    ResearchGraph,
)
from amre.research.journal import ArchiveBatch, publish_immutable, repository_revision
from amre.research.memory_models import EventInput, EventKind
from amre.research.models import (
    ContractInput,
    EvidenceRecord,
    Identifier,
    ResearchContract,
    Review,
    ReviewStatus,
    StatementRef,
    VersionedRecord,
    statement_hash,
)
from amre.research.storage import atomic_write, read_yaml, validate, workspace_path, yaml_text

_CONTRACT_EDGES = {
    "assumptions": Relation.USES_ASSUMPTION,
    "dependencies": Relation.DEPENDS_ON,
    "research_routes": Relation.ALTERNATIVE_ROUTE,
    "obstacles": Relation.BLOCKED_BY,
    "evidence_references": Relation.SUPPORTED_BY,
}


class WorkspaceDocument(VersionedRecord):
    contracts: tuple[ResearchContract, ...] = ()
    graph: GraphDocument = GraphDocument()
    reviews: dict[Identifier, Review] = Field(default_factory=dict)
    event_ids: tuple[Identifier, ...] = ()
    literature: LiteratureCatalog = LiteratureCatalog()

    def contract(self, problem_id: str) -> ResearchContract:
        for contract in self.contracts:
            if contract.problem_id == problem_id:
                return contract
        raise ResearchError(f"Unknown research problem: {problem_id}")

    def research_fingerprint(self) -> str:
        """Identify mathematical state independently of journal and checkpoint pointers."""
        data = self.model_dump_json(
            exclude={
                "event_ids": True,
                "literature": True,
                "contracts": {"__all__": {"checkpoint_reference"}},
            }
        )
        return sha256(data.encode()).hexdigest()


def _compile(document: WorkspaceDocument) -> ResearchGraph:
    nodes = list(document.graph.nodes)
    edges = list(document.graph.edges)
    contract_ids = {contract.problem_id for contract in document.contracts}
    if any(
        edge.source in contract_ids and edge.relation in _CONTRACT_EDGES.values() for edge in edges
    ):
        raise ResearchError("Use contract fields for outgoing problem dependencies and references")
    for contract in document.contracts:
        nodes.append(
            GraphNode(
                id=contract.problem_id,
                kind=NodeKind.RESEARCH_PROBLEM,
                label=contract.problem_id,
                statement=contract.statement,
                claim_status=contract.claim_status,
            )
        )
        for field, relation in _CONTRACT_EDGES.items():
            edges.extend(
                GraphEdge(source=contract.problem_id, target=target, relation=relation)
                for target in getattr(contract, field)
            )
    return ResearchGraph(
        GraphDocument(
            nodes=tuple(nodes), edges=tuple(edges), assumptions=document.graph.assumptions
        ),
        document.reviews,
    )


def _changed_nodes(
    previous: WorkspaceDocument,
    before: ResearchGraph,
    updated: WorkspaceDocument,
    after: ResearchGraph,
) -> set[str]:
    old_nodes = {node.id: node for node in before.document.nodes}
    new_nodes = {node.id: node for node in after.document.nodes}
    common = old_nodes.keys() & new_nodes.keys()
    changed = {
        node_id
        for node_id in common
        if old_nodes[node_id].model_dump(exclude={"claim_status", "obstruction"})
        != new_nodes[node_id].model_dump(exclude={"claim_status", "obstruction"})
    }
    if any(old_nodes[node_id].kind != new_nodes[node_id].kind for node_id in changed):
        raise ResearchError("A graph node cannot change its kind")
    if any(old_nodes[node_id].evidence is not None for node_id in changed):
        raise ResearchError("Evidence records are immutable. Add a new evidence ID")
    reduction_subjects = {node_id for node_id in changed if new_nodes[node_id].kind in ASSERTIONS}
    old_contracts = {contract.problem_id: contract for contract in previous.contracts}
    for contract in updated.contracts:
        old = old_contracts.get(contract.problem_id)
        if old and (old.definitions, old.acceptance_criteria) != (
            contract.definitions,
            contract.acceptance_criteria,
        ):
            changed.add(contract.problem_id)
            reduction_subjects.add(contract.problem_id)

    old_edges = {(edge.source, edge.target, edge.relation) for edge in before.document.edges}
    new_edges = {(edge.source, edge.target, edge.relation) for edge in after.document.edges}
    for source, target, relation in old_edges ^ new_edges:
        if relation in DEPENDENCY_RELATIONS or relation == Relation.USES_ASSUMPTION:
            changed.add(source)
        if relation == Relation.USES_ASSUMPTION:
            reduction_subjects.add(source)
        if relation == Relation.ALTERNATIVE_ROUTE:
            changed.add(target)
    changed.update(
        source for source, _, relation in old_edges - new_edges if relation == Relation.SUPPORTED_BY
    )

    old_lattice = previous.graph.assumptions
    new_lattice = updated.graph.assumptions
    logic_changed = (old_lattice.implications, old_lattice.conflicts) != (
        new_lattice.implications,
        new_lattice.conflicts,
    )
    rule_assumptions = {
        item for rule in new_lattice.implications for item in (*rule.premises, rule.conclusion)
    }
    rule_assumptions.update(
        item for conflict in new_lattice.conflicts for item in (conflict.left, conflict.right)
    )
    logic_changed = logic_changed or bool(changed & rule_assumptions)
    for source, target, relation in old_edges | new_edges:
        if relation == Relation.USES_ASSUMPTION and (logic_changed or target in changed):
            changed.add(source)
            reduction_subjects.add(source)
    for subject in reduction_subjects & new_nodes.keys():
        changed.update(after.candidate_routes(subject))

    affected = changed & common
    for node_id in changed:
        if node_id in old_nodes:
            affected.update(before.affected_by(node_id))
        if node_id in new_nodes:
            affected.update(after.affected_by(node_id))
    return affected & common


def read_statement_file(path: Path) -> str:
    if path.suffix != ".tex":
        raise ResearchError(f"Expected a .tex file: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
        statement_hash(text)
        return text
    except (OSError, ValueError):
        raise ResearchError(f"Cannot read a non-empty UTF-8 LaTeX statement: {path}") from None


class ResearchStore:
    def __init__(self, workspace_root: Path):
        self.root = workspace_root.resolve()
        if not self.root.is_dir():
            raise ResearchError(f"Research workspace is not a directory: {self.root}")

    def _path(self, relative: str) -> Path:
        return workspace_path(self.root, relative)

    @property
    def manifest(self) -> Path:
        return self._path("research/research.yaml")

    @contextmanager
    def _writing(self) -> Iterator[None]:
        lock = self._path(".amre/research.lock")
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(lock), timeout=10):
                yield
        except Timeout:
            raise ResearchError("Timed out waiting for another research update") from None
        except OSError as exc:
            raise ResearchError(f"Could not write research workspace ({exc.strerror})") from None

    def _check_files(
        self,
        document: WorkspaceDocument,
        graph: ResearchGraph,
        pending: Mapping[str, str],
        immutable_paths: Collection[str] = (),
    ) -> None:
        checked = set()
        staged = set(pending).union(immutable_paths)
        sources = (*graph.document.nodes, *document.literature.candidates)
        for source in sources:
            if (reference := source.statement) and reference not in checked:
                path = self._path(reference.path)
                text = pending.get(reference.path)
                if text is None or path.exists():
                    text = read_statement_file(path)
                if statement_hash(text) != reference.sha256:
                    raise ResearchError(f"LaTeX statement digest mismatch: {reference.path}")
                checked.add(reference)
        for node in graph.document.nodes:
            if node.evidence:
                self._check_artifact(node.evidence.file_path, staged)
            if node.obstruction:
                self._check_artifact(node.obstruction.step.file, staged)
        for contract in document.contracts:
            if checkpoint := contract.checkpoint_reference:
                self._check_artifact(checkpoint.path, staged)

    def _check_artifact(self, relative: str, pending: Collection[str]) -> None:
        path = self._path(relative)
        if relative in pending:
            return
        try:
            exists = path.is_file()
        except OSError:
            exists = False
        if not exists:
            raise ResearchError(f"Research artifact not found: {relative}")

    def _load(self, *, check_files: bool = True) -> tuple[WorkspaceDocument, ResearchGraph]:
        raw = read_yaml(self.manifest, missing_ok=True)
        document = validate(WorkspaceDocument, raw if raw is not None else {}, "research workspace")
        graph = _compile(document)
        if check_files:
            self._check_files(document, graph, {})
        return document, graph

    def read(self) -> WorkspaceDocument:
        return self._load()[0]

    def graph(self) -> ResearchGraph:
        return self._load()[1]

    def snapshot(self) -> tuple[WorkspaceDocument, ResearchGraph]:
        return self._load()

    def load_contract(self, problem_id: str) -> ResearchContract:
        return self.read().contract(problem_id)

    def read_statement(self, reference: StatementRef) -> str:
        text = read_statement_file(self._path(reference.path))
        if statement_hash(text) != reference.sha256:
            raise ResearchError(f"LaTeX statement digest mismatch: {reference.path}")
        return text

    def _commit(
        self,
        previous: WorkspaceDocument,
        before: ResearchGraph,
        updated: WorkspaceDocument,
        pending: Mapping[str, str],
    ) -> None:
        node_ids = {node.id for node in updated.graph.nodes}
        node_ids.update(contract.problem_id for contract in updated.contracts)
        reviews = {key: value for key, value in updated.reviews.items() if key in node_ids}
        updated = validate(
            WorkspaceDocument, updated.model_copy(update={"reviews": reviews}), "research workspace"
        )
        after = _compile(updated)
        for node_id in _changed_nodes(previous, before, updated, after):
            reviews[node_id] = Review(
                revision=before.review(node_id).revision + 1, status=ReviewStatus.NEEDS_RECHECK
            )
        updated = validate(
            WorkspaceDocument,
            updated.model_copy(update={"reviews": dict(sorted(reviews.items()))}),
            "research workspace",
        )
        updated, immutable = self._stage_research_events(previous, before, updated, after, pending)
        self._write(updated, pending, immutable=immutable)

    def _stage_research_events(
        self,
        previous: WorkspaceDocument,
        before: ResearchGraph,
        updated: WorkspaceDocument,
        after: ResearchGraph,
        pending: Mapping[str, str],
    ) -> tuple[WorkspaceDocument, Mapping[str, bytes]]:
        old_problems = {contract.problem_id for contract in previous.contracts}
        old_nodes = {node.id for node in before.document.nodes}
        added_evidence = tuple(
            node.evidence
            for node in after.document.nodes
            if node.evidence is not None and node.id not in old_nodes
        )
        batch = ArchiveBatch(self.root, pending)
        revision = None
        events = []
        for contract in updated.contracts:
            problem_id = contract.problem_id
            if problem_id not in old_problems:
                events.append(
                    (
                        contract.statement.path,
                        EventInput(kind=EventKind.PROBLEM_CREATED, problem_id=problem_id),
                        contract,
                    )
                )
            if added_evidence:
                subjects = {problem_id, *after.research_inputs(problem_id)}
                events.extend(
                    (
                        contract.statement.path,
                        EventInput(
                            kind=EventKind.EVIDENCE_ADDED,
                            problem_id=problem_id,
                            target_id=evidence.evidence_id,
                            artifact_paths=(evidence.file_path,),
                        ),
                        after.node(evidence.evidence_id),
                    )
                    for evidence in added_evidence
                    if evidence.assertion_id in subjects
                )
        if events:
            revision = repository_revision(self.root)
        event_ids = list(updated.event_ids)
        for anchor, data, subject in events:
            event_ids.append(
                batch.event(
                    data,
                    sequence=len(event_ids) + 1,
                    anchor=anchor,
                    revision=revision,
                    subject=subject,
                ).event_id
            )
        return updated.model_copy(update={"event_ids": tuple(event_ids)}), batch.files

    def _write(
        self,
        document: WorkspaceDocument,
        pending: Mapping[str, str],
        *,
        immutable: Mapping[str, bytes] | None = None,
    ) -> None:
        document = validate(WorkspaceDocument, document, "research workspace")
        graph = _compile(document)
        self._check_files(document, graph, pending, immutable or ())
        referenced = {node.statement.path for node in graph.document.nodes if node.statement}
        referenced.update(item.statement.path for item in document.literature.candidates)
        if unused := pending.keys() - referenced:
            raise ResearchError(f"Unused LaTeX statement: {min(unused)}")
        for relative, text in pending.items():
            path = self._path(relative)
            if not path.exists():
                atomic_write(path, text)
        if immutable:
            publish_immutable(self.root, immutable)
        atomic_write(self.manifest, yaml_text(document.model_dump(mode="json")))

    def create(
        self,
        metadata: object,
        statement: str,
        *,
        graph: GraphDocument | None = None,
        statements: Iterable[str] = (),
        expected: WorkspaceDocument | None = None,
    ) -> ResearchContract:
        spec = validate(ContractInput, metadata, "contract metadata")
        reference = _statement_ref(statement)
        pending = _statement_files(statements)
        pending[reference.path] = statement
        contract = validate(
            ResearchContract, {**spec.model_dump(), "statement": reference}, "research contract"
        )
        with self._writing():
            previous, before = self._load()
            if expected is not None and previous != expected:
                raise WorkspaceChanged("Research workspace changed since it was read")
            updated = previous.model_copy(
                update={
                    "contracts": (*previous.contracts, contract),
                    "graph": previous.graph if graph is None else graph,
                }
            )
            self._commit(previous, before, updated, pending)
        return contract

    def update(
        self,
        problem_id: str,
        patch: Mapping[str, object],
        *,
        statement: str | None = None,
        graph: GraphDocument | None = None,
        statements: Iterable[str] = (),
        expected: WorkspaceDocument | None = None,
    ) -> ResearchContract:
        if "statement" in patch:
            raise ResearchError("Supply a LaTeX file separately from the metadata patch")
        if "problem_id" in patch and patch["problem_id"] != problem_id:
            raise ResearchError("Cannot change problem_id")
        with self._writing():
            previous, before = self._load()
            if expected is not None and previous != expected:
                raise WorkspaceChanged("Research workspace changed since it was read")
            contract = previous.contract(problem_id)
            fields = contract.model_dump()
            fields.update(patch)
            pending = _statement_files(statements)
            if statement is not None:
                reference = _statement_ref(statement)
                fields["statement"] = reference
                pending[reference.path] = statement
            replacement = validate(ResearchContract, fields, "research contract")
            updated = previous.model_copy(
                update={
                    "contracts": tuple(
                        replacement if item.problem_id == problem_id else item
                        for item in previous.contracts
                    ),
                    "graph": previous.graph if graph is None else graph,
                }
            )
            self._commit(previous, before, updated, pending)
        return replacement

    def replace_graph(
        self,
        graph: GraphDocument,
        *,
        expected: GraphDocument | None = None,
        statements: Iterable[str] = (),
    ) -> None:
        pending = _statement_files(statements)
        with self._writing():
            previous, before = self._load()
            if expected is not None and previous.graph != expected:
                raise ResearchError("Research graph changed since it was read")
            updated = previous.model_copy(update={"graph": graph})
            self._commit(previous, before, updated, pending)

    def register_evidence(
        self, record: EvidenceRecord, *, expected: WorkspaceDocument | None = None
    ) -> None:
        """Attach a validated record to its assertion without changing claim status."""
        record = validate(EvidenceRecord, record, "evidence record")
        with self._writing():
            previous, before = self._load()
            if expected is not None and previous != expected:
                raise WorkspaceChanged("Research changed before evidence registration")
            subject = before.node(record.assertion_id)
            evidence_node = GraphNode(
                id=record.evidence_id,
                kind=NodeKind.EVIDENCE,
                label=record.run.tool,
                evidence=record,
            )
            graph = previous.graph
            contracts = previous.contracts
            if any(contract.problem_id == subject.id for contract in contracts):
                contracts = tuple(
                    contract.model_copy(
                        update={
                            "evidence_references": (
                                *contract.evidence_references,
                                record.evidence_id,
                            )
                        }
                    )
                    if contract.problem_id == subject.id
                    else contract
                    for contract in contracts
                )
            else:
                edge = GraphEdge(
                    source=subject.id, target=evidence_node.id, relation=Relation.SUPPORTED_BY
                )
                graph = graph.model_copy(update={"edges": (*graph.edges, edge)})
            graph = graph.model_copy(update={"nodes": (*graph.nodes, evidence_node)})
            updated = previous.model_copy(update={"graph": graph, "contracts": contracts})
            self._commit(previous, before, updated, {})

    def acknowledge_recheck(self, node_id: str, *, revision: int) -> None:
        """Record explicit review of a revision without granting proof or evidence status."""
        with self._writing():
            document, graph = self._load()
            review = graph.review(node_id)
            if review.revision != revision:
                raise ResearchError(f"Revision changed since {node_id} was reviewed")
            if review.status == ReviewStatus.NEEDS_RECHECK:
                reviews = {**document.reviews, node_id: Review(revision=revision)}
                updated = validate(
                    WorkspaceDocument,
                    document.model_copy(update={"reviews": reviews}),
                    "research workspace",
                )
                self._write(updated, {})


def _statement_ref(text: str) -> StatementRef:
    try:
        return StatementRef.from_text(text)
    except ValueError as exc:
        raise ResearchError(str(exc)) from None


def _statement_files(statements: Iterable[str]) -> dict[str, str]:
    return {_statement_ref(text).path: text for text in statements}
