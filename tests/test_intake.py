import json

import pytest

from amre.cli import main
from amre.errors import ResearchError
from amre.execution.adapters import AdapterOutput
from amre.execution.models import AdapterName, Usage
from amre.execution.queue import TaskQueue
from amre.intake.models import DraftContent, IntakeLimits
from amre.intake.service import IntakeStore
from amre.research.storage import read_yaml


@pytest.fixture
def intake(tmp_path):
    store = IntakeStore(tmp_path / "draft", workspace=tmp_path)
    material = tmp_path / "old-proof.tex"
    material.write_bytes(b"% Earlier proof outline\r\np^2=2q^2; hence p is even.\r\n")
    sources = store.import_materials([material])
    request = store.prepare("Prove that sqrt(2) is irrational.", IntakeLimits())
    return store, material, sources[0], request


def content(material, request):
    user = next(s for s in request.sources if s.source_id == request.instruction_source)
    return {
        "goal": {
            "id": "Goal",
            "text": r"\sqrt{2}\notin\mathbb{Q}",
            "origin": "user",
            "citations": [
                {
                    "source_id": user.source_id,
                    "start_line": 1,
                    "end_line": user.line_count,
                    "quote": "Prove that sqrt(2) is irrational.",
                }
            ],
        },
        "assumptions": [],
        "definitions": [],
        "dependencies": [],
        "existing_results": [
            {
                "id": "OldProof",
                "text": "The old outline asserts the parity step.",
                "origin": "material",
                "citations": [
                    {
                        "source_id": material.source_id,
                        "start_line": 2,
                        "end_line": 2,
                        "quote": "p^2=2q^2; hence p is even.",
                    }
                ],
                "note": "Unverified prior argument, not a verified theorem.",
            }
        ],
        "gaps": [
            {
                "id": "Parity",
                "text": "Justify the parity implication.",
                "origin": "model_suggestion",
                "note": "The outline omits this explanation.",
            }
        ],
        "acceptance_criteria": [
            {
                "id": "Criterion",
                "text": "Write a complete proof.",
                "origin": "model_suggestion",
                "note": "Proposed criterion.",
            }
        ],
        "questions": [],
    }


def response_file(store, data, name="response.json"):
    file = store.root / name
    file.write_text(json.dumps(data), encoding="utf-8")
    return file


def test_archive_versions_exact_bytes_and_source_identity(intake):
    store, material, source, request = intake
    original = material.read_bytes()
    assert store.import_materials([material]) == (source,)
    assert len(store.manifest().sources) == 2
    material.write_text("Changed proof", encoding="utf-8")
    changed = store.import_materials([material])[0]
    assert changed.source_id != source.source_id
    material.unlink()
    assert store.text(source).splitlines()[1] == "p^2=2q^2; hence p is even."
    assert store.path(f"sources/{source.sha256}.bin").read_bytes() == original
    # Prepared requests freeze the earlier material selection.
    assert changed not in store.request(request.request_id).sources


