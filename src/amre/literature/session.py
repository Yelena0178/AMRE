"""Deterministic literature lookup within a scoped session tool call."""

from pathlib import Path

from amre.execution.session_tools import ToolResponse, ToolSpec
from amre.literature.catalog import LiteratureLibrary
from amre.literature.providers import SearchRequest
from amre.research.storage import validate


def literature_tool(root: Path) -> ToolSpec:
    library = LiteratureLibrary(root)

    def search(raw: dict[str, object], _remaining: float, _task, _interrupted) -> ToolResponse:
        request = validate(SearchRequest, raw, "literature tool query")
        result = library.search(request)
        return ToolResponse(status="COMPLETED", value=result.model_dump(mode="json"))

    return ToolSpec("literature.search", search, input_schema=SearchRequest.model_json_schema())
