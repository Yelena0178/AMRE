"""Compare real AMRE execution paths under the same deterministic model fixture."""

import platform
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from amre.benchmarks.models import (
    Ablation,
    BenchmarkCase,
    BenchmarkResult,
    BenchmarkSuite,
    CaseResult,
    WorkflowMetrics,
)
from amre.domains.gr.service import GRComputations
from amre.errors import ResearchError
from amre.execution.adapters import AdapterOutput, MockModelAdapter
from amre.execution.deep import DeepResearchRunner
from amre.execution.failure_memory import FailureMemory
from amre.execution.models import DraftArtifact, ReasoningResult
from amre.execution.orchestrator import ResearchOrchestrator
from amre.execution.skill_models import ReasoningMode
from amre.research.graph import GraphDocument, GraphEdge, GraphNode
from amre.research.memory import ResearchMemory
from amre.research.models import EvidenceRecord, RunInfo, StatementRef, unique_ids
from amre.research.storage import atomic_write, read_yaml, validate, workspace_path, yaml_text
from amre.research.store import ResearchStore, read_statement_file
from amre.scheduling.service import PortfolioScheduler


@dataclass(frozen=True)
class TrialPolicy:
    context_bytes: int = 16000
    prompt_template: str = "standard-v1"
    deep_target_percent: int = 85
    strategy: dict | None = None
    problem_values: dict | None = None


def _write(root: Path, relative: str, data: dict):
    atomic_write(workspace_path(root, relative), yaml_text(data))


def _marker(step_id: str) -> str:
    return f"Original fixture transition {step_id}."


class CaseAdapter(MockModelAdapter):
    def __init__(self, case: BenchmarkCase, *, obstacle_driven=True):
        super().__init__(self._fixture_result)
        self.case = case
        self.position = 0
        self.context_losses = 0
        self.trace: list[str] = []
        self.obstacle_driven = obstacle_driven
        self.repeated_failures = 0

    def _artifact(self, step):
        return DraftArtifact(
            name=f"{step.step_id}.tex", text=f"% {_marker(step.step_id)}\n" + step.derivation
        )

    def _fixture_result(self, task):
        step = self.case.steps[self.position - 1]
        return ReasoningResult(
            outcome="PROOF_CANDIDATE"
            if self.position == len(self.case.steps)
            else "PARTIAL_PROGRESS",
            summary=f"Offline fixture candidate for {step.step_id}.",
            next_action="Independently review the exact inference and assumptions.",
            artifacts=(self._artifact(step),),
            assumptions_used=tuple(f"H-{index + 1}" for index in range(len(self.case.assumptions))),
        )

    def run(self, task, **options):
        if self.position:
            previous = self.case.steps[self.position - 1]
            if _marker(previous.step_id) not in task.context:
                self.context_losses += 1
        self.position += 1
        self.trace.append(self.case.steps[self.position - 1].step_id)
        return super().run(task, **options)

    def run_deep(self, task, *, on_event, interrupted, **options):
        hypotheses = tuple(f"H-{index + 1}" for index in range(len(self.case.assumptions)))
        for step in self.case.steps:
            if failure := interrupted():
                return AdapterOutput(failure=failure)
            if step.strategy == ReasoningMode.OBSTACLE_DIAGNOSIS:
                obstruction = {
                    "kind": "KEY_OBSTACLE",
                    "summary": "Local flux requires a justified boundary condition.",
                    "assumptions": hypotheses,
                    "artifact": {
                        "name": "flux-obligation.tex",
                        "text": r"Justify \(\int_{\partial\Omega}F\cdot n=0\).",
                    },
                }
                self.trace.append("Record boundary obstruction")
                on_event(obstruction)
                if not self.obstacle_driven:
                    self.trace.append("Repeat the direct method at the same boundary")
                    self.repeated_failures += 1
                    on_event(obstruction)
            on_event(
                {
                    "kind": "STRATEGY_SWITCH",
                    "strategy": step.strategy,
                    "summary": f"Consider the fixture inference for {step.step_id}.",
                }
            )
            on_event(
                {
                    "kind": "PROOF_PROGRESS",
                    "summary": f"Candidate transition {step.step_id}.",
                    "strategy": step.strategy,
                    "assumptions": hypotheses,
                    "artifact": self._artifact(step),
                }
            )
            self.position += 1
            self.trace.append(step.step_id)
        return super().run(task, interrupted=interrupted, **options)


