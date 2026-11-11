"""Fixtures and measurements for scoped offline workflow comparisons."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints

from amre.execution.skill_models import ReasoningMode
from amre.research.models import (
    Budget,
    Identifier,
    Record,
    RelativePath,
    Sha256,
    Text,
    VersionedRecord,
)


class BenchmarkClass(StrEnum):
    B1 = "B1"
    B2 = "B2"
    B3 = "B3"


class BenchmarkStep(Record):
    step_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")]
    strategy: ReasoningMode
    derivation: Text


class BenchmarkCase(VersionedRecord):
    case_id: Identifier
    kind: BenchmarkClass
    title: Text
    statement: RelativePath
    reference: RelativePath
    assumptions: Annotated[tuple[Text, ...], Field(min_length=1)]
    tool: Identifier
    input: RelativePath
    expected: Annotated[dict[str, object], Field(min_length=1)]
    budget: Budget
    steps: Annotated[tuple[BenchmarkStep, ...], Field(min_length=2, max_length=6)]


class BenchmarkSuite(VersionedRecord):
    name: Text
    cases: Annotated[tuple[BenchmarkCase, ...], Field(min_length=1, max_length=10)]


class WorkflowMetrics(Record):
    variant: Identifier
    model: str = "mock"
    workspace: RelativePath
    attempts: int
    mathematical_candidates: int
    context_loss_events: int
    repeated_failures: int
    completed: bool
    state_restored: bool
    elapsed_ms: int
    reserved_peak_ms: int
    budget_ms: int
    tokens: int | str
    cost_microusd: int | str
    computation_checks: int
    formal_checks: int = 0


class Ablation(Record):
    dimension: Text
    variants: tuple[Identifier, Identifier]
    trace: tuple[tuple[Text, ...], tuple[Text, ...]]
    repeated_failures: tuple[int, int]


class CaseResult(Record):
    case_id: Identifier
    kind: BenchmarkClass
    reference: RelativePath
    checked: dict[str, bool]
    computation: RelativePath
    workflows: tuple[WorkflowMetrics, ...]
    ablations: tuple[Ablation, ...]


class BenchmarkResult(VersionedRecord):
    suite: Text
    seed: int
    engine: str = "OFFLINE_MOCK"
    environment: dict[str, str]
    fixture_digest: Sha256
    cases: tuple[CaseResult, ...]
    real_model_effect: str = "UNVERIFIED"
