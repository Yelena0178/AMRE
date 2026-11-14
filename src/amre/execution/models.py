"""Contracts for research dispatch, outcomes, and the execution ledger."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from amre.execution.skill_models import ReasoningMode, SkillOutput
from amre.research.memory_models import NonnegativeInt
from amre.research.models import Identifier, PositiveInt, Record, Text, VersionedRecord, unique_ids
from amre.research.obstructions import ObstructionDraft


class ExecutionState(StrEnum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AdapterName(StrEnum):
    MOCK = "mock"
    CODEX = "codex"


class ResearchWorkflow(StrEnum):
    DEEP = "deep"
    SHORT = "short"


PromptTemplate = Literal["standard-v1", "explicit-gaps-v2"]


class ReasoningOutcome(StrEnum):
    NO_PROGRESS = "NO_PROGRESS"
    PARTIAL_PROGRESS = "PARTIAL_PROGRESS"
    PROOF_CANDIDATE = "PROOF_CANDIDATE"
    COUNTEREXAMPLE_CANDIDATE = "COUNTEREXAMPLE_CANDIDATE"
    VALID_REDUCTION_CANDIDATE = "VALID_REDUCTION_CANDIDATE"
    BLOCKED = "BLOCKED"


class Failure(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    NONZERO_EXIT = "NONZERO_EXIT"
    INVALID_RESULT = "INVALID_RESULT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    TOOL_LIMIT = "TOOL_LIMIT"
    TOKEN_LIMIT = "TOKEN_LIMIT"
    INTERRUPTED = "INTERRUPTED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    STALE_RESEARCH = "STALE_RESEARCH"


class ReasoningTask(VersionedRecord):
    purpose: Literal["research", "intake"] = "research"
    attempt_id: Identifier
    problem_id: Identifier
    route_id: Identifier | None = None
    checkpoint_id: Identifier
    assertion_revision: PositiveInt
    strategy_version: Text
    objective: Text
    context: str
    reasoning_mode: ReasoningMode | None = None
    skill_version: str | None = None
    skill_instructions: str = ""
    skill_inputs: dict[str, object] = Field(default_factory=dict)
    obstacle_id: Identifier | None = None
    dispatch_id: Identifier | None = None
    deep_session_id: Identifier | None = None
    omitted_context: tuple[str, ...] = ()
    tools: tuple[Identifier, ...] = ()
    prompt_template: PromptTemplate = "standard-v1"


ArtifactName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,47}\.(tex|md|txt)$")
]


class DraftArtifact(Record):
    name: ArtifactName
    text: Text


class ReasoningResult(VersionedRecord):
    outcome: ReasoningOutcome
    summary: Text
    next_action: Text
    artifacts: tuple[DraftArtifact, ...] = ()
    obstacles: tuple[Text, ...] = ()
    unresolved_steps: tuple[Text, ...] = ()
    assumptions_used: tuple[Identifier, ...] = ()
    computation_artifacts: tuple[DraftArtifact, ...] = ()
    new_obstacles: tuple[ObstructionDraft, ...] = ()
    skill_output: SkillOutput | None = None

    @property
    def all_artifacts(self) -> tuple[DraftArtifact, ...]:
        return (*self.artifacts, *self.computation_artifacts)

    @model_validator(mode="after")
    def _validate_artifacts(self) -> Self:
        unique_ids(tuple(artifact.name for artifact in self.all_artifacts), "artifact names")
        unique_ids(self.assumptions_used, "assumptions used")
        if (
            self.outcome
            in {
                ReasoningOutcome.PROOF_CANDIDATE,
                ReasoningOutcome.COUNTEREXAMPLE_CANDIDATE,
                ReasoningOutcome.VALID_REDUCTION_CANDIDATE,
            }
            and not self.artifacts
        ):
            raise ValueError("a mathematical candidate requires a derivation artifact")
        return self


class Usage(Record):
    tokens: NonnegativeInt | None = None
    cost_microusd: NonnegativeInt | None = None
    tool_calls: NonnegativeInt | None = None


class AttemptReceipt(VersionedRecord):
    task: ReasoningTask
    adapter: AdapterName
    model: str | None = None
    elapsed_ms: NonnegativeInt
    exit_code: int | None = None
    usage: Usage = Usage()
    stdout: str = ""
    stderr: str = ""
    failure: Failure | None = None
    result: ReasoningResult | None = None

    @model_validator(mode="after")
    def _check_result(self) -> Self:
        if (self.failure is None) == (self.result is None):
            raise ValueError("attempt receipt requires a result or a failure")
        return self


class Limits(Record):
    attempts: PositiveInt
    seconds: PositiveInt
    tool_calls: NonnegativeInt = 0


class BranchDispatch(Record):
    decision_id: Identifier
    problem_id: Identifier
    route_id: Identifier
    objective: Text
    strategy_version: Text
    graph_fingerprint: Text
    limits: Limits
    max_attempt_ms: PositiveInt


class DeepPolicy(Record):
    session_seconds: PositiveInt = 1800
    max_semantic_events: Annotated[int, Field(strict=True, ge=1, le=256)] = 64
    budget_target_percent: Annotated[int, Field(strict=True, ge=0, le=100)] = 85
    native_codex_sessions: bool = False
    tools: tuple[Identifier, ...] = ()
    session_tool_calls: NonnegativeInt = 0

    @model_validator(mode="after")
    def _tool_policy(self) -> Self:
        unique_ids(self.tools, "deep tool names")
        if self.tools and not self.session_tool_calls:
            raise ValueError("deep tools require a positive session tool limit")
        return self


class ExecutionPolicy(VersionedRecord):
    workspace: Limits = Limits(attempts=100, seconds=3600)
    program: Limits = Limits(attempts=40, seconds=1800)
    problem: Limits = Limits(attempts=12, seconds=600)
    attempt: Limits = Limits(attempts=1, seconds=60)
    max_parallel: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    max_context_bytes: PositiveInt = 16000
    max_output_bytes: PositiveInt = 131072
    workflow: ResearchWorkflow = ResearchWorkflow.DEEP
    deep: DeepPolicy = DeepPolicy()
    prompt_template: PromptTemplate = "standard-v1"
    strategy_version: Identifier | None = None


class ResearchProgram(VersionedRecord):
    name: Identifier
    problems: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    limits: Limits | None = None

    @model_validator(mode="after")
    def _unique_problems(self) -> Self:
        unique_ids(self.problems, "program problems")
        return self


class Reservation(Record):
    attempt_id: Identifier
    problem_id: Identifier
    program: Identifier | None
    checkpoint_id: Identifier
    adapter: AdapterName
    reserved_ms: PositiveInt
    settled_ms: NonnegativeInt | None = None
    usage: Usage = Usage()
    reasoning_mode: ReasoningMode | None = None
    obstacle_id: Identifier | None = None
    route_id: Identifier | None = None
    dispatch_id: Identifier | None = None
    final_state: ExecutionState | None = None
    deep_session_id: Identifier | None = None
    tool_cap: NonnegativeInt = 0
    tools: tuple[Identifier, ...] = ()
    prompt_template: PromptTemplate = "standard-v1"
    strategy_version: Text | None = None


class ExecutionJob(Record):
    problem_id: Identifier
    program: Identifier | None = None
    state: ExecutionState = ExecutionState.CREATED
    attempt_id: Identifier | None = None
    request: ExecutionState | None = None
    detail: str | None = None
    deep_session_id: Identifier | None = None

    @property
    def key(self) -> str:
        return f"{self.program or ''}:{self.problem_id}"


class ExecutionLedger(VersionedRecord):
    jobs: dict[str, ExecutionJob] = Field(default_factory=dict)
    reservations: tuple[Reservation, ...] = ()

    @model_validator(mode="after")
    def _check_integrity(self) -> Self:
        unique_ids(tuple(item.attempt_id for item in self.reservations), "attempt reservations")
        unique_ids(
            tuple(item.dispatch_id for item in self.reservations if item.dispatch_id),
            "scheduler dispatch reservations",
        )
        attempts = {item.attempt_id: item for item in self.reservations}
        if any(item.dispatch_id and not item.route_id for item in self.reservations):
            raise ValueError("Scheduler reservations require a route")
        if any(
            item.settled_ms is None and item.final_state is not None for item in self.reservations
        ):
            raise ValueError("An open reservation cannot have a final state")
        running = 0
        for key, job in self.jobs.items():
            if key != job.key:
                raise ValueError("execution job key does not match its identity")
            if job.state == ExecutionState.RUNNING:
                running += 1
                item = attempts.get(job.attempt_id)
                if (
                    item is None
                    or item.settled_ms is not None
                    or (item.problem_id, item.program) != (job.problem_id, job.program)
                ):
                    raise ValueError("running job has no matching open reservation")
        if running > 1 or running != sum(item.settled_ms is None for item in self.reservations):
            raise ValueError("execution ledger has unmatched open reservations")
        return self
