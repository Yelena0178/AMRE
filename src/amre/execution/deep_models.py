"""Semantic messages and durable views of a continuous research session."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, model_validator

from amre.execution.models import DraftArtifact, ExecutionState
from amre.execution.skill_models import ReasoningMode
from amre.research.memory_models import NonnegativeInt
from amre.research.models import Identifier, Record, RelativePath, Text, VersionedRecord


class Continuity(StrEnum):
    FRESH = "FRESH"
    RECONSTRUCTED = "CHECKPOINT_RECONSTRUCTION"
    NATIVE = "NATIVE_SESSION"


class SemanticKind(StrEnum):
    PROOF_PROGRESS = "PROOF_PROGRESS"
    KEY_OBSTACLE = "KEY_OBSTACLE"
    ASSUMPTION_CHANGE = "ASSUMPTION_CHANGE"
    NEW_BRANCH = "NEW_BRANCH"
    STRATEGY_SWITCH = "STRATEGY_SWITCH"
    LOCAL_STEP = "LOCAL_STEP"
    BUDGET_BOUNDARY = "BUDGET_BOUNDARY"
    USER_PAUSE = "USER_PAUSE"
    USER_STOP = "USER_STOP"
    USER_CHECKPOINT = "USER_CHECKPOINT"
    INTERRUPTED = "INTERRUPTED"
    TOOL_RESULT = "TOOL_RESULT"


MODEL_SEMANTIC_KINDS = frozenset(
    {
        SemanticKind.PROOF_PROGRESS,
        SemanticKind.KEY_OBSTACLE,
        SemanticKind.ASSUMPTION_CHANGE,
        SemanticKind.NEW_BRANCH,
        SemanticKind.STRATEGY_SWITCH,
        SemanticKind.LOCAL_STEP,
    }
)


class SemanticUpdate(Record):
    kind: SemanticKind
    summary: Text
    strategy: ReasoningMode | None = None
    assumptions: tuple[Identifier, ...] = ()
    unresolved: tuple[Text, ...] = ()
    next_action: Text | None = None
    artifact: DraftArtifact | None = None

    @model_validator(mode="after")
    def _source(self) -> Self:
        if self.kind in {
            SemanticKind.PROOF_PROGRESS,
            SemanticKind.KEY_OBSTACLE,
            SemanticKind.ASSUMPTION_CHANGE,
            SemanticKind.NEW_BRANCH,
        }:
            if self.artifact is None or not self.artifact.name.endswith((".md", ".tex")):
                raise ValueError("Significant mathematical events need an original derivation")
        if self.kind == SemanticKind.STRATEGY_SWITCH and self.strategy is None:
            raise ValueError("A strategy change must identify the new skill")
        return self


class SemanticRecord(VersionedRecord):
    event_id: Identifier
    attempt_id: Identifier
    update: SemanticUpdate
    source_file: RelativePath | None


class DeepState(VersionedRecord):
    session_id: Identifier
    problem_id: Identifier
    research_goal: Text
    active_route: Identifier | None = None
    attempt_id: Identifier
    working_context: str
    continuity: Continuity
    context_loss_risk: tuple[Text, ...] = ()
    artifacts: tuple[RelativePath, ...] = ()
    semantic_events: tuple[RelativePath, ...] = ()
    last_checkpoint: Identifier
    started_at: AwareDatetime | None = None
    strategy_version: Text | None = None
    native_session_id: str | None = None


class ReasoningBudget(Record):
    session_ms: NonnegativeInt
    charged_ms: NonnegativeInt
    reserved_ms: NonnegativeInt
    remaining_ms: NonnegativeInt
    tokens: int | str
    cost_microusd: int | str


class DeepResearchSession(DeepState):
    session_status: ExecutionState
    reasoning_budget: ReasoningBudget
    elapsed_time: NonnegativeInt
