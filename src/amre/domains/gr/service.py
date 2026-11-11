"""Execute, persist, and attach scoped GR artifacts to the existing research graph."""

import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from amre.domains.gr.models import GRConventions
from amre.domains.gr.pack import INPUTS, TOOL_VERSION
from amre.domains.models import ComputationArtifact, ComputationResult
from amre.errors import ResearchError
from amre.execution.adapters import redact, run_process
from amre.execution.models import Failure, ReasoningTask
from amre.research.graph import ASSERTIONS, GraphNode, NodeKind
from amre.research.journal import publish_immutable, read_versioned, repository_revision
from amre.research.models import EvidenceRecord, RunInfo
from amre.research.obstructions import ExcludedScope, ObstructionRecord, ObstructionStep
from amre.research.store import ResearchStore


def artifact_path(computation_id: str) -> str:
    return f"research/computations/{computation_id}.json"


class GRComputations:
    def __init__(self, root: Path):
        self.store = ResearchStore(root)
        self.root = self.store.root

    def run(
        self,
        tool: str,
        arguments: dict[str, object],
        *,
        timeout: float = 12,
        problem_id: str | None = None,
        assertion_id: str | None = None,
        task: ReasoningTask | None = None,
        interrupted: Callable[[], Failure | None] = lambda: None,
    ) -> ComputationArtifact:
        if tool not in INPUTS or not 0 < timeout <= 30:
            raise ResearchError("Unknown GR tool or timeout outside (0, 30] seconds")
        if task:
            problem_id = task.problem_id
        if assertion_id is not None and problem_id is None:
            raise ResearchError("Assertion binding requires a research problem")
        revision = None
        git_revision = repository_revision(self.root)
        if problem_id:
            graph = self.store.graph()
            assertion_id = assertion_id or problem_id
            if (
                graph.node(problem_id).kind != NodeKind.RESEARCH_PROBLEM
                or assertion_id not in {problem_id, *graph.research_inputs(problem_id)}
                or graph.node(assertion_id).kind not in ASSERTIONS
            ):
                raise ResearchError("Computation assertion is outside the contract scope")
            revision = graph.review(assertion_id).revision
        data = redact(json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False))
        if len(data.encode()) > 32768:
            raise ResearchError("GR tool input exceeds 32 KiB")
        request = json.loads(data)["arguments"]
        allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "SYSTEMDRIVE", "COMSPEC"}
        raw = run_process(
            [sys.executable, "-I", "-m", "amre.domains.gr.worker"],
            cwd=self.root,
            env={key: value for key, value in os.environ.items() if key.upper() in allowed},
            data=data.encode(),
            timeout=timeout,
            max_bytes=65536,
            interrupted=interrupted,
        )
        if raw.failure or raw.exit_code:
            status = {
                Failure.UNAVAILABLE: "UNAVAILABLE",
                Failure.PAUSED: "BLOCKED",
                Failure.CANCELLED: "BLOCKED",
                Failure.INTERRUPTED: "BLOCKED",
            }.get(raw.failure, "FAILED")
            result = ComputationResult(
                status=status,
                scope="Bounded GR worker execution",
                detail=str(raw.failure or "Worker exited without a validated result"),
            )
        else:
            try:
                result = ComputationResult.model_validate_json(raw.stdout)
            except ValueError:
                result = ComputationResult(
                    status="FAILED",
                    scope="GR worker response",
                    detail="Worker did not return a validated result",
                )
        key = f"Calc-{uuid4().hex}"
        conventions = request.get("conventions")
        try:
            conventions = GRConventions.model_validate(conventions).model_dump(mode="json")
        except ValueError:
            conventions = conventions if isinstance(conventions, dict) else {}
        artifact = ComputationArtifact(
            computation_id=key,
            tool=tool,
            tool_version=TOOL_VERSION,
            request=request,
            conventions=conventions,
            result=result,
            path=artifact_path(key),
            timestamp=datetime.now(UTC),
            repository_revision=git_revision,
            problem_id=problem_id,
            assertion_id=assertion_id,
            assertion_revision=revision,
            attempt_id=task.attempt_id if task else None,
            session_id=task.deep_session_id if task else None,
        )
        publish_immutable(
            self.root, {artifact.path: artifact.model_dump_json(indent=2).encode("utf-8")}
        )
        if not task:
            self.register(artifact)
        return artifact

    def show(self, computation_id: str) -> ComputationArtifact:
        if (
            not computation_id.startswith("Calc-")
            or len(computation_id) != 37
            or any(character not in "0123456789abcdef" for character in computation_id[5:])
        ):
            raise ResearchError("Invalid computation ID")
        path = artifact_path(computation_id)
        artifact = read_versioned(self.root, path, ComputationArtifact)
        if (artifact.computation_id, artifact.path) != (computation_id, path):
            raise ResearchError("Computation identity does not match its path")
        return artifact

    def register(self, artifact: ComputationArtifact) -> str | None:
        if artifact.problem_id is None:
            return None
        document, graph = self.store.snapshot()
        if graph.review(artifact.assertion_id).revision != artifact.assertion_revision:
            return None
        key = f"Result-{artifact.computation_id}"
        if key in {node.id for node in graph.document.nodes}:
            return key
        result = artifact.result
        if result.status == "COMPLETED":
            self.store.register_evidence(
                EvidenceRecord(
                    evidence_id=key,
                    source_type="COMPUTATION",
                    file_path=artifact.path,
                    git_revision=artifact.repository_revision,
                    run=RunInfo(
                        tool=artifact.tool,
                        version=artifact.tool_version,
                        run_id=artifact.computation_id,
                    ),
                    check_scope=f"{result.scope}. Relation to the entire assertion needs review.",
                    check_result="PASS",
                    coverage="PARTIAL",
                    direction="SUPPORT",
                    assertion_id=artifact.assertion_id,
                    assertion_revision=artifact.assertion_revision,
                    properties=("COMPUTATION_TESTED",),
                ),
                expected=document,
            )
        elif result.status == "FAILED":
            problem = document.contract(artifact.problem_id)
            obstacle = ObstructionRecord(
                obstacle_id=key,
                category="ASSUMPTION",
                description=result.detail or "A scoped consistency check failed",
                step=ObstructionStep(file=artifact.path, anchor="GR tool result", line=1),
                excluded_scope=ExcludedScope(
                    method=artifact.tool,
                    domain=result.scope,
                    restriction="Only this input and computation were checked.",
                ),
                attempted_methods=(artifact.tool,),
                potential_resolutions=(
                    "Inspect the input and selected conventions before retrying.",
                ),
                problem_id=artifact.problem_id,
                assertion_revision=graph.review(artifact.problem_id).revision,
                attempts=(artifact.attempt_id or artifact.computation_id,),
            )
            graph_doc = document.graph.model_copy(
                update={
                    "nodes": (
                        *document.graph.nodes,
                        GraphNode(
                            id=key,
                            kind="OBSTACLE",
                            label="GR tool validation failed",
                            obstruction=obstacle,
                        ),
                    ),
                }
            )
            self.store.update(
                problem.problem_id,
                {"obstacles": (*problem.obstacles, key)},
                graph=graph_doc,
                expected=document,
            )
        else:
            return None
        return key
