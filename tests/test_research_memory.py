import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from amre.errors import ResearchError
from amre.research import storage
from amre.research.context import build_context
from amre.research.graph import GraphDocument, GraphEdge, GraphNode
from amre.research.index import rebuild_index
from amre.research.journal import checkpoint_path, event_path, replay
from amre.research.memory import ResearchMemory
from amre.research.memory_models import EventKind
from amre.research.models import EvidenceRecord, StatementRef

_PROBLEM = "P-Cold"
_STATEMENTS = {
    _PROBLEM: r"\[\forall x\in\mathbb{R},\; (x+1)^2\geq 0.\]",
    "L1": r"\[x\in\mathbb{R}\implies x+1\in\mathbb{R}.\]",
    "L2": r"\[\forall y\in\mathbb{R},\; y^2\geq 0.\]",
    "Route": r"\[(L_1\land L_2)\implies P.\]",
}
_NEXT = "Compare the two lemmas and resolve the boundary case."


def _evidence(identifier: str, subject: str, path: str, *, human: bool = False) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=identifier,
        assertion_id=subject,
        assertion_revision=1,
        file_path=path,
        source_type="HUMAN" if human else "MODEL",
        properties=("HUMAN_CONFIRMED",) if human else (),
        direction="SUPPORT",
        coverage="FULL_ASSERTION",
        check_scope="Synthetic fixture only",
        check_result="PASS" if human else "INCONCLUSIVE",
        run={"tool": "fixture", "run_id": "memory-test"},
    )


@pytest.fixture
def memory(tmp_path: Path) -> ResearchMemory:
    memory = ResearchMemory(tmp_path)
    route_ref = StatementRef.from_text(_STATEMENTS["Route"])
    route_evidence = _evidence("E-Route", "Route", route_ref.path)
    graph = GraphDocument(
        nodes=(
            GraphNode(
                id="L1",
                kind="LEMMA",
                label="Closure of real numbers",
                statement=StatementRef.from_text(_STATEMENTS["L1"]),
            ),
            GraphNode(
                id="L2",
                kind="LEMMA",
                label="Nonnegative real squares",
                statement=StatementRef.from_text(_STATEMENTS["L2"]),
            ),
            GraphNode(
                id="Route", kind="PROOF_ROUTE", label="Compose the lemmas", statement=route_ref
            ),
            GraphNode(
                id="E-Route", kind="EVIDENCE", label="Candidate reduction", evidence=route_evidence
            ),
            GraphNode(id="O-Boundary", kind="OBSTACLE", label="Boundary case is unresolved"),
            GraphNode(
                id="H-Real", kind="ASSUMPTION", label="x is real", assumption_type="MATHEMATICAL"
            ),
            GraphNode(
                id="Ref",
                kind="LITERATURE_REFERENCE",
                label="Synthetic reference",
                citation="Synthetic bibliography item for the memory test",
            ),
        ),
        edges=(
            GraphEdge(source="Route", target="L1", relation="REQUIRES_ALL"),
            GraphEdge(source="Route", target="L2", relation="REQUIRES_ALL"),
            GraphEdge(source="Route", target="E-Route", relation="SUPPORTED_BY"),
            GraphEdge(source="L1", target="Ref", relation="CITES"),
        ),
    )
    memory.research.create(
        {
            "problem_id": _PROBLEM,
            "assumptions": ["H-Real"],
            "research_routes": ["Route"],
            "obstacles": ["O-Boundary"],
            "definitions": [{"id": "D-Shift", "text": r"$y=x+1$."}],
            "acceptance_criteria": [{"id": "Criterion", "description": "Cover the boundary case."}],
            "budget": {"max_tokens": 3000, "timeout_seconds": 60},
            "provenance": {"author": "fixture", "source": "Synthetic research data"},
        },
        _STATEMENTS[_PROBLEM],
        graph=graph,
        statements=(_STATEMENTS["L1"], _STATEMENTS["L2"], _STATEMENTS["Route"]),
    )
    artifact = tmp_path / "research" / "synthetic-review.bin"
    artifact.write_bytes(b"\x00\xffsynthetic record")
    memory.research.register_evidence(
        _evidence("E-L1", "L1", "research/synthetic-review.bin", human=True)
    )
    return memory


