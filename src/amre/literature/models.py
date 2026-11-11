"""Bibliographic, source-check, and research topic contracts."""

import re
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, AwareDatetime, Field, model_validator

from amre.research.models import (
    AcceptanceCriterion,
    Identifier,
    Record,
    Sha256,
    StatementRef,
    Text,
    VersionedRecord,
    unique_ids,
)


def _doi(value: str) -> str:
    value = value.strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")
    if not re.fullmatch(r"10\.\d{4,9}/[^\s?#]+", value):
        raise ValueError("invalid DOI")
    return value


def _arxiv(value: str) -> str:
    value = value.strip().removeprefix("arXiv:").lower()
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})", value):
        raise ValueError("use an unversioned arXiv ID")
    return value


def _source_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("bibliographic source must be a public HTTPS link")
    return value


DOI = Annotated[str, AfterValidator(_doi)]
ArxivID = Annotated[str, AfterValidator(_arxiv)]
SourceURL = Annotated[str, AfterValidator(_source_url)]


class ProviderName(StrEnum):
    OFFLINE = "offline"
    ARXIV = "arxiv"
    CROSSREF = "crossref"
    SEMANTIC_SCHOLAR = "semantic-scholar"


class SourceStatus(StrEnum):
    METADATA_ONLY = "METADATA_ONLY"
    EXTRACTED_UNCHECKED = "EXTRACTED_UNCHECKED"
    SOURCE_CONFIRMED = "SOURCE_CONFIRMED"


class PaperMetadata(VersionedRecord):
    title: Text
    authors: tuple[Text, ...] = ()
    year: Annotated[int, Field(strict=True, ge=1, le=9999)] | None = None
    doi: DOI | None = None
    arxiv_id: ArxivID | None = None
    version: Text | None = None
    provider: ProviderName
    source_url: SourceURL
    retrieved_at: AwareDatetime
    fixture: bool = False

    def identity(self) -> str:
        return self.model_dump_json(exclude={"retrieved_at"})

    def fingerprint(self) -> str:
        return sha256(self.identity().encode()).hexdigest()


class PaperRecord(VersionedRecord):
    paper_id: Identifier
    metadata: Annotated[tuple[PaperMetadata, ...], Field(min_length=1)]
    status: SourceStatus = SourceStatus.METADATA_ONLY

    @model_validator(mode="after")
    def _check_metadata(self) -> Self:
        if self.status != SourceStatus.METADATA_ONLY:
            raise ValueError("bibliographic metadata cannot confirm a source statement")
        if len({item.doi for item in self.metadata if item.doi}) > 1:
            raise ValueError("conflicting DOIs")
        if len({item.arxiv_id for item in self.metadata if item.arxiv_id}) > 1:
            raise ValueError("conflicting arXiv IDs")
        if len({item.fixture for item in self.metadata}) != 1:
            raise ValueError("fixture and real metadata cannot be combined")
        return self


class PaperImport(VersionedRecord):
    papers: Annotated[tuple[PaperMetadata, ...], Field(min_length=1, max_length=100)]


class SourceLocator(Record):
    section: Text | None = None
    page: Text | None = None
    number: Text | None = None
    scope: Text

    @model_validator(mode="after")
    def _pinpoint(self) -> Self:
        if not (self.section or self.page or self.number):
            raise ValueError("source locator needs a section, page or theorem number")
        return self


class SourceReview(Record):
    checked_by: Text
    reviewed_at: AwareDatetime
    source_digest: Sha256


class TheoremDraft(VersionedRecord):
    theorem_id: Identifier
    paper_id: Identifier
    paper_version: Sha256
    label: Text
    locator: SourceLocator
    assumptions: tuple[Identifier, ...] = ()
    open_problem: bool = False

    @model_validator(mode="after")
    def _unique_assumptions(self) -> Self:
        unique_ids(self.assumptions, "source assumption")
        return self


class TheoremRecord(TheoremDraft):
    statement: StatementRef
    source_status: SourceStatus = SourceStatus.EXTRACTED_UNCHECKED
    review: SourceReview | None = None

    @model_validator(mode="after")
    def _check_review(self) -> Self:
        if self.source_status == SourceStatus.METADATA_ONLY:
            raise ValueError("a theorem record requires an extracted statement")
        if (self.source_status == SourceStatus.SOURCE_CONFIRMED) != (self.review is not None):
            raise ValueError("source confirmation requires a named source review")
        return self


