"""Intake drafts are deliberately separate from executable research contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from amre.execution.models import ReasoningTask
from amre.research.models import (
    Identifier,
    PositiveInt,
    Record,
    Sha256,
    Text,
    VersionedRecord,
    unique_ids,
)


class Source(Record):
    source_id: Identifier
    name: Text
    kind: Literal["material", "request", "revision"]
    sha256: Sha256
    size_bytes: PositiveInt
    line_count: PositiveInt


class Citation(Record):
    source_id: Identifier
    start_line: PositiveInt
    end_line: PositiveInt
    quote: Text

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("citation end precedes start")
        return self


class Item(Record):
    id: Identifier
    text: Text
    origin: Literal["user", "material", "model_suggestion", "unresolved"]
    citations: tuple[Citation, ...] = ()
    note: str = ""

    @model_validator(mode="after")
    def attributed(self) -> Self:
        if self.origin in {"user", "material"} and not self.citations:
            raise ValueError("user/material content requires an exact source citation")
        if self.origin in {"model_suggestion", "unresolved"} and not self.note.strip():
            raise ValueError("suggestions and unresolved content require an explanation")
        return self


SECTIONS = (
    "assumptions",
    "definitions",
    "dependencies",
    "existing_results",
    "gaps",
    "acceptance_criteria",
    "questions",
)


class DraftContent(VersionedRecord):
    status: Literal["DRAFT_UNVERIFIED"] = "DRAFT_UNVERIFIED"
    goal: Item
    assumptions: tuple[Item, ...]
    definitions: tuple[Item, ...]
    dependencies: tuple[Item, ...]
    existing_results: tuple[Item, ...]
    gaps: tuple[Item, ...]
    acceptance_criteria: Annotated[tuple[Item, ...], Field(min_length=1)]
    questions: tuple[Item, ...]

    def items(self) -> tuple[Item, ...]:
        return (self.goal, *(item for field in SECTIONS for item in getattr(self, field)))

    @model_validator(mode="after")
    def identifiable(self) -> Self:
        unique_ids(tuple(item.id for item in self.items()), "draft item IDs")
        if any(item.origin != "unresolved" for item in self.questions):
            raise ValueError("open questions must remain unresolved")
        return self


class IntakeLimits(Record):
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)] = 120
    max_tokens: Annotated[int, Field(strict=True, ge=1, le=1000000)] = 8000
    max_context_bytes: Annotated[int, Field(strict=True, ge=1024, le=2097152)] = 262144
    max_output_bytes: Annotated[int, Field(strict=True, ge=1024, le=2097152)] = 262144


class PreparedRequest(VersionedRecord):
    request_id: Identifier
    base_version: int = Field(strict=True, ge=0)
    instruction_source: Identifier
    sources: tuple[Source, ...]
    limits: IntakeLimits
    task: ReasoningTask


class Version(Record):
    number: PositiveInt
    request_id: Identifier
    files: dict[str, Sha256]


class Manifest(VersionedRecord):
    sources: tuple[Source, ...] = ()
    versions: tuple[Version, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> Self:
        unique_ids(tuple(s.source_id for s in self.sources), "source IDs")
        if tuple(v.number for v in self.versions) != tuple(range(1, len(self.versions) + 1)):
            raise ValueError("draft versions must be sequential")
        unique_ids(tuple(v.request_id for v in self.versions), "published requests")
        if any(set(v.files) != {"draft.yaml", "statement.tex", "review.md"} for v in self.versions):
            raise ValueError("each version must contain the three review artifacts")
        return self
