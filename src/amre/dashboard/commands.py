"""Export and build the public research dashboard."""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from amre.dashboard.export import load_public_selection, snapshot
from amre.errors import ResearchError
from amre.execution.adapters import run_process
from amre.research.storage import atomic_write


def add_dashboard_commands(commands: argparse._SubParsersAction) -> None:
    dashboard = commands.add_parser("dashboard", help="export or build the read-only dashboard")
    actions = dashboard.add_subparsers(dest="action", required=True)
    export = actions.add_parser("export")
    export.add_argument("--policy", type=Path)
    export.add_argument("--output", type=Path)
    build = actions.add_parser("build")
    build.add_argument("--policy", type=Path)
    build.add_argument("--output", type=Path)
    build.add_argument("--frontend", type=Path)


def build_dashboard(data: str, frontend: Path, output: Path) -> None:
    frontend, output = frontend.resolve(), output.resolve()
    if output.exists():
        raise ResearchError("Dashboard output exists. Choose an unused build directory")
    node = shutil.which("node")
    vite = frontend / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not vite.is_file():
        raise ResearchError("Dashboard requires Node.js and npm ci in the frontend directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="amre-dashboard-", dir=output.parent) as directory:
        staging = Path(directory)
        snapshot_path = staging / "research-state.json"
        atomic_write(snapshot_path, data)
        site = staging / "site"
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "SYSTEMDRIVE",
            "LOCALAPPDATA",
        }
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env["AMRE_DASHBOARD_SNAPSHOT"] = str(snapshot_path)
        result = run_process(
            [str(Path(node).resolve()), str(vite), "build", "--outDir", str(site)],
            cwd=frontend,
            env=env,
            data=b"",
            timeout=120,
            max_bytes=131072,
            interrupted=lambda: None,
        )
        if result.failure or result.exit_code or not (site / "index.html").is_file():
            raise ResearchError(
                "Dashboard build failed. Run npm run build in the frontend directory"
            )
        if (site / "research-state.json").read_text(encoding="utf-8") != data:
            raise ResearchError("Dashboard build did not embed the selected snapshot")
        if output.exists():
            raise ResearchError("Dashboard output appeared during the build")
        site.rename(output)


def run_dashboard_command(args: argparse.Namespace, root: Path) -> int:
    public = snapshot(root, load_public_selection(root, args.policy))
    data = public.model_dump_json(indent=2) + "\n"
    if args.action == "export":
        output = args.output or root / ".amre" / "dashboard" / "research-state.json"
        atomic_write(output, data)
    else:
        output = args.output or root / ".amre" / "dashboard" / "site"
        frontend = args.frontend or Path(__file__).resolve().parents[3] / "dashboard"
        build_dashboard(data, frontend, output)
    print(json.dumps({"output": str(output.resolve()), "problems": len(public.problems)}))
    return 0
