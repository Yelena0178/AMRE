"""Build a bounded active context from a restored checkpoint."""

from collections.abc import Iterator
from dataclasses import dataclass

from amre.errors import ResearchError
from amre.research.graph import DEPENDENCY_RELATIONS, GraphEdge, Relation
from amre.research.memory import RestoredState
from amre.research.memory_models import ContextSourceKind

_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class ActiveContext:
    text: str
    size_bytes: int
    max_bytes: int
    omitted: tuple[str, ...]


def _dependencies(state: RestoredState) -> tuple[GraphEdge, ...]:
    graph = state.graph
    active = state.checkpoint.active_route
    result = []
    seen = set()
    queue = [state.checkpoint.problem_id]
    outgoing: dict[str, list[GraphEdge]] = {}
    for edge in graph.document.edges:
        if edge.relation in DEPENDENCY_RELATIONS:
            outgoing.setdefault(edge.source, []).append(edge)
    while queue:
        node_id = queue.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        selected = active is not None and active in graph.candidate_routes(node_id)
        for edge in outgoing.get(node_id, ()):
            if selected and edge.relation == Relation.ALTERNATIVE_ROUTE and edge.target != active:
                continue
            result.append(edge)
            queue.append(edge.target)
    return tuple(result)


def _evidence_lines(state: RestoredState, node_id: str) -> list[str]:
    return [
        f"{item.evidence_id} | {item.direction} | {item.source_type} | "
        f"{item.coverage} | {item.check_result} | revision {item.assertion_revision} | "
        f"{', '.join(item.properties) or 'no verification property'} | "
        f"{item.check_scope} | {item.file_path}"
        for item in state.graph.evidence_for(node_id)
    ]


def _required_blocks(state: RestoredState, dependencies: tuple[GraphEdge, ...]) -> tuple[str, ...]:
    checkpoint = state.checkpoint
    contract = checkpoint.contract
    graph = state.graph
    statement = f"## Proposition {contract.problem_id}\n{state.read_statement(contract.statement)}"
    assumptions = []
    for identifier in graph.assumptions_for(contract.problem_id):
        node = graph.node(identifier)
        assumptions.append(f"{node.id} ({node.assumption_type}): {node.label}")
        if node.statement is not None:
            assumptions.append(state.read_statement(node.statement))
    definitions = [f"{item.id}: {item.text}" for item in contract.definitions]
    objective = [checkpoint.next_research_action]
    objective.extend(f"Unresolved: {item}" for item in checkpoint.unresolved_proof_obligations)
    route = []
    if checkpoint.active_route:
        node = graph.node(checkpoint.active_route)
        route.append(f"{node.id}: {node.label}")
        if node.statement is not None:
            route.append(state.read_statement(node.statement))
        route.extend(_evidence_lines(state, node.id))
    links = [f"{edge.source} {edge.relation} {edge.target}" for edge in dependencies]
    blockers = ", ".join(checkpoint.active_obstacles) or "None recorded"
    evidence = ", ".join(checkpoint.evidence_references) or "None recorded"
    return (
        statement,
        "## Assumptions and definitions\n"
        + ("\n".join(assumptions + definitions) or "None declared"),
        "## Current objective\n" + "\n".join(objective),
        "## Active proof route\n" + ("\n".join(route) or "None selected"),
        "## Dependency relations\n" + ("\n".join(links) or "None recorded"),
        "## Active obstacle IDs\n" + blockers,
        "## Checkpoint evidence IDs\n" + evidence,
    )


def _node_blocks(
    state: RestoredState, dependencies: tuple[GraphEdge, ...]
) -> Iterator[tuple[str, str]]:
    checkpoint = state.checkpoint
    graph = state.graph
    seen = {checkpoint.problem_id, checkpoint.active_route}
    for edge in dependencies:
        if edge.target in seen:
            continue
        seen.add(edge.target)
        node = graph.node(edge.target)
        lines = [f"### Dependency {node.id}: {node.label}"]
        if node.statement is not None:
            lines.append(state.read_statement(node.statement))
        for assumption_id in graph.assumptions_for(node.id):
            assumption = graph.node(assumption_id)
            lines.append(f"Assumption {assumption.id}: {assumption.label}")
            if assumption.statement is not None:
                lines.append(state.read_statement(assumption.statement))
        review = graph.review(node.id)
        lines.append(
            f"{graph.claim_status(node.id)} | revision {review.revision} | {review.status}"
        )
        lines.extend(_evidence_lines(state, node.id))
        yield f"dependency:{node.id}", "\n".join(lines)


