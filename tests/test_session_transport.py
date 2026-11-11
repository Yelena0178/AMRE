import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from amre.execution import adapters
from amre.execution.adapters import AdapterOutput, CodexCLIAdapter
from amre.execution.deep import DeepResearchRunner
from amre.execution.tool_transport import MCP_SERVER, MCP_TOKEN_ENV, MCP_TOOL
from amre.literature.catalog import LiteratureLibrary
from amre.literature.models import PaperImport
from amre.research.storage import read_yaml, yaml_text

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_live_adapter_routes_only_authenticated_local_tools_without_paid_launch(
    example_store,
    monkeypatch,
):
    for name in ("AMRE_MODEL_CHILD", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TOOL_CALL_ID"):
        monkeypatch.delenv(name, raising=False)
    root = example_store.root
    library = LiteratureLibrary(root)
    library.import_papers(
        PaperImport.model_validate(
            read_yaml(EXAMPLES / "literature" / "synthetic-papers.yaml")
        ).papers
    )
    cap = {"attempts": 4, "seconds": 90, "tool_calls": 1}
    (root / "research" / "execution.yaml").write_text(
        yaml_text(
            {
                "workspace": cap,
                "problem": cap,
                "deep": {"tools": ["literature.search"], "session_tool_calls": 1},
            }
        ),
        encoding="utf-8",
    )
    launches = []

    def fake(argv, *, env, data, stdout_line, **_options):
        if "--help" in argv:
            return AdapterOutput(
                stdout=b"--sandbox --json --ignore-user-config --ignore-rules "
                b"--skip-git-repo-check --ephemeral --disable --strict-config",
                exit_code=0,
            )
        if argv[1:3] == ["features", "list"]:
            return AdapterOutput(
                stdout=b"shell_tool\nhooks\nmulti_agent\napps\nplugins\nskill_mcp_dependency_install",
                exit_code=0,
            )
        if argv[-3:] == ["mcp", "list", "--json"]:
            verified = "mcp_servers.outside.enabled=false" in argv
            records = [
                {
                    "name": "outside",
                    "enabled": not verified,
                    "transport": {"type": "stdio"},
                }
            ]
            if verified:
                assert 'mcp_servers.outside.command="amre-disabled-mcp"' in argv
                records.append(
                    {
                        "name": MCP_SERVER,
                        "enabled": True,
                        "transport": {"type": "streamable_http"},
                    }
                )
            return AdapterOutput(stdout=json.dumps(records).encode(), exit_code=0)
        prefix = f"mcp_servers.{MCP_SERVER}.url="
        url = json.loads(
            next(item.removeprefix(prefix) for item in argv if item.startswith(prefix))
        )
        token = env[MCP_TOKEN_ENV]
        assert token not in " ".join(argv) and token.encode() not in data
        assert "mcp_servers.outside.enabled=false" in argv
        assert b"literature.search" in data and "--ephemeral" in argv
        launches.append((url, token))

        def rpc(method, params, *, auth=True, origin=None):
            content = json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            ).encode()
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["Authorization"] = f"Bearer {token}"
            if origin:
                headers["Origin"] = origin
            with urlopen(Request(url, data=content, headers=headers), timeout=10) as response:
                return json.load(response)["result"]

        with pytest.raises(HTTPError) as unauthorized:
            rpc("ping", {}, auth=False)
        assert unauthorized.value.code == 401
        with pytest.raises(HTTPError) as origin_denied:
            rpc("ping", {}, origin="https://outside.example")
        assert origin_denied.value.code == 403
        assert rpc("initialize", {"protocolVersion": "2025-11-25"})["capabilities"] == {"tools": {}}
        schema = rpc("tools/list", {})["tools"][0]["inputSchema"]
        assert schema["properties"]["name"]["enum"] == ["literature.search"]
        request = {
            "name": MCP_TOOL,
            "arguments": {
                "request_id": "ScopedLookup",
                "name": "literature.search",
                "arguments": {"query": "boundary"},
            },
        }
        reply = rpc("tools/call", request)
        assert not reply["isError"]
        assert json.loads(reply["content"][0]["text"])["value"]["papers"][0]["fixture"]
        assert rpc("tools/call", request) == reply
        request["arguments"]["request_id"] = "NewLookup"
        assert rpc("tools/call", request)["isError"]
        events = [
            {
                "type": "item.completed",
                "item": {"type": "mcp_tool_call", "server": MCP_SERVER, "tool": MCP_TOOL},
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "outcome": "NO_PROGRESS",
                            "summary": "Mock bridge fixture",
                            "next_action": "Review",
                        }
                    ),
                },
            },
            {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}},
        ]
        lines = [json.dumps(event).encode() for event in events]
        for line in lines:
            stdout_line(line)
        return AdapterOutput(stdout=b"\n".join(lines), stderr=token.encode(), exit_code=0)

    monkeypatch.setattr(adapters, "run_process", fake)
    runner = DeepResearchRunner(root)
    job = runner.start("P-T", CodexCLIAdapter(sys.executable, live=True))
    assert job.state == "COMPLETED" and len(launches) == 1
    reservation = runner.runner.queue.read().reservations[0]
    assert reservation.usage.tool_calls == 1 and reservation.usage.tokens == 5
    url, token = launches[0]
    with pytest.raises(URLError):
        urlopen(url, timeout=1)
    receipt = root / "research" / "attempts" / reservation.attempt_id / "receipt.yaml"
    assert token not in receipt.read_text(encoding="utf-8")
