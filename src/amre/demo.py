"""An isolated offline end-to-end research demonstration."""

import argparse
import json
from pathlib import Path

from amre.benchmarks.models import BenchmarkSuite
from amre.benchmarks.service import TrialPolicy, prepare_case
from amre.dashboard.export import snapshot
from amre.dashboard.models import PublicSelection
from amre.errors import ResearchError
from amre.execution.adapters import MockModelAdapter
from amre.execution.deep import DeepResearchRunner
from amre.execution.skill_models import ReasoningMode
from amre.literature.catalog import LiteratureLibrary
from amre.literature.discovery import CandidateQueue
from amre.literature.models import CandidateDraft, PaperImport
from amre.reports import generate_report
from amre.research.memory import ResearchMemory
from amre.research.storage import atomic_write, read_yaml, validate, yaml_text
from amre.scheduling.service import PortfolioScheduler


class DemoModel(MockModelAdapter):
    def __init__(self, case, calculation: dict):
        super().__init__()
        self.case = case
        self.calculation = calculation

    def run_deep_with_tools(self, task, *, call_tool, on_event, **options):
        for name, arguments in (
            ("literature.search", {"query": "boundary"}),
            ("gr.wave_energy", self.calculation),
        ):
            response = call_tool({"request_id": name, "name": name, "arguments": arguments})
            if response.status != "COMPLETED":
                raise ResearchError(f"Offline demo tool failed: {name}")
        return self.run_deep(task, on_event=on_event, **options)

    def run_deep(self, task, *, on_event, **options):
        for step in self.case.steps[:2]:
            on_event(
                {
                    "kind": "STRATEGY_SWITCH",
                    "strategy": step.strategy,
                    "summary": f"Consider {step.step_id} in the wave equation.",
                }
            )
            on_event(
                {
                    "kind": "PROOF_PROGRESS",
                    "strategy": step.strategy,
                    "summary": f"Candidate wave argument for {step.step_id}.",
                    "assumptions": ["H-1", "H-2"],
                    "artifact": {"name": f"{step.step_id}.tex", "text": step.derivation},
                }
            )
        return self.run(task, **options)


def run_demo(output: Path, examples: Path, seed: int) -> dict:
    output = output.resolve()
    if output.exists():
        raise ResearchError("Demo output exists. Choose an unused workspace")
    suite = validate(BenchmarkSuite, read_yaml(examples / "benchmarks.yaml"), "demo benchmark")
    case = next((item for item in suite.cases if item.case_id == "FlatWave"), None)
    if case is None:
        raise ResearchError("Demo needs the FlatWave research fixture")
    prepare_case(output, case, examples.resolve(), "No computation has been run.", TrialPolicy())
    problem = case.case_id
    cap = {"attempts": 12, "seconds": case.budget.timeout_seconds, "tool_calls": 4}
    atomic_write(
        output / "research" / "execution.yaml",
        yaml_text(
            {
                "workspace": cap,
                "problem": cap,
                "deep": {"tools": ["literature.search", "gr.wave_energy"], "session_tool_calls": 2},
            }
        ),
    )
    library = LiteratureLibrary(output)
    synthetic = validate(
        PaperImport, read_yaml(examples / "literature" / "synthetic-papers.yaml"), "demo sources"
    )
    papers = library.import_papers(synthetic.papers)
    paper = papers[0]
    library.link(problem, paper.paper_id, "Synthetic fixture for the citation workflow")
    memory = ResearchMemory(output)
    initial = memory.create_checkpoint(
        problem,
        {
            "active_route": "A",
            "next_research_action": "Analyze the local energy and boundary flux.",
        },
    )
    runner = DeepResearchRunner(output)
    job = runner.start(
        problem,
        DemoModel(case, read_yaml(examples / case.input)),
        mode=ReasoningMode.PROOF_CONSTRUCTION,
    )
    if job.state != "COMPLETED":
        raise ResearchError(f"Demo deep attempt failed: {job.detail}")
    graph = library.store.graph()
    obstruction = next(
        (
            item.obstruction
            for item in graph.document.nodes
            if item.obstruction and item.obstruction.category == "BOUNDARY"
        ),
        None,
    )
    if obstruction is None:
        raise ResearchError("Demo did not publish its boundary obstruction")
    library.link(obstruction.obstacle_id, paper.paper_id, "Synthetic source link for the obstacle")
    candidate_queue = CandidateQueue(output)
    candidate = candidate_queue.discover(
        CandidateDraft(
            kind="MISSING_LEMMA",
            title="Vanishing flux beyond the support",
            field="PDE",
            parent_problem=problem,
            obstacle_id=obstruction.obstacle_id,
            route_id="A",
            paper_ids=(paper.paper_id,),
            assumptions=("H-1", "H-2"),
            significance="Isolate the compact-support argument for the outer boundary.",
            acceptance_criteria=(
                {"id": "Flux", "description": "Justify the zero boundary trace."},
            ),
        ),
        r"On a ball containing the common compact support strictly in its interior, "
        r"show that \(\int_{\partial B_R}F\cdot n\,dS=0\) for \(F=-c^2u_t\nabla u\).",
    )
    child = candidate_queue.promote(candidate.candidate_id, "AMRE offline demo")
    decision, dispatched = PortfolioScheduler(output).run(
        problem, None, seed, adapter=DemoModel(case, read_yaml(examples / case.input))
    )[0]
    if dispatched.state != "COMPLETED":
        raise ResearchError(f"Demo scheduling failed: {dispatched.reason}")
    restored = ResearchMemory(output).restore(problem)
    if not restored.workspace_matches:
        raise ResearchError("Demo checkpoint could not restore the current graph")
    delivery = output / "deliverables"
    report = generate_report(output, problem, delivery)
    public = snapshot(
        output,
        PublicSelection(
            problems=(problem, child.problem_id),
            statement_ids=(problem, child.problem_id, "A", "B"),
            detail_ids=(problem, child.problem_id, obstruction.obstacle_id, paper.paper_id),
        ),
    )
    export = delivery / "research-state.json"
    atomic_write(export, public.model_dump_json(indent=2) + "\n")
    result = {
        "engine": "OFFLINE_MOCK",
        "problem_id": problem,
        "paper_id": paper.paper_id,
        "candidate_id": candidate.candidate_id,
        "derived_problem": child.problem_id,
        "obstacle_id": obstruction.obstacle_id,
        "session_id": job.deep_session_id,
        "initial_checkpoint": initial.checkpoint_id,
        "checkpoint": restored.checkpoint.checkpoint_id,
        "decision_id": decision.decision_id,
        "active_route": restored.checkpoint.active_route,
        "computation_evidence": [
            item.evidence_id
            for item in restored.graph.evidence_for(problem)
            if "COMPUTATION_TESTED" in item.properties
        ],
        "reports": [item.relative_to(output).as_posix() for item in report],
        "dashboard": export.relative_to(output).as_posix(),
    }
    atomic_write(delivery / "demo.json", json.dumps(result, indent=2) + "\n")
    return result


def add_demo_command(commands: argparse._SubParsersAction) -> None:
    demo = commands.add_parser("demo", help="run the complete offline research pipeline")
    demo.add_argument("--output", type=Path, required=True)
    demo.add_argument("--examples", type=Path)
    demo.add_argument("--seed", type=int, default=19)


def run_demo_command(args: argparse.Namespace) -> int:
    examples = args.examples or Path(__file__).resolve().parents[2] / "examples"
    result = run_demo(args.output, examples, args.seed)
    print(json.dumps(result, indent=2))
    return 0
