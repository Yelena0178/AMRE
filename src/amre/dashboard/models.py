"""Allowlisted public dashboard schema."""

from typing import Literal

from pydantic import AwareDatetime, Field

from amre.execution.models import ExecutionState
from amre.research.graph import NodeKind, Relation
from amre.research.models import GitRevision, Identifier, Record, RelativePath, VersionedRecord

UsageValue = int | Literal["UNKNOWN"]


class PublicSelection(VersionedRecord):
    problems: tuple[Identifier, ...] = ()
    programs: tuple[Identifier, ...] = ()
    statement_ids: tuple[Identifier, ...] = ()
    detail_ids: tuple[Identifier, ...] = ()
    private_artifacts: tuple[RelativePath, ...] = ()


class PublicNode(Record):
    id: Identifier
    kind: NodeKind
    label: str
    review_status: str
    claim_status: str | None = None
    statement: str | None = None
    assumptions: tuple[Identifier, ...] = ()
    evidence: tuple[Identifier, ...] = ()
    detail: str | None = None
    category: str | None = None
    obstacle_status: str | None = None
    obstacle_attempts: tuple[Identifier, ...] = ()
    obstacle_routes: tuple[Identifier, ...] = ()


class PublicEdge(Record):
    source: Identifier
    target: Identifier
    relation: Relation


class PublicProblem(Record):
    problem_id: Identifier
    research_status: str
    claim_status: str
    route_ids: tuple[Identifier, ...]
    active_route: Identifier | None
    checkpoint: Identifier | None
    obstacle_ids: tuple[Identifier, ...]
    evidence_ids: tuple[Identifier, ...]
    definitions: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    next_action: str | None = None


class PublicProgram(Record):
    program: Identifier
    problems: tuple[Identifier, ...]


class PublicAttempt(Record):
    attempt_id: Identifier
    problem_id: Identifier
    route_id: Identifier | None
    session_id: Identifier | None
    state: ExecutionState | Literal["UNKNOWN"]
    strategy: str | None
    elapsed_ms: UsageValue
    reserved_ms: int
    tokens: UsageValue
    cost_microusd: UsageValue
    tool_calls: UsageValue


class PublicSession(Record):
    session_id: Identifier
    problem_id: Identifier
    route_id: Identifier | None
    state: ExecutionState
    elapsed_ms: int
    session_limit_ms: int | None
    checkpoint: Identifier | None
    continuity: str | None
    semantic_events: int


class PublicCheckpoint(Record):
    checkpoint_id: Identifier
    problem_id: Identifier
    route_id: Identifier | None
    session_id: Identifier | None
    dispatch_phase: str | None
    timestamp: AwareDatetime


class PublicEvent(Record):
    event_id: Identifier
    problem_id: Identifier
    kind: str
    route_id: Identifier | None = None
    timestamp: AwareDatetime


class PublicDecision(Record):
    decision_id: Identifier
    problem_id: Identifier
    route_id: Identifier
    strategy_version: str | None
    allocation: str
    branch_state: str
    stagnation_count: int
    cooldown_remaining: int


class PublicArtifact(Record):
    artifact_id: Identifier
    assertion_id: Identifier
    source_type: str
    check_result: str
    coverage: str
    properties: tuple[str, ...]
    private: bool


class PublicBudget(Record):
    elapsed_ms: int
    reserved_ms: int
    tokens: UsageValue
    cost_microusd: UsageValue
    tool_calls: UsageValue


class ResearchSnapshot(VersionedRecord):
    generated_at: AwareDatetime
    source_revision: GitRevision | None
    programs: tuple[PublicProgram, ...] = ()
    problems: tuple[PublicProblem, ...] = ()
    nodes: tuple[PublicNode, ...] = ()
    edges: tuple[PublicEdge, ...] = ()
    attempts: tuple[PublicAttempt, ...] = ()
    sessions: tuple[PublicSession, ...] = ()
    checkpoints: tuple[PublicCheckpoint, ...] = ()
    events: tuple[PublicEvent, ...] = ()
    decisions: tuple[PublicDecision, ...] = ()
    artifacts: tuple[PublicArtifact, ...] = ()
    budget: PublicBudget
    omitted_details: int = Field(default=0, ge=0)
