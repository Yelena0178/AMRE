import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

import pytest

from amre.errors import ResearchError
from amre.execution.adapters import MockModelAdapter
from amre.execution.deep import DeepResearchRunner
from amre.execution.deep_models import SemanticKind
from amre.execution.orchestrator import ResearchOrchestrator
from amre.execution.skill_models import ReasoningMode
from amre.literature import providers
from amre.literature.catalog import LiteratureLibrary
from amre.literature.discovery import CandidateQueue
from amre.literature.models import (
    CandidateDraft,
    PaperImport,
    ProviderName,
    SourceStatus,
    TheoremDraft,
)
from amre.literature.providers import PublicMetadataProvider, SearchRequest
from amre.research.context import build_context
from amre.research.memory import ResearchMemory
from amre.research.storage import read_yaml, yaml_text

_FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "literature" / "synthetic-papers.yaml"
_STATEMENT = r"If \(x\in\mathbb R\), then \(x^2\geq 0\)."


@pytest.fixture
def library(example_store):
    service = LiteratureLibrary(example_store.root)
    service.import_papers(PaperImport.model_validate(read_yaml(_FIXTURE)).papers)
    return service


def _draft(paper, **changes):
    return TheoremDraft(
        paper_id=paper.paper_id,
        paper_version=paper.metadata[0].fingerprint(),
        **{
            "theorem_id": "Source-Square",
            "label": "Synthetic square lemma",
            "assumptions": ("H-Real",),
            "locator": {"number": "2", "page": "3", "scope": "Real-domain inequality"},
            **changes,
        },
    )


def test_import_version_dedup_citations_context_and_invalidation(library):
    paper, sparse = library.read().papers
    assert sparse.metadata[0].year is None and not sparse.metadata[0].authors
    first = paper.metadata[0]
    assert library.search(SearchRequest(doi=first.doi.upper())).papers == (first,)
    assert library.search(SearchRequest(arxiv_id=first.arxiv_id)).papers == (first,)
    assert library.import_papers(
        (first.model_copy(update={"retrieved_at": datetime.now(UTC)}),)
    ) == (paper,)
    draft = _draft(paper)
    unconfirmed = library.import_theorem(draft, _STATEMENT)
    assert unconfirmed.source_status == SourceStatus.EXTRACTED_UNCHECKED
    with pytest.raises(ResearchError, match="does not contain"):
        library.import_theorem(draft, _STATEMENT, source_review=("Reader", "Only metadata"))
    confirmed = library.import_theorem(
        draft,
        _STATEMENT,
        source_review=("Reader", "Theorem 2. " + _STATEMENT),
    )
    assert confirmed.source_status == SourceStatus.SOURCE_CONFIRMED and confirmed.review
    citation = library.link("L1", confirmed.theorem_id, "Inequality hypothesis and conclusion")
    assert library.link("L1", confirmed.theorem_id, citation.scope) == citation
    library.link("P-T", paper.paper_id, "Bibliographic background")
    graph = library.store.graph()
    assert graph.assumptions_for(confirmed.theorem_id) == ("H-Real",)
    assert paper.paper_id in graph.research_inputs("L1")
    assert graph.review("L1").status == "CURRENT"
    memory = ResearchMemory(library.root)
    before = memory.create_checkpoint(
        "P-T", {"active_route": "A", "next_research_action": "Check L1"}
    )
    context = build_context(memory.restore("P-T", before.checkpoint_id), max_bytes=16000)
    assert "SOURCE_CONFIRMED" in context.text and _STATEMENT in context.text
    assert "quadratic extension" not in context.text
    updated = first.model_copy(update={"version": "v2", "source_url": first.source_url[:-1] + "2"})
    merged = library.import_papers((updated,))[0]
    assert merged.paper_id == paper.paper_id and len(merged.metadata) == 2
    current = library.store.graph()
    assert current.review("L1").status == current.review("P-T").status == "NEEDS_RECHECK"
    assert not memory.restore("P-T", before.checkpoint_id).workspace_matches
    assert library.read().theorems[0].paper_version == first.fingerprint()
    assert citation in library.read().citations and not current.evidence_for("L1")


