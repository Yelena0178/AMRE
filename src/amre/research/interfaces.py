"""Typed integration boundaries. Implementations belong to later phases."""

from pathlib import Path
from typing import Protocol

from amre.research.memory import RestoredState
from amre.research.memory_models import Checkpoint
from amre.research.models import ResearchContract
from amre.research.types import (
    ArtifactRef,
    AssumptionId,
    CandidateResult,
    EvidenceRecord,
    LiteratureReference,
    Obstruction,
    ProblemId,
    ResearchProblem,
    RunLimits,
)


class ResearchContractLoader(Protocol):
    def load_contract(self, problem_id: ProblemId) -> ResearchContract:
        """Load a validated contract from its research workspace."""
        ...


class KnowledgeEvidenceGraph(Protocol):
    def dependency_alternatives(self, problem_id: ProblemId) -> tuple[tuple[ProblemId, ...], ...]:
        """Choose any outer alternative. Each inner group requires all its problems."""
        ...

    def evidence_for(self, problem_id: ProblemId) -> tuple[EvidenceRecord, ...]: ...


class AssumptionLattice(Protocol):
    def implies(self, premises: tuple[AssumptionId, ...], conclusion: AssumptionId) -> bool | None:
        """Return None when implication is undetermined."""
        ...


class CheckpointStore(Protocol):
    def create_checkpoint(self, problem_id: ProblemId, payload: object) -> Checkpoint: ...

    def restore(self, problem_id: ProblemId, checkpoint_id: str | None = None) -> RestoredState: ...


class ReasoningRunner(Protocol):
    def run(
        self, problem: ResearchProblem, workspace: Path, limits: RunLimits
    ) -> CandidateResult: ...


class ObstructionMemory(Protocol):
    def record(self, obstruction: Obstruction) -> None: ...

    def for_problem(self, problem_id: ProblemId) -> tuple[Obstruction, ...]: ...


class BranchScheduler(Protocol):
    def choose(self, candidates: tuple[ProblemId, ...], limits: RunLimits) -> ProblemId | None: ...


class LiteratureIntelligence(Protocol):
    def search(self, query: str) -> tuple[LiteratureReference, ...]: ...


class GRDomainPack(Protocol):
    def check(self, problem: ResearchProblem, candidate: ArtifactRef) -> ArtifactRef: ...


class ResearchDashboard(Protocol):
    def publish(self, report: ArtifactRef) -> str:
        """Return the published report URI after an authorized publication."""
        ...


class StrategyBenchmark(Protocol):
    def evaluate(
        self, strategy_id: str, suite: tuple[ProblemId, ...], limits: RunLimits
    ) -> ArtifactRef: ...


class ResearchReports(Protocol):
    def render(
        self, problem: ResearchProblem, evidence: tuple[EvidenceRecord, ...]
    ) -> ArtifactRef: ...
