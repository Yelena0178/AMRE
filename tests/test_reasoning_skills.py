import subprocess
import sys
from pathlib import Path

import pytest

from amre.errors import ResearchError
from amre.execution.adapters import MockModelAdapter
from amre.execution.failure_memory import FailureMemory, recommendations
from amre.execution.models import AttemptReceipt, ReasoningResult
from amre.execution.orchestrator import ResearchOrchestrator, receipt_path
from amre.execution.skill_models import ReasoningMode
from amre.execution.skills import SKILLS, mock_skill_result
from amre.research.journal import read_versioned
from amre.research.models import EvidenceRecord, RunInfo
from amre.research.obstructions import ObstructionCategory
from amre.research.storage import read_yaml

PROBLEM = "P-T"
PROOF = ReasoningMode.PROOF_CONSTRUCTION


@pytest.fixture
def runner(example_store):
    service = ResearchOrchestrator(example_store.root)
    notes = Path(__file__).resolve().parents[1] / "examples" / "and_or" / "checkpoint-notes.yaml"
    service.memory.create_checkpoint(PROBLEM, read_yaml(notes))
    return service


def _receipt(root, job):
    return read_versioned(root, receipt_path(job.attempt_id), AttemptReceipt)


def test_all_modes_and_cold_obstruction_to_alternative_route(runner):
    root = runner.root
    modes = (
        PROOF,
        ReasoningMode.METHOD_INNOVATION,
        ReasoningMode.PROOF_ROUTE_EXPLORATION,
        ReasoningMode.LEMMA_DECOMPOSITION,
        ReasoningMode.COUNTEREXAMPLE_SEARCH,
        ReasoningMode.OBSTACLE_DIAGNOSIS,
        ReasoningMode.MATHEMATICAL_SYNTHESIS,
    )
    receipts = []
    obstacle_id = None
    for mode in modes:
        service = ResearchOrchestrator(root)
        job = service.run(PROBLEM, MockModelAdapter(), mode=mode)
        assert job.state == "COMPLETED", job.detail
        receipt = _receipt(root, job)
        receipts.append(receipt)
        assert type(receipt.result.skill_output) is SKILLS[mode].result_schema
        assert receipt.task.skill_version == SKILLS[mode].version
        assert receipt.result.skill_output.self_check.unverified_steps
        assert receipt.result.artifacts[0].name.endswith(".md")
        if mode == PROOF:
            failure = next(
                item
                for item in FailureMemory(root).obstacles(PROBLEM)
                if item["category"] == "BOUNDARY"
            )
            obstacle_id = failure["obstacle_id"]
            assert failure["step"]["line"] == 2
            assert "F(b)-F(a)" in (root / failure["step"]["file"]).read_text(encoding="utf-8")
        if mode == ReasoningMode.METHOD_INNOVATION:
            assert receipt.task.skill_inputs["obstruction"]["obstacle_id"] == obstacle_id
            assert receipt.task.skill_inputs["obstruction"]["attempted_methods"]
            assert receipt.result.skill_output.replacement_method == "weighted trace estimate"

    assert len({receipt.task.skill_instructions for receipt in receipts}) == 7
    assert len({receipt.result.artifacts[0].text for receipt in receipts}) == 7
    memory = FailureMemory(root)
    rows = memory.attempts(PROBLEM)
    assert len(rows) == 7
    assert not rows[4]["progress"], "a failed witness search is no mathematical progress"
    assert rows[4]["computation_artifacts"][0].endswith("sample.txt")
    reduction = next(item for item in rows[2]["progress"] if item["kind"] == "CANDIDATE_REDUCTION")
    graph = runner.memory.research.graph()
    assert reduction["subject_id"] in graph.candidate_routes(PROBLEM)
    assert graph.dependency_alternatives(PROBLEM)
    evidence = graph.node(reduction["evidence_id"]).evidence
    assert evidence.assertion_id == reduction["subject_id"] and evidence.source_type == "MODEL"
    assert not evidence.properties
    assert runner.memory.restore(PROBLEM).workspace_matches
    assert (
        memory.attempts(PROBLEM, failures=True, obstacle_id=obstacle_id)[0]["reasoning_mode"]
        == PROOF
    )


