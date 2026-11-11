"""Versioned skill registry, scoped inputs, and deterministic contract checks."""

from dataclasses import dataclass

from amre.errors import ResearchError
from amre.execution.models import DraftArtifact, ReasoningOutcome, ReasoningResult, ReasoningTask
from amre.execution.skill_models import (
    ClaimDraft,
    CounterexampleSearch,
    LemmaDecomposition,
    MathematicalSynthesis,
    MethodInnovation,
    ObstacleDiagnosis,
    ProofConstruction,
    ProofRouteExploration,
    ReasoningMode,
)
from amre.research.graph import ASSERTIONS, NodeKind
from amre.research.memory import RestoredState
from amre.research.models import Record, statement_hash

_M = ReasoningMode
_O = ReasoningOutcome


@dataclass(frozen=True)
class ResearchSkill:
    name: ReasoningMode
    purpose: str
    applicability: str
    required_inputs: tuple[str, ...]
    prompt_template: str
    result_schema: type[Record]
    evidence_requirements: str
    failure_conditions: str
    outcomes: frozenset[ReasoningOutcome]
    version: str = "1.0"

    @property
    def instructions(self) -> str:
        return "\n".join(
            (
                self.purpose,
                self.prompt_template,
                self.evidence_requirements,
                f"Failure conditions: {self.failure_conditions}",
                "Report assumptions, logical gaps, possible counterexamples, and unverified steps.",
                "Permitted outcomes: " + ", ".join(sorted(self.outcomes)),
            )
        )


def _skill(name, purpose, applicability, inputs, prompt, schema, evidence, failure, *outcomes):
    return ResearchSkill(
        name,
        purpose,
        applicability,
        inputs,
        prompt,
        schema,
        evidence,
        failure,
        frozenset((_O.NO_PROGRESS, _O.BLOCKED, *outcomes)),
    )


SKILLS = {
    skill.name: skill
    for skill in (
        _skill(
            _M.PROOF_CONSTRUCTION,
            "Construct an argument for the exact assertion.",
            "An assertion with a contract and explicit success criteria.",
            ("criteria", "dependencies"),
            "Order the inference steps. Separate justified transitions from open obligations.",
            ProofConstruction,
            "Save the full argument. Link obligations only to scoped evidence.",
            "Missing derivation, unsupported assumptions, or unjustified evidence references.",
            _O.PROOF_CANDIDATE,
            _O.PARTIAL_PROGRESS,
        ),
        _skill(
            _M.LEMMA_DECOMPOSITION,
            "Break the assertion into smaller mathematical claims.",
            "An assertion whose dependencies can be extended with candidate lemmas.",
            ("dependencies", "assumptions"),
            "State separate lemma propositions and explain how their conclusions compose.",
            LemmaDecomposition,
            "Give a distinct source artifact for each proposed lemma.",
            "Circular prerequisites or no independently stated lemma.",
            _O.PARTIAL_PROGRESS,
        ),
        _skill(
            _M.COUNTEREXAMPLE_SEARCH,
            "Try to refute a quantified assertion.",
            "An assertion with a specified domain and hypotheses.",
            ("assumptions", "criteria"),
            "State the domain and witness. Check every hypothesis before claiming a violation.",
            CounterexampleSearch,
            "Keep computations and witness derivations as candidate evidence.",
            "A witness outside the domain or a candidate without a violated conclusion.",
            _O.COUNTEREXAMPLE_CANDIDATE,
            _O.PARTIAL_PROGRESS,
        ),
        _skill(
            _M.OBSTACLE_DIAGNOSIS,
            "Locate the mathematical cause of a recorded failure.",
            "A recorded open obstruction in the problem's scoped graph.",
            ("obstruction",),
            "Name the failing inference and explain the mechanism and remaining untested scope.",
            ObstacleDiagnosis,
            "Refer to the exact obstacle ID and its archived proof step.",
            "A different obstacle ID or an inference outside the known scope.",
            _O.PARTIAL_PROGRESS,
        ),
        _skill(
            _M.METHOD_INNOVATION,
            "Propose a change of method at a concrete obstruction.",
            "An open obstruction with scope and previous attempts.",
            ("obstruction", "dependencies"),
            "Name the old and new methods, the new quantity, and checks for it.",
            MethodInnovation,
            "Give a derivation or calculation motivating the replacement.",
            "A replacement identical to the failed method or missing validation checks.",
            _O.PARTIAL_PROGRESS,
        ),
        _skill(
            _M.PROOF_ROUTE_EXPLORATION,
            "Propose a conditional reduction with explicit prerequisites.",
            "An assertion with an existing dependency to use in an alternative route.",
            ("dependencies", "routes", "assumptions"),
            "Propose an alternate reduction. Identify existing lemmas and required assumptions.",
            ProofRouteExploration,
            "Save the reduction and cite the exact existing prerequisite IDs.",
            "Unknown, empty, or circular prerequisites; missing reduction artifact.",
            _O.VALID_REDUCTION_CANDIDATE,
        ),
        _skill(
            _M.MATHEMATICAL_SYNTHESIS,
            "Combine existing evidence into a scoped conclusion.",
            "A problem with at least one archived evidence record.",
            ("evidence", "dependencies"),
            "Separate supported conclusions, conditional conclusions, and open dependencies.",
            MathematicalSynthesis,
            "Cite exact evidence IDs and retain missing conditions.",
            "No citable evidence, unknown source IDs, or unsupported claims of verification.",
            _O.PARTIAL_PROGRESS,
        ),
    )
}


