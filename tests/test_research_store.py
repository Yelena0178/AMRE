from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from amre.cli import main
from amre.errors import ResearchError
from amre.research import storage
from amre.research.graph import GraphDocument, GraphEdge, GraphNode
from amre.research.models import ClaimStatus, EvidenceRecord, ReviewStatus, StatementRef
from amre.research.storage import read_yaml
from amre.research.store import ResearchStore

_LATEX = r"\[x=x.\]"


@pytest.fixture
def metadata(fixture_dir: Path) -> dict[str, object]:
    data = read_yaml(fixture_dir / "research" / "contract.yaml")
    assert data is not None
    return data


def test_cli_crud_and_round_trip(
    tmp_path: Path,
    fixture_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    metadata_file = fixture_dir / "research" / "contract.yaml"
    latex = tmp_path / "input.tex"
    latex.write_text(_LATEX, encoding="utf-8")
    assert main(["problem", "list"]) == 0
    assert not (tmp_path / "research").exists()
    assert main(["problem", "create", str(metadata_file), "--statement", str(latex)]) == 0
    assert main(["problem", "list"]) == 0
    assert "P-Test\tOPEN\tUNPROVED\tCURRENT" in capsys.readouterr().out
    assert main(["problem", "show", "P-Test"]) == 0
    assert _LATEX in capsys.readouterr().out
    assert main(["problem", "validate", "P-Test"]) == 0

    store = ResearchStore(tmp_path)
    before = store.load_contract("P-Test")
    patch = tmp_path / "patch.yaml"
    patch.write_text(
        "claim_status: REFUTATION_CANDIDATE\nresearch_status: IN_PROGRESS\n", encoding="utf-8"
    )
    assert main(["problem", "update", "P-Test", str(patch)]) == 0
    updated = store.load_contract("P-Test")
    assert updated.definitions == before.definitions
    assert updated.provenance == before.provenance
    assert updated.claim_status == ClaimStatus.REFUTATION_CANDIDATE
    assert store.graph().review("P-Test").revision == 1

    latex.write_text(r"\[x+0=x.\]", encoding="utf-8")
    assert main(["problem", "update", "P-Test", "--statement", str(latex)]) == 0
    loaded = ResearchStore(tmp_path)
    assert loaded.load_contract("P-Test").statement != before.statement
    assert loaded.graph().review("P-Test").status == ReviewStatus.NEEDS_RECHECK
    assert loaded.read_statement(before.statement) == _LATEX


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"extra": True}, "Extra inputs"),
        ({"budget": {"max_tokens": 10, "timeout_seconds": 30, "extra": True}}, "budget.extra"),
        ({"budget": {"max_tokens": "10", "timeout_seconds": 30}}, "valid integer"),
        ({"schema_version": 2}, "schema_version"),
        ({"schema_version": True}, "schema_version"),
        ({"dependencies": ["Absent"]}, "Unknown graph node"),
        ({"assumptions": ["Repeat", "repeat"]}, "duplicate assumptions"),
        ({"checkpoint_reference": {"path": "../escape.txt"}}, "workspace-relative"),
        ({"checkpoint_reference": {"path": "research/absent.txt"}}, "artifact not found"),
        ({"problem_id": "Another"}, "Cannot change problem_id"),
    ],
)
def test_invalid_contract_patch_is_atomic(
    tmp_path: Path, metadata: dict[str, object], patch: dict[str, object], message: str
) -> None:
    store = ResearchStore(tmp_path)
    store.create(metadata, _LATEX)
    before = store.manifest.read_bytes()
    with pytest.raises(ResearchError, match=message):
        store.update("P-Test", patch, statement=r"\[2=2.\]")
    assert store.manifest.read_bytes() == before
    assert len(list((tmp_path / "research" / "statements").iterdir())) == 1