class _NoHistory(ResearchOrchestrator):
    def _context(self, restored, mode, obstacle_id):
        checkpoint = restored.checkpoint.model_copy(
            update={
                "context_sources": tuple(
                    item
                    for item in restored.checkpoint.context_sources
                    if item.path == "research/benchmark-tool.txt"
                ),
                "candidate_results": (),
                "established_results": (),
            }
        )
        return super()._context(
            replace(restored, checkpoint=checkpoint, failed_attempts=()),
            mode,
            obstacle_id,
        )


def prepare_case(root, case, fixture_root, tool_result, policy):
    root.mkdir(parents=True, exist_ok=False)
    store = ResearchStore(root)
    nodes = []
    statements = []
    for index, label in enumerate(case.assumptions, start=1):
        nodes.append(
            GraphNode(
                id=f"H-{index}",
                kind="ASSUMPTION",
                label=label,
                assumption_type="MATHEMATICAL",
            )
        )
    lemmas = case.steps[:2] if case.kind != "B1" else ()
    for step in lemmas:
        statements.append(step.derivation)
        nodes.append(
            GraphNode(
                id=step.step_id,
                kind="LEMMA",
                label=step.step_id,
                statement=StatementRef.from_text(step.derivation),
            )
        )
    edges = []
    routes = ("A", "B") if case.kind == "B3" else ()
    if routes:
        for route, premise in zip(routes, lemmas, strict=True):
            statement = (
                r"Candidate route A: integrate the local identity, leaving a boundary obligation."
                if route == "A"
                else r"Candidate route B: use compact support to eliminate the boundary flux first."
            )
            reference = StatementRef.from_text(statement)
            statements.append(statement)
            nodes.extend(
                (
                    GraphNode(
                        id=route,
                        kind="PROOF_ROUTE",
                        label=f"Fixture route {route}",
                        statement=reference,
                    ),
                    GraphNode(
                        id=f"Draft-{route}",
                        kind="EVIDENCE",
                        label="Candidate reduction",
                        evidence=EvidenceRecord(
                            evidence_id=f"Draft-{route}",
                            assertion_id=route,
                            assertion_revision=1,
                            source_type="MODEL",
                            file_path=reference.path,
                            run=RunInfo(tool="benchmark-fixture", run_id=f"Draft-{route}"),
                            check_scope="Seed route, pending independent review",
                            check_result="INCONCLUSIVE",
                            coverage="PARTIAL",
                            direction="SUPPORT",
                        ),
                    ),
                )
            )
            edges.append(GraphEdge(source=route, target=f"Draft-{route}", relation="SUPPORTED_BY"))
            edges.append(GraphEdge(source=route, target=premise.step_id, relation="REQUIRES_ALL"))
    statement = read_statement_file(workspace_path(fixture_root, case.statement))
    store.create(
        {
            "problem_id": case.case_id,
            "assumptions": [f"H-{index + 1}" for index in range(len(case.assumptions))],
            "dependencies": [step.step_id for step in lemmas] if not routes else [],
            "research_routes": routes,
            "acceptance_criteria": [{"id": "Review", "description": "Review the scoped result."}],
            "budget": case.budget,
            "provenance": {"author": "AMRE benchmark", "source": case.title},
        },
        statement,
        graph=GraphDocument(nodes=tuple(nodes), edges=tuple(edges)),
        statements=statements,
    )
    limit = {"attempts": max(3, len(case.steps)), "seconds": case.budget.timeout_seconds}
    _write(
        root,
        "research/execution.yaml",
        {
            "workspace": limit,
            "problem": limit,
            "attempt": limit,
            "deep": {
                "session_seconds": case.budget.timeout_seconds,
                "budget_target_percent": policy.deep_target_percent,
            },
            "max_context_bytes": policy.context_bytes,
            "prompt_template": policy.prompt_template,
        },
    )
    if routes:
        _write(
            root,
            "research/portfolio.yaml",
            {
                "strategy": policy.strategy or {"stagnation_window": 2, "cooldown_attempts": 2},
                "problem_values": policy.problem_values or {},
                "branches": [
                    {"problem_id": case.case_id, "route_id": "B", "mode": "COUNTEREXAMPLE_SEARCH"}
                ],
            },
        )
    source = "research/benchmark-tool.txt"
    atomic_write(workspace_path(root, source), tool_result)
    ResearchMemory(root).create_checkpoint(
        case.case_id,
        {
            "active_route": routes[0] if routes else None,
            "next_research_action": "Examine the exact contract.",
            "context_sources": [{"path": source, "kind": "DERIVATION"}],
        },
    )


