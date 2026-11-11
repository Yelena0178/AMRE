"""Run one bounded GR computation in a dedicated worker process."""

import json
import shutil
import sys

from amre.domains.gr.consistency import consistency
from amre.domains.gr.pack import INPUTS
from amre.domains.models import ComputationResult
from amre.errors import ResearchError
from amre.research.storage import validate


def evaluate(tool: str, payload: object) -> ComputationResult:
    try:
        spec = validate(INPUTS[tool], payload, "GR tool input")
        if tool in {"gr.lean", "gr.xact"}:
            executable = "lean" if tool == "gr.lean" else "wolframscript"
            installed = shutil.which(executable) is not None
            return ComputationResult(
                status="UNAVAILABLE",
                scope="Optional external proof or tensor backend",
                detail=(
                    f"{executable} has no configured scoped adapter in this workspace."
                    if installed
                    else f"{executable} was not found on PATH."
                ),
            )
        if tool == "gr.consistency":
            return consistency(spec)
        if tool == "gr.numeric":
            from amre.domains.gr.numeric import numeric

            return numeric(spec)
        from amre.domains.gr.symbolic import curvature, wave_energy

        return curvature(spec) if tool == "gr.curvature" else wave_energy(spec)
    except ImportError:
        return ComputationResult(
            status="UNAVAILABLE",
            scope="GR tool prerequisites",
            detail="Install the AMRE gr extra (SymPy and mpmath).",
        )
    except (ResearchError, ValueError, ArithmeticError) as exc:
        return ComputationResult(
            status="FAILED",
            scope="GR input or calculation validation",
            detail=str(exc)[:2000] or type(exc).__name__,
        )


def main() -> int:
    try:
        content = sys.stdin.buffer.read(32769)
        if len(content) > 32768:
            return 2
        data = json.loads(content)
        tool, payload = data["tool"], data["arguments"]
        if tool not in INPUTS:
            return 2
    except (ValueError, TypeError, KeyError):
        return 2
    sys.stdout.buffer.write(evaluate(tool, payload).model_dump_json().encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
