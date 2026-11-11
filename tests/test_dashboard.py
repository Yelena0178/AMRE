import shutil
from pathlib import Path

import pytest

from amre.dashboard.commands import build_dashboard
from amre.dashboard.export import load_public_selection, snapshot
from amre.dashboard.models import PublicSelection, ResearchSnapshot
from amre.execution.adapters import AdapterOutput, MockModelAdapter
from amre.execution.deep import DeepResearchRunner
from amre.execution.models import Failure
from amre.research.memory import ResearchMemory
from amre.research.models import EvidenceRecord, RunInfo
from amre.scheduling.service import PortfolioScheduler

POLICY = Path(__file__).resolve().parents[1] / "examples" / "and_or" / "dashboard-public.yaml"


def test_public_default_is_empty_and_explicit_policy_preserves_and_or_graph(example_store):
    root = example_store.root
    empty = snapshot(root, load_public_selection(root))
    assert not empty.problems and not empty.nodes
    selection = load_public_selection(root, POLICY)
    public = snapshot(root, selection)
    assert public == ResearchSnapshot.model_validate_json(public.model_dump_json())
    assert public.programs[0].problems == ("P-T",)
    assert {edge.relation for edge in public.edges} >= {"REQUIRES_ALL", "ALTERNATIVE_ROUTE"}
    assert {node.id for node in public.nodes if node.statement} >= {"P-T", "A", "B", "L1"}
    assert "H-Real" in next(node for node in public.nodes if node.id == "P-T").assumptions


def test_private_details_paths_and_raw_source_content_are_filtered(example_store):
    root = example_store.root
    secret = "sk-AMRE-SYNTHETIC-MARKER-DO-NOT-EXPORT"
    path = r"C:\Users\PrivateResearch\.env"
    contract = example_store.update(
        "P-T",
        {"definitions": [{"id": "Sensitive", "text": f"api_key={secret} at {path}"}]},
    )
    source = "research/restricted-original.txt"
    (root / source).write_text("Restricted text of a fake paper. " + secret, encoding="utf-8")
    example_store.register_evidence(
        EvidenceRecord(
            evidence_id="Restricted-Source",
            assertion_id="P-T",
            assertion_revision=2,
            source_type="LITERATURE",
            file_path=source,
            run=RunInfo(tool="fixture", run_id="PrivateRun"),
            check_scope=path,
            check_result="INCONCLUSIVE",
            coverage="PARTIAL",
            direction="SUPPORT",
        )
    )
    memory = ResearchMemory(root)
    memory.create_checkpoint(
        "P-T",
        {
            "active_route": "A",
            "next_research_action": f"Review {path}",
            "unresolved_proof_obligations": ["A public obligation", secret],
        },
    )
    policy = PublicSelection(
        problems=("P-T",),
        detail_ids=("P-T",),
        statement_ids=("P-T",),
        private_artifacts=(source, contract.statement.path),
    )
    data = snapshot(root, policy)
    assert data.omitted_details == 3
    assert not data.problems[0].definitions and data.problems[0].next_action is None
    assert data.problems[0].obligations == ("A public obligation",)
    assert next(item for item in data.nodes if item.id == "P-T").statement is None
    assert next(item for item in data.artifacts if item.artifact_id == "Restricted-Source").private
    raw = data.model_dump_json()
    assert all(
        value not in raw for value in (secret, ".env", source, "Restricted text", "PrivateRun")
    )


def test_static_build_contains_only_approved_snapshot(example_store):
    frontend = POLICY.parents[2] / "dashboard"
    if not shutil.which("node") or not (frontend / "node_modules" / "vite").is_dir():
        pytest.skip("Install dashboard dependencies to validate the static build")
    root = example_store.root
    marker = "Synthetic unpublishable original AMRE-PRIVATE-FIXTURE"
    (root / "research" / "unpublished.tex").write_text(marker, encoding="utf-8")
    public = snapshot(root, load_public_selection(root, POLICY))
    output = root / "site"
    raw = public.model_dump_json(indent=2) + "\n"
    build_dashboard(raw, frontend, output)
    assert (output / "research-state.json").read_text(encoding="utf-8") == raw
    for path in output.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert marker.encode() not in content and b"unpublished.tex" not in content


def test_snapshot_tracks_real_session_checkpoint_route_and_unknown_budget(example_store):
    root = example_store.root
    example_store.update("P-T", {"obstacles": []})
    scheduler = PortfolioScheduler(root)
    decision, dispatch = scheduler.run("P-T", None, 19)[0]
    assert dispatch.state == "COMPLETED"
    exported = snapshot(root, load_public_selection(root, POLICY))
    assert exported.sessions[0].elapsed_ms > 0
    assert exported.sessions[0].semantic_events > 1
    assert exported.sessions[0].route_id == decision.dispatch.route_id
    assert exported.checkpoints[-1].dispatch_phase == "AFTER_ATTEMPT"
    assert exported.decisions[0].decision_id == decision.decision_id
    assert any(item.kind == "ROUTE_SWITCHED" for item in exported.events)

    class Lost(MockModelAdapter):
        def run_deep(self, task, **kwargs):
            return AdapterOutput(failure=Failure.INTERRUPTED)

    runner = DeepResearchRunner(root)
    paused = runner.start("P-T", Lost())
    assert paused.state == "PAUSED"
    exported = snapshot(root, load_public_selection(root, POLICY))
    assert exported.budget.tokens == "UNKNOWN" and exported.budget.cost_microusd == "UNKNOWN"
    assert exported.sessions[-1].checkpoint == exported.checkpoints[-1].checkpoint_id
    assert exported.attempts[-1].state == exported.sessions[-1].state == "PAUSED"
