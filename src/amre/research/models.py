"""Versioned, Git-serializable records for contracts and research evidence."""

import hashlib
import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitRevision = Annotated[str, StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]


def _relative_path(value: str) -> str:
    if re.search(r'[:\\<>|"\x00-\x1f?*]', value) or any(
        not part or part in {".", ".."} or part.endswith((" ", ".")) for part in value.split("/")
    ):
        raise ValueError("expected a portable workspace-relative path")
    return value


RelativePath = Annotated[str, AfterValidator(_relative_path)]


def unique_ids(ids: tuple[str, ...], label: str) -> None:
    if len({identifier.casefold() for identifier in ids}) != len(ids):
        raise ValueError(f"duplicate {label}")


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class VersionedRecord(Record):
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def _integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


class ResearchStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


class ClaimStatus(StrEnum):
    UNPROVED = "UNPROVED"
    PARTIAL = "PARTIAL"
    PROOF_CANDIDATE = "PROOF_CANDIDATE"
    REFUTATION_CANDIDATE = "REFUTATION_CANDIDATE"


class ReviewStatus(StrEnum):
    CURRENT = "CURRENT"
    NEEDS_RECHECK = "NEEDS_RECHECK"


class Review(Record):
    revision: PositiveInt = 1
    status: ReviewStatus = ReviewStatus.CURRENT


class Budget(Record):
    max_tokens: PositiveInt
    timeout_seconds: PositiveInt


class Provenance(Record):
    author: Text
    source: Text
    git_revision: GitRevision | None = None


class ArtifactLocation(Record):
    path: RelativePath
    git_revision: GitRevision | None = None


class Definition(Record):
    id: Identifier
    text: Text


class AcceptanceCriterion(Record):
    id: Identifier
    description: Text


class DerivationOrigin(Record):
    parent_problem: Identifier
    obstacle_id: Identifier
    route_id: Identifier
    source_evidence: tuple[Identifier, ...]
    fingerprint: Sha256
    depth: PositiveInt


class ContractInput(VersionedRecord):
    problem_id: Identifier
    assumptions: tuple[Identifier, ...] = ()
    definitions: tuple[Definition, ...] = ()
    dependencies: tuple[Identifier, ...] = ()
    acceptance_criteria: Annotated[tuple[AcceptanceCriterion, ...], Field(min_length=1)]
    research_routes: tuple[Identifier, ...] = ()
    obstacles: tuple[Identifier, ...] = ()
    budget: Budget
    research_status: ResearchStatus = ResearchStatus.OPEN
    claim_status: ClaimStatus = ClaimStatus.UNPROVED
    evidence_references: tuple[Identifier, ...] = ()
    checkpoint_reference: ArtifactLocation | None = None
    provenance: Provenance
    derivation: DerivationOrigin | None = None

    @model_validator(mode="after")
    def _unique_references(self) -> Self:
        for field in (
            "assumptions",
            "dependencies",
            "research_routes",
            "obstacles",
            "evidence_references",
        ):
            unique_ids(getattr(self, field), field)
        unique_ids(tuple(item.id for item in self.definitions), "definition IDs")
        unique_ids(tuple(item.id for item in self.acceptance_criteria), "criterion IDs")
        return self


class StatementRef(Record):
    path: RelativePath
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def _latex_path(cls, value: str) -> str:
        if PurePosixPath(value).suffix != ".tex":
            raise ValueError("mathematical statements must reference a .tex file")
        return value

    @classmethod
    def from_text(cls, text: str) -> Self:
        digest = statement_hash(text)
        return cls(path=f"research/statements/{digest}.tex", sha256=digest)


def statement_hash(text: str) -> str:
    text = text.removeprefix("\ufeff")
    if not any(line.strip() and not line.lstrip().startswith("%") for line in text.splitlines()):
        raise ValueError("LaTeX statement must contain non-comment content")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ResearchContract(ContractInput):
    statement: StatementRef


class EvidenceSource(StrEnum):
    MODEL = "MODEL"
    COMPUTATION = "COMPUTATION"
    FORMAL_TOOL = "FORMAL_TOOL"
    HUMAN = "HUMAN"
    LITERATURE = "LITERATURE"


class EvidenceProperty(StrEnum):
    COMPUTATION_TESTED = "COMPUTATION_TESTED"
    FORMALLY_VERIFIED = "FORMALLY_VERIFIED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"


class CheckResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class Coverage(StrEnum):
    PARTIAL = "PARTIAL"
    FULL_ASSERTION = "FULL_ASSERTION"


class EvidenceDirection(StrEnum):
    SUPPORT = "SUPPORT"
    REFUTE = "REFUTE"


class RunInfo(Record):
    tool: Text
    version: Text | None = None
    run_id: Text


class EvidenceRecord(Record):
    evidence_id: Identifier
    source_type: EvidenceSource
    file_path: RelativePath
    git_revision: GitRevision | None = None
    run: RunInfo
    check_scope: Text
    check_result: CheckResult
    coverage: Coverage
    direction: EvidenceDirection
    assertion_id: Identifier
    assertion_revision: PositiveInt
    properties: tuple[EvidenceProperty, ...] = ()

    @model_validator(mode="after")
    def _check_properties(self) -> Self:
        source_for = {
            EvidenceProperty.COMPUTATION_TESTED: EvidenceSource.COMPUTATION,
            EvidenceProperty.FORMALLY_VERIFIED: EvidenceSource.FORMAL_TOOL,
            EvidenceProperty.HUMAN_CONFIRMED: EvidenceSource.HUMAN,
        }
        unique_ids(self.properties, "evidence properties")
        for prop in self.properties:
            if source_for[prop] != self.source_type:
                raise ValueError(f"{prop} is incompatible with source type {self.source_type}")
            if (
                prop != EvidenceProperty.COMPUTATION_TESTED
                and self.check_result != CheckResult.PASS
            ):
                raise ValueError(f"{prop} requires a passing result")
        return self

    def applies(self, revision: int, direction: EvidenceDirection) -> bool:
        """Check declared scope and provenance for the current assertion revision."""
        return (
            self.assertion_revision == revision
            and self.direction == direction
            and self.check_result == CheckResult.PASS
            and self.coverage == Coverage.FULL_ASSERTION
            and any(
                prop in self.properties
                for prop in (EvidenceProperty.FORMALLY_VERIFIED, EvidenceProperty.HUMAN_CONFIRMED)
            )
        )
