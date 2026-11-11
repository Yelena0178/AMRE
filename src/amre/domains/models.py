"""Contracts for independently scoped, replayable domain computations."""

from collections.abc import Mapping
from typing import Annotated, Protocol

from pydantic import AwareDatetime, Field

from amre.execution.session_tools import ToolStatus
from amre.research.models import (
    GitRevision,
    Identifier,
    Record,
    RelativePath,
    Text,
    VersionedRecord,
)


class ToolDescription(Record):
    name: Identifier
    scope: Text
    input_schema: dict[str, object]


class DomainPack(Protocol):
    domain_name: str
    conventions: type[Record]
    definitions: Mapping[str, str]
    available_tools: Mapping[str, ToolDescription]
    consistency_checks: tuple[str, ...]
    benchmarks: tuple[str, ...]
    reasoning_guidelines: tuple[str, ...]


class ProofObligation(Record):
    subject: Text
    automation: Annotated[str, Field(pattern="^(NOT_AUTOMATED|PARTIALLY_AUTOMATED)$")]
    condition: Text


class ComputationResult(Record):
    status: ToolStatus
    scope: Text
    detail: Text | None = None
    output: dict[str, object] = Field(default_factory=dict)
    obligations: tuple[ProofObligation, ...] = ()
    software: dict[str, str] = Field(default_factory=dict)


class ComputationArtifact(VersionedRecord):
    computation_id: Identifier
    tool: Identifier
    tool_version: Text
    request: dict[str, object]
    conventions: dict[str, object]
    result: ComputationResult
    path: RelativePath
    timestamp: AwareDatetime
    repository_revision: GitRevision | None = None
    problem_id: Identifier | None = None
    assertion_id: Identifier | None = None
    assertion_revision: Annotated[int, Field(strict=True, gt=0)] | None = None
    attempt_id: Identifier | None = None
    session_id: Identifier | None = None