def _notes() -> dict[str, object]:
    return {
        "next_research_action": _NEXT,
        "strategy_version": "test-v1",
        "active_route": "Route",
        "usage": {"tokens_used": 42, "elapsed_seconds": 4},
        "established_results": [
            {
                "assertion_id": "L1",
                "summary": "Fixture-confirmed closure.",
                "evidence_references": ["E-L1"],
            },
        ],
        "candidate_results": [
            {"assertion_id": "L2", "summary": "Candidate derivation: the square."}
        ],
    }


def _record_attempt(memory: ResearchMemory) -> None:
    memory.append_event({"kind": "ATTEMPT_STARTED", "problem_id": _PROBLEM, "target_id": "Run-1"})
    memory.append_event(
        {
            "kind": "ATTEMPT_COMPLETED",
            "problem_id": _PROBLEM,
            "target_id": "Run-1",
            "outcome": "FAILED",
            "summary": "The attempted sign argument missed the boundary.",
        }
    )


def _cli(root: Path, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "amre", *args],
        cwd=root,
        env={**os.environ, "AMRE_WORKSPACE_ROOT": str(root), "PYTHONUTF8": "1"},
        capture_output=True,
        encoding="utf-8",
        timeout=20,
        check=True,
    )
    return result.stdout


def test_cold_restart_across_independent_processes(memory: ResearchMemory) -> None:
    _record_attempt(memory)
    root = memory.root
    notes = root / "notes.yaml"
    notes.write_text(yaml.safe_dump(_notes()), encoding="utf-8")
    checkpoint_id = _cli(root, "checkpoint", "create", _PROBLEM, str(notes)).strip()
    shown = yaml.safe_load(
        _cli(root, "checkpoint", "show", _PROBLEM, "--checkpoint", checkpoint_id)
    )
    del memory

    result = json.loads(_cli(root, "checkpoint", "restore", _PROBLEM))
    checkpoint = result["checkpoint"]
    assert checkpoint == shown
    assert result["workspace_matches"]
    assert result["statement"] == _STATEMENTS[_PROBLEM]
    assert checkpoint["active_route"] == "Route"
    assert checkpoint["active_obstacles"] == ["O-Boundary"]
    assert checkpoint["unresolved_proof_obligations"] == ["Cover the boundary case."]
    assert checkpoint["last_attempt"]["outcome"] == "FAILED"
    assert checkpoint["budget_snapshot"]["tokens_used"] == 42
    assert checkpoint["next_research_action"] == _NEXT
    assert checkpoint["strategy_version"] == "test-v1"
    assert {"E-L1", "E-Route"} == set(checkpoint["evidence_references"])
    assert {"L1", "L2"} <= {node["id"] for node in checkpoint["graph"]["nodes"]}
    context = _cli(root, "context", "build", _PROBLEM, "--max-bytes", "12000")
    assert all(
        text in context
        for text in (
            _STATEMENTS["L1"],
            _STATEMENTS["L2"],
            "O-Boundary",
            "E-L1",
            _NEXT,
        )
    )
    assert not (root / ".amre" / "research-index.sqlite3").exists()


