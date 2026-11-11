"""Storage-independent research vocabulary shared across module boundaries."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import NewType

from amre.research.models import EvidenceRecord as EvidenceRecord

ProblemId = NewType("ProblemId", str)
AssumptionId = NewType("AssumptionId", str)


class EvidenceLevel(StrEnum):
    """Phase 01 vocabulary. Persisted records use source, scope, and properties."""

    CANDIDATE_PROOF = "candidate_proof"
    COMPUTATIONAL_CHECK = "computational_check"
    FORMAL_PROOF = "formal_proof"
    HUMAN_CONFIRMED = "human_confirmed"


@dataclass(frozen=True)
class ArtifactRef:
    """Reference a workspace-relative artifact and, when known, its Git revision."""

    path: PurePosixPath
    revision: str | None = None


@dataclass(frozen=True)
class Assumption:
    id: AssumptionId
    statement: str


@dataclass(frozen=True)
class ResearchProblem:
    id: ProblemId
    proposition: str
    assumptions: tuple[Assumption, ...]
    dependencies: tuple[ProblemId, ...]
    acceptance_criteria: tuple[str, ...]


@dataclass(frozen=True)
class CheckpointRef:
    problem_id: ProblemId
    artifact: ArtifactRef


@dataclass(frozen=True)
class Obstruction:
    problem_id: ProblemId
    reason: str
    artifact: ArtifactRef


@dataclass(frozen=True)
class RunLimits:
    """Inputs for a future runner to enforce at its execution boundary."""

    max_tokens: int
    timeout_seconds: int


@dataclass(frozen=True)
class CandidateResult:
    """The runner output carries candidate artifacts without an evidence level."""

    problem_id: ProblemId
    artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True)
class LiteratureReference:
    title: str
    uri: str
