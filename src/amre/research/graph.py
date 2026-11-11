"""Typed AND/OR research dependencies backed by NetworkX."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

import networkx as nx
from pydantic import model_validator

from amre.errors import ResearchError
from amre.research.assumptions import AssumptionDocument, AssumptionKind, AssumptionLattice
from amre.research.models import (
    ClaimStatus,
    EvidenceDirection,
    EvidenceRecord,
    Identifier,
    Record,
    Review,
    ReviewStatus,
    StatementRef,
    Text,
    VersionedRecord,
)
from amre.research.obstructions import ObstructionRecord, ObstructionStatus


class NodeKind(StrEnum):
    CLAIM = "CLAIM"
    LEMMA = "LEMMA"
    THEOREM = "THEOREM"
    RESEARCH_PROBLEM = "RESEARCH_PROBLEM"
    PROOF_ROUTE = "PROOF_ROUTE"
    ASSUMPTION = "ASSUMPTION"
    OBSTACLE = "OBSTACLE"
    EVIDENCE = "EVIDENCE"
    LITERATURE_REFERENCE = "LITERATURE_REFERENCE"


class Relation(StrEnum):
    DEPENDS_ON = "DEPENDS_ON"
    REQUIRES_ALL = "REQUIRES_ALL"
    ALTERNATIVE_ROUTE = "ALTERNATIVE_ROUTE"
    USES_ASSUMPTION = "USES_ASSUMPTION"
    SUPPORTED_BY = "SUPPORTED_BY"
    BLOCKED_BY = "BLOCKED_BY"
    DERIVED_FROM = "DERIVED_FROM"
    CITES = "CITES"


ASSERTIONS = {NodeKind.CLAIM, NodeKind.LEMMA, NodeKind.THEOREM, NodeKind.RESEARCH_PROBLEM}
_REASONING_NODES = ASSERTIONS | {NodeKind.PROOF_ROUTE}
_REQUIRES = {Relation.DEPENDS_ON, Relation.REQUIRES_ALL, Relation.DERIVED_FROM}
DEPENDENCY_RELATIONS = _REQUIRES | {Relation.ALTERNATIVE_ROUTE}
_EDGE_TYPES = {
    Relation.DEPENDS_ON: (_REASONING_NODES, ASSERTIONS),
    Relation.REQUIRES_ALL: ({NodeKind.PROOF_ROUTE}, ASSERTIONS),
    Relation.ALTERNATIVE_ROUTE: (ASSERTIONS, {NodeKind.PROOF_ROUTE}),
    Relation.USES_ASSUMPTION: (
        _REASONING_NODES | {NodeKind.LITERATURE_REFERENCE},
        {NodeKind.ASSUMPTION},
    ),
    Relation.SUPPORTED_BY: (_REASONING_NODES, {NodeKind.EVIDENCE}),
    Relation.BLOCKED_BY: (_REASONING_NODES, {NodeKind.OBSTACLE}),
    Relation.DERIVED_FROM: (_REASONING_NODES, ASSERTIONS),
    Relation.CITES: (set(NodeKind), {NodeKind.LITERATURE_REFERENCE}),
}


class GraphNode(Record):
    id: Identifier
    kind: NodeKind
    label: Text
    statement: StatementRef | None = None
    evidence: EvidenceRecord | None = None
    citation: Text | None = None
    assumption_type: AssumptionKind | None = None
    claim_status: ClaimStatus | None = None
    obstruction: ObstructionRecord | None = None

    @property
    def obstacle_open(self) -> bool:
        return self.kind == NodeKind.OBSTACLE and (
            self.obstruction is None or self.obstruction.status == ObstructionStatus.OPEN
        )

    @model_validator(mode="after")
    def _check_payload(self) -> Self:
        if self.kind in _REASONING_NODES and self.statement is None:
            raise ValueError("an assertion or proof reduction requires a LaTeX statement")
        if (self.kind == NodeKind.EVIDENCE) != (self.evidence is not None):
            raise ValueError("only evidence nodes require an evidence record")
        if self.evidence is not None and self.id != self.evidence.evidence_id:
            raise ValueError("node ID must match evidence_id")
        if (self.kind == NodeKind.LITERATURE_REFERENCE) != (self.citation is not None):
            raise ValueError("only literature nodes require a citation")
        if (self.kind == NodeKind.ASSUMPTION) != (self.assumption_type is not None):
            raise ValueError("only assumption nodes require a mathematical or physical type")
        if self.claim_status is not None and self.kind not in _REASONING_NODES:
            raise ValueError("claim status applies to assertions and proof reductions")
        if self.obstruction is not None and (
            self.kind != NodeKind.OBSTACLE or self.id != self.obstruction.obstacle_id
        ):
            raise ValueError("obstruction record must match its obstacle node")
        return self


class GraphEdge(Record):
    source: Identifier
    target: Identifier
    relation: Relation


class GraphDocument(VersionedRecord):
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    assumptions: AssumptionDocument = AssumptionDocument()


@dataclass(frozen=True)
class RouteAssessment:
    route_id: str
    unmet_dependencies: tuple[str, ...]
    missing_assumptions: tuple[str, ...]
    conflicts: tuple[tuple[str, str], ...]
    blocked_by: tuple[str, ...]
    refuted: tuple[str, ...]
    reduction_supported: bool
    needs_recheck: bool

    @property
    def ready(self) -> bool:
        return self.reduction_supported and not (
            self.unmet_dependencies
            or self.missing_assumptions
            or self.conflicts
            or self.blocked_by
            or self.refuted
            or self.needs_recheck
        )


class ResearchGraph:
    """A validated graph snapshot. Edges point from a consumer to its input."""

    def __init__(self, document: GraphDocument, reviews: Mapping[str, Review] | None = None):
        self.document = GraphDocument.model_validate(document)
        self._reviews = dict(reviews or {})
        self._graph = nx.MultiDiGraph()
        seen = set()
        for node in self.document.nodes:
            folded = node.id.casefold()
            if folded in seen:
                raise ResearchError(f"Duplicate graph node ID: {node.id}")
            seen.add(folded)
            self._graph.add_node(node.id, record=node)
        for node_id in self._reviews:
            self.node(node_id)

        self._dependencies = nx.DiGraph()
        self._dependencies.add_nodes_from(self._graph)
        self._influence = nx.DiGraph()
        self._influence.add_nodes_from(self._graph)
        for edge in self.document.edges:
            self._add_edge(edge)

        if not nx.is_directed_acyclic_graph(self._dependencies):
            cycle = nx.find_cycle(self._dependencies)
            route = [*(source for source, _ in cycle), cycle[0][0]]
            raise ResearchError("Dependency cycle: " + " -> ".join(route))
        self._check_routes_and_evidence()
        self.assumption_lattice = AssumptionLattice(
            (node.id for node in self.document.nodes if node.kind == NodeKind.ASSUMPTION),
            self.document.assumptions,
        )

    def _add_edge(self, edge: GraphEdge) -> None:
        source = self.node(edge.source)
        target = self.node(edge.target)
        from_kinds, to_kinds = _EDGE_TYPES[edge.relation]
        if source.id == target.id or source.kind not in from_kinds or target.kind not in to_kinds:
            raise ResearchError(f"Invalid {edge.relation} edge: {source.id} -> {target.id}")
        if self._graph.has_edge(source.id, target.id, key=edge.relation):
            raise ResearchError(f"Duplicate {edge.relation} edge: {source.id} -> {target.id}")
        self._graph.add_edge(source.id, target.id, key=edge.relation)
        self._influence.add_edge(source.id, target.id)
        if edge.relation in DEPENDENCY_RELATIONS:
            self._dependencies.add_edge(source.id, target.id)

    def _check_routes_and_evidence(self) -> None:
        for node in self.document.nodes:
            if node.kind == NodeKind.PROOF_ROUTE:
                if len(self._sources(node.id, Relation.ALTERNATIVE_ROUTE)) != 1:
                    raise ResearchError(f"Proof route {node.id} must have exactly one owner")
                if not self._targets(node.id, {Relation.REQUIRES_ALL}):
                    raise ResearchError(
                        f"Proof route {node.id} must require at least one assertion"
                    )
                if not self._targets(node.id, {Relation.SUPPORTED_BY}):
                    raise ResearchError(
                        f"Proof route {node.id} requires a reduction evidence record"
                    )
            if evidence := node.evidence:
                if self._sources(node.id, Relation.SUPPORTED_BY) != (evidence.assertion_id,):
                    raise ResearchError(f"Evidence {node.id} must support its declared assertion")
                if evidence.assertion_revision > self.review(evidence.assertion_id).revision:
                    raise ResearchError(f"Evidence {node.id} refers to a future assertion revision")

    def node(self, node_id: str) -> GraphNode:
        if node_id not in self._graph:
            raise ResearchError(f"Unknown graph node: {node_id}")
        return self._graph.nodes[node_id]["record"]

    def review(self, node_id: str) -> Review:
        self.node(node_id)
        return self._reviews.get(node_id, Review())

    def claim_status(self, node_id: str) -> ClaimStatus:
        node = self.node(node_id)
        if node.kind not in _REASONING_NODES:
            raise ResearchError(f"Expected an assertion or proof reduction: {node_id}")
        return node.claim_status or ClaimStatus.UNPROVED

    def _targets(self, node_id: str, relations: set[Relation]) -> tuple[str, ...]:
        self.node(node_id)
        return tuple(
            sorted(
                {v for _, v, key in self._graph.out_edges(node_id, keys=True) if key in relations}
            )
        )

    def _sources(self, node_id: str, relation: Relation) -> tuple[str, ...]:
        return tuple(
            sorted(u for u, _, key in self._graph.in_edges(node_id, keys=True) if key == relation)
        )

    def upstream(self, node_id: str) -> tuple[str, ...]:
        self.node(node_id)
        return tuple(sorted(nx.descendants(self._dependencies, node_id)))

    def downstream(self, node_id: str) -> tuple[str, ...]:
        self.node(node_id)
        return tuple(sorted(nx.ancestors(self._dependencies, node_id)))

    def affected_by(self, node_id: str) -> tuple[str, ...]:
        self.node(node_id)
        return tuple(sorted(nx.ancestors(self._influence, node_id)))

    def research_inputs(self, node_id: str) -> tuple[str, ...]:
        """Return dependencies and associated assumptions, evidence, obstacles, and citations."""
        self.node(node_id)
        return tuple(sorted(nx.descendants(self._influence, node_id)))

    def problems_affected_by_obstacle(self, obstacle_id: str) -> tuple[str, ...]:
        if self.node(obstacle_id).kind != NodeKind.OBSTACLE:
            raise ResearchError(f"Expected an obstacle: {obstacle_id}")
        return tuple(
            node_id
            for node_id in self.affected_by(obstacle_id)
            if self.node(node_id).kind == NodeKind.RESEARCH_PROBLEM
        )

    def candidate_routes(self, assertion_id: str) -> tuple[str, ...]:
        return self._targets(assertion_id, {Relation.ALTERNATIVE_ROUTE})

    def dependency_alternatives(self, assertion_id: str) -> tuple[tuple[str, ...], ...]:
        mandatory = set(self._targets(assertion_id, _REQUIRES))
        routes = self.candidate_routes(assertion_id)
        if not routes:
            return (tuple(sorted(mandatory)),) if mandatory else ()
        return tuple(
            tuple(sorted(mandatory.union(self._targets(route, _REQUIRES)))) for route in routes
        )

    def assumptions_for(self, assertion_id: str) -> tuple[str, ...]:
        return self._targets(assertion_id, {Relation.USES_ASSUMPTION})

    def evidence_for(self, assertion_id: str) -> tuple[EvidenceRecord, ...]:
        return tuple(
            evidence
            for node_id in self._targets(assertion_id, {Relation.SUPPORTED_BY})
            if (evidence := self.node(node_id).evidence) is not None
        )

    def _has_decisive_evidence(self, node_id: str, direction: EvidenceDirection) -> bool:
        revision = self.review(node_id).revision
        return any(record.applies(revision, direction) for record in self.evidence_for(node_id))

    def open_blockers(self, node_id: str) -> tuple[str, ...]:
        return tuple(
            item
            for item in self._targets(node_id, {Relation.BLOCKED_BY})
            if self.node(item).obstacle_open
        )

    def route_assessments(
        self, assertion_id: str, *, available_assumptions: Iterable[str] | None = None
    ) -> tuple[RouteAssessment, ...]:
        """Evaluate route prerequisites without changing the assertion or its claim status."""
        if self.node(assertion_id).kind not in ASSERTIONS:
            raise ResearchError(f"Expected an assertion: {assertion_id}")
        declared = set(self.assumptions_for(assertion_id))
        available = self.assumption_lattice.closure(
            declared if available_assumptions is None else available_assumptions
        )
        conflicts = self.assumption_lattice.conflicts(available)
        subgraph = self._dependencies.subgraph({assertion_id, *self.upstream(assertion_id)})
        supported: dict[str, bool] = {}
        for node_id in reversed(list(nx.topological_sort(subgraph))):
            if node_id == assertion_id:
                continue
            prerequisites = self._targets(node_id, _REQUIRES)
            supported[node_id] = (
                self.review(node_id).status == ReviewStatus.CURRENT
                and not self.open_blockers(node_id)
                and not self._has_decisive_evidence(node_id, EvidenceDirection.REFUTE)
                and not conflicts
                and available.issuperset(self.assumptions_for(node_id))
                and all(supported[item] for item in prerequisites)
                and (
                    self._has_decisive_evidence(node_id, EvidenceDirection.SUPPORT)
                    or any(supported[route] for route in self.candidate_routes(node_id))
                )
            )

        assessments = []
        for route in self.candidate_routes(assertion_id):
            required = set(self._targets(assertion_id, _REQUIRES))
            required.update(self._targets(route, _REQUIRES))
            missing = (declared | set(self.assumptions_for(route))) - available
            blockers = set(self.open_blockers(assertion_id))
            blockers.update(self.open_blockers(route))
            assessments.append(
                RouteAssessment(
                    route_id=route,
                    unmet_dependencies=tuple(
                        sorted(item for item in required if not supported[item])
                    ),
                    missing_assumptions=tuple(sorted(missing)),
                    conflicts=conflicts,
                    blocked_by=tuple(sorted(blockers)),
                    refuted=tuple(
                        node_id
                        for node_id in (assertion_id, route)
                        if self._has_decisive_evidence(node_id, EvidenceDirection.REFUTE)
                    ),
                    reduction_supported=self._has_decisive_evidence(
                        route, EvidenceDirection.SUPPORT
                    ),
                    needs_recheck=self.review(route).status == ReviewStatus.NEEDS_RECHECK,
                )
            )
        return tuple(assessments)

    def has_supported_route(
        self, assertion_id: str, *, available_assumptions: Iterable[str] | None = None
    ) -> bool:
        return any(
            route.ready
            for route in self.route_assessments(
                assertion_id, available_assumptions=available_assumptions
            )
        )
