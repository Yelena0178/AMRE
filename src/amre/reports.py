"""Build local Markdown and LaTeX reports from scoped research records."""

import argparse
import json
import re
from pathlib import Path

from amre.execution.adapters import redact
from amre.execution.failure_memory import FailureMemory
from amre.execution.queue import TaskQueue
from amre.research.graph import ASSERTIONS, NodeKind
from amre.research.journal import repository_revision
from amre.research.memory import ResearchMemory
from amre.research.models import ReviewStatus
from amre.research.storage import atomic_write, workspace_path
from amre.research.store import read_statement_file

_TEX = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
    "→": r"\ensuremath{\to}",
    "≥": r"\ensuremath{\geq}",
    "≤": r"\ensuremath{\leq}",
    "×": r"\ensuremath{\times}",
    "µ": r"\ensuremath{\mu}",
}
_MATH = re.compile(r"(\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\])")
_SAFE_MATH = frozenset(
    "alpha beta gamma delta epsilon theta phi rho sigma Gamma Delta Omega partial nabla "
    "mathbb mathrm mathcal operatorname text frac tfrac dfrac int sum cdot times leq geq "
    "to in sin cos cot pi left right sqrt quad qquad vert big Big bmod".split()
)


def _tex_escape(value: str) -> str:
    return "".join(_TEX.get(character, character) for character in value)


def _tex_statement(value: str) -> str:
    parts = _MATH.split(value)
    for index, part in enumerate(parts):
        math = part[2:-2]
        if (
            index % 2
            and set(re.findall(r"\\([a-zA-Z]+)", math)).issubset(_SAFE_MATH)
            and not re.search(r"[%#&$]|\^\^|\\[^a-zA-Z,!:;]", math)
            and math.count("{") == math.count("}")
        ):
            continue
        parts[index] = _tex_escape(part)
    return "".join(parts)


def _text(value: str) -> str:
    return redact(value).replace("\r\n", "\n").replace("\r", "\n")


def _markdown_block(value: str) -> str:
    longest = max((len(group) for group in re.findall(r"\x60+", value)), default=0)
    fence = chr(96) * max(3, longest + 1)
    return f"{fence}latex\n{value.rstrip()}\n{fence}"