def test_candidate_generation_four_origins_and_boundary_promotion(library):
    root = library.root
    paper = library.read().papers[0]
    theorem = library.import_theorem(_draft(paper), _STATEMENT)
    open_source = library.import_theorem(
        _draft(paper, theorem_id="Source-Open", open_problem=True),
        r"Does \(F\) extend?",
        source_review=("Reader", r"Question: Does \(F\) extend?"),
    )
    store = library.store
    store.update("P-T", {"obstacles": []})
    memory = ResearchMemory(root)
    memory.create_checkpoint("P-T", {"active_route": "A", "next_research_action": "Find a bound"})
    job = ResearchOrchestrator(root).run(
        "P-T", MockModelAdapter(), mode=ReasoningMode.PROOF_CONSTRUCTION
    )
    assert job.state == "COMPLETED"
    obstruction = next(
        item.obstruction for item in store.graph().document.nodes if item.obstruction
    )
    assert obstruction.category == "BOUNDARY"
    queue = CandidateQueue(root)
    base = {
        "title": "Candidate",
        "field": "PDE",
        "program": "Algebra",
        "significance": "Isolate a necessary endpoint estimate.",
        "acceptance_criteria": [{"id": "Check", "description": "Verify each endpoint assumption."}],
    }
    missing_draft = CandidateDraft(
        **base,
        kind="MISSING_LEMMA",
        parent_problem="P-T",
        obstacle_id=obstruction.obstacle_id,
        route_id="A",
        assumptions=("H-Real",),
        paper_ids=(paper.paper_id,),
        priority=9,
    )
    missing = queue.discover(missing_draft, r"Show \(u(0)=0\) under the declared boundary data.")
    assert (
        queue.discover(missing_draft, r"Show \(u(0)=0\) under the declared boundary data.")
        == missing
    )
    relaxed = queue.discover(
        CandidateDraft(**base, kind="ASSUMPTION_RELAXATION", theorem_id=theorem.theorem_id),
        r"Characterize the ordered domains with \(x^2\geq0\).",
    )
    source = queue.discover(
        CandidateDraft(**base, kind="EXPLICIT_OPEN_PROBLEM", theorem_id=open_source.theorem_id),
        r"Does \(F\) extend?",
    )
    queue.discover(
        CandidateDraft(**base, kind="MODEL_EXTENSION", parent_problem="P-T"),
        r"Establish the square inequality for a norm in a product space.",
    )
    assert source.source_reported_open and not source.open_confirmed
    assert relaxed.novelty == "NOVELTY_UNCONFIRMED"
    assert len(queue.list(field="PDE", program="Algebra")) == 4
    assert queue.list(parent="P-T")[0] == missing
    contract = queue.promote(missing.candidate_id, "Fixture researcher")
    assert queue.promote(missing.candidate_id, "Fixture researcher") == contract
    assert contract.derivation.parent_problem == "P-T"
    assert contract.derivation.obstacle_id == obstruction.obstacle_id
    assert contract.claim_status == "UNPROVED"
    assert paper.paper_id in store.graph().research_inputs(contract.problem_id)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        (
            ProviderName.ARXIV,
            b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            b"<id>http://arxiv.org/abs/9901.12345v2</id><title>Test</title></entry></feed>",
        ),
        (
            ProviderName.CROSSREF,
            b'{"message":{"items":[{"title":["Test"],"DOI":"10.55555/test"}]}}',
        ),
        (
            ProviderName.SEMANTIC_SCHOLAR,
            b'{"data":[{"title":"Test",'
            b'"url":"https://example.invalid/test","externalIds":{"ArXiv":"9901.12345v2"}}]}',
        ),
    ],
)
def test_provider_adapters_offline_replay_cache_and_failures(tmp_path, monkeypatch, name, payload):
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "literature.yaml").write_text(
        yaml_text({"enabled_providers": [name.value]}),
        encoding="utf-8",
    )
    calls = []

    def fake(url, headers, timeout):
        calls.append(url)
        if "outage" in url:
            raise HTTPError(url, 429, "fixture limit", {"Retry-After": "30"}, None)
        return payload

    monkeypatch.setattr(providers, "_download", fake)
    disabled = PublicMetadataProvider(tmp_path, name, online=False)
    assert disabled.search(SearchRequest(query="test")).diagnostic.startswith("UNAVAILABLE")
    assert not calls
    provider = PublicMetadataProvider(tmp_path, name, online=True)
    query = SearchRequest(query="test", limit=1)
    results = provider.search(query)
    assert results.papers[0].title == "Test"
    assert provider.search(query).cached and len(calls) == 1
    assert provider.search(SearchRequest(query="cooldown")).diagnostic.startswith("RATE_LIMITED")
    (tmp_path / ".amre" / "literature" / name / "retry-after").write_text("0")
    assert provider.search(SearchRequest(query="outage")).diagnostic.startswith("HTTP_429")


