"""Typed result contracts for seven distinct mathematical reasoning modes."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from amre.research.models import Identifier, Record, RelativePath, Text, VersionedRecord


class ReasoningMode(StrEnum):
    PROOF_CONSTRUCTION = "PROOF_CONSTRUCTION"
    LEMMA_DECOMPOSITION = "LEMMA_DECOMPOSITION"
    COUNTEREXAMPLE_SEARCH = "COUNTEREXAMPLE_SEARCH"
    OBSTACLE_DIAGNOSIS = "OBSTACLE_DIAGNOSIS"
    METHOD_INNOVATION = "METHOD_INNOVATION"
    PROOF_ROUTE_EXPLORATION = "PROOF_ROUTE_EXPLORATION"
    MATHEMATICAL_SYNTHESIS = "MATHEMATICAL_SYNTHESIS"


class SelfCheck(Record):
    assumptions: tuple[Identifier, ...]
    logical_gaps: tuple[Text, ...]
    possible_counterexamples: tuple[Text, ...]
    unverified_steps: tuple[Text, ...]


class ClaimDraft(Record):
    label: Text
    statement: Text
    artifact: Text
    assumptions: tuple[Identifier, ...] = ()
    dependencies: tuple[Identifier, ...] = ()


class ObligationEvidence(Record):
    criterion_id: Identifier
    evidence_id: Identifier


class _SkillResult(Record):
    self_check: SelfCheck


class ProofConstruction(_SkillResult):
    mode: Literal[ReasoningMode.PROOF_CONSTRUCTION]
    ordered_steps: Annotated[tuple[Text, ...], Field(min_length=1)]
    obligation_evidence: tuple[ObligationEvidence, ...] = ()


class LemmaDecomposition(_SkillResult):
    mode: Literal[ReasoningMode.LEMMA_DECOMPOSITION]
    lemmas: tuple[ClaimDraft, ...]
    composition: Text


class CounterexampleSearch(_SkillResult):
    mode: Literal[ReasoningMode.COUNTEREXAMPLE_SEARCH]
    tested_domain: Text
    test_case: Text
    assumption_checks: Annotated[tuple[Text, ...], Field(min_length=1)]
    violated_conclusion: Text | None


class ObstacleDiagnosis(_SkillResult):
    mode: Literal[ReasoningMode.OBSTACLE_DIAGNOSIS]
    obstacle_id: Identifier
    failed_inference: Text
    mechanism: Text
    remaining_scope: Text


class MethodInnovation(_SkillResult):
    mode: Literal[ReasoningMode.METHOD_INNOVATION]
    obstacle_id: Identifier
    prior_method: Text
    replacement_method: Text
    new_quantity: Text
    validation_checks: Annotated[tuple[Text, ...], Field(min_length=1)]


class ProofRouteExploration(_SkillResult):
    mode: Literal[ReasoningMode.PROOF_ROUTE_EXPLORATION]
    reduction: ClaimDraft | None


class MathematicalSynthesis(_SkillResult):
    mode: Literal[ReasoningMode.MATHEMATICAL_SYNTHESIS]
    source_evidence: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    conditional_conclusion: Text
    remaining_dependencies: tuple[Identifier, ...]


SkillOutput = Annotated[
    ProofConstruction
    | LemmaDecomposition
    | CounterexampleSearch
    | ObstacleDiagnosis
    | MethodInnovation
    | ProofRouteExploration
    | MathematicalSynthesis,
    Field(discriminator="mode"),
]


class ProgressKind(StrEnum):
    OBLIGATION_SATISFIED = "OBLIGATION_SATISFIED"
    CANDIDATE_REDUCTION = "CANDIDATE_REDUCTION"
    CANDIDATE_DERIVATION = "CANDIDATE_DERIVATION"
    OBSTACLE_DISCOVERED = "OBSTACLE_DISCOVERED"
    TEXT_REPEAT = "TEXT_REPEAT"


class ProgressEvent(VersionedRecord):
    attempt_id: Identifier
    problem_id: Identifier
    route_id: Identifier | None = None
    kind: ProgressKind
    subject_id: Identifier
    source_file: RelativePath
    evidence_id: Identifier