def test_duplicate_ids_and_yaml_keys(
    tmp_path: Path,
    metadata: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    store = ResearchStore(tmp_path)
    store.create(metadata, _LATEX)
    with pytest.raises(ResearchError, match="Duplicate graph node ID"):
        store.create({**metadata, "problem_id": "p-test"}, _LATEX)

    patch = tmp_path / "duplicate.yaml"
    patch.write_text("budget:\n  max_tokens: 42\n  max_tokens: 999\n", encoding="utf-8")
    assert main(["problem", "update", "P-Test", str(patch)]) == 2
    assert "Duplicate YAML key" in caplog.text
    assert store.load_contract("P-Test").budget.max_tokens == 1000


def test_contract_owned_route_can_be_created_in_one_transaction(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    store = ResearchStore(tmp_path)
    reduction = r"\[L\implies P.\]"
    reduction_ref = StatementRef.from_text(reduction)
    evidence = EvidenceRecord(
        evidence_id="E-Reduction",
        source_type="MODEL",
        file_path=reduction_ref.path,
        run={"tool": "test", "run_id": "test"},
        check_scope="Reduction",
        check_result="INCONCLUSIVE",
        coverage="FULL_ASSERTION",
        direction="SUPPORT",
        assertion_id="R",
        assertion_revision=1,
    )
    graph = GraphDocument(
        nodes=(
            GraphNode(
                id="L", kind="LEMMA", label="Lemma", statement=StatementRef.from_text(_LATEX)
            ),
            GraphNode(
                id="R",
                kind="PROOF_ROUTE",
                label="Reduction",
                statement=reduction_ref,
            ),
            GraphNode(
                id=evidence.evidence_id, kind="EVIDENCE", label="Candidate", evidence=evidence
            ),
        ),
        edges=(
            GraphEdge(source="R", target="L", relation="REQUIRES_ALL"),
            GraphEdge(source="R", target=evidence.evidence_id, relation="SUPPORTED_BY"),
        ),
    )
    store.create(
        {**metadata, "research_routes": ["R"]}, _LATEX, graph=graph, statements=(reduction,)
    )
    persisted = ResearchStore(tmp_path).graph()
    assert persisted.candidate_routes("P-Test") == ("R",)
    assert persisted.dependency_alternatives("P-Test") == (("L",),)
    assert not persisted.has_supported_route("P-Test")


def test_tex_edits_outside_store_detected(tmp_path: Path, metadata: dict[str, object]) -> None:
    store = ResearchStore(tmp_path)
    contract = store.create(metadata, _LATEX)
    (tmp_path / contract.statement.path).write_text(r"\[0=1.\]", encoding="utf-8")
    with pytest.raises(ResearchError, match="digest mismatch"):
        store.read()


def test_manifest_failure_leaves_previous_snapshot(
    tmp_path: Path, metadata: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path)
    original = store.create(metadata, _LATEX)
    replace = storage.os.replace

    def fail_manifest(source: Path, target: Path) -> None:
        if target == store.manifest:
            raise OSError(5, "simulated publication failure")
        replace(source, target)

    monkeypatch.setattr(storage.os, "replace", fail_manifest)
    with pytest.raises(ResearchError, match="publication failure"):
        store.update("P-Test", {}, statement=r"\[x=x+0.\]")
    assert store.load_contract("P-Test") == original
    assert not list((tmp_path / "research").rglob("*.tmp"))


def test_independent_writers_merge_against_latest_contract(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    store = ResearchStore(tmp_path)
    store.create(metadata, _LATEX)
    with ThreadPoolExecutor(max_workers=2) as pool:
        status = pool.submit(
            ResearchStore(tmp_path).update, "P-Test", {"research_status": "CLOSED"}
        )
        claim = pool.submit(ResearchStore(tmp_path).update, "P-Test", {"claim_status": "PARTIAL"})
        status.result()
        claim.result()
    updated = store.load_contract("P-Test")
    assert updated.research_status == "CLOSED"
    assert updated.claim_status == "PARTIAL"


def test_model_cannot_declare_formal_or_human_evidence() -> None:
    for prop in ("FORMALLY_VERIFIED", "HUMAN_CONFIRMED", "COMPUTATION_TESTED"):
        with pytest.raises(ValidationError, match="incompatible with source type"):
            EvidenceRecord(
                evidence_id="E-Model",
                source_type="MODEL",
                file_path="proof.tex",
                run={"tool": "test", "run_id": "test"},
                check_scope="Theorem",
                check_result="PASS",
                coverage="FULL_ASSERTION",
                direction="SUPPORT",
                assertion_id="T",
                assertion_revision=1,
                properties=(prop,),
            )


def test_register_candidate_evidence_for_contract(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    store = ResearchStore(tmp_path)
    contract = store.create(metadata, _LATEX)
    record = EvidenceRecord(
        evidence_id="E-Model",
        source_type="MODEL",
        file_path=contract.statement.path,
        run={"tool": "test", "run_id": "test"},
        check_scope="Proposed proof",
        check_result="INCONCLUSIVE",
        coverage="PARTIAL",
        direction="SUPPORT",
        assertion_id=contract.problem_id,
        assertion_revision=1,
    )
    store.register_evidence(record)
    document, graph = ResearchStore(tmp_path).snapshot()
    assert document.contract(contract.problem_id).evidence_references == (record.evidence_id,)
    assert graph.evidence_for(contract.problem_id) == (record,)
    assert graph.claim_status(contract.problem_id) == ClaimStatus.UNPROVED
    with pytest.raises(ResearchError, match="duplicate evidence_references"):
        store.register_evidence(record)
