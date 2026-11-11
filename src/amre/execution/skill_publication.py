"""Publish a validated skill result through the existing graph transaction."""

from dataclasses import dataclass
from hashlib import sha256

from pydantic import Field

from amre.errors import ResearchError, WorkspaceChanged
from amre.execution.models import AttemptReceipt, ReasoningOutcome
from amre.execution.skill_models import (
    LemmaDecomposition,
    ProgressEvent,
    ProgressKind,
    ProofConstruction,
    ProofRouteExploration,
)
from amre.research.graph import GraphEdge, GraphNode, NodeKind
from amre.research.journal import load_events, publish_immutable, read_versioned, verify_blob
from amre.research.memory import RestoredState
from amre.research.models import (
    EvidenceRecord,
    ReviewStatus,
    RunInfo,
    StatementRef,
    VersionedRecord,
)
from amre.research.obstructions import ObstructionRecord
from amre.research.storage import workspace_path, yaml_text
from amre.research.store import ResearchStore


def draft_path(attempt_id: str, name: str) -> str:
    return f"research/attempts/{attempt_id}/draft-{name}"


def progress_path(attempt_id: str) -> str:
    return f"research/attempts/{attempt_id}/progress.yaml"


class ProgressBatch(VersionedRecord):
    events: tuple[ProgressEvent, ...] = Field(min_length=1)


@dataclass(frozen=True)
class Publication:
    root_evidence: tuple[str, ...]
    new_obstacles: tuple[str, ...] = ()
    progress: str | None = None


def _id(prefix: str, attempt: str, number: int) -> str:
    return f"{prefix}-{attempt}-{number}"


def _prior_obligations(store: ResearchStore, event_ids, problem: str, attempt: str) -> set:
    seen = set()
    for event in load_events(store.root, event_ids):
        data = event.data
        if (
            data.kind != "ATTEMPT_COMPLETED"
            or data.problem_id != problem
            or data.target_id == attempt
        ):
            continue
        for reference in event.artifacts:
            if reference.path == progress_path(data.target_id):
                verify_blob(store.root, reference)
                batch = read_versioned(store.root, reference.blob_path, ProgressBatch)
                seen.update(
                    (item.subject_id, item.evidence_id)
                    for item in batch.events
                    if item.kind == ProgressKind.OBLIGATION_SATISFIED
                )
    return seen


