"""Queries over published attempts and scoped obstruction records."""

import re
from dataclasses import dataclass
from pathlib import Path

from amre.errors import ResearchError
from amre.execution.models import AttemptReceipt, ReasoningOutcome
from amre.execution.skill_models import ProgressKind, ReasoningMode
from amre.execution.skill_publication import ProgressBatch, progress_path
from amre.execution.skills import claim_drafts
from amre.research.graph import NodeKind
from amre.research.journal import read_versioned
from amre.research.memory import MemoryInventory, ResearchMemory
from amre.research.memory_models import EventKind
from amre.research.models import ReviewStatus
from amre.research.obstructions import ObstructionCategory, ObstructionStatus


@dataclass(frozen=True)
class MethodAdvice:
    mode: ReasoningMode
    action: str


_SWITCH = {
    ObstructionCategory.BOUNDARY: "Introduce a trace lemma with explicit boundary conditions.",
    ObstructionCategory.REGULARITY: "Test a weaker space with explicit trace or embedding.",
    ObstructionCategory.COMPACTNESS: "Test a tighter space or a concentration counterexample.",
    ObstructionCategory.SPECTRAL: "Compare spectral and energy estimates on the same domain.",
    ObstructionCategory.ASSUMPTION: "Formulate changed hypotheses in a separate Research Contract.",
    ObstructionCategory.ALGEBRAIC: "Isolate the failing algebraic identity as a separate lemma.",
    ObstructionCategory.GAUGE: "Test a different gauge with explicit residual constraints.",
    ObstructionCategory.NONLINEARITY: "Try a different nonlinear estimate with explicit constants.",
    ObstructionCategory.CITATION: "Isolate the missing cited implication as a separate lemma.",
    ObstructionCategory.UNKNOWN: "Isolate the exact failed inference before switching methods.",
}


def recommendations(category: ObstructionCategory) -> tuple[MethodAdvice, ...]:
    return (
        MethodAdvice(ReasoningMode.METHOD_INNOVATION, _SWITCH[category]),
        MethodAdvice(
            ReasoningMode.COUNTEREXAMPLE_SEARCH,
            "Search for a witness within the exact excluded scope.",
        ),
        MethodAdvice(
            ReasoningMode.LEMMA_DECOMPOSITION,
            "Derive a separate Research Contract for the unresolved mathematical step.",
        ),
    )