def prepare_skill(
    state: RestoredState, mode: ReasoningMode, obstacle_id: str | None
) -> tuple[ResearchSkill, dict[str, object], str | None]:
    skill = SKILLS[mode]
    checkpoint = state.checkpoint
    problem = checkpoint.problem_id
    graph = state.graph
    scoped = {node.id: node for node in graph.document.nodes}
    obstacles = [
        node.obstruction
        for node in scoped.values()
        if node.obstacle_open and node.obstruction is not None
    ]
    if obstacle_id is not None:
        obstacles = [item for item in obstacles if item.obstacle_id == obstacle_id]
        if not obstacles:
            raise ResearchError(f"No open scoped obstruction: {obstacle_id}")
    obstacle = next((item for item in obstacles if checkpoint.active_route in item.routes), None)
    obstacle = obstacle or (obstacles[-1] if obstacles else None)
    catalog = {
        "criteria": [
            item.model_dump(mode="json") for item in checkpoint.contract.acceptance_criteria
        ],
        "dependencies": [key for key in graph.upstream(problem) if scoped[key].kind in ASSERTIONS],
        "assumptions": list(graph.assumptions_for(problem)),
        "routes": [node.id for node in scoped.values() if node.kind == NodeKind.PROOF_ROUTE],
        "evidence": [node.id for node in scoped.values() if node.evidence is not None],
        "obstruction": obstacle.model_dump(mode="json") if obstacle else None,
    }
    for key in skill.required_inputs:
        if key in {"obstruction", "evidence"} and not catalog[key]:
            raise ResearchError(f"{mode} requires {key}")
    if mode == _M.PROOF_ROUTE_EXPLORATION and not catalog["dependencies"]:
        raise ResearchError("PROOF_ROUTE_EXPLORATION requires an existing prerequisite")
    inputs = {key: catalog[key] for key in skill.required_inputs}
    if obstacle:
        inputs["obstruction"] = catalog["obstruction"]
    return skill, inputs, obstacle.obstacle_id if obstacle else None


def claim_drafts(result: ReasoningResult) -> tuple[ClaimDraft, ...]:
    detail = result.skill_output
    if isinstance(detail, LemmaDecomposition):
        return detail.lemmas
    if isinstance(detail, ProofRouteExploration) and detail.reduction is not None:
        return (detail.reduction,)
    return ()


