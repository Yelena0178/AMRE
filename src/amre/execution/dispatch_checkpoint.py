"""Prepare an idempotent pair of checkpoints around a planned route switch."""

from amre.errors import ResearchError, WorkspaceChanged
from amre.execution.models import BranchDispatch
from amre.research.graph import NodeKind
from amre.research.memory import MemoryInventory, ResearchMemory, RestoredState
from amre.research.memory_models import EventKind
from amre.research.models import ReviewStatus


def decision_path(decision_id: str) -> str:
    return f"research/scheduler/decisions/{decision_id}.yaml"


def prepare_dispatch(
    memory: ResearchMemory,
    inventory: MemoryInventory,
    plan: BranchDispatch,
) -> RestoredState:
    problem = plan.problem_id
    graph = inventory.graph
    scope = {problem, *graph.research_inputs(problem)}
    if inventory.document.research_fingerprint() != plan.graph_fingerprint:
        raise WorkspaceChanged("Research changed since the scheduler decision")
    if plan.route_id not in scope or graph.node(plan.route_id).kind != NodeKind.PROOF_ROUTE:
        raise ResearchError("Selected route is outside the problem scope")
    state = inventory.states.get(problem)
    latest = next(
        (
            item
            for item in inventory.checkpoints
            if state and item.checkpoint_id == state.latest_checkpoint
        ),
        None,
    )
    if latest and latest.dispatch_id == plan.decision_id and latest.dispatch_phase == "READY":
        restored = memory.restore(problem, latest.checkpoint_id)
        if not restored.workspace_matches:
            raise WorkspaceChanged("Research changed after the scheduler checkpoint")
        return restored

    previous_route = latest.active_route if latest and latest.active_route in scope else None
    contract = inventory.document.contract(problem)
    notes = {
        "next_research_action": latest.next_research_action
        if latest
        else contract.acceptance_criteria[0].description,
        "strategy_version": latest.strategy_version if latest else plan.strategy_version,
        "active_route": previous_route,
        "dispatch_id": plan.decision_id,
    }
    if latest:
        notes["usage"] = latest.budget_snapshot.model_dump(
            include={"tokens_used", "elapsed_seconds"}
        )
        notes["context_sources"] = latest.context_sources
        notes["unresolved_proof_obligations"] = latest.unresolved_proof_obligations
        valid = [
            item
            for item in (*latest.established_results, *latest.candidate_results)
            if item.assertion_id in scope
            and set(item.evidence_references).issubset(
                evidence.evidence_id for evidence in graph.evidence_for(item.assertion_id)
            )
        ]
        established = []
        for item in valid:
            review = graph.review(item.assertion_id)
            records = graph.evidence_for(item.assertion_id)
            if (
                item in latest.established_results
                and review.status == ReviewStatus.CURRENT
                and any(
                    record.evidence_id in item.evidence_references
                    and record.applies(review.revision, "SUPPORT")
                    for record in records
                )
                and not any(record.applies(review.revision, "REFUTE") for record in records)
            ):
                established.append(item)
        notes["established_results"] = established
        notes["candidate_results"] = [item for item in valid if item not in established]

    def current_document():
        document = memory.research.read()
        if document.research_fingerprint() != plan.graph_fingerprint:
            raise WorkspaceChanged("Research changed during scheduler checkpoint preparation")
        return document

    if previous_route != plan.route_id:
        if not any(
            item.dispatch_id == plan.decision_id and item.dispatch_phase == "BEFORE_SWITCH"
            for item in inventory.checkpoints
        ):
            memory.create_checkpoint(
                problem, {**notes, "dispatch_phase": "BEFORE_SWITCH"}, expected=current_document()
            )
        path = decision_path(plan.decision_id)
        if not any(
            event.data.kind == EventKind.ROUTE_SWITCHED and path in event.data.artifact_paths
            for event in inventory.events
        ):
            memory.append_event(
                {
                    "kind": EventKind.ROUTE_SWITCHED,
                    "problem_id": problem,
                    "target_id": plan.route_id,
                    "summary": plan.objective,
                    "artifact_paths": [path],
                },
                expected=current_document(),
            )
    ready = memory.create_checkpoint(
        problem,
        {
            **notes,
            "active_route": plan.route_id,
            "next_research_action": plan.objective,
            "strategy_version": plan.strategy_version,
            "dispatch_phase": "READY",
        },
        expected=current_document(),
    )
    restored = memory.restore(problem, ready.checkpoint_id)
    if not restored.workspace_matches:
        raise WorkspaceChanged("Research changed after the scheduler checkpoint")
    return restored
