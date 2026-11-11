"""Expose GR computations through the bounded domain-neutral session tool bridge."""

import json
from pathlib import Path

from amre.domains.gr.pack import INPUTS
from amre.domains.gr.service import GRComputations
from amre.errors import ResearchError, WorkspaceChanged
from amre.execution.session_tools import ToolResponse, ToolSpec


def gr_tools(root: Path) -> tuple[ToolSpec, ...]:
    service = GRComputations(root)

    def publish(value, task):
        artifact = service.show(value["computation_id"])
        if (artifact.attempt_id, artifact.problem_id, artifact.session_id) != (
            task.attempt_id,
            task.problem_id,
            task.deep_session_id,
        ):
            raise ResearchError("GR artifact does not match the archived attempt")
        try:
            service.register(artifact)
        except WorkspaceChanged:
            # The immutable result remains available if the research moved to a new revision.
            return

    def scoped(name):
        def execute(arguments, remaining, task, interrupted):
            payload = dict(arguments)
            assertion_id = payload.pop("assertion_id", None)
            if assertion_id is not None and not isinstance(assertion_id, str):
                raise ResearchError("Expected a scoped assertion ID")
            artifact = service.run(
                name,
                payload,
                timeout=min(remaining, 12),
                assertion_id=assertion_id,
                task=task,
                interrupted=interrupted,
            )
            result = artifact.result
            value = {
                "computation_id": artifact.computation_id,
                "scope": result.scope,
                "path": artifact.path,
                "output": result.output,
                "obligations": [item.model_dump(mode="json") for item in result.obligations],
                "software": result.software,
            }
            if len(json.dumps(value).encode()) > 10000:
                value["output"] = {"archived": True}
            return ToolResponse(status=result.status, value=value, detail=result.detail)

        schema = INPUTS[name].model_json_schema()
        schema["properties"]["assertion_id"] = {"type": "string"}
        return ToolSpec(name, execute, publish, schema)

    return tuple(scoped(name) for name in INPUTS)
