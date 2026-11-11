"""Assemble a static snapshot from authorized, structured research data."""

import re
from datetime import UTC, datetime
from pathlib import Path

from amre.dashboard.models import (
    PublicArtifact,
    PublicAttempt,
    PublicBudget,
    PublicCheckpoint,
    PublicDecision,
    PublicEdge,
    PublicEvent,
    PublicNode,
    PublicProblem,
    PublicProgram,
    PublicSelection,
    PublicSession,
    ResearchSnapshot,
)
from amre.execution.adapters import redact
from amre.execution.deep import DeepSessionStore
from amre.execution.models import ExecutionJob, ExecutionState
from amre.execution.queue import TaskQueue, load_program
from amre.research.graph import NodeKind
from amre.research.journal import repository_revision
from amre.research.memory import ResearchMemory
from amre.research.storage import read_yaml, validate, workspace_path
from amre.research.store import read_statement_file
from amre.scheduling.service import PortfolioScheduler

_PRIVATE = re.compile(
    r"(?i)(?:\b[a-z]:[/\\]|\\\\[^\\\s]+\\|/(?:users|home|mnt|root|var|tmp|etc)/|"
    r"\.env(?:\b|[._-])|(?:password|passwd|secret|api[_ -]?key|access[_ -]?token)\s*[:=]|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:https?|ssh)://[^/\s]+@|"
    r"\b(?:akia[a-z0-9]{16}|xox[baprs]-[a-z0-9-]{10,}|eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.))"
)
_UNKNOWN = "UNKNOWN"


def load_public_selection(root: Path, policy: Path | None = None) -> PublicSelection:
    path = policy or workspace_path(root, "research/dashboard-public.yaml")
    return validate(
        PublicSelection,
        read_yaml(path, missing_ok=policy is None) or {},
        "dashboard public selection",
    )


def _safe(text: str) -> bool:
    return len(text) <= 10000 and redact(text) == text and not _PRIVATE.search(text)


def _usage(value):
    return value if value is not None else _UNKNOWN


def _selected_nodes(graph, problems):
    nodes = {item.id: item for item in graph.document.nodes}
    edges = {}
    for item in graph.document.edges:
        edges.setdefault(item.source, []).append(item.target)
    found = set()
    pending = list(reversed(problems))
    while pending:
        identifier = pending.pop()
        if identifier in found or not _safe(identifier):
            continue
        if nodes[identifier].kind == NodeKind.RESEARCH_PROBLEM and identifier not in problems:
            continue
        found.add(identifier)
        pending.extend(edges.get(identifier, ()))
    return found