def test_exact_obstacle_repetition_keeps_versions_and_different_scope_is_only_candidate(runner):
    adapter = MockModelAdapter()
    first = runner.run(PROBLEM, adapter, mode=PROOF)
    second = ResearchOrchestrator(runner.root).run(PROBLEM, adapter, mode=PROOF)

    def distinct(task):
        result = mock_skill_result(task)
        data = result.model_dump(mode="json")
        data["new_obstacles"][0]["excluded_scope"]["restriction"] = "with a periodic trace"
        return ReasoningResult.model_validate(data)

    third = runner.run(PROBLEM, MockModelAdapter(distinct), mode=PROOF)
    assert all(job.state == "COMPLETED" for job in (first, second, third))
    memory = FailureMemory(runner.root)
    records = [item for item in memory.obstacles(PROBLEM) if item["category"] == "BOUNDARY"]
    assert len(records) == 2
    old = next(item for item in records if item["version"] == 2)
    assert old["attempts"] == [first.attempt_id, second.attempt_id]
    assert records[0]["excluded_scope"] != records[1]["excluded_scope"]
    assert memory.search(PROBLEM, old["obstacle_id"])[0]["match"] == "EXACT_ID"
    similar = memory.search(PROBLEM, "trace term")
    assert len(similar) == 2 and {item["match"] for item in similar} == {"CANDIDATE"}
    rows = memory.attempts(PROBLEM)
    assert {item["kind"] for item in rows[1]["progress"]} == {"TEXT_REPEAT"}
    assert not rows[1]["new_obstacles"]
    assert rows[1]["encountered_obstacles"] == [old["obstacle_id"]]
    assert "OBSTACLE_DISCOVERED" in {item["kind"] for item in rows[2]["progress"]}
    advice = recommendations(ObstructionCategory.BOUNDARY)
    assert any(item.mode == ReasoningMode.METHOD_INNOVATION for item in advice)
    assert "trace lemma" in advice[0].action


def test_duplicate_reduction_reuses_the_existing_route(runner):
    mode = ReasoningMode.PROOF_ROUTE_EXPLORATION
    for _ in range(2):
        assert runner.run(PROBLEM, MockModelAdapter(), mode=mode).state == "COMPLETED"
    graph = runner.memory.research.graph()
    assert len(graph.candidate_routes(PROBLEM)) == 1
    rows = FailureMemory(runner.root).attempts(PROBLEM)
    assert {event["kind"] for event in rows[0]["progress"]} == {"CANDIDATE_REDUCTION"}
    assert {event["kind"] for event in rows[1]["progress"]} == {"TEXT_REPEAT"}


@pytest.mark.parametrize(
    "error", ["missing_source", "false_locator", "missing_evidence", "wrong_mode"]
)
def test_invalid_skill_output_is_not_published(runner, error):
    def invalid(task):
        data = mock_skill_result(task).model_dump(mode="json")
        obstruction = data["new_obstacles"][0]
        if error == "missing_source":
            obstruction["step"]["file"] = "absent.md"
        elif error == "false_locator":
            obstruction["step"]["line"] = 999
        elif error == "missing_evidence":
            obstruction["evidence_refs"] = ["MissingEvidence"]
        else:
            data["skill_output"] = None
        return ReasoningResult.model_validate(data)

    job = runner.run(PROBLEM, MockModelAdapter(invalid), mode=PROOF)
    assert job.state == "FAILED" and job.detail == "INVALID_RESULT"
    assert not any(
        item["category"] == "BOUNDARY" for item in FailureMemory(runner.root).obstacles(PROBLEM)
    )


def _independent(runner, assertion, evidence_id, scope):
    store = runner.memory.research
    graph = store.graph()
    source = f"research/{evidence_id}.txt"
    (runner.root / source).write_text("Independent verification fixture.", encoding="utf-8")
    store.register_evidence(
        EvidenceRecord(
            evidence_id=evidence_id,
            source_type="HUMAN",
            file_path=source,
            run=RunInfo(tool="fixture", run_id="ReviewFixture"),
            check_scope=scope,
            check_result="PASS",
            coverage="FULL_ASSERTION",
            assertion_id=assertion,
            assertion_revision=graph.review(assertion).revision,
            direction="SUPPORT",
            properties=("HUMAN_CONFIRMED",),
        )
    )


