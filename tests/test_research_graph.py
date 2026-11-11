import pytest

from amre.errors import ResearchError
from amre.research.assumptions import AssumptionDocument, AssumptionSet, Conflict, Implication
from amre.research.graph import GraphDocument, GraphEdge, GraphNode, ResearchGraph, RouteAssessment
from amre.research.models import (
    ClaimStatus,
    Coverage,
    EvidenceDirection,
    EvidenceRecord,
    EvidenceSource,
    ReviewStatus,
    StatementRef,
)
from amre.research.store import ResearchStore


def _edge(source: str, relation: str, target: str) -> GraphEdge:
    return GraphEdge(source=source, relation=relation, target=target)


def _add_evidence(
    store: ResearchStore,
    subjects: tuple[str, ...],
    *,
    source: EvidenceSource = EvidenceSource.HUMAN,
    coverage: Coverage = Coverage.FULL_ASSERTION,
    direction: EvidenceDirection = EvidenceDirection.SUPPORT,
) -> None:
    document, graph = store.snapshot()
    nodes = list(document.graph.nodes)
    edges = list(document.graph.edges)
    artifact = store.root / "research" / "synthetic-check.txt"
    artifact.write_text("Synthetic test record. No mathematical verification.", encoding="utf-8")
    prop = {
        EvidenceSource.HUMAN: "HUMAN_CONFIRMED",
        EvidenceSource.FORMAL_TOOL: "FORMALLY_VERIFIED",
        EvidenceSource.COMPUTATION: "COMPUTATION_TESTED",
    }[source]
    for subject in subjects:
        revision = graph.review(subject).revision
        evidence_id = f"Test-{subject}-{source}-{coverage}-{direction}-{revision}"
        evidence = EvidenceRecord(
            evidence_id=evidence_id,
            source_type=source,
            file_path="research/synthetic-check.txt",
            git_revision="a" * 40,
            run={"tool": "pytest fixture", "version": "1", "run_id": "synthetic"},
            check_scope="Test only",
            check_result="PASS",
            coverage=coverage,
            direction=direction,
            assertion_id=subject,
            assertion_revision=revision,
            properties=(prop,),
        )
        nodes.append(
            GraphNode(id=evidence_id, kind="EVIDENCE", label="Synthetic", evidence=evidence)
        )
        edges.append(_edge(subject, "SUPPORTED_BY", evidence_id))
    store.replace_graph(
        GraphDocument(
            nodes=tuple(nodes), edges=tuple(edges), assumptions=document.graph.assumptions
        ),
        expected=document.graph,
    )


def _assess(store: ResearchStore) -> dict[str, RouteAssessment]:
    return {item.route_id: item for item in store.graph().route_assessments("T")}


def test_and_or_requires_all_lemmas_and_verified_reduction(example_store: ResearchStore) -> None:
    graph = example_store.graph()
    assert graph.candidate_routes("T") == ("A", "B")
    assert graph.dependency_alternatives("T") == (("L1", "L2"), ("L3", "L4"))
    assert graph.upstream("T") == ("A", "B", "L1", "L2", "L3", "L4")
    assert graph.downstream("L1") == ("A", "P-T", "T")
    assert graph.problems_affected_by_obstacle("O-Review") == ("P-T",)
    assert not graph.has_supported_route("T")

    _add_evidence(example_store, ("A", "L1", "L3", "L4"))
    _add_evidence(example_store, ("L2",), source=EvidenceSource.COMPUTATION)
    _add_evidence(example_store, ("L2",), coverage=Coverage.PARTIAL)
    assessments = _assess(example_store)
    assert assessments["A"].unmet_dependencies == ("L2",)
    assert assessments["A"].reduction_supported
    assert not assessments["A"].ready
    assert assessments["B"].unmet_dependencies == ()
    assert not assessments["B"].reduction_supported
    assert not example_store.graph().has_supported_route("T")

    _add_evidence(example_store, ("L2",), source=EvidenceSource.FORMAL_TOOL)
    graph = example_store.graph()
    assert _assess(example_store)["A"].ready
    assert not _assess(example_store)["B"].ready
    assert graph.has_supported_route("T")
    assert graph.claim_status("T") == ClaimStatus.PROOF_CANDIDATE