def test_event_replay_preserves_committed_records(memory: ResearchMemory) -> None:
    for kind, target in (
        ("CLAIM_PROPOSED", "L2"),
        ("OBSTACLE_DISCOVERED", "O-Boundary"),
        ("ROUTE_SWITCHED", "Route"),
    ):
        memory.append_event({"kind": kind, "problem_id": _PROBLEM, "target_id": target})
    _record_attempt(memory)
    memory.append_event({"kind": "PROBLEM_PAUSED", "problem_id": _PROBLEM})
    before = memory.inventory().events
    contents = [(memory.root / event_path(item.event_id)).read_bytes() for item in before]
    checkpoint = memory.create_checkpoint(_PROBLEM, {"next_research_action": _NEXT})

    inventory = ResearchMemory(memory.root).inventory()
    assert set(item.data.kind for item in inventory.events) == set(EventKind)
    assert inventory.events[:-1] == before
    assert [item.sequence for item in inventory.events] == list(range(1, len(before) + 2))
    assert [(memory.root / event_path(item.event_id)).read_bytes() for item in before] == contents
    state = replay(inventory.events)[_PROBLEM]
    assert state.paused and state.latest_checkpoint == checkpoint.checkpoint_id
    assert state.active_route == "Route"
    assert checkpoint.research_status == "PAUSED"
    assert len(state.failed_attempts) == 1
    with pytest.raises(ResearchError, match="No matching active attempt"):
        memory.append_event(
            {
                "kind": "ATTEMPT_COMPLETED",
                "problem_id": _PROBLEM,
                "target_id": "Run-1",
                "outcome": "FAILED",
                "summary": "Repeated completion",
            }
        )
    assert memory.inventory().events == inventory.events


def test_crash_before_manifest_keeps_previous_checkpoint(
    memory: ResearchMemory, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = memory.create_checkpoint(_PROBLEM, _notes())
    previous = memory.research.manifest.read_bytes()
    replace = storage.os.replace

    def fail_manifest(source: Path, target: Path) -> None:
        if target == memory.research.manifest:
            raise OSError(5, "simulated interruption")
        replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_manifest)
        with pytest.raises(ResearchError, match="simulated interruption"):
            memory.create_checkpoint(_PROBLEM, {"next_research_action": "Uncommitted next step"})
    assert memory.research.manifest.read_bytes() == previous
    restarted = ResearchMemory(memory.root)
    assert restarted.restore(_PROBLEM).checkpoint == first
    assert restarted.inventory().checkpoints == (first,)
    second = restarted.create_checkpoint(_PROBLEM, {"next_research_action": "Retry from files"})
    assert restarted.inventory().checkpoints == (first, second)
    assert second.event_sequence == first.event_sequence + 1


def test_abrupt_process_exit_releases_lock_and_keeps_checkpoint(memory: ResearchMemory) -> None:
    first = memory.create_checkpoint(_PROBLEM, _notes())
    script = """
import os
import sys
from pathlib import Path

from amre.research import storage
from amre.research.memory import ResearchMemory

memory = ResearchMemory(Path.cwd())
replace = storage.os.replace

def interrupt(source, target):
    if target == memory.research.manifest:
        os._exit(73)
    replace(source, target)

storage.os.replace = interrupt
memory.create_checkpoint(sys.argv[1], {"next_research_action": "Interrupted"})
"""
    result = subprocess.run(
        [sys.executable, "-c", script, _PROBLEM],
        cwd=memory.root,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 73, result.stderr
    restarted = ResearchMemory(memory.root)
    assert restarted.restore(_PROBLEM).checkpoint == first
    second = restarted.create_checkpoint(_PROBLEM, {"next_research_action": "Resumed"})
    assert restarted.inventory().checkpoints == (first, second)


@pytest.mark.parametrize("missing", ["event", "checkpoint", "blob"])
def test_missing_archive_files_are_detected(memory: ResearchMemory, missing: str) -> None:
    checkpoint = memory.create_checkpoint(_PROBLEM, _notes())
    inventory = memory.inventory()
    paths = {
        "event": event_path(inventory.events[-1].event_id),
        "checkpoint": checkpoint_path(checkpoint.checkpoint_id),
        "blob": checkpoint.artifacts[0].blob_path,
    }
    (memory.root / paths[missing]).unlink()
    with pytest.raises(ResearchError, match="not found|Missing or unreadable"):
        rebuild_index(memory)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 99, "schema_version"),
        ("event_sequence", 100, "event metadata differ"),
        ("strategy_version", "changed", "archived snapshot"),
    ],
)
def test_invalid_checkpoint_versions_and_metadata(
    memory: ResearchMemory, field: str, value: str | int, message: str
) -> None:
    checkpoint = memory.create_checkpoint(_PROBLEM, _notes())
    path = memory.root / checkpoint_path(checkpoint.checkpoint_id)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data[field] = value
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ResearchError, match=message):
        ResearchMemory(memory.root).restore(_PROBLEM)
    with pytest.raises(ResearchError, match=message):
        rebuild_index(memory)