def _collect(root, output, case, adapter, variant, tool_passed):
    memory = ResearchMemory(root)
    restored = memory.restore(case.case_id)
    inventory = memory.inventory()
    ledger = ResearchOrchestrator(root).queue.read()
    attempts = ledger.reservations
    files = [
        path
        for row in FailureMemory(root).attempts(case.case_id, inventory=inventory)
        for path in row.get("proof_artifacts", [])
    ]
    for session in {item.deep_session_id for item in attempts if item.deep_session_id}:
        files.extend(DeepResearchRunner(root).inspect(session).artifacts)
    originals = [workspace_path(root, item).read_text(encoding="utf-8") for item in files]
    found = sum(any(_marker(step.step_id) in text for text in originals) for step in case.steps)

    def aggregate(field):
        values = [getattr(item.usage, field) for item in attempts]
        return "UNKNOWN" if any(item is None for item in values) else sum(values)

    return WorkflowMetrics(
        variant=variant,
        workspace=root.relative_to(output).as_posix(),
        attempts=len(attempts),
        mathematical_candidates=found,
        context_loss_events=adapter.context_losses,
        repeated_failures=adapter.repeated_failures,
        completed=found == len(case.steps)
        and tool_passed
        and all(item.final_state == "COMPLETED" for item in attempts),
        state_restored=restored.workspace_matches and restored.checkpoint.last_attempt is not None,
        elapsed_ms=sum(item.settled_ms or 0 for item in attempts),
        reserved_peak_ms=max((item.reserved_ms for item in attempts), default=0),
        budget_ms=case.budget.timeout_seconds * 1000,
        tokens=aggregate("tokens"),
        cost_microusd=aggregate("cost_microusd"),
        computation_checks=int(tool_passed),
    )


def _trial(root, output, case, fixture_root, variant, tool, tool_passed, policy):
    prepare_case(root, case, fixture_root, tool, policy)
    adapter = CaseAdapter(case, obstacle_driven=variant != "deep-linear")
    if variant.startswith("deep"):
        DeepResearchRunner(root).start(case.case_id, adapter)
    else:
        runner = (_NoHistory if variant == "short-without-memory" else ResearchOrchestrator)(root)
        for step in case.steps:
            job = runner.run(case.case_id, adapter)
            if job.state != "COMPLETED":
                break
            cp = runner.memory.restore(case.case_id).checkpoint
            if variant != "short":
                continue
            source = f"research/attempts/{job.attempt_id}/draft-{step.step_id}.tex"
            runner.memory.create_checkpoint(
                case.case_id,
                {
                    "active_route": cp.active_route,
                    "next_research_action": cp.next_research_action,
                    "context_sources": [
                        {"path": source, "kind": "DERIVATION"},
                        *cp.context_sources,
                    ],
                    "unresolved_proof_obligations": cp.unresolved_proof_obligations,
                    "candidate_results": cp.candidate_results,
                    "usage": cp.budget_snapshot.model_dump(
                        include={"elapsed_seconds", "tokens_used"}
                    ),
                },
            )
    return _collect(root, output, case, adapter, variant, tool_passed), tuple(adapter.trace)


def _portfolio_trial(root, case, fixture_root, tool, seed, policy, adaptive):
    prepare_case(root, case, fixture_root, tool, policy)
    runner = ResearchOrchestrator(root)
    for _ in range(2):
        runner.run(case.case_id, MockModelAdapter(), mode=ReasoningMode.PROOF_CONSTRUCTION)
    if adaptive:
        PortfolioScheduler(root).run(case.case_id, None, seed)
    else:
        runner.run(case.case_id, MockModelAdapter(), mode=ReasoningMode.PROOF_CONSTRUCTION)
    rows = FailureMemory(root).attempts(case.case_id)
    trace = tuple(f"{row['route_id']} / {row['reasoning_mode']}" for row in rows)
    seen = set()
    repeats = 0
    for row in rows:
        keys = set(row["encountered_obstacles"])
        repeats += len(seen & keys)
        seen.update(keys)
    return trace, repeats


