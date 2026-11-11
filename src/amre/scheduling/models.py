"""Versioned inputs and durable outputs for research scheduling."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from amre.execution.models import BranchDispatch, ExecutionState, Limits
from amre.execution.skill_models import ProgressEvent, ReasoningMode
from amre.research.memory_models import NonnegativeInt
from amre.research.models import (
    Identifier,
    PositiveInt,
    Record,
    Sha256,
    Text,
    VersionedRecord,
    unique_ids,
)

Percent = Annotated[int, Field(strict=True, ge=0, le=100)]


class BranchState(StrEnum):
    UNEXPLORED = "UNEXPLORED"
    ACTIVE = "ACTIVE"
    PROMISING = "PROMISING"
    STAGNATED = "STAGNATED"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    REFUTED = "REFUTED"


class Allocation(StrEnum):
    CURRENT = "CURRENT"
    ALTERNATIVE = "ALTERNATIVE"
    EXPLORATION = "EXPLORATION"


class Strategy(Record):
    version: Text = "portfolio-v1"
    current: Percent = 60
    alternative: Percent = 25
    exploration: Percent = 15
    exploration_floor: Percent = 15
    stagnation_window: Annotated[int, Field(strict=True, ge=2, le=50)] = 3
    stagnation_ms: PositiveInt = 15000
    cooldown_attempts: NonnegativeInt = 2
    max_active_branches: PositiveInt = 3
    max_decisions_per_run: Annotated[int, Field(strict=True, ge=1, le=100)] = 10
    branch_limits: Limits = Limits(attempts=8, seconds=300)
    exploration_seconds: PositiveInt = 120
    max_derived_depth: PositiveInt = 2
    max_children: PositiveInt = 3
    max_derived_total: PositiveInt = 20

    @model_validator(mode="after")
    def _check_shares(self) -> Self:
        if self.current + self.alternative + self.exploration != 100:
            raise ValueError("allocation percentages must sum to 100")
        return self

    def shares(self) -> dict[Allocation, int]:
        exploration = max(self.exploration, self.exploration_floor)
        available = 100 - exploration
        current = available * self.current // max(1, self.current + self.alternative)
        return {
            Allocation.CURRENT: current,
            Allocation.ALTERNATIVE: available - current,
            Allocation.EXPLORATION: exploration,
        }


class BranchSettings(Record):
    problem_id: Identifier
    route_id: Identifier
    method: Text | None = None
    next_action: Text | None = None
    mode: ReasoningMode = ReasoningMode.PROOF_CONSTRUCTION
    limits: Limits | None = None
    enabled: bool = True

    @property
    def key(self) -> str:
        return f"{self.problem_id}:{self.route_id}"


class PortfolioSettings(VersionedRecord):
    strategy: Strategy = Strategy()
    problem_values: dict[Identifier, PositiveInt] = Field(default_factory=dict)
    branches: tuple[BranchSettings, ...] = ()

    @model_validator(mode="after")
    def _check_branches(self) -> Self:
        unique_ids(tuple(item.key for item in self.branches), "portfolio branch settings")
        return self


class StagnationEvent(Record):
    kind: str = "STAGNATED"
    attempts: tuple[Identifier, ...]
    repeated_obstacles: tuple[Identifier, ...]
    repeated_drafts: int
    charged_ms: NonnegativeInt
    cooldown_remaining: NonnegativeInt


class ResearchBranch(Record):
    problem_id: Identifier
    route_id: Identifier
    owner: Identifier
    method: Text
    prerequisites: tuple[Identifier, ...]
    assumptions: tuple[Identifier, ...]
    unmet_prerequisites: tuple[Identifier, ...]
    obstacles: tuple[Identifier, ...]
    evidence: tuple[Identifier, ...]
    progress: tuple[ProgressEvent, ...]
    attempts: tuple[Identifier, ...]
    charged_ms: NonnegativeInt
    next_action: Text
    state: BranchState
    stagnation: StagnationEvent | None = None
    reactivated_by: tuple[Identifier, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.problem_id}:{self.route_id}"


class Score(Record):
    problem_value: int
    mathematical_progress: int
    dependency_leverage: int
    novelty: int
    cost_penalty: int
    failure_penalty: int

    @property
    def total(self) -> int:
        return (
            self.problem_value
            + self.mathematical_progress
            + self.dependency_leverage
            + self.novelty
            - self.cost_penalty
            - self.failure_penalty
        )


class Candidate(Record):
    branch: ResearchBranch
    allocation: Allocation
    score: Score
    mode: ReasoningMode
    obstacle_id: Identifier | None = None
    exclusions: tuple[Text, ...] = ()
    reasons: tuple[Text, ...] = ()
    continuity_priority: bool = False
    reservation_ms: NonnegativeInt = 0
    limits: Limits


class PlanningInput(Record):
    scope: Text
    position: NonnegativeInt
    seed: int
    strategy: Strategy
    graph_fingerprint: Sha256
    dispatched: dict[Allocation, NonnegativeInt]
    candidates: tuple[Candidate, ...]


class DecisionRecord(VersionedRecord):
    decision_id: Identifier
    inputs: PlanningInput
    shortlisted: tuple[Text, ...]
    allocation_debt: dict[Allocation, int]
    selected: Text | None = None
    reason: Text
    dispatch: BranchDispatch | None = None

    def candidate(self) -> Candidate | None:
        return next(
            (item for item in self.inputs.candidates if item.branch.key == self.selected), None
        )


class DispatchResult(Record):
    decision_id: Identifier
    attempt_id: Identifier | None = None
    state: ExecutionState
    reason: Text


class ScheduleJournal(VersionedRecord):
    decisions: tuple[Identifier, ...] = ()
    results: tuple[DispatchResult, ...] = ()

    @model_validator(mode="after")
    def _check_results(self) -> Self:
        unique_ids(self.decisions, "scheduler decisions")
        unique_ids(tuple(item.decision_id for item in self.results), "scheduler results")
        if not set(item.decision_id for item in self.results).issubset(self.decisions):
            raise ValueError("scheduler result refers to an unpublished decision")
        if len(self.decisions) - len(self.results) > 1:
            raise ValueError("only one scheduler decision may be pending")
        return self
