"""Single-worker dispatch and durable multi-scope reservations."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from amre.errors import ResearchError
from amre.execution.models import (
    AdapterName,
    AttemptReceipt,
    BranchDispatch,
    ExecutionJob,
    ExecutionLedger,
    ExecutionPolicy,
    ExecutionState,
    Limits,
    ResearchProgram,
    Reservation,
)
from amre.execution.skill_models import ReasoningMode
from amre.research.models import ResearchContract
from amre.research.storage import atomic_write, read_yaml, validate, workspace_path, yaml_text


class TaskQueue:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = workspace_path(self.root, ".amre/execution.yaml")

    def read(self) -> ExecutionLedger:
        return validate(
            ExecutionLedger, read_yaml(self.path, missing_ok=True) or {}, "execution ledger"
        )

    @contextmanager
    def _lock(self, name: str, *, timeout: int) -> Iterator[None]:
        lock = workspace_path(self.root, f".amre/{name}.lock")
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(lock), timeout=timeout):
                yield
        except Timeout:
            raise ResearchError(f"Research execution lock is busy: {name}") from None
        except OSError as exc:
            raise ResearchError(f"Execution workspace I/O failed: {exc.strerror}") from None

    def worker(self):
        return self._lock("execution-worker", timeout=0)

    def scheduler(self):
        return self._lock("scheduler", timeout=0)

    def edit(self, change: Callable[[ExecutionLedger], ExecutionLedger]) -> ExecutionLedger:
        with self._lock("execution-ledger", timeout=10):
            previous = self.read()
            current = validate(ExecutionLedger, change(previous), "execution ledger")
            if current != previous:
                atomic_write(self.path, yaml_text(current.model_dump(mode="json")))
            return current

    @staticmethod
    def with_job(ledger: ExecutionLedger, job: ExecutionJob) -> ExecutionLedger:
        return ledger.model_copy(update={"jobs": {**ledger.jobs, job.key: job}})

    def control(
        self, job: ExecutionJob, state: ExecutionState, *, session_id: str | None = None
    ) -> ExecutionJob:
        def change(ledger: ExecutionLedger) -> ExecutionLedger:
            current = ledger.jobs.get(job.key, job)
            if session_id is not None and current.deep_session_id != session_id:
                raise ResearchError("A newer job owns this problem")
            if current.state in {ExecutionState.CANCELLED, ExecutionState.COMPLETED}:
                return ledger
            update = (
                {"request": state}
                if current.state == ExecutionState.RUNNING
                else {
                    "state": state,
                    "request": None,
                }
            )
            return self.with_job(ledger, current.model_copy(update=update))

        return self.edit(change).jobs[job.key]

    def settle(
        self, job: ExecutionJob, receipt: AttemptReceipt, state: ExecutionState
    ) -> ExecutionJob:
        def change(ledger: ExecutionLedger) -> ExecutionLedger:
            current = ledger.jobs[job.key]
            if current.attempt_id != receipt.task.attempt_id:
                raise ResearchError("Execution reservation changed")
            updated = current.model_copy(
                update={
                    "state": current.request or state,
                    "request": None,
                    "detail": receipt.failure or receipt.result.summary,
                }
            )
            reservations = tuple(
                item.model_copy(
                    update={
                        "settled_ms": receipt.elapsed_ms,
                        "usage": receipt.usage,
                        "final_state": updated.state,
                    }
                )
                if item.attempt_id == current.attempt_id and item.settled_ms is None
                else item
                for item in ledger.reservations
            )
            return self.with_job(ledger.model_copy(update={"reservations": reservations}), updated)

        return self.edit(change).jobs[job.key]


class BudgetManager:
    def __init__(self, policy: ExecutionPolicy):
        self.policy = policy

    @staticmethod
    def used_ms(attempts: tuple[Reservation, ...]) -> int:
        return sum(
            item.reserved_ms if item.settled_ms is None else item.settled_ms for item in attempts
        )

    def _scopes(
        self,
        ledger: ExecutionLedger,
        job: ExecutionJob,
        program: ResearchProgram | None,
        *,
        route_id: str | None = None,
        branch_limits: Limits | None = None,
        route_by_checkpoint: dict[str, str | None] | None = None,
    ) -> list[tuple[str, Limits, tuple[Reservation, ...]]]:
        scopes = [
            ("workspace", self.policy.workspace, ledger.reservations),
            (
                "problem",
                self.policy.problem,
                tuple(item for item in ledger.reservations if item.problem_id == job.problem_id),
            ),
        ]
        if program is not None:
            attempts = tuple(item for item in ledger.reservations if item.program == program.name)
            scopes.append(("program", self.policy.program, attempts))
            if program.limits is not None:
                scopes.append(("program override", program.limits, attempts))
        if branch_limits is not None:
            previous_routes = route_by_checkpoint or {}
            attempts = tuple(
                item
                for item in ledger.reservations
                if item.problem_id == job.problem_id
                and (item.route_id or previous_routes.get(item.checkpoint_id)) == route_id
            )
            scopes.append(("branch", branch_limits, attempts))
        return scopes

    def remaining_ms(
        self,
        ledger: ExecutionLedger,
        job: ExecutionJob,
        contract: ResearchContract,
        program: ResearchProgram | None,
        *,
        prior_seconds: int = 0,
        prior_tokens: int = 0,
        route_id: str | None = None,
        branch_limits: Limits | None = None,
        route_by_checkpoint: dict[str, str | None] | None = None,
        deep_session_id: str | None = None,
    ) -> int:
        if prior_tokens >= contract.budget.max_tokens:
            raise ResearchError("contract token budget exhausted")
        scopes = self._scopes(
            ledger,
            job,
            program,
            route_id=route_id,
            branch_limits=branch_limits,
            route_by_checkpoint=route_by_checkpoint,
        )
        if deep_session_id is None:
            scopes.append(("attempt", self.policy.attempt, ()))
        remaining_ms = contract.budget.timeout_seconds * 1000
        for name, limits, attempts in scopes:
            if len(attempts) >= limits.attempts:
                raise ResearchError(f"{name} attempt budget exhausted")
            elapsed = self.used_ms(attempts)
            remaining_ms = min(remaining_ms, limits.seconds * 1000 - elapsed)
            if remaining_ms <= 0:
                raise ResearchError(f"{name} time budget exhausted")
        contract_usage = self.used_ms(scopes[1][2])
        remaining_ms = min(
            remaining_ms,
            contract.budget.timeout_seconds * 1000 - max(contract_usage, prior_seconds * 1000),
        )
        if remaining_ms <= 0:
            raise ResearchError("contract time budget exhausted")
        if deep_session_id is not None:
            earlier = tuple(
                item for item in ledger.reservations if item.deep_session_id == deep_session_id
            )
            remaining_ms = min(
                remaining_ms, self.policy.deep.session_seconds * 1000 - self.used_ms(earlier)
            )
            if remaining_ms <= 0:
                raise ResearchError("deep session time budget exhausted")
        return remaining_ms

    def reserve(
        self,
        ledger: ExecutionLedger,
        job: ExecutionJob,
        contract: ResearchContract,
        program: ResearchProgram | None,
        checkpoint_id: str,
        adapter: AdapterName,
        *,
        prior_seconds: int = 0,
        prior_tokens: int = 0,
        reasoning_mode: ReasoningMode | None = None,
        obstacle_id: str | None = None,
        route_id: str | None = None,
        dispatch: BranchDispatch | None = None,
        route_by_checkpoint: dict[str, str | None] | None = None,
        deep_session_id: str | None = None,
        tools: tuple[str, ...] = (),
    ) -> tuple[ExecutionLedger, Reservation]:
        if dispatch and any(
            item.dispatch_id == dispatch.decision_id for item in ledger.reservations
        ):
            raise ResearchError("Scheduler decision already has a reservation")
        remaining_ms = self.remaining_ms(
            ledger,
            job,
            contract,
            program,
            prior_seconds=prior_seconds,
            prior_tokens=prior_tokens,
            route_id=route_id,
            branch_limits=dispatch.limits if dispatch else None,
            route_by_checkpoint=route_by_checkpoint,
            deep_session_id=deep_session_id,
        )
        if dispatch:
            remaining_ms = min(remaining_ms, dispatch.max_attempt_ms)
        tool_cap = 0
        if tools:
            scopes = self._scopes(
                ledger,
                job,
                program,
                route_id=route_id,
                branch_limits=dispatch.limits if dispatch else None,
                route_by_checkpoint=route_by_checkpoint,
            )

            def used(attempts: tuple[Reservation, ...]) -> int:
                return sum(
                    item.tool_cap if item.usage.tool_calls is None else item.usage.tool_calls
                    for item in attempts
                )

            session_used = used(
                tuple(
                    item for item in ledger.reservations if item.deep_session_id == deep_session_id
                )
            )
            tool_cap = max(
                0,
                min(
                    self.policy.deep.session_tool_calls - session_used,
                    *(limits.tool_calls - used(attempts) for _, limits, attempts in scopes),
                ),
            )
        reservation = Reservation(
            attempt_id=f"AT-{uuid4().hex}",
            problem_id=job.problem_id,
            program=job.program,
            checkpoint_id=checkpoint_id,
            adapter=adapter,
            reserved_ms=remaining_ms,
            reasoning_mode=reasoning_mode,
            obstacle_id=obstacle_id,
            route_id=route_id,
            dispatch_id=dispatch.decision_id if dispatch else None,
            deep_session_id=deep_session_id,
            tool_cap=tool_cap,
            tools=tools if tool_cap else (),
            prompt_template=self.policy.prompt_template,
            strategy_version=dispatch.strategy_version
            if dispatch
            else self.policy.strategy_version,
        )
        running = job.model_copy(
            update={
                "state": ExecutionState.RUNNING,
                "attempt_id": reservation.attempt_id,
                "request": None,
                "detail": None,
                "deep_session_id": deep_session_id,
            }
        )
        updated = ledger.model_copy(update={"reservations": (*ledger.reservations, reservation)})
        return TaskQueue.with_job(updated, running), reservation


def load_policy(root: Path) -> ExecutionPolicy:
    from amre.strategy.config import active_profile

    path = workspace_path(root, "research/execution.yaml")
    policy = validate(ExecutionPolicy, read_yaml(path, missing_ok=True) or {}, "execution policy")
    if active := active_profile(root):
        profile = active.profile
        policy = policy.model_copy(
            update={
                "max_context_bytes": profile.context_bytes,
                "prompt_template": profile.prompt_template,
                "strategy_version": active.version_id,
                "deep": policy.deep.model_copy(
                    update={"budget_target_percent": profile.deep_target_percent}
                ),
            }
        )
    return policy


def load_program(root: Path, name: str) -> ResearchProgram:
    # Validate the identifier before constructing a filesystem path.
    spec = validate(ResearchProgram, {"name": name, "problems": ["Placeholder"]}, "program name")
    path = workspace_path(root, f"research/programs/{spec.name}.yaml")
    program = validate(ResearchProgram, read_yaml(path), "research program")
    if program.name != name:
        raise ResearchError("Program filename and declared name differ")
    return program
