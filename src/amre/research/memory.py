"""Persistent research memory over the committed workspace journal."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path
from uuid import uuid4

from amre.errors import ResearchError, WorkspaceChanged
from amre.research.assumptions import AssumptionDocument
from amre.research.graph import ASSERTIONS, GraphDocument, NodeKind, ResearchGraph
from amre.research.journal import (
    ArchiveBatch,
    ReplayState,
    archived_bytes,
    checkpoint_path,
    load_events,
    matches_live_artifact,
    read_versioned,
    replay,
    repository_revision,
    verify_blob,
)
from amre.research.memory_models import (
    ArchiveRef,
    BudgetSnapshot,
    Checkpoint,
    CheckpointInput,
    EventInput,
    EventKind,
    ResearchEvent,
)
from amre.research.models import (
    ArtifactLocation,
    EvidenceDirection,
    ResearchStatus,
    ReviewStatus,
    StatementRef,
    statement_hash,
)
from amre.research.storage import validate
from amre.research.store import ResearchStore, WorkspaceDocument


def _scope(graph: ResearchGraph, problem_id: str) -> ResearchGraph:
    selected = {problem_id, *graph.research_inputs(problem_id)}
    lattice = graph.document.assumptions
    relevant = {
        node.id
        for node in graph.document.nodes
        if node.id in selected and node.kind == NodeKind.ASSUMPTION
    }
    links = [set((*rule.premises, rule.conclusion)) for rule in lattice.implications]
    links.extend({conflict.left, conflict.right} for conflict in lattice.conflicts)
    relevant.update(rule.conclusion for rule in lattice.implications if not rule.premises)
    while True:
        linked = relevant.union(*(link for link in links if link & relevant))
        if linked == relevant:
            break
        relevant = linked
    selected.update(relevant)
    document = GraphDocument(
        nodes=tuple(node for node in graph.document.nodes if node.id in selected),
        edges=tuple(
            edge
            for edge in graph.document.edges
            if edge.source in selected and edge.target in selected
        ),
        assumptions=AssumptionDocument(
            sets=tuple(item for item in lattice.sets if relevant.issuperset(item.members)),
            implications=tuple(
                rule
                for rule in lattice.implications
                if relevant.issuperset((*rule.premises, rule.conclusion))
            ),
            conflicts=tuple(
                item for item in lattice.conflicts if relevant.issuperset((item.left, item.right))
            ),
        ),
    )
    return ResearchGraph(document, {node: graph.review(node) for node in sorted(selected)})


def _source_paths(checkpoint: Checkpoint) -> set[str]:
    paths = {source.path for source in checkpoint.context_sources}
    for node in checkpoint.graph.nodes:
        if node.statement is not None:
            paths.add(node.statement.path)
        if node.evidence is not None:
            paths.add(node.evidence.file_path)
        if node.obstruction is not None:
            paths.add(node.obstruction.step.file)
    return paths


def _result_evidence(checkpoint: Checkpoint, graph: ResearchGraph) -> None:
    for evidence_id in checkpoint.evidence_references:
        if graph.node(evidence_id).kind != NodeKind.EVIDENCE:
            raise ResearchError(f"Expected an evidence reference: {evidence_id}")
    results = chain(
        ((result, True) for result in checkpoint.established_results),
        ((result, False) for result in checkpoint.candidate_results),
    )
    for result, established in results:
        subject = graph.node(result.assertion_id)
        if subject.kind not in ASSERTIONS:
            raise ResearchError(f"Expected a result assertion: {result.assertion_id}")
        records = {item.evidence_id: item for item in graph.evidence_for(subject.id)}
        if set(result.evidence_references) - records.keys():
            raise ResearchError(f"Result evidence must refer to its assertion: {subject.id}")
        if established:
            review = graph.review(subject.id)
            if (
                review.status != ReviewStatus.CURRENT
                or not any(
                    records[key].applies(review.revision, EvidenceDirection.SUPPORT)
                    for key in result.evidence_references
                )
                or any(
                    item.applies(review.revision, EvidenceDirection.REFUTE)
                    for item in records.values()
                )
            ):
                raise ResearchError(
                    f"Established result lacks current supporting evidence: {subject.id}"
                )


def _checkpoint_graph(checkpoint: Checkpoint) -> ResearchGraph:
    if checkpoint.reviews.keys() != {node.id for node in checkpoint.graph.nodes}:
        raise ResearchError("Checkpoint must include the review version of every graph node")
    graph = ResearchGraph(checkpoint.graph, checkpoint.reviews)
    node = graph.node(checkpoint.problem_id)
    if node.kind != NodeKind.RESEARCH_PROBLEM or node.statement != checkpoint.contract.statement:
        raise ResearchError("Checkpoint contract and graph statements differ")
    if (
        checkpoint.active_route is not None
        and graph.node(checkpoint.active_route).kind != NodeKind.PROOF_ROUTE
    ):
        raise ResearchError("Checkpoint active_route must be a proof route")
    for obstacle in checkpoint.active_obstacles:
        if graph.node(obstacle).kind != NodeKind.OBSTACLE:
            raise ResearchError(f"Expected an active obstacle: {obstacle}")
    if checkpoint.contract.budget.model_dump() != checkpoint.budget_snapshot.model_dump(
        include={"max_tokens", "timeout_seconds"}
    ):
        raise ResearchError("Checkpoint and contract budget limits differ")
    _result_evidence(checkpoint, graph)
    paths = {ref.path for ref in checkpoint.artifacts}
    if missing := _source_paths(checkpoint) - paths:
        raise ResearchError(f"Checkpoint has unarchived references: {', '.join(sorted(missing))}")
    return graph


@dataclass(frozen=True)
class RestoredState:
    root: Path
    checkpoint: Checkpoint
    graph: ResearchGraph
    failed_attempts: tuple[ResearchEvent, ...]
    workspace_matches: bool

    def archive_ref(self, path: str) -> ArchiveRef:
        for reference in self.checkpoint.artifacts:
            if reference.path == path:
                return reference
        raise ResearchError(f"File is not archived in this checkpoint: {path}")

    def read_artifact(self, path: str) -> bytes:
        return archived_bytes(self.root, self.archive_ref(path))

    def read_text(self, path: str) -> str:
        contents = self.read_artifact(path)
        try:
            return contents.decode("utf-8-sig")
        except UnicodeError:
            raise ResearchError(f"Archived context source is not UTF-8 text: {path}") from None

    def read_statement(self, reference: StatementRef) -> str:
        text = self.read_text(reference.path)
        try:
            matches = statement_hash(text) == reference.sha256
        except ValueError:
            matches = False
        if not matches:
            raise ResearchError(f"Checkpoint statement digest mismatch: {reference.path}")
        return text


@dataclass(frozen=True)
class MemoryInventory:
    document: WorkspaceDocument
    graph: ResearchGraph
    events: tuple[ResearchEvent, ...]
    checkpoints: tuple[Checkpoint, ...]
    states: dict[str, ReplayState]


class ResearchMemory:
    def __init__(self, workspace_root: Path):
        self.research = ResearchStore(workspace_root)
        self.root = self.research.root

    def _events(self, document: WorkspaceDocument) -> tuple[ResearchEvent, ...]:
        events = load_events(self.root, document.event_ids)
        problems = {contract.problem_id for contract in document.contracts}
        if any(event.data.problem_id not in problems for event in events):
            raise ResearchError("Event log references a missing research problem")
        return events

    def _publish(
        self, document: WorkspaceDocument, event: ResearchEvent, batch: ArchiveBatch
    ) -> None:
        updated = document.model_copy(update={"event_ids": (*document.event_ids, event.event_id)})
        self.research._write(updated, {}, immutable=batch.files)

    def append_event(
        self,
        payload: object,
        *,
        expected: WorkspaceDocument | None = None,
    ) -> ResearchEvent:
        """Record an explicit research action; problem creation and checkpointing emit their own."""
        data = validate(EventInput, payload, "research event")
        if data.kind in {
            EventKind.PROBLEM_CREATED,
            EventKind.CHECKPOINT_CREATED,
            EventKind.EVIDENCE_ADDED,
        }:
            raise ResearchError(f"{data.kind} is published by the corresponding research operation")
        with self.research._writing():
            document, graph = self.research.snapshot()
            if expected is not None and document != expected:
                raise WorkspaceChanged("Research workspace changed before event publication")
            contract = document.contract(data.problem_id)
            focused = _scope(graph, data.problem_id)
            paths = list(data.artifact_paths)
            target = None
            if data.kind in {
                EventKind.CLAIM_PROPOSED,
                EventKind.OBSTACLE_DISCOVERED,
                EventKind.ROUTE_SWITCHED,
            }:
                target = focused.node(data.target_id)
                allowed = {
                    EventKind.CLAIM_PROPOSED: ASSERTIONS,
                    EventKind.OBSTACLE_DISCOVERED: {NodeKind.OBSTACLE},
                    EventKind.ROUTE_SWITCHED: {NodeKind.PROOF_ROUTE},
                }
                if target.kind not in allowed[data.kind]:
                    raise ResearchError(f"Invalid target for {data.kind}: {target.id}")
                if target.statement is not None and target.statement.path not in paths:
                    paths.append(target.statement.path)
            data = validate(
                EventInput,
                data.model_copy(update={"artifact_paths": tuple(paths)}),
                "research event",
            )
            events = self._events(document)
            batch = ArchiveBatch(self.root)
            event = batch.event(
                data,
                sequence=len(events) + 1,
                anchor=contract.statement.path,
                revision=repository_revision(self.root),
                subject=target,
            )
            replay((*events, event))
            self._publish(document, event, batch)
        return event

    def create_checkpoint(
        self,
        problem_id: str,
        payload: object,
        *,
        expected: WorkspaceDocument | None = None,
    ) -> Checkpoint:
        notes = validate(CheckpointInput, payload, "checkpoint input")
        with self.research._writing():
            document, live_graph = self.research.snapshot()
            if expected is not None and document != expected:
                raise WorkspaceChanged("Research workspace changed before checkpoint")
            contract = document.contract(problem_id)
            events = self._events(document)
            state = replay(events).get(problem_id, ReplayState())
            graph = _scope(live_graph, problem_id)
            checkpoint_id = f"CP-{uuid4().hex}"
            revision = repository_revision(self.root)
            batch = ArchiveBatch(self.root)
            paths = {source.path for source in notes.context_sources}
            evidence_ids = []
            obstacles = []
            for node in graph.document.nodes:
                if node.statement is not None:
                    paths.add(node.statement.path)
                if node.evidence is not None:
                    paths.add(node.evidence.file_path)
                    evidence_ids.append(node.id)
                if node.obstruction is not None:
                    paths.add(node.obstruction.step.file)
                if node.obstacle_open:
                    obstacles.append(node.id)
            fields = notes.model_dump(
                exclude={"usage", "active_obstacles", "unresolved_proof_obligations"}
            )
            fields.update(
                active_route=notes.active_route
                if "active_route" in notes.model_fields_set
                else state.active_route,
                evidence_references=notes.evidence_references
                if "evidence_references" in notes.model_fields_set
                else tuple(sorted(evidence_ids)),
            )
            for source in notes.context_sources:
                ref = batch.include(source.path)
                try:
                    batch.files[ref.blob_path].decode("utf-8-sig")
                except UnicodeError:
                    raise ResearchError(
                        f"Context source must be UTF-8 text: {source.path}"
                    ) from None
            checkpoint = Checkpoint(
                **fields,
                checkpoint_id=checkpoint_id,
                problem_id=problem_id,
                research_status=ResearchStatus.PAUSED if state.paused else contract.research_status,
                last_attempt=state.last_attempt,
                active_obstacles=notes.active_obstacles
                if notes.active_obstacles is not None
                else tuple(sorted(obstacles)),
                unresolved_proof_obligations=notes.unresolved_proof_obligations
                if notes.unresolved_proof_obligations is not None
                else tuple(item.description for item in contract.acceptance_criteria),
                budget_snapshot=BudgetSnapshot(
                    **contract.budget.model_dump(), **notes.usage.model_dump()
                ),
                repository_revision=revision,
                timestamp=datetime.now(UTC),
                event_sequence=len(events) + 1,
                contract=contract,
                graph=graph.document,
                reviews={node.id: graph.review(node.id) for node in graph.document.nodes},
                artifacts=tuple(batch.include(path) for path in sorted(paths)),
            )
            _checkpoint_graph(checkpoint)
            path = checkpoint_path(checkpoint_id)
            batch.record(path, checkpoint)
            event = batch.event(
                EventInput(
                    kind=EventKind.CHECKPOINT_CREATED,
                    problem_id=problem_id,
                    target_id=checkpoint_id,
                    active_route=checkpoint.active_route,
                    artifact_paths=(path,),
                ),
                sequence=checkpoint.event_sequence,
                anchor=contract.statement.path,
                revision=revision,
            )
            replacement = contract.model_copy(
                update={"checkpoint_reference": ArtifactLocation(path=path, git_revision=revision)}
            )
            updated = document.model_copy(
                update={
                    "contracts": tuple(
                        replacement if item.problem_id == problem_id else item
                        for item in document.contracts
                    )
                }
            )
            self._publish(updated, event, batch)
        return checkpoint

    def _checkpoint(self, event: ResearchEvent) -> Checkpoint:
        path = checkpoint_path(event.data.target_id)
        checkpoint = read_versioned(self.root, path, Checkpoint)
        if (
            checkpoint.checkpoint_id != event.data.target_id
            or checkpoint.problem_id != event.data.problem_id
            or checkpoint.event_sequence != event.sequence
            or checkpoint.repository_revision != event.repository_revision
            or checkpoint.active_route != event.data.active_route
        ):
            raise ResearchError(f"Checkpoint and event metadata differ: {path}")
        if path not in event.data.artifact_paths:
            raise ResearchError(f"Checkpoint event does not reference its file: {path}")
        # Compare parsed content so Git line-ending conversions do not affect YAML integrity.
        reference = next(ref for ref in event.artifacts if ref.path == path)
        verify_blob(self.root, reference)
        archived = read_versioned(self.root, reference.blob_path, Checkpoint)
        if checkpoint != archived:
            raise ResearchError(f"Checkpoint differs from its archived snapshot: {path}")
        return checkpoint

    def restore(self, problem_id: str, checkpoint_id: str | None = None) -> RestoredState:
        document, live_graph = self.research._load(check_files=False)
        current = document.contract(problem_id)
        events = self._events(document)
        replay(events)
        matching = (
            event
            for event in reversed(events)
            if event.data.kind == EventKind.CHECKPOINT_CREATED
            and event.data.problem_id == problem_id
            and (checkpoint_id is None or event.data.target_id == checkpoint_id)
        )
        event = next(matching, None)
        if event is None:
            raise ResearchError(f"No matching checkpoint for {problem_id}")
        checkpoint = self._checkpoint(event)
        graph = _checkpoint_graph(checkpoint)
        state = replay(events[: event.sequence - 1]).get(problem_id, ReplayState())
        self._verify_artifacts(
            chain(checkpoint.artifacts, *(item.artifacts for item in state.failed_attempts))
        )
        if checkpoint.last_attempt != state.last_attempt:
            raise ResearchError("Checkpoint attempt does not match event replay")
        current_scope = _scope(live_graph, problem_id)
        workspace_matches = (
            current.model_dump(exclude={"checkpoint_reference"})
            == checkpoint.contract.model_dump(exclude={"checkpoint_reference"})
            and current_scope.document == checkpoint.graph
            and all(current_scope.review(key) == value for key, value in checkpoint.reviews.items())
            and all(matches_live_artifact(self.root, ref) for ref in checkpoint.artifacts)
        )
        restored = RestoredState(
            self.root, checkpoint, graph, tuple(state.failed_attempts), workspace_matches
        )
        restored.read_statement(checkpoint.contract.statement)
        return restored

    def _verify_artifacts(self, references: Iterable[ArchiveRef]) -> None:
        seen = set()
        for ref in references:
            key = (ref.sha256, ref.size)
            if key not in seen:
                verify_blob(self.root, ref)
                seen.add(key)

    def inventory(self) -> MemoryInventory:
        document, graph = self.research.snapshot()
        events = self._events(document)
        states = replay(events)
        checkpoints = tuple(
            self._checkpoint(event)
            for event in events
            if event.data.kind == EventKind.CHECKPOINT_CREATED
        )
        for checkpoint in checkpoints:
            _checkpoint_graph(checkpoint)
        self._verify_artifacts(
            ref for record in (*events, *checkpoints) for ref in record.artifacts
        )
        registered = {checkpoint_path(item.checkpoint_id): item for item in checkpoints}
        for contract in document.contracts:
            reference = contract.checkpoint_reference
            if reference is not None and reference.path.startswith("research/checkpoints/"):
                item = registered.get(reference.path)
                if item is None or (
                    item.problem_id != contract.problem_id
                    or item.repository_revision != reference.git_revision
                ):
                    raise ResearchError(
                        f"Checkpoint reference does not match journal: {contract.problem_id}"
                    )
        return MemoryInventory(document, graph, events, checkpoints, states)
