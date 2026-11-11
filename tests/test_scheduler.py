import subprocess
import sys

import pytest

from amre.errors import ResearchError
from amre.execution.adapters import MockModelAdapter
from amre.execution.orchestrator import ResearchOrchestrator
from amre.execution.queue import load_program
from amre.execution.skill_models import ReasoningMode
from amre.research.assumptions import Conflict
from amre.research.graph import GraphEdge, GraphNode
from amre.research.memory import ResearchMemory
from amre.research.models import EvidenceRecord, RunInfo, StatementRef
from amre.research.storage import yaml_text
from amre.scheduling.derivation import derive_problem
from amre.scheduling.models import Allocation, BranchState, Strategy
from amre.scheduling.planner import choose
from amre.scheduling.service import PortfolioScheduler

P = "P-T"
PROOF = ReasoningMode.PROOF_CONSTRUCTION
SEARCH = ReasoningMode.COUNTEREXAMPLE_SEARCH


def _candidate(decision, route, problem=P):
    return next(
        item
        for item in decision.inputs.candidates
        if item.branch.route_id == route and item.branch.problem_id == problem
    )


def _write(root, filename, data):
    path = root / "research" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text(data), encoding="utf-8")


def _checkpoint(root, route):
    return ResearchMemory(root).create_checkpoint(
        P,
        {
            "active_route": route,
            "next_research_action": f"Check route {route}.",
        },
    )


def _route(route, statement, prerequisites):
    reference = StatementRef.from_text(statement)
    evidence_id = f"Draft-{route}"
    nodes = (
        GraphNode(id=route, kind="PROOF_ROUTE", label=f"Method {route}", statement=reference),
        GraphNode(
            id=evidence_id,
            kind="EVIDENCE",
            label="Unverified fixture",
            evidence=EvidenceRecord(
                evidence_id=evidence_id,
                source_type="MODEL",
                file_path=reference.path,
                run=RunInfo(tool="fixture", run_id=f"Run-{route}"),
                check_scope="Candidate reduction",
                check_result="INCONCLUSIVE",
                coverage="PARTIAL",
                assertion_id=route,
                assertion_revision=1,
                direction="SUPPORT",
            ),
        ),
    )
    edges = (
        GraphEdge(source=route, target=evidence_id, relation="SUPPORTED_BY"),
        *(GraphEdge(source=route, target=item, relation="REQUIRES_ALL") for item in prerequisites),
    )
    return nodes, edges


@pytest.fixture
def portfolio(example_store):
    store = example_store
    store.update(P, {"obstacles": []})
    document = store.read()
    statement = r"Candidate: use the shifted quadratic and the real-square inequality."
    nodes, edges = _route("C", statement, ("L2",))
    store.replace_graph(
        document.graph.model_copy(
            update={
                "nodes": (*document.graph.nodes, *nodes),
                "edges": (
                    *document.graph.edges,
                    *edges,
                    GraphEdge(source="T", target="C", relation="ALTERNATIVE_ROUTE"),
                    GraphEdge(source="B", target="L2", relation="REQUIRES_ALL"),
                ),
            }
        ),
        statements=(statement,),
        expected=document.graph,
    )
    _write(
        store.root,
        "portfolio.yaml",
        {
            "strategy": {"stagnation_window": 2, "cooldown_attempts": 2, "exploration_seconds": 2},
            "branches": [
                {"problem_id": P, "route_id": route, "mode": SEARCH.value} for route in ("B", "C")
            ],
        },
    )
    _checkpoint(store.root, "A")
    return PortfolioScheduler(store.root)


def _independent(scheduler, assertion, evidence_id, *, direction="SUPPORT"):
    store = scheduler.memory.research
    file = f"research/{evidence_id}.txt"
    (scheduler.root / file).write_text(
        "Synthetic review for scheduling verification.", encoding="utf-8"
    )
    store.register_evidence(
        EvidenceRecord(
            evidence_id=evidence_id,
            source_type="HUMAN",
            file_path=file,
            run=RunInfo(tool="fixture", run_id=f"Review-{evidence_id}"),
            check_scope="Synthetic scheduler fixture",
            check_result="PASS",
            coverage="FULL_ASSERTION",
            assertion_id=assertion,
            assertion_revision=store.graph().review(assertion).revision,
            direction=direction,
            properties=("HUMAN_CONFIRMED",),
        )
    )