def _sections(memory: ResearchMemory, problem_id: str):
    inventory = memory.inventory()
    contract = inventory.document.contract(problem_id)
    graph = inventory.graph
    scope = {problem_id, *graph.research_inputs(problem_id)}
    nodes = [item for item in graph.document.nodes if item.id in scope]
    state = inventory.states.get(problem_id)
    latest = (
        memory.restore(problem_id, state.latest_checkpoint)
        if state and state.latest_checkpoint
        else None
    )
    cp = latest.checkpoint if latest else None
    sections = [
        (
            "Research contract",
            (
                f"Research status {cp.research_status if cp else contract.research_status}",
                f"Claim status {contract.claim_status}",
                f"Git revision {repository_revision(memory.root) or 'UNKNOWN'}",
                *(
                    f"Acceptance {item.id} — {item.description}"
                    for item in contract.acceptance_criteria
                ),
            ),
        ),
        (
            "Assumptions and definitions",
            (
                *(
                    f"{item.id} — {item.label} ({item.assumption_type})"
                    for item in nodes
                    if item.kind == NodeKind.ASSUMPTION
                ),
                *(f"{item.id} — {item.text}" for item in contract.definitions),
            ),
        ),
        (
            "Dependencies and proof routes",
            tuple(
                f"{edge.source} {edge.relation} {edge.target}"
                for edge in graph.document.edges
                if edge.source in scope
                and edge.target in scope
                and edge.relation
                in {"DEPENDS_ON", "ALTERNATIVE_ROUTE", "REQUIRES_ALL", "DERIVED_FROM"}
            ),
        ),
    ]
    established = []
    candidates = []
    evidence_lines = []
    for item in nodes:
        if item.kind not in ASSERTIONS | {NodeKind.PROOF_ROUTE}:
            continue
        review = graph.review(item.id)
        records = graph.evidence_for(item.id)
        support = [record for record in records if record.applies(review.revision, "SUPPORT")]
        refute = [record for record in records if record.applies(review.revision, "REFUTE")]
        if review.status == ReviewStatus.CURRENT and support and not refute:
            established.append(f"{item.id} — {', '.join(record.evidence_id for record in support)}")
        elif graph.claim_status(item.id) != "UNPROVED":
            candidates.append(f"{item.id} — {graph.claim_status(item.id)} · {review.status}")
        for record in records:
            flags = ", ".join(record.properties) or "No independent evidence property"
            evidence_lines.append(
                f"{record.evidence_id} → {item.id} · {record.source_type} · "
                f"{record.check_result} · {record.coverage} · {flags} · "
                f"revision {record.assertion_revision} · {record.file_path}\n"
                f"Scope {record.check_scope}"
            )
    attempts = FailureMemory(memory.root).attempts(problem_id, inventory=inventory)
    ledger = TaskQueue(memory.root).read()
    charges = [item for item in ledger.reservations if item.problem_id == problem_id]
    elapsed = sum(item.settled_ms or 0 for item in charges)

    def usage(field):
        values = [getattr(item.usage, field) for item in charges]
        return "UNKNOWN" if any(value is None for value in values) else str(sum(values))

    sections.extend(
        (
            ("Current independently supported results", tuple(established)),
            ("Candidate mathematical results", tuple(candidates)),
            ("Evidence and computation artifacts", tuple(evidence_lines)),
            (
                "Obstructions",
                tuple(
                    f"{item.id} — {item.obstruction.description} · {item.obstruction.category} · "
                    f"{item.obstruction.status} · {item.obstruction.step.file}"
                    if item.obstruction
                    else f"{item.id} — {item.label}"
                    for item in nodes
                    if item.kind == NodeKind.OBSTACLE
                ),
            ),
            (
                "Literature",
                tuple(
                    f"{item.id} — {item.citation}"
                    for item in nodes
                    if item.kind == NodeKind.LITERATURE_REFERENCE
                ),
            ),
            (
                "Execution",
                (
                    f"Settled time {elapsed} ms · attempts {len(charges)} · "
                    f"tokens {usage('tokens')} · cost (µUSD) {usage('cost_microusd')}",
                    *(
                        f"{item['attempt_id']} — {item.get('outcome') or item['journal_outcome']}"
                        for item in attempts
                    ),
                ),
            ),
            (
                "Checkpoint and next steps",
                (
                    f"Checkpoint {cp.checkpoint_id} · route {cp.active_route or 'None recorded'}",
                    "Checkpoint corresponds to current graph"
                    if latest.workspace_matches
                    else "The checkpoint predates current graph revisions. Recheck the archive.",
                    cp.next_research_action,
                    *cp.unresolved_proof_obligations,
                )
                if cp
                else ("No checkpoint recorded.",),
            ),
            (
                "Evidence limits",
                (
                    "Candidate results remain candidates pending evidence at the required scope.",
                    "Symbolic and numerical checks retain their recorded scopes and assumptions.",
                ),
            ),
        )
    )
    statement = read_statement_file(workspace_path(memory.root, contract.statement.path))
    return _text(statement), sections


def generate_report(root: Path, problem_id: str, output: Path) -> tuple[Path, Path]:
    statement, sections = _sections(ResearchMemory(root), problem_id)
    title = f"Research report · {problem_id}"
    md = [f"# {title}", "## Precise statement", _markdown_block(statement)]
    tex = [
        r"\documentclass{article}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{amsmath,amssymb}",
        r"\begin{document}",
        r"\section*{" + _tex_escape(title) + "}",
        r"\section*{Precise statement}",
        r"\begin{quote}",
        _tex_statement(statement),
        r"\end{quote}",
    ]
    for heading, entries in sections:
        values = tuple(_text(entry) for entry in entries) or ("None recorded.",)
        md.extend((f"## {heading}", *values))
        tex.append(r"\section*{" + _tex_escape(heading) + "}")
        for value in values:
            tex.append(r"\par ".join(map(_tex_escape, value.splitlines())) + r"\par")
    tex.append(r"\end{document}")
    markdown = output / f"{problem_id}.md"
    latex = output / f"{problem_id}.tex"
    atomic_write(markdown, "\n\n".join(md) + "\n")
    atomic_write(latex, "\n".join(tex) + "\n")
    return markdown, latex


def add_report_commands(commands: argparse._SubParsersAction) -> None:
    report = commands.add_parser("report", help="write a local Markdown and LaTeX research report")
    actions = report.add_subparsers(dest="action", required=True)
    generate = actions.add_parser("generate")
    generate.add_argument("--problem", required=True)
    generate.add_argument("--output", type=Path)


def run_report_command(args: argparse.Namespace, root: Path) -> int:
    markdown, latex = generate_report(root, args.problem, args.output or root / ".amre" / "reports")
    print(json.dumps({"markdown": str(markdown.resolve()), "latex": str(latex.resolve())}))
    return 0