def test_full_draft_and_revision_preserve_parent_and_do_not_write_research(intake):
    store, _, material, request = intake
    first = content(material, request)
    path = store.accept(
        request.request_id, response_file(store, first), model="declared-test-model"
    )
    originals = {file.name: file.read_bytes() for file in path.iterdir()}
    saved = store.load_version()
    assert saved["content"]["status"] == "DRAFT_UNVERIFIED"
    assert saved["producer"]["identity"] == "user_declared_not_authenticated"
    revision = store.prepare(
        "Keep the goal, but add an explicit parity lemma obligation.", IntakeLimits()
    )
    second = json.loads(json.dumps(first))
    second["gaps"][0]["text"] = "Prove: if an integer square is even, the integer is even."
    second["gaps"][0]["origin"] = "user"
    second["gaps"][0]["citations"] = [
        {
            "source_id": revision.instruction_source,
            "start_line": 1,
            "end_line": 1,
            "quote": "Keep the goal, but add an explicit parity lemma obligation.",
        }
    ]
    output = store.accept(
        revision.request_id, response_file(store, second), model="same-test-model"
    )
    current = store.load_version()
    assert current["version"] == 2 and current["parent_version"] == 1
    assert current["changes"][0]["id"] == "Parity"
    assert current["changes"][0]["before"]["origin"] == "model_suggestion"
    assert current["changes"][0]["after"]["origin"] == "user"
    assert {f.name for f in output.iterdir()} == {"draft.yaml", "statement.tex", "review.md"}
    assert all((path / name).read_bytes() == data for name, data in originals.items())
    assert not (store.workspace / "research").exists()
    assert not (store.workspace / ".amre").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_source",
        "wrong_quote",
        "line_range",
        "false_origin",
        "missing_citation",
        "verified",
        "graph",
    ],
)
def test_reject_untraceable_or_out_of_scope_output_without_publication(intake, mutation):
    store, _, material, request = intake
    draft = content(material, request)
    citation = draft["existing_results"][0]["citations"][0]
    if mutation == "unknown_source":
        citation["source_id"] = "Missing"
    elif mutation == "wrong_quote":
        citation["quote"] = "A condition that never appeared in the original."
    elif mutation == "line_range":
        citation["end_line"] = 100
    elif mutation == "false_origin":
        draft["existing_results"][0]["origin"] = "user"
    elif mutation == "missing_citation":
        draft["goal"]["citations"] = []
    elif mutation == "verified":
        draft["status"] = "FORMALLY_VERIFIED"
    else:
        draft["graph"] = {"nodes": []}
    with pytest.raises(ResearchError):
        store.accept(request.request_id, response_file(store, draft), model="test")
    assert not store.manifest().versions


def test_stale_response_cannot_overwrite_new_draft(intake):
    store, _, material, first = intake
    stale = store.prepare("Alternate wording", IntakeLimits())
    store.accept(first.request_id, response_file(store, content(material, first)), model="test")
    # The draft body can be valid while its preparation base is stale.
    with pytest.raises(ResearchError, match="changed"):
        store.accept(stale.request_id, response_file(store, content(material, first)), model="test")
    assert len(store.manifest().versions) == 1


def test_budget_rejection_never_silently_omits_sources(intake):
    store, _, _, _ = intake
    before = store.manifest()
    with pytest.raises(ResearchError, match="no sources were omitted"):
        store.prepare("new request", IntakeLimits(max_context_bytes=1024))
    assert store.manifest() == before


def test_corrupt_source_and_context_are_rejected_before_any_adapter_call(intake):
    store, _, source, request = intake
    file = store.path(f"requests/{request.request_id}.json")
    raw = json.loads(file.read_text(encoding="utf-8"))
    raw["task"]["context"] = "{}"
    file.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ResearchError, match="archived input"):
        store.request(request.request_id)
    store.path(f"sources/{source.sha256}.bin").write_bytes(b"corrupted")
    with pytest.raises(ResearchError, match="hash mismatch"):
        store.text(source)


class RecordingAdapter:
    name = AdapterName.MOCK

    def __init__(self, draft, *, usage=Usage(tokens=40, tool_calls=0), failure=None):
        self.draft = draft
        self.usage = usage
        self.failure = failure
        self.calls = 0

    def run(self, task, *, timeout, max_bytes, interrupted):
        self.calls += 1
        assert task.purpose == "intake" and not task.tools and not task.deep_session_id
        assert "Do not solve" in task.objective or "do not solve" in task.objective
        assert timeout == 120 and max_bytes == 262144
        assert interrupted() is None
        result = json.dumps(
            {
                "outcome": "PARTIAL_PROGRESS",
                "summary": "Draft prepared",
                "next_action": "Review",
                "artifacts": [{"name": "intake.txt", "text": json.dumps(self.draft)}],
            }
        )
        return AdapterOutput(
            stdout=result.encode(),
            result_json=result,
            exit_code=0,
            usage=self.usage,
            failure=self.failure,
            model="offline-test-double",
        )