def snapshot(root: Path, selection: PublicSelection) -> ResearchSnapshot:
    memory = ResearchMemory(root)
    root = memory.root
    inventory = memory.inventory()
    graph = inventory.graph
    omitted = 0
    selected = tuple(
        identifier for identifier in dict.fromkeys(selection.problems) if _safe(identifier)
    )
    contracts = tuple(inventory.document.contract(identifier) for identifier in selected)
    visible = _selected_nodes(graph, selected)

    def safe_text(text: str | None) -> str | None:
        nonlocal omitted
        if text is None or _safe(text):
            return text
        omitted += 1
        return None

    def visible_id(identifier):
        return identifier if identifier in visible else None

    nodes = []
    artifacts = []
    for node in graph.document.nodes:
        if node.id not in visible:
            continue
        detail = None
        statement = None
        obstruction = node.obstruction
        if node.id in selection.detail_ids:
            if obstruction and obstruction.step.file not in selection.private_artifacts:
                step = obstruction.step.formula or ""
                detail = safe_text(f"{obstruction.description}\n{obstruction.step.anchor}\n{step}")
            elif node.citation:
                detail = safe_text(node.citation)
        if (
            node.statement
            and node.id in selection.statement_ids
            and node.statement.path not in selection.private_artifacts
        ):
            statement = safe_text(read_statement_file(workspace_path(root, node.statement.path)))
        nodes.append(
            PublicNode(
                id=node.id,
                kind=node.kind,
                label=(safe_text(node.label) or node.id)
                if node.id in selection.detail_ids
                else node.id,
                review_status=graph.review(node.id).status,
                claim_status=node.claim_status,
                statement=statement,
                assumptions=tuple(
                    item for item in graph.assumptions_for(node.id) if item in visible
                ),
                evidence=tuple(
                    item.evidence_id
                    for item in graph.evidence_for(node.id)
                    if item.evidence_id in visible
                ),
                detail=detail,
                category=obstruction.category if obstruction else None,
                obstacle_status=obstruction.status
                if obstruction
                else "OPEN"
                if node.obstacle_open
                else None,
                obstacle_attempts=tuple(item for item in obstruction.attempts if _safe(item))
                if obstruction
                else (),
                obstacle_routes=tuple(item for item in obstruction.routes if item in visible)
                if obstruction
                else (),
            )
        )
        if node.evidence and node.evidence.assertion_id in visible:
            item = node.evidence
            artifacts.append(
                PublicArtifact(
                    artifact_id=item.evidence_id,
                    assertion_id=item.assertion_id,
                    source_type=item.source_type,
                    check_result=item.check_result,
                    coverage=item.coverage,
                    properties=item.properties,
                    private=item.file_path in selection.private_artifacts,
                )
            )
    edges = tuple(
        PublicEdge(source=edge.source, target=edge.target, relation=edge.relation)
        for edge in graph.document.edges
        if edge.source in visible and edge.target in visible
    )
    checkpoints = {item.checkpoint_id: item for item in inventory.checkpoints}
    problems = []
    for contract in contracts:
        key = contract.problem_id
        state = inventory.states.get(key)
        checkpoint = checkpoints.get(state.latest_checkpoint) if state else None
        details = key in selection.detail_ids
        routes = tuple(
            item.id
            for item in nodes
            if item.kind == NodeKind.PROOF_ROUTE and item.id in graph.research_inputs(key)
        )
        problems.append(
            PublicProblem(
                problem_id=key,
                research_status=checkpoint.research_status
                if checkpoint
                else contract.research_status,
                claim_status=contract.claim_status,
                route_ids=routes,
                active_route=visible_id(checkpoint.active_route) if checkpoint else None,
                checkpoint=checkpoint.checkpoint_id if checkpoint else None,
                obstacle_ids=tuple(item for item in graph.open_blockers(key) if item in visible),
                evidence_ids=tuple(
                    item for item in contract.evidence_references if item in visible
                ),
                definitions=tuple(
                    filter(None, (safe_text(item.text) for item in contract.definitions))
                )
                if details
                else (),
                obligations=tuple(
                    filter(
                        None, (safe_text(item) for item in checkpoint.unresolved_proof_obligations)
                    )
                )
                if details and checkpoint
                else (),
                next_action=safe_text(checkpoint.next_research_action)
                if details and checkpoint
                else None,
            )
        )
    programs = []
    for name in dict.fromkeys(selection.programs):
        if _safe(name):
            program = load_program(root, name)
            programs.append(
                PublicProgram(
                    program=name,
                    problems=tuple(item for item in program.problems if item in selected),
                )
            )
    queue = TaskQueue(root)
    ledger = queue.read()
    attempts = tuple(item for item in ledger.reservations if item.problem_id in selected)

    def attempt_state(item):
        job = ledger.jobs.get(ExecutionJob(problem_id=item.problem_id, program=item.program).key)
        if item.final_state:
            return item.final_state
        if job and job.attempt_id == item.attempt_id:
            return job.state
        return ExecutionState.RUNNING if item.settled_ms is None else _UNKNOWN

    public_attempts = tuple(
        PublicAttempt(
            attempt_id=item.attempt_id,
            problem_id=item.problem_id,
            route_id=visible_id(item.route_id),
            session_id=item.deep_session_id,
            state=attempt_state(item),
            strategy=item.reasoning_mode,
            elapsed_ms=_usage(item.settled_ms),
            reserved_ms=item.reserved_ms,
            tokens=_usage(item.usage.tokens),
            cost_microusd=_usage(item.usage.cost_microusd),
            tool_calls=_usage(item.usage.tool_calls),
        )
        for item in attempts
    )
    sessions = []
    session_store = DeepSessionStore(memory, queue)
    for session_id in dict.fromkeys(
        item.deep_session_id for item in attempts if item.deep_session_id
    ):
        if not session_store._path(session_id).is_file():
            # The ledger can reserve a dispatch before its worker creates session.yaml.
            continue
        session = session_store.inspect(session_id)
        sessions.append(
            PublicSession(
                session_id=session_id,
                problem_id=session.problem_id,
                route_id=visible_id(session.active_route),
                state=session.session_status,
                elapsed_ms=session.elapsed_time,
                session_limit_ms=session.reasoning_budget.session_ms,
                checkpoint=session.last_checkpoint,
                continuity=session.continuity,
                semantic_events=len(session.semantic_events),
            )
        )
    decisions = []
    scheduler = PortfolioScheduler(root)
    for identifier in dict.fromkeys(item.dispatch_id for item in attempts if item.dispatch_id):
        decision = scheduler.decision(identifier)
        item = decision.candidate()
        if not item or item.branch.route_id not in visible:
            continue
        stalled = item.branch.stagnation
        decisions.append(
            PublicDecision(
                decision_id=identifier,
                problem_id=item.branch.problem_id,
                route_id=item.branch.route_id,
                strategy_version=safe_text(decision.inputs.strategy.version),
                allocation=item.allocation,
                branch_state=item.branch.state,
                stagnation_count=len(stalled.attempts) if stalled else 0,
                cooldown_remaining=stalled.cooldown_remaining if stalled else 0,
            )
        )

    def total(field):
        values = [getattr(item.usage, field) for item in attempts]
        return _UNKNOWN if any(value is None for value in values) else sum(values)

    return ResearchSnapshot(
        generated_at=datetime.now(UTC),
        source_revision=repository_revision(root),
        programs=tuple(programs),
        problems=tuple(problems),
        nodes=tuple(nodes),
        edges=edges,
        attempts=public_attempts,
        sessions=tuple(sessions),
        decisions=tuple(decisions),
        artifacts=tuple(artifacts),
        omitted_details=omitted,
        checkpoints=tuple(
            PublicCheckpoint(
                checkpoint_id=item.checkpoint_id,
                problem_id=item.problem_id,
                route_id=visible_id(item.active_route),
                session_id=item.deep_session_id,
                dispatch_phase=item.dispatch_phase,
                timestamp=item.timestamp,
            )
            for item in inventory.checkpoints
            if item.problem_id in selected
        ),
        events=tuple(
            PublicEvent(
                event_id=item.event_id,
                problem_id=item.data.problem_id,
                kind=item.data.kind,
                route_id=visible_id(item.data.active_route or item.data.target_id)
                if item.data.kind == "ROUTE_SWITCHED"
                else None,
                timestamp=item.timestamp,
            )
            for item in inventory.events
            if item.data.problem_id in selected
        ),
        budget=PublicBudget(
            elapsed_ms=sum(item.settled_ms or 0 for item in attempts),
            reserved_ms=sum(item.reserved_ms for item in attempts if item.settled_ms is None),
            tokens=total("tokens"),
            cost_microusd=total("cost_microusd"),
            tool_calls=total("tool_calls"),
        ),
    )