def test_historical_restore_isolated_from_live_changes(memory: ResearchMemory) -> None:
    checkpoint = memory.create_checkpoint(_PROBLEM, _notes())
    memory.research.update(_PROBLEM, {}, statement=r"\[x=x.\]")
    old_statement = memory.root / checkpoint.contract.statement.path
    old_statement.unlink()
    original_evidence = memory.root / "research" / "synthetic-review.bin"
    original_evidence.unlink()
    restored = ResearchMemory(memory.root).restore(_PROBLEM)
    assert not restored.workspace_matches
    assert restored.read_statement(checkpoint.contract.statement) == _STATEMENTS[_PROBLEM]
    assert restored.graph.evidence_for("L1")[0].evidence_id == "E-L1"
    assert restored.read_artifact("research/synthetic-review.bin") == b"\x00\xffsynthetic record"


def test_index_rebuilt_after_corruption(memory: ResearchMemory) -> None:
    checkpoint = memory.create_checkpoint(_PROBLEM, _notes())
    index = memory.root / ".amre" / "research-index.sqlite3"
    index.write_bytes(b"not a database")
    result = json.loads(_cli(memory.root, "memory", "rebuild"))
    assert result["index_present"]
    assert result == json.loads(_cli(memory.root, "memory", "inspect"))
    with closing(sqlite3.connect(index)) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone() == (checkpoint.event_sequence,)
        assert db.execute("SELECT active_route, latest_checkpoint FROM problems").fetchone() == (
            "Route",
            checkpoint.checkpoint_id,
        )
    index.unlink()
    assert ResearchMemory(memory.root).restore(_PROBLEM).checkpoint == checkpoint


def test_context_budget_keeps_whole_math_and_omits_unrelated_files(memory: ResearchMemory) -> None:
    _record_attempt(memory)
    (memory.root / "unrelated.txt").write_text("PRIVATE-UNRELATED", encoding="utf-8")
    derivation = r"\[\text{推导：}y^2\ge 0.\]"
    (memory.root / "derivation.tex").write_text(derivation, encoding="utf-8")
    (memory.root / "long-literature.txt").write_text("OVERSIZED" * 4000, encoding="utf-8")
    notes = _notes()
    notes["context_sources"] = [
        {"kind": "LITERATURE", "path": "long-literature.txt"},
        {"kind": "DERIVATION", "path": "derivation.tex"},
    ]
    memory.create_checkpoint(_PROBLEM, notes)
    restored = memory.restore(_PROBLEM)
    context = build_context(restored, max_bytes=4000)
    assert context.size_bytes == len(context.text.encode("utf-8")) <= 4000
    assert "L1" in context.text and "Route REQUIRES_ALL L2" in context.text
    assert all(text in context.text for text in (*_STATEMENTS.values(), derivation, "E-L1", _NEXT))
    assert "OVERSIZED" not in context.text and "PRIVATE-UNRELATED" not in context.text
    assert "LITERATURE:long-literature.txt" in context.omitted
    assert context.text.index("Failed attempt") < context.text.index(derivation)
    with pytest.raises(ResearchError, match="Required context needs"):
        build_context(restored, max_bytes=30)