def run_benchmark(
    suite_file: Path,
    output: Path,
    seed: int,
    *,
    policy: TrialPolicy = TrialPolicy(),
) -> BenchmarkResult:
    suite = validate(BenchmarkSuite, read_yaml(suite_file), "benchmark suite")
    try:
        unique_ids(tuple(case.case_id for case in suite.cases), "benchmark cases")
        for case in suite.cases:
            unique_ids(tuple(step.step_id for step in case.steps), "benchmark steps")
    except ValueError as exc:
        raise ResearchError(str(exc)) from None
    if output.exists():
        raise ResearchError("Benchmark output exists. Choose a new experiment directory")
    output = output.resolve()
    output.mkdir(parents=True)
    fixture_root = suite_file.resolve().parent
    sources = sorted(
        {path for case in suite.cases for path in (case.statement, case.reference, case.input)}
    )
    contents = [suite.model_dump_json().encode()]
    for source in sources:
        with workspace_path(fixture_root, source).open("rb") as file:
            data = file.read(262145)
        if len(data) > 262144:
            raise ResearchError("A benchmark source exceeds 256 KiB")
        contents.append(data)
    fixture_digest = sha256(b"\0".join(contents)).hexdigest()
    cases = []
    for case in suite.cases:
        read_statement_file(workspace_path(fixture_root, case.reference))
        case_root = output / case.case_id
        case_root.mkdir()
        service = GRComputations(case_root)
        verification = service.run(case.tool, read_yaml(workspace_path(fixture_root, case.input)))
        checks = {
            key: verification.result.status == "COMPLETED"
            and verification.result.output.get(key) == value
            for key, value in case.expected.items()
        }
        tool_passed = all(checks.values())
        tool = verification.result.model_dump_json(indent=2)
        variants = ("deep", "short", "short-without-memory")
        if case.kind == "B3":
            variants = (*variants, "deep-linear")
        metrics = []
        traces = {}
        for variant in variants:
            metric, trace = _trial(
                case_root / variant,
                output,
                case,
                fixture_root,
                variant,
                tool,
                tool_passed,
                policy,
            )
            traces[variant] = trace
            metrics.append(metric)
        ablations = [
            Ablation(
                dimension="Persistent memory retrieval",
                variants=("short", "short-without-memory"),
                trace=(traces["short"], traces["short-without-memory"]),
                repeated_failures=(0, 0),
            )
        ]
        if case.kind == "B3":
            ablations.append(
                Ablation(
                    dimension="Linear versus obstruction-driven",
                    variants=("deep-linear", "deep"),
                    trace=(traces["deep-linear"], traces["deep"]),
                    repeated_failures=tuple(
                        next(item.repeated_failures for item in metrics if item.variant == name)
                        for name in ("deep-linear", "deep")
                    ),
                )
            )
            fixed, fixed_repeat = _portfolio_trial(
                case_root / "fixed",
                case,
                fixture_root,
                tool,
                seed,
                policy,
                False,
            )
            adaptive, adaptive_repeat = _portfolio_trial(
                case_root / "adaptive",
                case,
                fixture_root,
                tool,
                seed,
                policy,
                True,
            )
            ablations.append(
                Ablation(
                    dimension="Fixed versus adaptive routing",
                    variants=("fixed", "adaptive"),
                    trace=(fixed, adaptive),
                    repeated_failures=(fixed_repeat, adaptive_repeat),
                )
            )
        cases.append(
            CaseResult(
                case_id=case.case_id,
                kind=case.kind,
                reference=case.reference,
                checked=checks,
                computation=(case_root / verification.path).relative_to(output).as_posix(),
                workflows=tuple(metrics),
                ablations=tuple(ablations),
            )
        )
    result = BenchmarkResult(
        suite=suite.name,
        seed=seed,
        cases=tuple(cases),
        fixture_digest=fixture_digest,
        environment={"python": platform.python_version(), "platform": platform.system()},
    )
    atomic_write(output / "benchmark.json", result.model_dump_json(indent=2) + "\n")
    return result