def test_adapter_flow_records_usage_and_shares_single_worker_lock(intake):
    store, _, material, request = intake

    class LockCheckingAdapter(RecordingAdapter):
        def run(self, *args, **kwargs):
            with pytest.raises(ResearchError, match="busy"), TaskQueue(store.workspace).worker():
                pytest.fail("The core model lock was released during dispatch")
            return super().run(*args, **kwargs)

    adapter = LockCheckingAdapter(content(material, request))
    with TaskQueue(store.workspace).worker(), pytest.raises(ResearchError, match="busy"):
        store.generate(request.request_id, adapter)
    assert adapter.calls == 0
    store.generate(request.request_id, adapter)
    assert adapter.calls == 1
    receipt = json.loads(store.path(f"runs/{request.request_id}.json").read_text())
    assert receipt["state"] == "COMPLETED" and receipt["usage"]["tokens"] == 40
    assert not (store.workspace / "research").exists()
    with pytest.raises(ResearchError):
        store.generate(request.request_id, adapter)
    assert adapter.calls == 1


@pytest.mark.parametrize(
    "usage", [Usage(tool_calls=0), Usage(tokens=8001, tool_calls=0), Usage(tokens=40, tool_calls=1)]
)
def test_invalid_usage_keeps_failed_receipt_and_never_retries(intake, usage):
    store, _, material, request = intake
    adapter = RecordingAdapter(content(material, request), usage=usage)
    with pytest.raises(ResearchError):
        store.generate(request.request_id, adapter)
    receipt = json.loads(store.path(f"runs/{request.request_id}.json").read_text())
    assert receipt["state"] == "FAILED" and not store.manifest().versions
    with pytest.raises(ResearchError, match="already dispatched"):
        store.generate(request.request_id, adapter)
    assert adapter.calls == 1


def test_interrupt_is_recorded_without_replaying_model(intake):
    store, _, material, request = intake

    class InterruptAdapter(RecordingAdapter):
        def run(self, *args, **kwargs):
            self.calls += 1
            raise KeyboardInterrupt

    adapter = InterruptAdapter(content(material, request))
    with pytest.raises(KeyboardInterrupt):
        store.generate(request.request_id, adapter)
    receipt = json.loads(store.path(f"runs/{request.request_id}.json").read_text())
    assert receipt["state"] == "FAILED" and receipt["error"] == "KeyboardInterrupt"
    with pytest.raises(ResearchError, match="already dispatched"):
        store.generate(request.request_id, adapter)
    assert adapter.calls == 1


def test_publication_crash_preserves_previous_version_and_next_request_can_recover(
    intake, monkeypatch
):
    store, _, material, request = intake
    draft = DraftContent.model_validate(content(material, request))
    store.publish(request, draft, producer={"mode": "test", "model": "test"})
    revision = store.prepare("Revise", IntakeLimits())
    save = store._save_manifest

    def crash(_):
        raise OSError("simulated crash before publication")

    monkeypatch.setattr(store, "_save_manifest", crash)
    with pytest.raises(OSError):
        store.publish(revision, draft, producer={"mode": "test", "model": "test"})
    assert store.load_version()["version"] == 1
    monkeypatch.setattr(store, "_save_manifest", save)
    recovery = store.prepare("Revise after interrupted publication", IntakeLimits())
    store.publish(recovery, draft, producer={"mode": "test", "model": "test"})
    assert store.load_version()["version"] == 2


def test_material_formats_credentials_and_directory_boundary(tmp_path):
    for directory in (tmp_path, tmp_path / "research" / "draft", tmp_path / ".amre" / "draft"):
        with pytest.raises(ResearchError):
            IntakeStore(directory, workspace=tmp_path)
    store = IntakeStore(tmp_path / "draft", workspace=tmp_path)
    for name, data in (
        ("paper.pdf", b"unsupported"),
        ("binary.txt", b"\xff"),
        ("empty.md", b" "),
        ("nul.tex", b"a\0b"),
        ("huge.txt", b"x" * 524289),
        ("key.md", ("sk-" + "synthetic-credential-for-rejection").encode()),
    ):
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(ResearchError):
            store.import_materials([path])
    assert not store.manifest().sources