def test_obstacle_to_stagnation_alternative_exploration_and_budget_stop(portfolio):
    root = portfolio.root
    _checkpoint(root, "B")
    runner = ResearchOrchestrator(root)
    assert runner.run(P, MockModelAdapter(), mode=SEARCH).state == "COMPLETED"
    _independent(portfolio, "L3", "Checked-L3")
    _checkpoint(root, "A")
    assert runner.run(P, MockModelAdapter(), mode=PROOF).state == "COMPLETED"
    retained = portfolio.explain(P, None, 19)
    assert retained.selected == f"{P}:A" and retained.candidate().continuity_priority
    assert runner.run(P, MockModelAdapter(), mode=PROOF).state == "COMPLETED"
    initial = portfolio.explain(P, None, 19)
    a, b, c = (_candidate(initial, route) for route in ("A", "B", "C"))
    assert a.branch.state == BranchState.STAGNATED
    assert a.branch.stagnation.repeated_obstacles
    assert len(a.branch.attempts) == 2 and a.branch.stagnation.cooldown_remaining == 2
    assert b.branch.state == BranchState.PROMISING and b.branch.reactivated_by == ("L3",)
    assert c.branch.state == BranchState.UNEXPLORED
    assert initial.selected == f"{P}:B"

    dry = portfolio.simulate(P, None, 19, 5, 1000)
    assert dry == portfolio.simulate(P, None, 19, 5, 1000)
    assert f"{P}:C" in {item.selected for item in dry}
    assert len(portfolio.queue.read().reservations) == 3
    reports = portfolio.run(P, None, 19, steps=5)
    assert reports[0][0].selected == f"{P}:B"
    exploration = next(
        (decision, result) for decision, result in reports if decision.selected == f"{P}:C"
    )
    assert exploration[0].dispatch.max_attempt_ms <= 2000
    assert all(result.state == "COMPLETED" for _, result in reports)
    for decision, result in reports:
        assert portfolio.decision(decision.decision_id) == decision == choose(decision.inputs)
        charge = next(
            item
            for item in portfolio.queue.read().reservations
            if item.attempt_id == result.attempt_id
        )
        assert charge.route_id == decision.dispatch.route_id
        assert charge.deep_session_id.startswith("DS-")
        assert 0 < charge.settled_ms <= charge.reserved_ms
    history = portfolio.memory.inventory()
    switched = reports[0][0].decision_id
    checkpoints = [item for item in history.checkpoints if item.dispatch_id == switched]
    assert [item.dispatch_phase for item in checkpoints] == [
        "BEFORE_SWITCH",
        "READY",
        "AFTER_ATTEMPT",
    ]
    assert [item.active_route for item in checkpoints] == ["A", "B", "B"]
    assert (
        checkpoints[-1].budget_snapshot.elapsed_seconds
        >= checkpoints[0].budget_snapshot.elapsed_seconds
    )

    _independent(portfolio, "L2", "Checked-Shared-L2")
    updated = portfolio.explain(P, None, 19)
    assert "L2" not in _candidate(updated, "A").branch.unmet_prerequisites
    assert "L2" not in _candidate(updated, "C").branch.unmet_prerequisites
    assert _candidate(updated, "A").branch.attempts[:2] == a.branch.attempts
    assert "L2" in _candidate(updated, "A").branch.reactivated_by
    count = len(portfolio.queue.read().reservations)
    _write(root, "execution.yaml", {"workspace": {"attempts": count, "seconds": 3600}})
    stopped = portfolio.run(P, None, 19, steps=4)
    assert len(stopped) == 1 and stopped[0][1].state == "BLOCKED"
    assert len(portfolio.queue.read().reservations) == count
    assert all(
        "workspace attempt budget exhausted" in item.exclusions
        for item in stopped[0][0].inputs.candidates
    )


def test_shares_floor_active_limit_and_seeded_replay(portfolio):
    base = portfolio.explain(P, None, 11).inputs
    kinds = (Allocation.CURRENT, Allocation.ALTERNATIVE, Allocation.EXPLORATION)
    arranged = tuple(
        item.model_copy(update={"allocation": kind})
        for item, kind in zip(base.candidates, kinds, strict=True)
    )
    counts = dict.fromkeys(kinds, 0)
    for index in range(20):
        inputs = base.model_copy(
            update={
                "candidates": arranged,
                "position": index,
                "dispatched": dict(counts),
                "strategy": Strategy(max_active_branches=1),
            }
        )
        result = choose(inputs)
        assert result == choose(inputs)
        assert len(result.shortlisted) == 1
        counts[result.candidate().allocation] += 1
    assert list(counts.values()) == [12, 5, 3]
    assert (
        Strategy(current=80, alternative=15, exploration=5).shares()[Allocation.EXPLORATION] == 15
    )