def publish_skill(
    store: ResearchStore, receipt: AttemptReceipt, original: RestoredState
) -> Publication:
    task = receipt.task
    result = receipt.result
    detail = result.skill_output
    attempt = task.attempt_id
    problem = task.problem_id
    document, graph = store.snapshot()
    contract = document.contract(problem)
    nodes = {node.id: node for node in document.graph.nodes}
    edges = list(document.graph.edges)
    original_obstacles = {
        node.obstruction.identity(problem, task.assertion_revision): node.obstruction
        for node in original.graph.document.nodes
        if node.obstruction is not None
        and node.obstruction.problem_id == problem
        and node.obstruction.assertion_revision == task.assertion_revision
    }
    artifact_ids = {
        artifact.name: _id("Evidence", attempt, i)
        for i, artifact in enumerate(result.all_artifacts)
    }
    already_applied = bool(artifact_ids) and set(artifact_ids.values()).issubset(nodes)
    if not already_applied and (
        not original.workspace_matches or graph.review(problem).revision != task.assertion_revision
    ):
        raise WorkspaceChanged("Research changed during the attempt")

    root_refs = list(contract.evidence_references)
    problem_obstacles = list(contract.obstacles)
    routes = list(contract.research_routes)
    statements = []
    artifact_subjects = {}
    created_obstacles = []
    progress = []
    reduction = None
    route_id = _id("Route", attempt, 0)
    if isinstance(detail, ProofRouteExploration) and detail.reduction is not None:
        claim = detail.reduction
        reduction = (
            claim.label,
            claim.statement,
            claim.dependencies,
            claim.assumptions,
            claim.artifact,
        )
        artifact_subjects[claim.artifact] = route_id
    elif isinstance(detail, LemmaDecomposition) and detail.lemmas:
        lemmas = []
        for i, claim in enumerate(detail.lemmas):
            reference = StatementRef.from_text(claim.statement)
            same = next(
                (
                    node.id
                    for node in original.graph.document.nodes
                    if node.kind == NodeKind.LEMMA
                    and node.statement.sha256 == reference.sha256
                    and set(original.graph.assumptions_for(node.id)) == set(claim.assumptions)
                    and {
                        edge.target
                        for edge in original.graph.document.edges
                        if edge.source == node.id and edge.relation == "DEPENDS_ON"
                    }
                    == set(claim.dependencies)
                ),
                None,
            )
            lemma_id = same or _id("Lemma", attempt, i)
            lemmas.append(lemma_id)
            artifact_subjects[claim.artifact] = lemma_id
            if same:
                continue
            nodes[lemma_id] = GraphNode(
                id=lemma_id,
                kind=NodeKind.LEMMA,
                label=claim.label,
                statement=reference,
                claim_status="PROOF_CANDIDATE",
            )
            statements.append(claim.statement)
            edges.extend(
                GraphEdge(source=lemma_id, target=item, relation=relation)
                for relation, items in (
                    ("DEPENDS_ON", claim.dependencies),
                    ("USES_ASSUMPTION", claim.assumptions),
                )
                for item in dict.fromkeys(items)
            )
        reduction = (
            "Candidate lemma composition",
            detail.composition,
            tuple(lemmas),
            task.skill_inputs.get("assumptions", ()),
            detail.lemmas[0].artifact,
        )

    existing_ids = {node.id for node in original.graph.document.nodes}
    repeated_route = False
    if reduction is not None:
        _, statement, dependencies, assumptions, name = reduction
        digest = StatementRef.from_text(statement).sha256
        existing = next(
            (
                key
                for key in original.graph.candidate_routes(problem)
                if original.graph.node(key).statement.sha256 == digest
                and set(original.graph.assumptions_for(key)) == set(assumptions)
                and {
                    edge.target
                    for edge in original.graph.document.edges
                    if edge.source == key and edge.relation == "REQUIRES_ALL"
                }
                == set(dependencies)
            ),
            None,
        )
        if existing:
            route_id = existing
            repeated_route = True
            if isinstance(detail, ProofRouteExploration):
                artifact_subjects[name] = existing

    def evidence(identifier: str, subject: str, name: str) -> None:
        record = EvidenceRecord(
            evidence_id=identifier,
            source_type="MODEL",
            file_path=draft_path(attempt, name),
            git_revision=original.checkpoint.repository_revision,
            run=RunInfo(tool=receipt.adapter, version=receipt.model, run_id=attempt),
            check_scope=f"Unverified {task.reasoning_mode} candidate for {subject}",
            check_result="INCONCLUSIVE",
            coverage="PARTIAL",
            assertion_id=subject,
            assertion_revision=original.graph.review(subject).revision
            if subject in existing_ids
            else 1,
            direction="REFUTE"
            if result.outcome == ReasoningOutcome.COUNTEREXAMPLE_CANDIDATE
            else "SUPPORT",
        )
        nodes[identifier] = GraphNode(
            id=identifier, kind=NodeKind.EVIDENCE, label=name, evidence=record
        )
        if subject == problem:
            root_refs.append(identifier)
        else:
            edges.append(GraphEdge(source=subject, target=identifier, relation="SUPPORTED_BY"))

    def emit(kind: ProgressKind, subject: str, source: str, pointer: str) -> None:
        progress.append(
            ProgressEvent(
                attempt_id=attempt,
                problem_id=problem,
                route_id=task.route_id,
                kind=kind,
                subject_id=subject,
                source_file=source,
                evidence_id=pointer,
            )
        )

    prior_content = (
        {
            original.archive_ref(node.evidence.file_path).sha256
            for node in original.graph.document.nodes
            if node.evidence is not None and node.evidence.source_type == "MODEL"
        }
        if artifact_ids
        else set()
    )
    for artifact in result.all_artifacts:
        source = draft_path(attempt, artifact.name)
        try:
            data = workspace_path(store.root, source).read_bytes()
        except OSError:
            raise ResearchError(f"Missing attempt artifact: {artifact.name}") from None
        if data != artifact.text.encode():
            raise ResearchError(f"Attempt artifact differs from receipt: {artifact.name}")
        subject = artifact_subjects.get(artifact.name, problem)
        identifier = artifact_ids[artifact.name]
        evidence(identifier, subject, artifact.name)
        if sha256(data).hexdigest() in prior_content:
            emit(ProgressKind.TEXT_REPEAT, subject, source, identifier)
        elif (subject != route_id or repeated_route) and result.outcome not in {
            ReasoningOutcome.NO_PROGRESS,
            ReasoningOutcome.BLOCKED,
        }:
            emit(ProgressKind.CANDIDATE_DERIVATION, subject, source, identifier)

    if reduction is not None and not repeated_route:
        label, statement, dependencies, assumptions, name = reduction
        nodes[route_id] = GraphNode(
            id=route_id,
            kind=NodeKind.PROOF_ROUTE,
            label=label,
            statement=StatementRef.from_text(statement),
            claim_status="PROOF_CANDIDATE",
        )
        routes.append(route_id)
        statements.append(statement)
        edges.extend(
            GraphEdge(source=route_id, target=item, relation=relation)
            for relation, items in (
                ("REQUIRES_ALL", dependencies),
                ("USES_ASSUMPTION", assumptions),
            )
            for item in dict.fromkeys(items)
        )
        pointer = artifact_ids[name]
        if isinstance(detail, LemmaDecomposition):
            pointer = _id("Reduction", attempt, 0)
            evidence(pointer, route_id, name)
        emit(ProgressKind.CANDIDATE_REDUCTION, route_id, draft_path(attempt, name), pointer)

    for index, draft in enumerate(result.new_obstacles):
        previous = original_obstacles.get(draft.identity(problem, task.assertion_revision))
        obstacle_id = previous.obstacle_id if previous else _id("Obstacle", attempt, index)
        path = draft_path(attempt, draft.step.file)
        pointer = artifact_ids[draft.step.file]
        scope = (task.route_id,) if task.route_id else ()
        if previous:
            record = previous.model_copy(
                update={
                    "attempts": (*previous.attempts, attempt),
                    "version": previous.version + 1,
                    "routes": tuple(dict.fromkeys((*previous.routes, *scope))),
                    "attempted_methods": tuple(
                        dict.fromkeys((*previous.attempted_methods, *draft.attempted_methods))
                    ),
                    "potential_resolutions": tuple(
                        dict.fromkeys(
                            (*previous.potential_resolutions, *draft.potential_resolutions)
                        )
                    ),
                    "evidence_refs": tuple(
                        dict.fromkeys((*previous.evidence_refs, *draft.evidence_refs, pointer))
                    ),
                }
            )
        else:
            record = ObstructionRecord(
                **draft.model_dump(exclude={"step", "evidence_refs"}),
                obstacle_id=obstacle_id,
                problem_id=problem,
                assertion_revision=task.assertion_revision,
                routes=scope,
                attempts=(attempt,),
                step=draft.step.model_copy(update={"file": path}),
                evidence_refs=tuple(dict.fromkeys((*draft.evidence_refs, pointer))),
            )
            created_obstacles.append(obstacle_id)
            emit(ProgressKind.OBSTACLE_DISCOVERED, obstacle_id, path, pointer)
        nodes[obstacle_id] = GraphNode(
            id=obstacle_id, kind=NodeKind.OBSTACLE, label=record.description, obstruction=record
        )
        if task.route_id:
            edges.append(GraphEdge(source=task.route_id, target=obstacle_id, relation="BLOCKED_BY"))
        else:
            problem_obstacles.append(obstacle_id)

    if isinstance(detail, ProofConstruction) and detail.obligation_evidence:
        prior = _prior_obligations(store, document.event_ids, problem, attempt)
        for item in detail.obligation_evidence:
            record = original.graph.node(item.evidence_id).evidence
            if (
                record.assertion_id == problem
                and record.check_scope == f"criterion:{item.criterion_id}"
                and original.graph.review(problem).status == ReviewStatus.CURRENT
                and record.applies(task.assertion_revision, "SUPPORT")
                and (item.criterion_id, item.evidence_id) not in prior
                and not any(
                    other.applies(task.assertion_revision, "REFUTE")
                    for other in original.graph.evidence_for(problem)
                )
            ):
                prior.add((item.criterion_id, item.evidence_id))
                emit(
                    ProgressKind.OBLIGATION_SATISFIED,
                    item.criterion_id,
                    record.file_path,
                    record.evidence_id,
                )

    if not already_applied and (artifact_ids or reduction or result.new_obstacles):
        status = {
            ReasoningOutcome.PROOF_CANDIDATE: "PROOF_CANDIDATE",
            ReasoningOutcome.COUNTEREXAMPLE_CANDIDATE: "REFUTATION_CANDIDATE",
            ReasoningOutcome.VALID_REDUCTION_CANDIDATE: "PARTIAL",
            ReasoningOutcome.PARTIAL_PROGRESS: "PARTIAL",
        }.get(result.outcome, contract.claim_status)
        store.update(
            problem,
            {
                "research_routes": tuple(dict.fromkeys(routes)),
                "obstacles": tuple(dict.fromkeys(problem_obstacles)),
                "evidence_references": tuple(dict.fromkeys(root_refs)),
                "claim_status": status,
            },
            graph=document.graph.model_copy(
                update={
                    "nodes": tuple(nodes.values()),
                    "edges": tuple(dict.fromkeys(edges)),
                }
            ),
            statements=statements,
            expected=document,
        )

    path = None
    if progress:
        path = progress_path(attempt)
        batch = ProgressBatch(events=tuple(progress))
        try:
            publish_immutable(store.root, {path: yaml_text(batch.model_dump(mode="json")).encode()})
        except OSError as exc:
            raise ResearchError(f"Could not persist progress: {exc.strerror}") from None
    return Publication(
        tuple(
            identifier
            for name, identifier in artifact_ids.items()
            if artifact_subjects.get(name, problem) == problem
        ),
        tuple(created_obstacles),
        path,
    )
