"""Authenticated, per-dispatch loopback MCP transport for allowlisted host tools."""

import json
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from amre.errors import ResearchError
from amre.execution.adapters import json_object
from amre.execution.session_tools import SessionToolCalls, ToolRequest

MCP_SERVER = "amre_session"
MCP_TOOL = "call"
MCP_TOKEN_ENV = "AMRE_SESSION_MCP_TOKEN"
_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")


@dataclass(frozen=True)
class LoopbackTools:
    url: str
    token: str
    schemas: dict[str, object]


def _rpc(message: dict, tools: SessionToolCalls):
    method = message.get("method")
    params = message.get("params", {})
    if message.get("jsonrpc") != "2.0" or not isinstance(params, dict):
        raise ResearchError("Invalid MCP message")
    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if requested in _PROTOCOLS else _PROTOCOLS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER, "version": "1.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        schema = ToolRequest.model_json_schema()
        schema["properties"]["name"] = {"type": "string", "enum": sorted(tools.tools)}
        return {
            "tools": [
                {
                    "name": MCP_TOOL,
                    "description": "Call an allowlisted AMRE tool and archive its result.",
                    "inputSchema": schema,
                }
            ]
        }
    if method == "tools/call" and params.get("name") == MCP_TOOL:
        result = tools(params.get("arguments"))
        return {
            "content": [{"type": "text", "text": result.model_dump_json()}],
            "isError": result.status != "COMPLETED",
        }
    raise ResearchError("Unknown or unavailable MCP method")


@contextmanager
def serve_tools(tools: SessionToolCalls) -> Iterator[LoopbackTools]:
    token = secrets.token_urlsafe(32)
    bearer = f"Bearer {token}".encode()

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(2)
            super().setup()

        def log_message(self, *_args) -> None:
            pass

        def _send(self, status: int, payload: dict | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:
            self._send(405)

        do_DELETE = do_GET

        def do_POST(self) -> None:
            address = f"127.0.0.1:{self.server.server_port}"
            if self.path != "/mcp" or self.headers.get("Host") != address:
                self._send(404)
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{address}":
                self._send(403)
                return
            auth = self.headers.get("Authorization", "").encode("latin-1")
            if not secrets.compare_digest(auth, bearer):
                self._send(401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 16384 or self.headers.get("Transfer-Encoding"):
                    self._send(413)
                    return
                message = json_object(self.rfile.read(length).decode("utf-8"))
            except (ResearchError, ValueError, OSError):
                self._send(400)
                return
            if "id" not in message:
                self._send(202)
                return
            identity = message["id"]
            if type(identity) not in (str, int):
                self._send(400)
                return
            try:
                response = {"result": _rpc(message, tools)}
            except (ResearchError, ValueError):
                response = {
                    "error": {"code": -32602, "message": "Unavailable or invalid tool call"}
                }
            try:
                self._send(200, {"jsonrpc": "2.0", "id": identity, **response})
            except OSError:
                return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = False
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        worker.start()
        try:
            yield LoopbackTools(
                url=f"http://127.0.0.1:{server.server_port}/mcp",
                token=token,
                schemas=tools.schemas(),
            )
        finally:
            server.shutdown()
            worker.join()
