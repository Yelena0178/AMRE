"""Project existing graph, journal, and budget facts into scheduling candidates."""

from collections import Counter
from pathlib import Path

from amre.errors import ResearchError
from amre.execution.failure_memory import FailureMemory
from amre.execution.models import (
    ExecutionJob,
    ExecutionLedger,
    ExecutionState,
    Limits,
    ResearchProgram,
)
from amre.execution.queue import BudgetManager, load_policy
from amre.execution.skill_models import ProgressEvent, ProgressKind, ReasoningMode
from amre.research.graph import NodeKind, ResearchGraph
from amre.research.memory import MemoryInventory
from amre.research.models import ResearchStatus, ReviewStatus
from amre.research.storage import read_yaml, validate, workspace_path
from amre.scheduling.models import (
    Allocation,
    BranchSettings,
    BranchState,
    Candidate,
    PortfolioSettings,
    ResearchBranch,
    Score,
    StagnationEvent,
    Strategy,
)

_MANDATORY = {"DEPENDS_ON", "REQUIRES_ALL", "DERIVED_FROM"}


def load_settings(root: Path) -> PortfolioSettings:
    from amre.strategy.config import active_profile

    path = workspace_path(root, "research/portfolio.yaml")
    settings = validate(
        PortfolioSettings, read_yaml(path, missing_ok=True) or {}, "portfolio settings"
    )
    if active := active_profile(root):
        profile = active.profile
        settings = settings.model_copy(
            update={
                "strategy": profile.portfolio.model_copy(update={"version": active.version_id}),
                "problem_values": {**settings.problem_values, **profile.problem_values},
            }
        )
    return settings


def _independent(graph: ResearchGraph, subject: str, direction: str) -> bool:
    review = graph.review(subject)
    return review.status == ReviewStatus.CURRENT and any(
        item.applies(review.revision, direction) for item in graph.evidence_for(subject)
    )


def _meaningful(problem: str, item: ProgressEvent) -> bool:
    return item.kind in {ProgressKind.OBLIGATION_SATISFIED, ProgressKind.CANDIDATE_REDUCTION} or (
        item.kind == ProgressKind.CANDIDATE_DERIVATION and item.subject_id != problem
    )


def _detect(
    problem: str,
    rows: tuple[dict, ...],
    settled: tuple,
    strategy: Strategy,
    known_obstacles: set[str],
) -> StagnationEvent | None:
    window = []
    for row in reversed(rows):
        progress = tuple(ProgressEvent.model_validate(item) for item in row["progress"])
        if any(_meaningful(problem, item) for item in progress):
            break
        window.append(row)
        if len(window) >= strategy.stagnation_window:
            break
    if len(window) < strategy.stagnation_window:
        return None
    counts = Counter(
        obstacle
        for row in window
        for obstacle in set(row["encountered_obstacles"])
        if obstacle in known_obstacles
    )
    repeated = tuple(sorted(key for key, number in counts.items() if number > 1))
    repeats = sum(
        item["kind"] == ProgressKind.TEXT_REPEAT for row in window for item in row["progress"]
    )
    attempts = {row["attempt_id"] for row in window}
    spent = sum(item.settled_ms for item in settled if item.attempt_id in attempts)
    if not (repeated or repeats or spent >= strategy.stagnation_ms):
        return None
    # Cooldown ticks advance only when a real attempt in the workspace settles.
    last = window[0]["attempt_id"]
    position = next(
        (index for index, item in enumerate(settled) if item.attempt_id == last), len(settled) - 1
    )
    cooldown = max(0, strategy.cooldown_attempts - (len(settled) - position - 1))
    return StagnationEvent(
        attempts=tuple(row["attempt_id"] for row in reversed(window)),
        repeated_obstacles=repeated,
        repeated_drafts=repeats,
        charged_ms=spent,
        cooldown_remaining=cooldown,
    )


def _tighten(base: Limits, override: Limits | None) -> Limits:
    if override is None:
        return base
    return Limits(
        attempts=min(base.attempts, override.attempts),
        seconds=min(base.seconds, override.seconds),
        tool_calls=min(base.tool_calls, override.tool_calls),
    )