def validate_skill(task: ReasoningTask, result: ReasoningResult, state: RestoredState) -> None:
    if task.reasoning_mode is None:
        if result.skill_output is not None or result.new_obstacles:
            raise ResearchError("Structured skill output requires a selected mode")
        return
    skill = SKILLS[task.reasoning_mode]
    detail = result.skill_output
    if result.obstacles:
        raise ResearchError("A selected skill requires structured new_obstacles")
    if (
        detail is None
        or type(detail) is not skill.result_schema
        or result.outcome not in skill.outcomes
    ):
        raise ResearchError("Reasoning result does not match the selected skill")
    if set(detail.self_check.assumptions) != set(result.assumptions_used):
        raise ResearchError("Self-check assumptions differ from the result")
    if isinstance(detail, ProofRouteExploration) and (detail.reduction is not None) != (
        result.outcome == _O.VALID_REDUCTION_CANDIDATE
    ):
        raise ResearchError("Reduction candidate and route exploration outcome disagree")
    if isinstance(detail, LemmaDecomposition) and bool(detail.lemmas) != (
        result.outcome == _O.PARTIAL_PROGRESS
    ):
        raise ResearchError("Lemma claims and decomposition outcome disagree")
    if any(not item.name.endswith((".md", ".tex")) for item in result.artifacts):
        raise ResearchError("Proof derivations require Markdown or LaTeX")
    graph = state.graph
    problem = task.problem_id
    allowed = graph.assumption_lattice.closure(graph.assumptions_for(problem))
    known = {node.id for node in graph.document.nodes if node.kind in ASSERTIONS} - {problem}
    evidence = {node.id for node in graph.document.nodes if node.evidence is not None}
    artifacts = {item.name: item.text for item in result.all_artifacts}
    if (
        isinstance(detail, (ObstacleDiagnosis, MethodInnovation))
        and detail.obstacle_id != task.obstacle_id
    ):
        raise ResearchError("Skill used a different obstruction")
    if isinstance(detail, MethodInnovation):
        prior_method = task.skill_inputs["obstruction"]["excluded_scope"]["method"]
        if detail.prior_method != prior_method or detail.prior_method == detail.replacement_method:
            raise ResearchError("Method switch must start from the recorded failed method")
    if (
        isinstance(detail, CounterexampleSearch)
        and result.outcome == _O.COUNTEREXAMPLE_CANDIDATE
        and not detail.violated_conclusion
    ):
        raise ResearchError("Counterexample candidate needs a conclusion to refute")
    if isinstance(detail, MathematicalSynthesis) and (
        not evidence.issuperset(detail.source_evidence)
        or not known.issuperset(detail.remaining_dependencies)
    ):
        raise ResearchError("Synthesis uses unknown evidence or dependencies")
    if isinstance(detail, ProofConstruction):
        criteria = {item.id for item in state.checkpoint.contract.acceptance_criteria}
        if any(
            item.criterion_id not in criteria or item.evidence_id not in evidence
            for item in detail.obligation_evidence
        ):
            raise ResearchError("Proof uses unknown evidence or acceptance criteria")
    for claim in claim_drafts(result):
        if (
            claim.artifact not in artifacts
            or not allowed.issuperset(claim.assumptions)
            or not known.issuperset(claim.dependencies)
        ):
            raise ResearchError("Candidate claim has missing source, assumptions or prerequisites")
        if isinstance(detail, ProofRouteExploration) and not claim.dependencies:
            raise ResearchError("Reduction must have at least one existing prerequisite")
        try:
            statement_hash(claim.statement)
        except ValueError as exc:
            raise ResearchError("Candidate claim needs a non-empty mathematical statement") from exc
    if len({claim.artifact for claim in claim_drafts(result)}) != len(claim_drafts(result)):
        raise ResearchError("Candidate claims require distinct source artifacts")
    identities = set()
    for draft in result.new_obstacles:
        if (
            draft.step.file not in artifacts
            or not evidence.issuperset(draft.evidence_refs)
            or not allowed.issuperset(draft.excluded_scope.assumptions)
        ):
            raise ResearchError("Obstruction needs a scoped source artifact and valid evidence")
        source = artifacts[draft.step.file]
        if draft.step.line is not None:
            lines = source.splitlines()
            if draft.step.line > len(lines):
                raise ResearchError("Obstruction locator does not match its source")
            source = lines[draft.step.line - 1]
        if draft.step.formula is not None and draft.step.formula not in source:
            raise ResearchError("Obstruction locator does not match its source")
        key = draft.identity(problem, task.assertion_revision)
        if key in identities:
            raise ResearchError("Duplicate obstruction in one result")
        identities.add(key)