def test_search_tool_runs_inside_one_deep_dispatch_with_persistent_receipt(library):
    root = library.root
    (root / "research" / "execution.yaml").write_text(
        yaml_text(
            {
                "deep": {"tools": ["literature.search"], "session_tool_calls": 1},
                "workspace": {"attempts": 4, "seconds": 90, "tool_calls": 1},
                "problem": {"attempts": 3, "seconds": 60, "tool_calls": 1},
            }
        ),
        encoding="utf-8",
    )
    dispatched = []

    class Model(MockModelAdapter):
        def run_deep_with_tools(self, task, *, call_tool, on_event, **options):
            dispatched.append(task.attempt_id)
            request = {
                "request_id": "Lookup",
                "name": "literature.search",
                "arguments": {"query": "boundary"},
            }
            result = call_tool(request)
            assert result.value["papers"][0]["fixture"]
            assert call_tool(request) == result
            assert call_tool({**request, "request_id": "More"}).status == "BLOCKED"
            return self.run_deep(task, on_event=on_event, **options)

    runner = DeepResearchRunner(root)
    job = runner.start("P-T", Model())
    assert job.state == "COMPLETED"
    session = runner.inspect(job.deep_session_id)
    assert len(dispatched) == len(runner.runner.queue.read().reservations) == 1
    assert runner.runner.queue.read().reservations[0].usage.tool_calls == 1
    restored = runner.runner.memory.restore("P-T", session.last_checkpoint)
    tools = [
        source
        for source in restored.checkpoint.context_sources
        if source.kind == "LITERATURE" and source.path.endswith("tool-1.md")
    ]
    assert len(tools) == 1
    assert json.loads(restored.read_text(tools[0].path))["response"]["status"] == "COMPLETED"
    assert SemanticKind.TOOL_RESULT in {
        read_yaml(root / source)["update"]["kind"] for source in session.semantic_events
    }


def test_installed_command_surface_offline(example_store):
    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "amre", *args],
            cwd=example_store.root,
            env={**os.environ, "AMRE_WORKSPACE_ROOT": str(example_store.root), "PYTHONUTF8": "1"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=25,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    paper = cli("literature", "import", str(_FIXTURE))[0]
    assert cli("literature", "show", paper["paper_id"])["version_fingerprints"]
    assert cli("literature", "search", "--query", "boundary")["papers"][0]["fixture"]
    assert cli("literature", "link", "P-T", paper["paper_id"], "--scope", "Fixture")["paper_id"]
    assert cli("candidates", "list") == []
    spec = example_store.root / "candidate.yaml"
    spec.write_text(
        yaml_text(
            {
                "kind": "MODEL_EXTENSION",
                "title": "Synthetic extension",
                "field": "Algebra",
                "parent_problem": "P-T",
                "paper_ids": [paper["paper_id"]],
                "significance": "Extend the declared synthetic domain.",
                "acceptance_criteria": [{"id": "Check", "description": "Check the new domain."}],
            }
        ),
        encoding="utf-8",
    )
    statement = example_store.root / "candidate.tex"
    statement.write_text(r"Prove \(x^2+y^2\geq0\) for real \(x,y\).", encoding="utf-8")
    candidate = cli("discover", "--metadata", str(spec), "--statement", str(statement))
    promoted = cli("candidates", "promote", candidate["candidate_id"], "--author", "Fixture")
    assert promoted["claim_status"] == "UNPROVED"
    assert (
        cli("candidates", "list", "--field", "Algebra")[0]["promoted_problem"]
        == promoted["problem_id"]
    )
