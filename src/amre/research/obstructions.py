"""Scoped obstruction records shared by the proof graph and execution."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from amre.research.models import Identifier, PositiveInt, Record, RelativePath, Text


class ObstructionCategory(StrEnum):
    ALGEBRAIC = "ALGEBRAIC"
    REGULARITY = "REGULARITY"
    BOUNDARY = "BOUNDARY"
    SPECTRAL = "SPECTRAL"
    GAUGE = "GAUGE"
    COMPACTNESS = "COMPACTNESS"
    NONLINEARITY = "NONLINEARITY"
    ASSUMPTION = "ASSUMPTION"
    CITATION = "CITATION"
    UNKNOWN = "UNKNOWN"


class ObstructionStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ExcludedScope(Record):
    method: Text
    domain: Text
    restriction: Text
    assumptions: tuple[Identifier, ...] = ()


class ObstructionStep(Record):
    file: RelativePath
    anchor: Text
    line: PositiveInt | None = None
    formula: Text | None = None

    @model_validator(mode="after")
    def _locatable(self) -> Self:
        if self.line is None and self.formula is None:
            raise ValueError("obstruction requires a line number or exact formula")
        return self


class ObstructionDraft(Record):
    category: ObstructionCategory
    description: Text
    step: ObstructionStep
    excluded_scope: ExcludedScope
    attempted_methods: Annotated[tuple[Text, ...], Field(min_length=1)]
    potential_resolutions: tuple[Text, ...] = ()
    evidence_refs: tuple[Identifier, ...] = ()

    def identity(self, problem_id: str, revision: int) -> tuple:
        scope = self.excluded_scope
        return (
            problem_id,
            revision,
            self.category,
            self.description,
            self.step.anchor,
            self.step.formula,
            self.step.line if self.step.formula is None else None,
            scope.method,
            scope.domain,
            scope.restriction,
            tuple(sorted(scope.assumptions)),
        )


class ObstructionRecord(ObstructionDraft):
    obstacle_id: Identifier
    problem_id: Identifier
    assertion_revision: PositiveInt
    routes: tuple[Identifier, ...] = ()
    attempts: tuple[Identifier, ...]
    status: ObstructionStatus = ObstructionStatus.OPEN
    version: PositiveInt = 1
    resolution_evidence: Identifier | None = None

    @model_validator(mode="after")
    def _resolution(self) -> Self:
        if self.status == ObstructionStatus.RESOLVED:
            if not self.resolution_evidence or self.resolution_evidence not in self.evidence_refs:
                raise ValueError("resolution requires a linked independent evidence ID")
        elif self.resolution_evidence is not None:
            raise ValueError("an open obstruction cannot have resolution evidence")
        return self
