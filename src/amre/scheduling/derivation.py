"""Derive a scoped bottleneck contract and attach it to a single proof route."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Annotated

from pydantic import Field

from amre.errors import ResearchError
from amre.research.graph import GraphEdge, NodeKind
from amre.research.models import (
    AcceptanceCriterion,
    Budget,
    DerivationOrigin,
    Identifier,
    Text,
    VersionedRecord,
    statement_hash,
)
from amre.research.storage import validate
from amre.research.store import ResearchStore
from amre.scheduling.portfolio import load_settings


class DerivationInput(VersionedRecord):
    assumptions: tuple[Identifier, ...] = ()
    dependencies: tuple[Identifier, ...] = ()
    acceptance_criteria: Annotated[tuple[AcceptanceCriterion, ...], Field(min_length=1)]
    evidence: tuple[Identifier, ...] = ()
    author: Text
    budget: Budget | None = None


def derive_problem(
    root: Path,
    parent_id: str,
    obstacle_id: str,
    route_id: str,
    raw: object,
    statement: str,
) -> str:
    spec = validate(DerivationInput, raw, "derived research contract")
    store = ResearchStore(root)
    document, graph = store.snapshot()
    parent = document.contract(parent_id)
    scope = {parent_id, *graph.research_inputs(parent_id)}
    if route_id not in scope or graph.node(route_id).kind != NodeKind.PROOF_ROUTE:
        raise ResearchError("Derived problem must attach to a scoped route")
    obstacle = graph.node(obstacle_id).obstruction if obstacle_id in scope else None
    if (
        obstacle is None
        or obstacle.problem_id != parent_id
        or not graph.node(obstacle_id).obstacle_open
        or obstacle.routes
        and route_id not in obstacle.routes
    ):
        raise ResearchError("Derivation needs a current scoped obstruction")
    assumptions = graph.assumption_lattice.closure(graph.assumptions_for(parent_id))
    if not assumptions.issuperset(spec.assumptions):
        raise ResearchError("Derived hypotheses must follow from the parent contract")
    if not set(spec.dependencies).issubset(scope - {parent_id}):
        raise ResearchError("Derived dependencies must refer to scoped prerequisite claims")
    sources = tuple(dict.fromkeys((*obstacle.evidence_refs, *spec.evidence)))
    if any(key not in scope or graph.node(key).evidence is None for key in sources):
        raise ResearchError("Derived source evidence must belong to the parent scope")
    fingerprint = sha256(
        json.dumps(
            {
                "parent": parent_id,
                "obstacle": obstacle_id,
                "statement": statement_hash(statement),
                "assumptions": sorted(spec.assumptions),
                "dependencies": sorted(spec.dependencies),
                "criteria": sorted(
                    (item.id, item.description) for item in spec.acceptance_criteria
                ),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    existing = next(
        (
            item
            for item in document.contracts
            if item.derivation and item.derivation.fingerprint == fingerprint
        ),
        None,
    )
    if existing:
        edge = GraphEdge(source=route_id, target=existing.problem_id, relation="REQUIRES_ALL")
        if edge not in document.graph.edges:
            store.replace_graph(
                document.graph.model_copy(update={"edges": (*document.graph.edges, edge)}),
                expected=document.graph,
            )
        return existing.problem_id
    strategy = load_settings(root).strategy
    depth = 1 + (parent.derivation.depth if parent.derivation else 0)
    derived = [item.derivation for item in document.contracts if item.derivation]
    if (
        depth > strategy.max_derived_depth
        or len(derived) >= strategy.max_derived_total
        or sum(item.parent_problem == parent_id for item in derived) >= strategy.max_children
    ):
        raise ResearchError("Research problem derivation limit reached")
    child = f"Derived-{fingerprint[:24]}"
    origin = DerivationOrigin(
        parent_problem=parent_id,
        obstacle_id=obstacle_id,
        route_id=route_id,
        source_evidence=sources,
        fingerprint=fingerprint,
        depth=depth,
    )
    edge = GraphEdge(source=route_id, target=child, relation="REQUIRES_ALL")
    store.create(
        {
            "problem_id": child,
            "assumptions": spec.assumptions,
            "dependencies": spec.dependencies,
            "acceptance_criteria": spec.acceptance_criteria,
            "budget": spec.budget or parent.budget,
            "provenance": {
                "author": spec.author,
                "source": f"Derived from {parent_id} at {obstacle_id}",
            },
            "derivation": origin,
        },
        statement,
        graph=document.graph.model_copy(update={"edges": (*document.graph.edges, edge)}),
        expected=document,
    )
    return child
