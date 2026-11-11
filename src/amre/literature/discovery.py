"""Deterministic topic discovery from precise statements and declared sources."""

from pathlib import Path

from amre.errors import ResearchError
from amre.literature.catalog import _find, _stable_id, add_citations, make_citation
from amre.literature.models import CandidateDraft, CandidateKind, OpenProblemCandidate, SourceStatus
from amre.research.graph import NodeKind
from amre.research.models import Budget, Provenance, ResearchContract, StatementRef
from amre.research.storage import validate, workspace_path
from amre.research.store import ResearchStore, read_statement_file
from amre.scheduling.derivation import derive_problem


class CandidateQueue:
    def __init__(self, root: Path):
        self.store = ResearchStore(root)

    def list(self, *, field=None, program=None, parent=None) -> tuple[OpenProblemCandidate, ...]:
        candidates = (
            item
            for item in self.store.read().literature.candidates
            if (field is None or item.field == field)
            and (program is None or item.program == program)
            and (parent is None or item.parent_problem == parent)
        )
        return tuple(sorted(candidates, key=lambda item: (-item.priority, item.candidate_id)))

    def discover(self, draft: CandidateDraft, statement: str) -> OpenProblemCandidate:
        reference = StatementRef.from_text(statement)
        with self.store._writing():
            document, graph = self.store.snapshot()
            catalog = document.literature
            papers = list(draft.paper_ids)
            parent = document.contract(draft.parent_problem) if draft.parent_problem else None
            if draft.theorem_id:
                theorem = _find(catalog.theorems, "theorem_id", draft.theorem_id)
                if theorem.paper_id not in papers:
                    papers.append(theorem.paper_id)
            else:
                theorem = None
            for paper in papers:
                _find(catalog.papers, "paper_id", paper)
            for assumption in draft.assumptions:
                if graph.node(assumption).kind != NodeKind.ASSUMPTION:
                    raise ResearchError("Candidate hypotheses must reference assumption nodes")
            if draft.kind == CandidateKind.EXPLICIT_OPEN_PROBLEM and (
                not theorem.open_problem
                or theorem.source_status != SourceStatus.SOURCE_CONFIRMED
                or theorem.statement != reference
            ):
                raise ResearchError(
                    "Use the exact source-confirmed statement of the reported problem"
                )
            if draft.kind == CandidateKind.ASSUMPTION_RELAXATION and not (
                set(draft.assumptions) < set(theorem.assumptions)
            ):
                raise ResearchError("Relaxation must explicitly remove a source assumption")
            if draft.kind == CandidateKind.MISSING_LEMMA:
                scope = graph.research_inputs(parent.problem_id)
                obstacle = graph.node(draft.obstacle_id)
                route = graph.node(draft.route_id)
                if (
                    obstacle.id not in scope
                    or route.id not in scope
                    or route.kind != NodeKind.PROOF_ROUTE
                    or not obstacle.obstacle_open
                    or obstacle.obstruction is None
                    or obstacle.obstruction.problem_id != parent.problem_id
                    or (obstacle.obstruction.routes and route.id not in obstacle.obstruction.routes)
                    or not graph.assumption_lattice.closure(parent.assumptions).issuperset(
                        draft.assumptions
                    )
                ):
                    raise ResearchError(
                        "Missing lemma needs a scoped open obstruction and hypotheses"
                    )
            candidate_id = _stable_id(
                "Topic",
                draft.kind,
                draft.parent_problem or "",
                draft.obstacle_id or "",
                draft.theorem_id or "",
                reference.sha256,
                *sorted(draft.assumptions),
            )
            old = next(
                (item for item in catalog.candidates if item.candidate_id == candidate_id), None
            )
            if old:
                return old
            record = OpenProblemCandidate(
                **draft.model_dump(exclude={"paper_ids"}),
                paper_ids=tuple(papers),
                candidate_id=candidate_id,
                statement=reference,
                source_reported_open=draft.kind == CandidateKind.EXPLICIT_OPEN_PROBLEM,
            )
            updated = catalog.model_copy(update={"candidates": (*catalog.candidates, record)})
            self.store._commit(
                document,
                graph,
                document.model_copy(update={"literature": updated}),
                {reference.path: statement},
            )
            return record

    def promote(self, candidate_id: str, author: str, *, budget: Budget | None = None):
        document = self.store.read()
        candidate = _find(document.literature.candidates, "candidate_id", candidate_id)
        if candidate.promoted_problem:
            return document.contract(candidate.promoted_problem)
        statement = read_statement_file(workspace_path(self.store.root, candidate.statement.path))
        derived = None
        if candidate.kind == CandidateKind.MISSING_LEMMA:
            derived = derive_problem(
                self.store.root,
                candidate.parent_problem,
                candidate.obstacle_id,
                candidate.route_id,
                {
                    "assumptions": candidate.assumptions,
                    "acceptance_criteria": candidate.acceptance_criteria,
                    "author": author,
                    "budget": budget,
                },
                statement,
            )
        with self.store._writing():
            document, graph = self.store.snapshot()
            catalog = document.literature
            candidate = _find(catalog.candidates, "candidate_id", candidate_id)
            if candidate.promoted_problem:
                return document.contract(candidate.promoted_problem)
            contracts = document.contracts
            if derived:
                contract = document.contract(derived)
            else:
                parent = (
                    document.contract(candidate.parent_problem)
                    if candidate.parent_problem
                    else None
                )
                contract = validate(
                    ResearchContract,
                    {
                        "problem_id": _stable_id("Problem", candidate_id),
                        "statement": candidate.statement,
                        "assumptions": candidate.assumptions,
                        "acceptance_criteria": candidate.acceptance_criteria,
                        "budget": budget
                        or (
                            parent.budget
                            if parent
                            else Budget(
                                max_tokens=4000,
                                timeout_seconds=120,
                            )
                        ),
                        "provenance": Provenance(
                            author=author,
                            source=f"Candidate {candidate_id} ({candidate.kind})",
                        ),
                    },
                    "promoted research contract",
                )
                contracts = (*contracts, contract)
            citations = tuple(
                make_citation(
                    catalog, contract.problem_id, source, f"Topic provenance {candidate_id}"
                )
                for source in (
                    *candidate.paper_ids,
                    *((candidate.theorem_id,) if candidate.theorem_id else ()),
                )
            )
            catalog, graph_doc = add_citations(catalog, document.graph, *citations)
            replacement = candidate.model_copy(update={"promoted_problem": contract.problem_id})
            catalog = catalog.model_copy(
                update={
                    "candidates": tuple(
                        replacement if item == candidate else item for item in catalog.candidates
                    )
                }
            )
            self.store._commit(
                document,
                graph,
                document.model_copy(
                    update={
                        "contracts": contracts,
                        "literature": catalog,
                        "graph": graph_doc,
                    }
                ),
                {},
            )
            return contract