def _replace_statement(store: ResearchStore, node_id: str, text: str) -> None:
    original = store.read().graph
    nodes = tuple(
        node.model_copy(update={"statement": StatementRef.from_text(text)})
        if node.id == node_id
        else node
        for node in original.nodes
    )
    store.replace_graph(original.model_copy(update={"nodes": nodes}), statements=(text,))


def test_dependency_change_preserves_the_unaffected_alternative(
    example_store: ResearchStore,
) -> None:
    _add_evidence(example_store, ("A", "B", "L1", "L2", "L3", "L4"))
    assert all(route.ready for route in _assess(example_store).values())
    _replace_statement(example_store, "L1", r"\[ x=x. \]")

    reloaded = ResearchStore(example_store.root)
    document, graph = reloaded.snapshot()
    for node in ("L1", "A", "T", "P-T"):
        assert graph.review(node).status == ReviewStatus.NEEDS_RECHECK
        assert graph.review(node).revision == 2
    assert graph.review("B").status == ReviewStatus.CURRENT
    assert graph.claim_status("T") == ClaimStatus.PROOF_CANDIDATE
    assert not _assess(reloaded)["A"].ready
    assert _assess(reloaded)["B"].ready
    assert graph.evidence_for("L1")[0].assertion_revision == 1
    assert document == example_store.read()
    with pytest.raises(ResearchError, match="Revision changed"):
        reloaded.acknowledge_recheck("L1", revision=1)
    reloaded.acknowledge_recheck("L1", revision=2)
    assert reloaded.graph().review("L1").status == ReviewStatus.CURRENT
    assert _assess(reloaded)["A"].unmet_dependencies == ("L1",)


def test_theorem_change_invalidates_its_proof_reductions(example_store: ResearchStore) -> None:
    _add_evidence(example_store, ("A", "B", "L1", "L2", "L3", "L4"))
    _replace_statement(example_store, "T", r"\[ x > 100. \]")
    graph = example_store.graph()
    assert all(graph.review(route).revision == 2 for route in ("A", "B"))
    assert not graph.has_supported_route("T")
    assert graph.review("L1").revision == 1


def test_passing_refutation_cannot_support_a_route(example_store: ResearchStore) -> None:
    _add_evidence(example_store, ("L1", "L2"))
    _add_evidence(example_store, ("A",), direction=EvidenceDirection.REFUTE)
    a = _assess(example_store)["A"]
    assert not a.reduction_supported
    assert a.refuted == ("A",)
    assert not a.ready
    _add_evidence(example_store, ("A",), direction=EvidenceDirection.SUPPORT)
    assert not _assess(example_store)["A"].ready
    assert example_store.graph().claim_status("T") == ClaimStatus.PROOF_CANDIDATE


def test_explicit_assumptions_and_conflicts(example_store: ResearchStore) -> None:
    _add_evidence(example_store, ("A", "B", "L1", "L2", "L3", "L4"))
    expanded = example_store.graph().document
    positive = GraphNode(
        id="H-Positive",
        kind="ASSUMPTION",
        label="The variables are real",
        assumption_type="MATHEMATICAL",
    )
    non_real = GraphNode(
        id="H-Nonreal",
        kind="ASSUMPTION",
        label="Non-real variables",
        assumption_type="MATHEMATICAL",
    )
    signed = GraphNode(
        id="H-Signed", kind="ASSUMPTION", label="Signed configuration", assumption_type="PHYSICAL"
    )
    assumptions = AssumptionDocument(
        sets=(AssumptionSet(id="Positive-Setting", members=("H-Positive", "H-Signed")),),
        implications=(
            Implication(
                premises=("H-Positive", "H-Signed"),
                conclusion="H-Real",
                basis="Explicit fixture entailment",
            ),
        ),
        conflicts=(Conflict(left="H-Positive", right="H-Nonreal", basis="Disjoint domains"),),
    )
    variant = expanded.model_copy(
        update={
            "nodes": (*expanded.nodes, positive, non_real, signed),
            "edges": (*expanded.edges, _edge("B", "USES_ASSUMPTION", "H-Positive")),
            "assumptions": assumptions,
        }
    )
    graph = ResearchGraph(variant)
    lattice = graph.assumption_lattice
    context = lattice.members("Positive-Setting")
    assert graph.assumptions_for("T") == ("H-Real",)
    assert lattice.implies(("H-Real",), "H-Positive") is None
    assert lattice.implies(("H-Positive",), "H-Real") is None
    assert lattice.implies(context, "H-Real") is True
    assert lattice.implies(context, "H-Nonreal") is False
    a, b = graph.route_assessments("T")
    assert a.ready and not b.ready
    assert b.missing_assumptions == ("H-Positive",)
    assert all(route.ready for route in graph.route_assessments("T", available_assumptions=context))
    conflicting = (*context, "H-Nonreal")
    assert lattice.conflicts(conflicting) == (("H-Nonreal", "H-Positive"),)
    assert not graph.has_supported_route("T", available_assumptions=conflicting)