class CitationRecord(VersionedRecord):
    citation_id: Identifier
    subject_id: Identifier
    paper_id: Identifier
    theorem_id: Identifier | None = None
    scope: Text


class CandidateKind(StrEnum):
    EXPLICIT_OPEN_PROBLEM = "EXPLICIT_OPEN_PROBLEM"
    ASSUMPTION_RELAXATION = "ASSUMPTION_RELAXATION"
    MISSING_LEMMA = "MISSING_LEMMA"
    MODEL_EXTENSION = "MODEL_EXTENSION"


class CandidateDraft(VersionedRecord):
    kind: CandidateKind
    title: Text
    field: Identifier
    program: Identifier | None = None
    parent_problem: Identifier | None = None
    obstacle_id: Identifier | None = None
    route_id: Identifier | None = None
    paper_ids: tuple[Identifier, ...] = ()
    theorem_id: Identifier | None = None
    assumptions: tuple[Identifier, ...] = ()
    significance: Text
    acceptance_criteria: Annotated[tuple[AcceptanceCriterion, ...], Field(min_length=1)]
    priority: Annotated[int, Field(strict=True, ge=1, le=10)] = 5

    @model_validator(mode="after")
    def _check_origins(self) -> Self:
        unique_ids(self.assumptions, "candidate assumption")
        unique_ids(self.paper_ids, "candidate paper")
        unique_ids(tuple(item.id for item in self.acceptance_criteria), "candidate criterion")
        if self.kind in {CandidateKind.EXPLICIT_OPEN_PROBLEM, CandidateKind.ASSUMPTION_RELAXATION}:
            if not self.theorem_id:
                raise ValueError("this topic needs a pinpointed literature source")
        if self.kind == CandidateKind.MISSING_LEMMA and not (
            self.parent_problem and self.obstacle_id and self.route_id
        ):
            raise ValueError("a missing lemma needs a parent, obstacle and route")
        if self.kind == CandidateKind.MODEL_EXTENSION and not (
            self.parent_problem or self.paper_ids or self.theorem_id
        ):
            raise ValueError("a model extension needs a parent problem or source")
        return self


class OpenProblemCandidate(CandidateDraft):
    candidate_id: Identifier
    statement: StatementRef
    novelty: str = "NOVELTY_UNCONFIRMED"
    open_confirmed: bool = False
    source_reported_open: bool = False
    promoted_problem: Identifier | None = None

    @model_validator(mode="after")
    def _check_novelty(self) -> Self:
        if self.novelty != "NOVELTY_UNCONFIRMED" or self.open_confirmed:
            raise ValueError("candidate discovery cannot certify novelty or open status")
        return self


class LiteratureCatalog(VersionedRecord):
    papers: tuple[PaperRecord, ...] = ()
    theorems: tuple[TheoremRecord, ...] = ()
    citations: tuple[CitationRecord, ...] = ()
    candidates: tuple[OpenProblemCandidate, ...] = ()

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        unique_ids(tuple(item.paper_id for item in self.papers), "paper")
        unique_ids(tuple(item.theorem_id for item in self.theorems), "source theorem")
        unique_ids(tuple(item.citation_id for item in self.citations), "citation")
        unique_ids(tuple(item.candidate_id for item in self.candidates), "candidate")
        versions = {
            paper.paper_id: {item.fingerprint() for item in paper.metadata} for paper in self.papers
        }
        theorem_sources = {item.theorem_id: item.paper_id for item in self.theorems}
        for theorem in self.theorems:
            if theorem.paper_version not in versions.get(theorem.paper_id, ()):
                raise ValueError("theorem references an unknown source version")
        for citation in self.citations:
            if citation.paper_id not in versions or (
                citation.theorem_id
                and theorem_sources.get(citation.theorem_id) != citation.paper_id
            ):
                raise ValueError("citation references an unknown source")
        for candidate in self.candidates:
            if not versions.keys() >= set(candidate.paper_ids) or (
                candidate.theorem_id and candidate.theorem_id not in theorem_sources
            ):
                raise ValueError("candidate references an unknown source")
        return self
