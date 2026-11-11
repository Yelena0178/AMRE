"""Persist scheduler decisions and dispatch through the existing worker and ledger."""

from hashlib import sha256
from pathlib import Path

from amre.errors import ResearchError
from amre.execution.adapters import MockModelAdapter, ModelAdapter
from amre.execution.deep import DeepResearchRunner
from amre.execution.dispatch_checkpoint import decision_path
from amre.execution.models import AdapterName, ExecutionJob, ExecutionState, ResearchProgram
from amre.execution.queue import BudgetManager, TaskQueue, load_policy
from amre.research.journal import publish_immutable, read_versioned
from amre.research.memory import ResearchMemory
from amre.research.storage import atomic_write, read_yaml, validate, workspace_path, yaml_text
from amre.scheduling.models import (
    Allocation,
    DecisionRecord,
    DispatchResult,
    PlanningInput,
    ScheduleJournal,
)
from amre.scheduling.planner import choose
from amre.scheduling.portfolio import candidates, load_settings


class PortfolioScheduler:
    def __init__(self, root: Path):
        self.memory = ResearchMemory(root)
        self.root = self.memory.root
        self.queue = TaskQueue(self.root)

    @staticmethod
    def scope(problem: str | None, program: ResearchProgram | None) -> str:
        if (problem is None) == (program is None):
            raise ResearchError("Select exactly one problem or program")
        return f"program:{program.name}" if program else f"problem:{problem}"

    def _journal_path(self, scope: str) -> Path:
        key = sha256(scope.encode()).hexdigest()[:24]
        return workspace_path(self.root, f"research/scheduler/journals/{key}.yaml")

    def journal(self, scope: str) -> ScheduleJournal:
        return validate(
            ScheduleJournal,
            read_yaml(self._journal_path(scope), missing_ok=True) or {},
            "scheduler journal",
        )

    def _save_journal(self, scope: str, journal: ScheduleJournal) -> None:
        validated = validate(ScheduleJournal, journal, "scheduler journal")
        atomic_write(self._journal_path(scope), yaml_text(validated.model_dump(mode="json")))

    def decision(self, decision_id: str) -> DecisionRecord:
        if (
            not decision_id.startswith("DR-")
            or len(decision_id) != 35
            or any(char not in "0123456789abcdef" for char in decision_id[3:])
        ):
            raise ResearchError("Invalid scheduler decision ID")
        record = read_versioned(self.root, decision_path(decision_id), DecisionRecord)
        if (
            record.decision_id != decision_id
            or decision_id not in self.journal(record.inputs.scope).decisions
        ):
            raise ResearchError("Scheduler decision is not published")
        if choose(record.inputs) != record:
            raise ResearchError("Stored decision differs from deterministic replay")
        return record

    def _counts(self, journal: ScheduleJournal) -> dict[Allocation, int]:
        counts = {item: 0 for item in Allocation}
        for result in journal.results:
            if result.attempt_id:
                record = self.decision(result.decision_id)
                counts[record.candidate().allocation] += 1
        return counts

    def _inputs(
        self,
        problem,
        program,
        seed,
        journal,
        inventory,
        ledger,
        settings,
        *,
        dispatched=None,
        position=None,
    ) -> PlanningInput:
        return PlanningInput(
            scope=self.scope(problem, program),
            seed=seed,
            position=len(journal.decisions) if position is None else position,
            strategy=settings.strategy,
            graph_fingerprint=inventory.document.research_fingerprint(),
            dispatched=self._counts(journal) if dispatched is None else dispatched,
            candidates=candidates(
                self.root,
                inventory,
                ledger,
                settings,
                program.problems if program else (problem,),
                program,
            ),
        )

    def explain(
        self, problem: str | None, program: ResearchProgram | None, seed: int
    ) -> DecisionRecord:
        journal = self.journal(self.scope(problem, program))
        return choose(
            self._inputs(
                problem,
                program,
                seed,
                journal,
                self.memory.inventory(),
                self.queue.read(),
                load_settings(self.root),
            )
        )

    def simulate(
        self,
        problem: str | None,
        program: ResearchProgram | None,
        seed: int,
        steps: int,
        assumed_ms: int,
    ) -> tuple[DecisionRecord, ...]:
        settings = load_settings(self.root)
        self._check_steps(steps, settings.strategy.max_decisions_per_run)
        if assumed_ms <= 0:
            raise ResearchError("Simulation charge must be positive")
        journal = self.journal(self.scope(problem, program))
        counts = self._counts(journal)
        inventory = self.memory.inventory()
        ledger = self.queue.read()
        budgets = BudgetManager(load_policy(self.root))
        records = []
        route_map = {item.checkpoint_id: item.active_route for item in inventory.checkpoints}
        for index in range(steps):
            decision = choose(
                self._inputs(
                    problem,
                    program,
                    seed,
                    journal,
                    inventory,
                    ledger,
                    settings,
                    dispatched=dict(counts),
                    position=len(journal.decisions) + index,
                )
            )
            records.append(decision)
            candidate = decision.candidate()
            if candidate is None:
                break
            plan = decision.dispatch
            job = ExecutionJob(
                problem_id=plan.problem_id, program=program.name if program else None
            )
            # Simulate one settlement against the same budget rules. No new graph facts are added.
            reserved_ledger, reserved = budgets.reserve(
                ledger,
                job,
                inventory.document.contract(plan.problem_id),
                program,
                "CP-Simulation",
                AdapterName.MOCK,
                route_id=plan.route_id,
                dispatch=plan,
                route_by_checkpoint=route_map,
                deep_session_id=f"DS-Simulation-{index}",
            )
            simulated = reserved.model_copy(
                update={
                    "attempt_id": f"Sim-{index}-{decision.decision_id}",
                    "settled_ms": min(assumed_ms, reserved.reserved_ms),
                    "final_state": ExecutionState.COMPLETED,
                }
            )
            ledger = reserved_ledger.model_copy(
                update={
                    "jobs": ledger.jobs,
                    "reservations": (*ledger.reservations, simulated),
                }
            )
            counts[candidate.allocation] += 1
        return tuple(records)

    @staticmethod
    def _check_steps(steps: int, limit: int) -> None:
        if not 1 <= steps <= limit:
            raise ResearchError(f"Scheduling steps must be between 1 and {limit}")

    def run(
        self,
        problem: str | None,
        program: ResearchProgram | None,
        seed: int,
        steps: int = 1,
        *,
        adapter: ModelAdapter | None = None,
    ) -> tuple[tuple[DecisionRecord, DispatchResult], ...]:
        """Schedule at session boundaries, defaulting to offline Mock."""
        adapter = adapter or MockModelAdapter()
        if adapter.name != AdapterName.MOCK:
            raise ResearchError("Automatic portfolio dispatch accepts offline Mock adapters only")
        scope = self.scope(problem, program)
        reports = []
        with self.queue.scheduler():
            settings = load_settings(self.root)
            self._check_steps(steps, settings.strategy.max_decisions_per_run)
            journal = self.journal(scope)
            for _ in range(steps):
                if len(journal.decisions) > len(journal.results):
                    decision = self.decision(journal.decisions[-1])
                else:
                    decision = choose(
                        self._inputs(
                            problem,
                            program,
                            seed,
                            journal,
                            self.memory.inventory(),
                            self.queue.read(),
                            settings,
                        )
                    )
                    publish_immutable(
                        self.root,
                        {
                            decision_path(decision.decision_id): yaml_text(
                                decision.model_dump(mode="json")
                            ).encode()
                        },
                    )
                    journal = journal.model_copy(
                        update={"decisions": (*journal.decisions, decision.decision_id)}
                    )
                    self._save_journal(scope, journal)
                result = self._dispatch(decision, program, adapter)
                journal = journal.model_copy(update={"results": (*journal.results, result)})
                self._save_journal(scope, journal)
                reports.append((decision, result))
                if result.attempt_id is None or result.state in {
                    ExecutionState.CANCELLED,
                    ExecutionState.PAUSED,
                }:
                    break
        return tuple(reports)

    def _dispatch(self, decision: DecisionRecord, program, adapter) -> DispatchResult:
        plan = decision.dispatch
        if plan is None:
            return DispatchResult(
                decision_id=decision.decision_id,
                state=ExecutionState.BLOCKED,
                reason=decision.reason,
            )
        previous = next(
            (
                item
                for item in self.queue.read().reservations
                if item.dispatch_id == decision.decision_id
            ),
            None,
        )
        if previous is not None and previous.settled_ms is not None:
            return DispatchResult(
                decision_id=decision.decision_id,
                attempt_id=previous.attempt_id,
                state=previous.final_state or ExecutionState.FAILED,
                reason="Recovered the already-settled reservation",
            )
        candidate = decision.candidate()
        deep = DeepResearchRunner(self.root)
        execute = deep.resume if previous is not None else deep.start
        job = execute(
            plan.problem_id,
            adapter,
            program=program,
            mode=candidate.mode,
            obstacle_id=candidate.obstacle_id,
            dispatch=plan,
        )
        reserved = next(
            (
                item
                for item in self.queue.read().reservations
                if item.dispatch_id == decision.decision_id
            ),
            None,
        )
        return DispatchResult(
            decision_id=decision.decision_id,
            attempt_id=reserved.attempt_id if reserved else None,
            state=job.state,
            reason=job.detail or job.state,
        )