class FailureMemory:
    def __init__(self, root: Path):
        self.memory = ResearchMemory(root)
        self.root = self.memory.root

    def obstacles(self, problem_id: str) -> tuple[dict, ...]:
        document, graph = self.memory.research.snapshot()
        document.contract(problem_id)
        return tuple(
            node.obstruction.model_dump(mode="json")
            if node.obstruction
            else {
                "obstacle_id": node.id,
                "description": node.label,
                "status": "OPEN",
                "category": "UNKNOWN",
                "legacy": True,
            }
            for key in graph.research_inputs(problem_id)
            if (node := graph.node(key)).kind == NodeKind.OBSTACLE
        )

    def obstacle(self, obstacle_id: str) -> dict:
        graph = self.memory.research.graph()
        node = graph.node(obstacle_id)
        if node.kind != NodeKind.OBSTACLE:
            raise ResearchError(f"Not an obstruction: {obstacle_id}")
        if node.obstruction is None:
            return {
                "obstacle_id": node.id,
                "description": node.label,
                "status": "OPEN",
                "category": "UNKNOWN",
                "legacy": True,
            }
        return node.obstruction.model_dump(mode="json")

    def search(self, problem_id: str, query: str) -> tuple[dict, ...]:
        tokens = set(re.findall(r"\w+", query.casefold()))
        hits = []
        for item in self.obstacles(problem_id):
            if query == item["obstacle_id"]:
                hits.append({"match": "EXACT_ID", **item})
            elif tokens & set(re.findall(r"\w+", item["description"].casefold())):
                hits.append({"match": "CANDIDATE", **item})
        return tuple(hits)

    def attempts(
        self,
        problem_id: str,
        *,
        failures: bool = False,
        obstacle_id: str | None = None,
        inventory: MemoryInventory | None = None,
    ) -> tuple[dict, ...]:
        inventory = inventory or self.memory.inventory()
        inventory.document.contract(problem_id)
        relevant_attempts = (
            set(self.obstacle(obstacle_id).get("attempts", [])) if obstacle_id else set()
        )
        rows = []
        for event in inventory.events:
            data = event.data
            if data.kind != EventKind.ATTEMPT_COMPLETED or data.problem_id != problem_id:
                continue
            path = f"research/attempts/{data.target_id}/receipt.yaml"
            reference = next((item for item in event.artifacts if item.path == path), None)
            if obstacle_id and data.target_id not in relevant_attempts:
                continue
            if reference is None:
                if not failures or data.outcome != "SUCCEEDED":
                    rows.append(
                        {
                            "attempt_id": data.target_id,
                            "problem_id": problem_id,
                            "journal_outcome": data.outcome,
                            "summary": data.summary,
                            "legacy": True,
                        }
                    )
                continue
            receipt = read_versioned(self.root, reference.blob_path, AttemptReceipt)
            result = receipt.result
            progress_ref = next(
                (item for item in event.artifacts if item.path == progress_path(data.target_id)),
                None,
            )
            progress = (
                read_versioned(self.root, progress_ref.blob_path, ProgressBatch).events
                if progress_ref
                else ()
            )
            obstacles = [
                node.obstruction.obstacle_id
                for node in inventory.graph.document.nodes
                if node.obstruction and receipt.task.attempt_id in node.obstruction.attempts
            ]
            if (
                failures
                and data.outcome == "SUCCEEDED"
                and result
                and not (
                    result.outcome in {ReasoningOutcome.NO_PROGRESS, ReasoningOutcome.BLOCKED}
                    or obstacles
                    or result.obstacles
                )
            ):
                continue
            rows.append(
                {
                    "attempt_id": receipt.task.attempt_id,
                    "problem_id": problem_id,
                    "route_id": receipt.task.route_id,
                    "reasoning_mode": receipt.task.reasoning_mode,
                    "skill_version": receipt.task.skill_version,
                    "journal_outcome": data.outcome,
                    "outcome": result.outcome if result else receipt.failure,
                    "new_claims": [claim.model_dump(mode="json") for claim in claim_drafts(result)]
                    if result
                    else [],
                    "new_obstacles": [
                        item.subject_id
                        for item in progress
                        if item.kind == ProgressKind.OBSTACLE_DISCOVERED
                    ]
                    if receipt.task.reasoning_mode
                    else list(result.obstacles)
                    if result
                    else [],
                    "encountered_obstacles": obstacles
                    or (list(result.obstacles) if result else []),
                    "proof_artifacts": [
                        f"research/attempts/{data.target_id}/draft-{item.name}"
                        for item in result.artifacts
                    ]
                    if result
                    else [],
                    "computation_artifacts": [
                        f"research/attempts/{data.target_id}/draft-{item.name}"
                        for item in result.computation_artifacts
                    ]
                    if result
                    else [],
                    "unresolved_steps": result.unresolved_steps if result else (),
                    "recommended_next_action": result.next_action if result else None,
                    "summary": data.summary,
                    "receipt": path,
                    "progress": [item.model_dump(mode="json") for item in progress],
                }
            )
        return tuple(rows)

    def resolve(self, obstacle_id: str, evidence_id: str) -> dict:
        store = self.memory.research
        document, graph = store.snapshot()
        node = graph.node(obstacle_id)
        obstacle = node.obstruction
        if obstacle is None:
            raise ResearchError("Only structured obstructions can be resolved")
        if obstacle.status == ObstructionStatus.RESOLVED:
            if obstacle.resolution_evidence == evidence_id:
                return {"obstacle_id": obstacle_id, "affected": graph.affected_by(obstacle_id)}
            raise ResearchError("Obstruction already resolved")
        evidence = graph.node(evidence_id).evidence
        if evidence is None or evidence.assertion_id not in {obstacle.problem_id, *obstacle.routes}:
            raise ResearchError("Resolution evidence must apply to the affected assertion")
        review = graph.review(evidence.assertion_id)
        if (
            review.status != ReviewStatus.CURRENT
            or not evidence.applies(review.revision, "SUPPORT")
            or evidence.check_scope != f"obstruction:{obstacle_id}"
            or any(
                item.applies(review.revision, "REFUTE")
                for item in graph.evidence_for(evidence.assertion_id)
            )
        ):
            raise ResearchError("Resolution needs current independent evidence for the exact scope")
        updated = node.model_copy(
            update={
                "obstruction": obstacle.model_copy(
                    update={
                        "status": ObstructionStatus.RESOLVED,
                        "resolution_evidence": evidence_id,
                        "version": obstacle.version + 1,
                        "evidence_refs": tuple(
                            dict.fromkeys((*obstacle.evidence_refs, evidence_id))
                        ),
                    }
                )
            }
        )
        nodes = tuple(updated if item.id == obstacle_id else item for item in document.graph.nodes)
        store.update(
            obstacle.problem_id,
            {},
            expected=document,
            graph=document.graph.model_copy(update={"nodes": nodes}),
        )
        return {"obstacle_id": obstacle_id, "affected": graph.affected_by(obstacle_id)}