def test_assumption_edit_marks_dependent_claims(example_store: ResearchStore) -> None:
    snapshot = example_store.read().graph
    nodes = tuple(
        node.model_copy(update={"label": "The variables are nonnegative real numbers"})
        if node.id == "H-Real"
        else node
        for node in snapshot.nodes
    )
    example_store.replace_graph(snapshot.model_copy(update={"nodes": nodes}), expected=snapshot)
    graph = example_store.graph()
    for assertion in ("T", "L1", "P-T", "A", "B"):
        assert graph.review(assertion).status == ReviewStatus.NEEDS_RECHECK
    assert graph.review("O-Review").status == ReviewStatus.CURRENT


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("duplicate_node", "Duplicate graph node ID"),
        ("unknown_reference", "Unknown graph node"),
        ("bad_edge_type", "Invalid REQUIRES_ALL"),
        ("dependency_cycle", "Dependency cycle"),
        ("duplicate_edge", "Duplicate ALTERNATIVE_ROUTE"),
        ("missing_reduction_inputs", "must require at least one"),
        ("missing_reduction_evidence", "requires a reduction evidence record"),
        ("wrong_evidence", "declared assertion"),
    ],
)
def test_rejects_invalid_graph(example_store: ResearchStore, case: str, message: str) -> None:
    data = example_store.graph().document
    nodes, edges = data.nodes, data.edges
    match case:
        case "duplicate_node":
            nodes += (nodes[0].model_copy(update={"id": nodes[0].id.lower()}),)
        case "unknown_reference":
            edges += (_edge("L1", "DEPENDS_ON", "Missing"),)
        case "bad_edge_type":
            edges += (_edge("L1", "REQUIRES_ALL", "L2"),)
        case "dependency_cycle":
            edges += (_edge("L1", "DEPENDS_ON", "T"),)
        case "duplicate_edge":
            edges += (edges[0],)
        case "missing_reduction_inputs":
            edges = tuple(
                edge
                for edge in edges
                if not (edge.source == "A" and edge.relation == "REQUIRES_ALL")
            )
        case "wrong_evidence":
            edges += (_edge("T", "SUPPORTED_BY", "E-A-Candidate"),)
        case "missing_reduction_evidence":
            nodes = tuple(node for node in nodes if node.id != "E-A-Candidate")
            edges = tuple(edge for edge in edges if edge.target != "E-A-Candidate")
    with pytest.raises(ResearchError, match=message):
        ResearchGraph(data.model_copy(update={"nodes": nodes, "edges": edges}))


def test_parallel_edges_and_transitive_obstacle_impact(example_store: ResearchStore) -> None:
    _add_evidence(example_store, ("A", "L1", "L2"))
    base = example_store.graph().document
    book = GraphNode(
        id="Ref", kind="LITERATURE_REFERENCE", label="Fixture", citation="Test citation"
    )
    edges = tuple(edge for edge in base.edges if edge.relation != "BLOCKED_BY")
    graph = ResearchGraph(
        base.model_copy(
            update={
                "nodes": (*base.nodes, book),
                "edges": (
                    *edges,
                    _edge("L1", "DERIVED_FROM", "L2"),
                    _edge("L1", "DEPENDS_ON", "L2"),
                    _edge("L1", "CITES", "Ref"),
                    _edge("L1", "BLOCKED_BY", "O-Review"),
                ),
            }
        )
    )
    assert graph.upstream("L1") == ("L2",)
    assert graph.problems_affected_by_obstacle("O-Review") == ("P-T",)
    assert graph.route_assessments("T")[0].unmet_dependencies == ("L1",)