def _old_assessment(checkpoint, problem: str, owner: str, route: str, available):
    if checkpoint is None:
        return None
    graph = ResearchGraph(checkpoint.graph, checkpoint.reviews)
    known = {node.id for node in checkpoint.graph.nodes}
    if owner not in known or problem not in known or not known.issuperset(available):
        return None
    return next(
        (
            item
            for item in graph.route_assessments(owner, available_assumptions=available)
            if item.route_id == route
        ),
        None,
    )


def candidates(
    root: Path,
    inventory: MemoryInventory,
    ledger: ExecutionLedger,
    settings: PortfolioSettings,
    problems: tuple[str, ...],
    program: ResearchProgram | None,
) -> tuple[Candidate, ...]:
    graph = inventory.graph
    strategy = settings.strategy
    checkpoints = {item.checkpoint_id: item for item in inventory.checkpoints}
    route_by_checkpoint = {key: value.active_route for key, value in checkpoints.items()}
    settled = tuple(item for item in ledger.reservations if item.settled_ms is not None)
    branch_settings = {item.key: item for item in settings.branches}
    budgets = BudgetManager(load_policy(root))
    service = FailureMemory(root)
    edges = graph.document.edges
    route_owners = {
        edge.target: edge.source for edge in edges if edge.relation == "ALTERNATIVE_ROUTE"
    }
    shared = Counter(
        edge.target for edge in edges if edge.relation in _MANDATORY and edge.source in route_owners
    )
    problem_ids = {contract.problem_id for contract in inventory.document.contracts}
    known_obstacles = {node.id for node in graph.document.nodes if node.kind == NodeKind.OBSTACLE}
    owners = {
        problem: {
            problem,
            *(
                edge.target
                for edge in edges
                if edge.source == problem
                and edge.relation in _MANDATORY
                and edge.target not in problem_ids
            ),
        }
        for problem in problem_ids
    }
    for item in settings.branches:
        if route_owners.get(item.route_id) not in owners.get(item.problem_id, ()):
            raise ResearchError(f"Portfolio branch is outside the research scope: {item.key}")
    result = []
    for problem in sorted(problems):
        contract = inventory.document.contract(problem)
        state = inventory.states.get(problem)
        checkpoint = checkpoints.get(state.latest_checkpoint) if state else None
        current_route = checkpoint.active_route if checkpoint else None
        available = graph.assumption_lattice.closure(graph.assumptions_for(problem))
        assessment_by_route = {
            item.route_id: item
            for owner in sorted(owners[problem])
            if owner in route_owners.values()
            for item in graph.route_assessments(owner, available_assumptions=available)
        }
        histories = service.attempts(problem, inventory=inventory)
        for route, assessment in sorted(assessment_by_route.items()):
            owner = route_owners[route]
            options = branch_settings.get(f"{problem}:{route}") or BranchSettings(
                problem_id=problem,
                route_id=route,
            )
            job = ExecutionJob(problem_id=problem, program=program.name if program else None)
            observed = ledger.jobs.get(job.key)
            limits = _tighten(strategy.branch_limits, options.limits)
            rows = tuple(row for row in histories if row.get("route_id") == route)
            used = tuple(
                item
                for item in ledger.reservations
                if item.problem_id == problem
                and (item.route_id or route_by_checkpoint.get(item.checkpoint_id)) == route
            )
            charged = budgets.used_ms(used)
            progress = tuple(
                ProgressEvent.model_validate(item) for row in rows for item in row["progress"]
            )
            required = tuple(
                sorted(
                    {
                        edge.target
                        for edge in edges
                        if edge.source in {owner, route} and edge.relation in _MANDATORY
                    }
                )
            )
            research_dependencies = {
                edge.target
                for edge in edges
                if edge.source in {problem, owner, route}
                and edge.relation in _MANDATORY
                and edge.target in problem_ids
            }
            required_problems = tuple(
                sorted(
                    key
                    for key in research_dependencies
                    if not (
                        _independent(graph, key, "SUPPORT")
                        and not _independent(graph, key, "REFUTE")
                        and not graph.open_blockers(key)
                        and available.issuperset(graph.assumptions_for(key))
                        or graph.has_supported_route(key, available_assumptions=available)
                    )
                )
            )
            assumptions = tuple(
                sorted(
                    {
                        assumption
                        for node in (owner, route, *required)
                        for assumption in graph.assumptions_for(node)
                    }
                )
            )
            blockers = tuple(
                sorted(
                    {
                        *assessment.blocked_by,
                        *graph.open_blockers(problem),
                        *(item for key in required for item in graph.open_blockers(key)),
                    }
                )
            )
            scoped_obstacles = tuple(key for key in blockers if graph.node(key).obstruction)
            missing = set(assumptions) - available
            exclusions = []
            reasons = []
            if not options.enabled:
                exclusions.append("Branch disabled in portfolio policy")
            if contract.research_status == ResearchStatus.CLOSED:
                exclusions.append("Research contract is closed")
            if (
                contract.research_status == ResearchStatus.PAUSED
                or (state and state.paused)
                or observed
                and observed.state in {ExecutionState.PAUSED, ExecutionState.CANCELLED}
            ):
                exclusions.append("Research is explicitly paused or cancelled")
            if missing or assessment.conflicts:
                exclusions.append(
                    "Incompatible hypotheses: "
                    + ", ".join(sorted(missing))
                    + ("; declared conflict" if assessment.conflicts else "")
                )
            if required_problems:
                exclusions.append(
                    "Awaiting required research problems: " + ", ".join(required_problems)
                )
            if set(blockers) - set(scoped_obstacles):
                exclusions.append(
                    "Unresolved blocker requires review: "
                    + ", ".join(sorted(set(blockers) - set(scoped_obstacles)))
                )
            if any(_independent(graph, key, "REFUTE") for key in {problem, owner}):
                exclusions.append("Target assertion has independently recorded refutation")
            if _independent(graph, problem, "SUPPORT") and not graph.open_blockers(problem):
                exclusions.append("Research contract already has current independent support")
            running = any(item.settled_ms is None for item in used)
            last_checkpoint = checkpoints.get(used[-1].checkpoint_id) if used else None
            old = _old_assessment(last_checkpoint, problem, owner, route, available)
            reopened = set()
            if old:
                reopened.update(
                    (set(old.unmet_dependencies) & set(required))
                    - set(assessment.unmet_dependencies)
                )
                reopened.update(set(old.blocked_by) - set(blockers))
            if checkpoint and not old:
                # For a downstream branch paused before any attempt, use its last graph snapshot.
                known = {node.id for node in checkpoint.graph.nodes}
                if known.issuperset(available):
                    historical = ResearchGraph(checkpoint.graph, checkpoint.reviews)
                    for key in research_dependencies & known:
                        if not _independent(historical, key, "SUPPORT") and _independent(
                            graph, key, "SUPPORT"
                        ):
                            reopened.add(key)
            stalled = (
                _detect(problem, rows, settled, strategy, known_obstacles) if not reopened else None
            )
            if reopened:
                reasons.append(
                    "Reconsidered after prerequisite or blocker changed: "
                    + ", ".join(sorted(reopened))
                )
            if assessment.unmet_dependencies:
                reasons.append("Open prerequisites: " + ", ".join(assessment.unmet_dependencies))
            if scoped_obstacles:
                reasons.append("Scoped obstacles: " + ", ".join(scoped_obstacles))
            if stalled:
                reasons.append("Stagnation window has no evidence-linked mathematical change")
            if not missing and not assessment.conflicts and _independent(graph, route, "REFUTE"):
                status = BranchState.REFUTED
                exclusions.append("Route has current independent refutation evidence")
            elif exclusions:
                status = BranchState.PAUSED
            elif assessment.ready and not blockers:
                status = BranchState.COMPLETED
                exclusions.append("Route and prerequisites are independently supported")
            elif running:
                status = BranchState.ACTIVE
            elif stalled:
                status = BranchState.STAGNATED
                if stalled.cooldown_remaining:
                    exclusions.append(f"Cooldown: {stalled.cooldown_remaining} completed attempts")
            elif reopened or any(_meaningful(problem, item) for item in progress):
                status = BranchState.PROMISING
            elif not rows and not used:
                status = BranchState.UNEXPLORED
            elif route == current_route:
                status = BranchState.ACTIVE
            else:
                status = BranchState.PAUSED
                reasons.append("Alternative route is available for activation")
            if status == BranchState.STAGNATED or (
                status == BranchState.UNEXPLORED and route != current_route
            ):
                allocation = Allocation.EXPLORATION
            else:
                allocation = (
                    Allocation.CURRENT if route == current_route else Allocation.ALTERNATIVE
                )
            mode = options.mode
            obstacle = next(
                (
                    key
                    for key in scoped_obstacles
                    if graph.node(key).obstruction.problem_id == problem
                ),
                None,
            )
            if status == BranchState.STAGNATED:
                mode = (
                    ReasoningMode.METHOD_INNOVATION
                    if obstacle
                    else ReasoningMode.COUNTEREXAMPLE_SEARCH
                )
            if (
                mode in {ReasoningMode.METHOD_INNOVATION, ReasoningMode.OBSTACLE_DIAGNOSIS}
                and not obstacle
            ):
                exclusions.append("Selected reasoning mode needs a scoped obstruction")
            try:
                remaining = budgets.remaining_ms(
                    ledger,
                    job,
                    contract,
                    program,
                    route_id=route,
                    branch_limits=limits,
                    route_by_checkpoint=route_by_checkpoint,
                    prior_seconds=checkpoint.budget_snapshot.elapsed_seconds if checkpoint else 0,
                    prior_tokens=checkpoint.budget_snapshot.tokens_used if checkpoint else 0,
                    deep_session_id="DS-Planned",
                )
            except ResearchError as exc:
                exclusions.append(str(exc))
                remaining = 0
            if allocation == Allocation.EXPLORATION:
                remaining = min(remaining, strategy.exploration_seconds * 1000)
            if any(item.settled_ms is None for item in ledger.reservations):
                exclusions.append("An execution reservation is open")
            route_evidence = tuple(
                sorted(
                    {
                        evidence.evidence_id
                        for key in (route, *required)
                        for evidence in graph.evidence_for(key)
                    }
                )
            )
            downstream = sum(key in problem_ids for key in graph.downstream(problem))
            method = options.method or graph.node(route).label
            objective = options.next_action or (
                f"Investigate route {route} ({method}). "
                f"Address {', '.join(assessment.unmet_dependencies)}."
                if assessment.unmet_dependencies
                else f"Check the reduction for route {route} ({method})."
            )
            if status == BranchState.STAGNATED:
                objective += " Examine an alternative method within the exact failed scope."
            continuity_priority = (
                allocation == Allocation.CURRENT
                and not stalled
                and bool(rows)
                and (
                    any(
                        _meaningful(problem, ProgressEvent.model_validate(item))
                        for item in rows[-1]["progress"]
                    )
                    or len(rows) == 1
                    and bool(rows[-1]["encountered_obstacles"])
                )
            )
            result.append(
                Candidate(
                    branch=ResearchBranch(
                        problem_id=problem,
                        route_id=route,
                        owner=owner,
                        method=method,
                        prerequisites=required,
                        assumptions=assumptions,
                        unmet_prerequisites=assessment.unmet_dependencies,
                        obstacles=blockers,
                        evidence=route_evidence,
                        progress=progress,
                        attempts=tuple(row["attempt_id"] for row in rows),
                        charged_ms=charged,
                        next_action=objective,
                        state=status,
                        stagnation=stalled,
                        reactivated_by=tuple(sorted(reopened)),
                    ),
                    allocation=allocation,
                    mode=mode,
                    obstacle_id=obstacle,
                    limits=limits,
                    score=Score(
                        problem_value=5 * settings.problem_values.get(problem, 1),
                        mathematical_progress=(
                            4 * sum(_meaningful(problem, item) for item in progress)
                            + 5 * len(reopened)
                        ),
                        dependency_leverage=3 * downstream + sum(shared[key] for key in required),
                        novelty=3
                        if not rows
                        else 1
                        if mode == ReasoningMode.METHOD_INNOVATION
                        else 0,
                        cost_penalty=charged // 10000,
                        failure_penalty=(
                            len(stalled.attempts) + len(stalled.repeated_obstacles)
                            if stalled
                            else 0
                        ),
                    ),
                    exclusions=tuple(exclusions),
                    reasons=tuple(reasons),
                    continuity_priority=continuity_priority,
                    reservation_ms=remaining,
                )
            )
    return tuple(result)