def build_context(state: RestoredState, *, max_bytes: int) -> ActiveContext:
    """Include complete sections within a strict UTF-8 byte budget."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ResearchError("Context budget must be a positive UTF-8 byte count")
    checkpoint = state.checkpoint
    dependencies = _dependencies(state)
    blocks = list(_required_blocks(state, dependencies))
    size = len(_SEPARATOR.join(blocks).encode("utf-8"))
    if size > max_bytes:
        raise ResearchError(f"Required context needs {size} UTF-8 bytes, budget is {max_bytes}")
    omitted = []

    def add(name: str, text: str) -> None:
        nonlocal size
        length = len((_SEPARATOR + text).encode("utf-8"))
        if size + length <= max_bytes:
            blocks.append(text)
            size += length
        else:
            omitted.append(name)

    def sources(kind: ContextSourceKind, heading: str) -> None:
        for source in checkpoint.context_sources:
            if source.kind == kind:
                name = f"{kind}:{source.path}"
                if state.archive_ref(source.path).size > max_bytes - size:
                    omitted.append(name)
                    continue
                add(name, f"### {heading} [{source.path}]\n{state.read_text(source.path)}")

    for name, text in _node_blocks(state, dependencies):
        add(name, text)
    for identifier in checkpoint.active_obstacles:
        node = state.graph.node(identifier)
        add(f"obstacle:{identifier}", f"### Obstacle {identifier}\n{node.label}")
    for event in reversed(state.failed_attempts):
        add(
            f"attempt:{event.data.target_id}",
            f"### Failed attempt {event.data.target_id}\n"
            f"{event.data.summary}\nEvent: {event.event_id}",
        )
    sources(ContextSourceKind.EXCLUDED_METHOD, "Excluded method")
    for label, results in (
        ("Established", checkpoint.established_results),
        ("Candidate", checkpoint.candidate_results),
    ):
        for result in reversed(results):
            refs = ", ".join(result.evidence_references) or "None recorded"
            add(
                f"result:{result.assertion_id}",
                f"### {label} derivation {result.assertion_id}\n{result.summary}\nEvidence: {refs}",
            )
    sources(ContextSourceKind.DERIVATION, "Derivation")
    relevant = {
        checkpoint.problem_id,
        checkpoint.active_route,
        *checkpoint.active_obstacles,
        *(edge.target for edge in dependencies),
    }
    literature = [
        edge.target
        for edge in state.graph.document.edges
        if edge.relation == Relation.CITES and edge.source in relevant
    ]
    seen_citations = set()
    for identifier in literature:
        if identifier in seen_citations:
            continue
        seen_citations.add(identifier)
        citation = state.graph.node(identifier)
        literature.extend(
            edge.target
            for edge in state.graph.document.edges
            if edge.relation == Relation.CITES
            and edge.source == identifier
            and edge.target not in seen_citations
        )
        block = f"### Literature {citation.id}\n{citation.citation}"
        review = state.graph.review(identifier)
        if citation.statement:
            if state.archive_ref(citation.statement.path).size > max_bytes - size:
                omitted.append(f"literature:{identifier}")
                continue
            conditions = ", ".join(state.graph.assumptions_for(identifier)) or "None specified"
            block += (
                f"\nReview: {review.status}\nSource assumptions: {conditions}\n"
                + state.read_text(citation.statement.path)
            )
        add(f"literature:{identifier}", block)
    sources(ContextSourceKind.LITERATURE, "Literature")
    return ActiveContext(_SEPARATOR.join(blocks), size, max_bytes, tuple(omitted))