def test_assumption_conflict_and_refuted_evidence_are_different_states(portfolio):
    store = portfolio.memory.research
    document = store.read()
    lattice = document.graph.assumptions.model_copy(
        update={
            "conflicts": (
                *document.graph.assumptions.conflicts,
                Conflict(left="H-Real", right="H-Nonreal", basis="Disjoint domains"),
            )
        }
    )
    store.replace_graph(
        document.graph.model_copy(
            update={
                "nodes": (
                    *document.graph.nodes,
                    GraphNode(
                        id="H-Nonreal",
                        kind="ASSUMPTION",
                        label="Non-real domain",
                        assumption_type="MATHEMATICAL",
                    ),
                ),
                "edges": (
                    *document.graph.edges,
                    GraphEdge(source="C", target="H-Nonreal", relation="USES_ASSUMPTION"),
                ),
                "assumptions": lattice,
            }
        ),
        expected=document.graph,
    )
    view = portfolio.explain(P, None, 2)
    assert _candidate(view, "C").branch.state == BranchState.PAUSED
    assert "L2" in _candidate(view, "C").branch.prerequisites
    assert not _candidate(view, "A").exclusions
    _independent(portfolio, "B", "Refuted-B", direction="REFUTE")
    assert _candidate(portfolio.explain(P, None, 2), "B").branch.state != BranchState.REFUTED
    store.acknowledge_recheck("B", revision=store.graph().review("B").revision)
    assert _candidate(portfolio.explain(P, None, 2), "B").branch.state == BranchState.REFUTED


def test_empty_and_blocked_portfolios(example_store):
    store = example_store
    portfolio = PortfolioScheduler(store.root)
    blocked = portfolio.explain(P, None, 1)
    assert blocked.inputs.candidates and not blocked.selected
    store.create(
        {
            "problem_id": "Empty",
            "acceptance_criteria": [{"id": "Check", "description": "Check it."}],
            "budget": {"max_tokens": 200, "timeout_seconds": 12},
            "provenance": {"author": "fixture", "source": "scheduler test"},
        },
        r"$x=x$",
    )
    empty = portfolio.run("Empty", None, 1)[0]
    assert not empty[0].inputs.candidates and empty[1].state == "BLOCKED"
    assert not portfolio.queue.read().reservations


def test_configured_stagnation_window_and_branch_limits(portfolio):
    runner = ResearchOrchestrator(portfolio.root)
    for _ in range(2):
        assert runner.run(P, MockModelAdapter(), mode=PROOF).state == "COMPLETED"
    assert _candidate(portfolio.explain(P, None, 0), "A").branch.state == BranchState.STAGNATED
    _write(
        portfolio.root,
        "portfolio.yaml",
        {
            "strategy": {"stagnation_window": 3},
            "branches": [
                {"problem_id": P, "route_id": "A", "limits": {"attempts": 2, "seconds": 60}}
            ],
        },
    )
    candidate = _candidate(portfolio.explain(P, None, 0), "A")
    assert candidate.branch.stagnation is None
    assert "branch attempt budget exhausted" in candidate.exclusions


def test_derived_contract_preserves_scope_and_deduplicates(portfolio):
    root = portfolio.root
    assert ResearchOrchestrator(root).run(P, MockModelAdapter(), mode=PROOF).state == "COMPLETED"
    graph = portfolio.memory.research.graph()
    obstacle = next(node.obstruction for node in graph.document.nodes if node.obstruction)
    spec = {
        "author": "fixture",
        "assumptions": ["H-Real"],
        "dependencies": ["L2"],
        "acceptance_criteria": [{"id": "Trace", "description": "Verify both endpoints."}],
    }
    statement = r"If \(F(a)=F(b)\), then \(F(b)-F(a)=0\)."
    child = derive_problem(root, P, obstacle.obstacle_id, "A", spec, statement)
    assert derive_problem(root, P, obstacle.obstacle_id, "A", spec, statement) == child
    stored = portfolio.memory.research.load_contract(child)
    assert stored.derivation.parent_problem == P and stored.derivation.source_evidence
    assert stored.derivation.depth == 1 and stored.claim_status == "UNPROVED"
    graph = portfolio.memory.research.graph()
    assert child in graph.upstream("A") and child not in graph.upstream("B")
    assert "Awaiting required research problems" in " ".join(
        _candidate(portfolio.explain(P, None, 0), "A").exclusions
    )
    _write(root, "portfolio.yaml", {"strategy": {"max_children": 1}})
    with pytest.raises(ResearchError, match="derivation limit"):
        derive_problem(root, P, obstacle.obstacle_id, "A", spec, r"A distinct proposition.")
    assert len(portfolio.memory.research.read().contracts) == 2