def test_obstruction_resolution_requires_scoped_evidence_and_rechecks_graph(runner):
    assert runner.run(PROBLEM, MockModelAdapter(), mode=PROOF).state == "COMPLETED"
    memory = FailureMemory(runner.root)
    obstacle = next(item for item in memory.obstacles(PROBLEM) if item["category"] == "BOUNDARY")
    identifier = obstacle["obstacle_id"]
    graph = runner.memory.research.graph()
    assert identifier in graph.open_blockers("A")
    with pytest.raises(ResearchError, match="independent"):
        memory.resolve(identifier, obstacle["evidence_refs"][0])
    _independent(runner, "A", "ReviewBoundary", f"obstruction:{identifier}")
    assert "A" in memory.resolve(identifier, "ReviewBoundary")["affected"]
    assert memory.obstacle(identifier)["status"] == "RESOLVED"
    assert identifier not in runner.memory.research.graph().open_blockers("A")
    assert runner.memory.restore(PROBLEM).workspace_matches is False


def test_progress_obligation_requires_independent_evidence_and_is_recorded_once(runner):
    contract = runner.memory.research.load_contract(PROBLEM)
    criterion = contract.acceptance_criteria[0].id
    _independent(runner, PROBLEM, "ReviewCriterion", f"criterion:{criterion}")
    runner.memory.create_checkpoint(
        PROBLEM, {"active_route": "A", "next_research_action": "Review"}
    )

    def respond(task):
        data = mock_skill_result(task).model_dump(mode="json")
        data["skill_output"]["obligation_evidence"] = [
            {"criterion_id": criterion, "evidence_id": "ReviewCriterion"}
        ]
        return ReasoningResult.model_validate(data)

    for _ in range(2):
        assert runner.run(PROBLEM, MockModelAdapter(respond), mode=PROOF).state == "COMPLETED"
    progress = [
        item
        for row in FailureMemory(runner.root).attempts(PROBLEM)
        for item in row["progress"]
        if item["kind"] == "OBLIGATION_SATISFIED"
    ]
    assert len(progress) == 1 and progress[0]["evidence_id"] == "ReviewCriterion"


def test_worker_recovery_after_skill_publication_is_idempotent(runner):
    script = """
import os
from pathlib import Path
from amre.execution.adapters import MockModelAdapter
from amre.execution.orchestrator import ResearchOrchestrator
from amre.execution.skill_models import ReasoningMode

class Interrupted(ResearchOrchestrator):
    def _record_outputs(self, *args):
        os._exit(76)

Interrupted(Path.cwd()).run("P-T", MockModelAdapter(), mode=ReasoningMode.PROOF_CONSTRUCTION)
"""
    child = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runner.root,
        capture_output=True,
        timeout=35,
        check=False,
    )
    assert child.returncode == 76, child.stderr
    restarted = ResearchOrchestrator(runner.root)
    job = restarted.run(PROBLEM, None, resume=True)
    assert job.state == "COMPLETED"
    assert len(restarted.queue.read().reservations) == 1
    memory = FailureMemory(runner.root)
    records = [item for item in memory.obstacles(PROBLEM) if item["category"] == "BOUNDARY"]
    assert len(records) == 1 and records[0]["attempts"] == [job.attempt_id]
    assert len(memory.attempts(PROBLEM)) == 1


def test_legacy_receipt_and_obstruction_read_without_migration(runner):
    job = runner.run(PROBLEM, MockModelAdapter())
    receipt = _receipt(runner.root, job)
    legacy = receipt.model_dump(mode="json")
    for name in (
        "reasoning_mode",
        "skill_version",
        "skill_inputs",
        "skill_instructions",
        "obstacle_id",
    ):
        del legacy["task"][name]
    for name in ("skill_output", "new_obstacles", "computation_artifacts"):
        del legacy["result"][name]
    restored = AttemptReceipt.model_validate(legacy)
    assert restored.task.reasoning_mode is None and restored.result.artifacts
    assert any(item.get("legacy") for item in FailureMemory(runner.root).obstacles(PROBLEM))