def mock_skill_result(task: ReasoningTask) -> ReasoningResult:
    """Distinct deterministic fixtures for exercising the seven output contracts."""
    mode = task.reasoning_mode
    inputs = task.skill_inputs
    detail = {
        "mode": mode,
        "self_check": {
            "assumptions": [],
            "logical_gaps": ["Candidate fixture needs independent verification."],
            "possible_counterexamples": ["Check endpoints and degenerate premises."],
            "unverified_steps": ["Verify the proposed inference under the exact hypotheses."],
        },
    }
    outcome = _O.PARTIAL_PROGRESS
    obstacles = ()
    computations = ()
    draft = "## Fixture derivation\n"
    match mode:
        case _M.PROOF_CONSTRUCTION:
            outcome = _O.PROOF_CANDIDATE
            draft += "Integrate the candidate identity. The term F(b)-F(a) has no sign estimate.\n"
            detail.update(
                ordered_steps=["Integrate the candidate identity.", "Estimate its trace."]
            )
            obstacles = (
                {
                    "category": "BOUNDARY",
                    "description": "The trace term F(b)-F(a) is uncontrolled.",
                    "step": {
                        "file": "derivation.md",
                        "anchor": "trace estimate",
                        "line": 2,
                        "formula": "F(b)-F(a)",
                    },
                    "excluded_scope": {
                        "method": "direct integration",
                        "domain": "closed interval",
                        "restriction": "without a trace condition",
                    },
                    "attempted_methods": ["Integrate without a trace estimate."],
                    "potential_resolutions": ["Propose a trace lemma with explicit endpoints."],
                },
            )
        case _M.LEMMA_DECOMPOSITION:
            draft += r"An endpoint condition $F(b)=F(a)$ would cancel the trace term."
            detail.update(
                lemmas=[
                    {
                        "label": "Trace cancellation candidate",
                        "statement": r"$F(b)=F(a)\implies F(b)-F(a)=0$.",
                        "artifact": "derivation.md",
                    }
                ],
                composition="Use cancellation before applying the interior estimate.",
            )
        case _M.COUNTEREXAMPLE_SEARCH:
            outcome = _O.NO_PROGRESS
            draft += "Evaluate the degenerate constant configuration. No violation is asserted."
            detail.update(
                tested_domain="Contract domain",
                test_case="constant configuration",
                assumption_checks=["Check whether constants satisfy each premise."],
                violated_conclusion=None,
            )
            sample = (0 + 1) ** 2
            computations = (
                DraftArtifact(
                    name="sample.txt",
                    text=f"Offline fixture calculation: x=0 gives (x+1)^2={sample}. Sample only.",
                ),
            )
        case _M.OBSTACLE_DIAGNOSIS:
            draft += "The sign of the boundary term is independent of the interior estimate."
            detail.update(
                obstacle_id=task.obstacle_id,
                failed_inference="Drop the trace term",
                mechanism="Interior bounds leave the trace sign undetermined.",
                remaining_scope="A trace-controlled class remains untested.",
            )
        case _M.METHOD_INNOVATION:
            draft += r"Test the shifted quantity $\widetilde{F}=F-F(a)$ with a trace estimate."
            detail.update(
                obstacle_id=task.obstacle_id,
                prior_method=inputs["obstruction"]["excluded_scope"]["method"],
                replacement_method="weighted trace estimate",
                new_quantity=r"$\widetilde{F}=F-F(a)$",
                validation_checks=["Check the weighted trace at both endpoints."],
            )
        case _M.PROOF_ROUTE_EXPLORATION:
            outcome = _O.VALID_REDUCTION_CANDIDATE
            dependency = inputs["dependencies"][0]
            draft += f"Condition the alternate reduction on {dependency}; verify the implication."
            detail.update(
                reduction={
                    "label": "Conditional alternate route",
                    "statement": f"If {dependency} holds, derive {task.problem_id}.",
                    "artifact": "derivation.md",
                    "dependencies": [dependency],
                }
            )
        case _M.MATHEMATICAL_SYNTHESIS:
            citation = inputs["evidence"][0]
            draft += f"Combine the scoped record {citation} with the remaining obligations."
            detail.update(
                source_evidence=[citation],
                conditional_conclusion="The argument remains conditional on all gaps.",
                remaining_dependencies=inputs["dependencies"],
            )
    return ReasoningResult(
        outcome=outcome,
        summary=f"Offline {mode} fixture.",
        next_action="Review the scoped obligations.",
        artifacts=(DraftArtifact(name="derivation.md", text=draft),),
        new_obstacles=obstacles,
        computation_artifacts=computations,
        skill_output=detail,
        unresolved_steps=("Verify the unproved transitions independently.",),
    )
