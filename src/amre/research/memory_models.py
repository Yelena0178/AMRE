"""Versioned checkpoints, event records, and archived research inputs."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from amre.research.graph import ASSERTIONS, GraphDocument, GraphNode, NodeKind
from amre.research.models import (
    Budget,
    GitRevision,
    Identifier,
    PositiveInt,
    Record,
    RelativePath,
    ResearchContract,
    ResearchStatus,
    Review,
    Sha256,
    Text,
    VersionedRecord,
    unique_ids,
)

NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]


class ArchiveRef(Record):
    path: RelativePath
    sha256: Sha256
    size: NonnegativeInt

    @property
    def blob_path(self) -> str:
        return f"research/archive/{self.sha256}"


class EventKind(StrEnum):
    PROBLEM_CREATED = "PROBLEM_CREATED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    ATTEMPT_COMPLETED = "ATTEMPT_COMPLETED"
    CLAIM_PROPOSED = "CLAIM_PROPOSED"
    EVIDENCE_ADDED = "EVIDENCE_ADDED"
    OBSTACLE_DISCOVERED = "OBSTACLE_DISCOVERED"
    ROUTE_SWITCHED = "ROUTE_SWITCHED"
    PROBLEM_PAUSED = "PROBLEM_PAUSED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"


class AttemptOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EventInput(Record):
    kind: EventKind
    problem_id: Identifier
    target_id: Identifier | None = None
    summary: Text | None = None
    outcome: AttemptOutcome | None = None
    active_route: Identifier | None = None
    artifact_paths: tuple[RelativePath, ...] = ()

    @model_validator(mode="after")
    def _check_payload(self) -> Self:
        needs_target = self.kind not in {EventKind.PROBLEM_CREATED, EventKind.PROBLEM_PAUSED}
        if needs_target != (self.target_id is not None):
            raise ValueError(f"invalid target_id for {self.kind}")
        if self.kind == EventKind.ATTEMPT_COMPLETED:
            if self.outcome is None or self.summary is None:
                raise ValueError("completed attempts require an outcome and summary")
        elif self.outcome is not None:
            raise ValueError("only completed attempts accept an outcome")
        if self.active_route is not None and self.kind != EventKind.CHECKPOINT_CREATED:
            raise ValueError("only checkpoint events include an active_route snapshot")
        unique_ids(self.artifact_paths, "event artifact paths")
        return self


class ResearchEvent(VersionedRecord):
    event_id: Identifier
    sequence: PositiveInt
    timestamp: AwareDatetime
    repository_revision: GitRevision | None
    data: EventInput
    artifacts: Annotated[tuple[ArchiveRef, ...], Field(min_length=1)]
    subject: ResearchContract | GraphNode | None = None

    @model_validator(mode="after")
    def _check_artifacts(self) -> Self:
        paths = tuple(ref.path for ref in self.artifacts)
        unique_ids(paths, "archived event paths")
        if not set(self.data.artifact_paths).issubset(paths):
            raise ValueError("event artifact paths must be archived")
        target_kinds = {
            EventKind.CLAIM_PROPOSED: ASSERTIONS,
            EventKind.EVIDENCE_ADDED: {NodeKind.EVIDENCE},
            EventKind.OBSTACLE_DISCOVERED: {NodeKind.OBSTACLE},
            EventKind.ROUTE_SWITCHED: {NodeKind.PROOF_ROUTE},
        }
        if self.data.kind == EventKind.PROBLEM_CREATED:
            if not isinstance(self.subject, ResearchContract) or (
                self.subject.problem_id != self.data.problem_id
                or self.subject.statement.path not in paths
            ):
                raise ValueError("creation event must archive its contract and statement")
        elif self.data.kind in target_kinds:
            if not isinstance(self.subject, GraphNode) or (
                self.subject.kind not in target_kinds[self.data.kind]
                or self.subject.id != self.data.target_id
            ):
                raise ValueError("event must contain its matching graph node")
            if self.subject.statement and self.subject.statement.path not in paths:
                raise ValueError("event must archive the subject statement")
            if self.subject.evidence and self.subject.evidence.file_path not in paths:
                raise ValueError("event must archive the evidence artifact")
        elif self.subject is not None:
            raise ValueError("this event type does not include a subject snapshot")
        return self


class AttemptRecord(Record):
    attempt_id: Identifier
    started_event: Identifier
    completed_event: Identifier | None = None
    outcome: AttemptOutcome | None = None
    summary: Text | None = None


class MemoryResult(Record):
    assertion_id: Identifier
    summary: Text
    evidence_references: tuple[Identifier, ...] = ()


class ContextSourceKind(StrEnum):
    EXCLUDED_METHOD = "EXCLUDED_METHOD"
    DERIVATION = "DERIVATION"
    LITERATURE = "LITERATURE"


class ContextSource(Record):
    kind: ContextSourceKind
    path: RelativePath


class BudgetUsage(Record):
    tokens_used: NonnegativeInt = 0
    elapsed_seconds: NonnegativeInt = 0


class BudgetSnapshot(Budget, BudgetUsage):
    pass


class CheckpointNotes(VersionedRecord):
    active_route: Identifier | None = None
    established_results: tuple[MemoryResult, ...] = ()
    candidate_results: tuple[MemoryResult, ...] = ()
    next_research_action: Text
    context_sources: tuple[ContextSource, ...] = ()
    evidence_references: tuple[Identifier, ...] = ()
    strategy_version: Text = "manual-v1"
    dispatch_id: Identifier | None = None
    dispatch_phase: Literal["BEFORE_SWITCH", "READY", "AFTER_ATTEMPT"] | None = None
    deep_session_id: Identifier | None = None

    @model_validator(mode="after")
    def _dispatch_metadata(self) -> Self:
        if (self.dispatch_id is None) != (self.dispatch_phase is None):
            raise ValueError("Checkpoint dispatch ID and phase must be provided together")
        return self


class CheckpointInput(CheckpointNotes):
    active_obstacles: tuple[Identifier, ...] | None = None
    unresolved_proof_obligations: tuple[Text, ...] | None = None
    usage: BudgetUsage = BudgetUsage()


class Checkpoint(CheckpointNotes):
    checkpoint_id: Identifier
    problem_id: Identifier
    research_status: ResearchStatus
    last_attempt: AttemptRecord | None
    active_obstacles: tuple[Identifier, ...]
    unresolved_proof_obligations: tuple[Text, ...]
    budget_snapshot: BudgetSnapshot
    repository_revision: GitRevision | None
    timestamp: AwareDatetime
    event_sequence: PositiveInt
    contract: ResearchContract
    graph: GraphDocument
    reviews: dict[Identifier, Review]
    artifacts: Annotated[tuple[ArchiveRef, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _check_snapshot(self) -> Self:
        if self.problem_id != self.contract.problem_id:
            raise ValueError("checkpoint and contract problem IDs differ")
        unique_ids(self.active_obstacles, "checkpoint obstacles")
        unique_ids(self.evidence_references, "checkpoint evidence references")
        unique_ids(tuple(ref.path for ref in self.artifacts), "checkpoint artifact paths")
        return self
