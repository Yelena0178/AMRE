"""Explicitly allowed deterministic tools for an ongoing model dispatch."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Lock

from amre.errors import ResearchError
from amre.execution.adapters import json_object, redact
from amre.execution.deep_models import SemanticKind, SemanticUpdate
from amre.execution.models import DraftArtifact, Failure, ReasoningTask
from amre.research.models import Identifier, Record, Text, VersionedRecord
from amre.research.storage import validate


class ToolStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


class ToolRequest(VersionedRecord):
    request_id: Identifier
    name: Identifier
    arguments: dict[str, object]


class ToolResponse(Record):
    status: ToolStatus
    value: dict[str, object] | None = None
    detail: Text | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    execute: Callable[
        [dict[str, object], float, ReasoningTask, Callable[[], Failure | None]], ToolResponse
    ]
    publish: Callable[[dict[str, object], ReasoningTask], None] | None = None
    input_schema: dict[str, object] | None = None


class ToolRegistry:
    def __init__(self, *tools: ToolSpec):
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ResearchError("Duplicate session tool names")

    def select(self, names: tuple[str, ...]) -> dict[str, ToolSpec]:
        if missing := set(names) - self._tools.keys():
            raise ResearchError("Unknown session tools: " + ", ".join(sorted(missing)))
        return {name: self._tools[name] for name in names}


class SessionToolCalls:
    def __init__(
        self,
        task: ReasoningTask,
        registry: ToolRegistry,
        cap: int,
        deadline: float,
        interrupted: Callable[[], Failure | None],
        save: Callable[[SemanticUpdate], None],
    ):
        self.task = task
        self.tools = registry.select(task.tools)
        self.cap = cap
        self.deadline = deadline
        self.interrupted = interrupted
        self.save = save
        self.completed: dict[str, tuple[ToolRequest, ToolResponse]] = {}
        self.count = 0
        self._lock = Lock()

    def schemas(self) -> dict[str, object]:
        return {name: spec.input_schema or {} for name, spec in self.tools.items()}

    def __call__(self, raw: object) -> ToolResponse:
        request = validate(ToolRequest, raw, "session tool request")
        if len(request.model_dump_json().encode()) > 4096:
            return ToolResponse(status="BLOCKED", detail="Tool input exceeds 4 KiB")
        with self._lock:
            if previous := self.completed.get(request.request_id):
                if previous[0] != request:
                    raise ResearchError("A session tool request ID cannot be reused with new input")
                return previous[1]
            remaining = self.deadline - time.monotonic()
            if self.count >= self.cap or remaining <= 0 or self.interrupted():
                return ToolResponse(status="BLOCKED", detail="Session tool budget or deadline")
            tool = self.tools.get(request.name)
            if not tool:
                return ToolResponse(status="UNAVAILABLE", detail="Tool is outside the allowlist")
            self.count += 1
            try:
                response = tool.execute(request.arguments, remaining, self.task, self.interrupted)
            except (ResearchError, OSError, ValueError):
                response = ToolResponse(status="FAILED", detail="Invalid input or tool failure")
            response = ToolResponse.model_validate_json(redact(response.model_dump_json()))
            if len(response.model_dump_json().encode()) > 16384:
                response = ToolResponse(status="BLOCKED", detail="Tool output exceeds 16 KiB")
            content = redact(
                json.dumps(
                    {
                        "problem_id": self.task.problem_id,
                        "attempt_id": self.task.attempt_id,
                        "request": request.model_dump(mode="json"),
                        "response": response.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            self.save(
                SemanticUpdate(
                    kind=SemanticKind.TOOL_RESULT,
                    summary=f"{request.name}: {response.status}",
                    artifact=DraftArtifact(name=f"tool-{self.count}.md", text=content),
                )
            )
            self.completed[request.request_id] = (request, response)
            return response


def workspace_tools(root: Path) -> ToolRegistry:
    from amre.domains.gr.session import gr_tools
    from amre.literature.session import literature_tool

    return ToolRegistry(literature_tool(root), *gr_tools(root))


def publish_attempt_tools(root: Path, task: ReasoningTask, updates: tuple[SemanticUpdate, ...]):
    tools = workspace_tools(root).select(task.tools)
    for update in updates:
        receipt = json_object(update.artifact.text)
        request = validate(ToolRequest, receipt.get("request"), "archived tool request")
        response = validate(ToolResponse, receipt.get("response"), "archived tool response")
        spec = tools[request.name]
        if spec.publish and response.value:
            spec.publish(response.value, task)