def test_program_reopens_downstream_after_independent_support(portfolio):
    store = portfolio.memory.research
    document = store.read()
    statement = "Candidate reduction for the downstream test."
    nodes, edges = _route("Downstream", statement, ("L2",))
    store.create(
        {
            "problem_id": "P-Downstream",
            "assumptions": ["H-Real"],
            "dependencies": [P],
            "research_routes": ["Downstream"],
            "acceptance_criteria": [{"id": "Check", "description": "Review downstream reduction."}],
            "budget": {"max_tokens": 800, "timeout_seconds": 40},
            "provenance": {"author": "fixture", "source": "scheduler program"},
        },
        r"$x^2\geq 0$",
        graph=document.graph.model_copy(
            update={
                "nodes": (*document.graph.nodes, *nodes),
                "edges": (*document.graph.edges, *edges),
            }
        ),
        statements=(statement,),
        expected=document,
    )
    portfolio.memory.create_checkpoint(
        "P-Downstream",
        {
            "active_route": "Downstream",
            "next_research_action": "Wait for upstream verification.",
        },
    )
    _write(portfolio.root, "programs/Pair.yaml", {"name": "Pair", "problems": [P, "P-Downstream"]})
    program = load_program(portfolio.root, "Pair")
    before = portfolio.explain(None, program, 0)
    assert before.selected == f"{P}:A"
    paused = _candidate(before, "Downstream", "P-Downstream")
    assert paused.branch.state == BranchState.PAUSED and paused.exclusions
    assert not any(
        item.branch.problem_id == "P-Downstream" and item.branch.route_id == "A"
        for item in before.inputs.candidates
    )
    _independent(portfolio, P, "Checked-Upstream")
    store.acknowledge_recheck(P, revision=store.graph().review(P).revision)
    after = portfolio.explain(None, program, 0)
    assert after.selected == "P-Downstream:Downstream"
    ready = after.candidate()
    assert ready.branch.reactivated_by == (P,) and not ready.exclusions
    dispatched = portfolio.run(None, program, 0)[0]
    assert dispatched[1].state == "COMPLETED"
    assert portfolio.queue.read().reservations[-1].program == "Pair"


@pytest.mark.parametrize("stage", ["switch", "settled"])
def test_pending_decision_recovers_without_duplicate_dispatch(portfolio, stage):
    root = portfolio.root
    # Force a switch so both checkpoint boundaries participate.
    _write(
        root,
        "portfolio.yaml",
        {
            "branches": [
                {"problem_id": P, "route_id": route, "enabled": route == "B"}
                for route in ("A", "B", "C")
            ]
        },
    )
    script = """
import os
import sys
from pathlib import Path
from amre.research.memory import ResearchMemory
from amre.scheduling.service import PortfolioScheduler

class Interrupted(PortfolioScheduler):
    def _save_journal(self, scope, journal):
        if sys.argv[1] == "settled" and journal.results:
            os._exit(76)
        super()._save_journal(scope, journal)

save = ResearchMemory.create_checkpoint
def create(self, problem, payload, **kwargs):
    if sys.argv[1] == "switch" and payload.get("dispatch_phase") == "READY":
        os._exit(76)
    return save(self, problem, payload, **kwargs)

ResearchMemory.create_checkpoint = create
Interrupted(Path.cwd()).run("P-T", None, 13)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, stage],
        cwd=root,
        capture_output=True,
        timeout=40,
        check=False,
    )
    assert child.returncode == 76, child.stderr
    restarted = PortfolioScheduler(root)
    journal = restarted.journal("problem:P-T")
    assert len(journal.decisions) == 1 and not journal.results
    record, result = restarted.run(P, None, 999)[0]
    assert record.inputs.seed == 13 and record.decision_id == journal.decisions[0]
    assert result.state == "COMPLETED"
    assert len(restarted.queue.read().reservations) == 1
    inventory = restarted.memory.inventory()
    assert sum(item.data.kind == "ROUTE_SWITCHED" for item in inventory.events) == 1
    checkpoints = [item for item in inventory.checkpoints if item.dispatch_id == record.decision_id]
    assert [item.dispatch_phase for item in checkpoints] == [
        "BEFORE_SWITCH",
        "READY",
        "AFTER_ATTEMPT",
    ]