@pytest.mark.parametrize("extension", [".txt", ".md", ".markdown", ".tex"])
def test_all_supported_material_types_preserve_bom_and_newlines(tmp_path, extension):
    store = IntakeStore(tmp_path / "draft", workspace=tmp_path)
    path = tmp_path / ("notes" + extension)
    original = b"\xef\xbb\xbfFirst line\r\nSecond line\n"
    path.write_bytes(original)
    source = store.import_materials([path])[0]
    assert store.text(source) == "First line\nSecond line\n"
    assert source.line_count == 2
    assert store.path(f"sources/{source.sha256}.bin").read_bytes() == original


def test_invalid_transport_output_is_archived_as_failure(intake):
    from amre.execution.models import Failure

    store, _, material, request = intake

    class FailingAdapter(RecordingAdapter):
        def run(self, *args, **kwargs):
            return AdapterOutput(failure=Failure.TIMEOUT, stderr=b"timed out")

    with pytest.raises(ResearchError, match="TIMEOUT"):
        store.generate(request.request_id, FailingAdapter(content(material, request)))
    receipt = json.loads(store.path(f"runs/{request.request_id}.json").read_text())
    assert receipt["state"] == "FAILED" and receipt["failure"] == "TIMEOUT"
    assert not store.manifest().versions


def test_real_cli_import_prepare_accept_revise_show_and_live_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMRE_WORKSPACE_ROOT", str(tmp_path))
    source = tmp_path / "old-proof.md"
    source.write_text("% Earlier proof outline\np^2=2q^2; hence p is even.\n", encoding="utf-8")
    draft = tmp_path / "draft"
    assert main(["intake", "import", "--draft", str(draft), str(source)]) == 0
    material = json.loads(capsys.readouterr().out)[0]
    assert (
        main(
            [
                "intake",
                "prepare",
                "--draft",
                str(draft),
                "--request",
                "Prove that sqrt(2) is irrational.",
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    request_id = prepared["request_id"]
    assert (
        main(
            [
                "intake",
                "generate",
                "--draft",
                str(draft),
                "--request-id",
                request_id,
                "--adapter",
                "codex",
            ]
        )
        == 2
    )
    capsys.readouterr()
    store = IntakeStore(draft, workspace=tmp_path)
    request = store.request(request_id)
    from amre.intake.models import Source

    response = response_file(store, content(Source.model_validate(material), request))
    assert (
        main(
            [
                "intake",
                "accept",
                "--draft",
                str(draft),
                "--request-id",
                request_id,
                "--response",
                str(response),
                "--model",
                "offline-test-response",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["intake", "show", "--draft", str(draft)]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == 1
    assert read_yaml(draft / "manifest.yaml")["versions"][0]["number"] == 1
    assert not (tmp_path / "research").exists()


def test_saved_mathematical_example_retains_exact_sources_and_no_new_assumptions():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    store = IntakeStore(root / "examples/intake/acceptance", workspace=root)
    first = store.load_version(1)
    second = store.load_version(2)
    for saved in (first, second):
        request = store.request(saved["request_id"])
        draft = DraftContent.model_validate(saved["content"])
        store._validate_citations(request, draft)
        assert draft.status == "DRAFT_UNVERIFIED"
        assert saved["producer"]["mode"] == "external_response"
    assert first["content"]["goal"] == second["content"]["goal"]
    assert first["content"]["assumptions"] == second["content"]["assumptions"]
    assert first["content"]["existing_results"] == second["content"]["existing_results"]
    assert set(change["id"] for change in second["changes"]) == {
        "CompleteProof",
        "ParityGap",
        "ReviewMethod",
    }
    assert "n\\in\\mathbb{Z}" in second["content"]["gaps"][0]["text"]
    assert second["content"]["questions"] == []
