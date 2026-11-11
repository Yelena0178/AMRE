"""Continuous execution, asynchronous semantic checkpoints, and recovery."""

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from amre.errors import ResearchError
from amre.execution.adapters import ModelAdapter, redact
from amre.execution.deep_models import (
    MODEL_SEMANTIC_KINDS,
    Continuity,
    DeepResearchSession,
    DeepState,
    ReasoningBudget,
    SemanticKind,
    SemanticRecord,
    SemanticUpdate,
)
from amre.execution.models import ExecutionJob, ExecutionState, Failure, ReasoningTask
from amre.execution.queue import TaskQueue, load_policy
from amre.research.journal import checkpoint_path, publish_immutable, read_versioned
from amre.research.memory import ResearchMemory
from amre.research.storage import atomic_write, validate, workspace_path, yaml_text


def _file(session_id: str, filename: str) -> str:
    return f"research/deep/{session_id}/{filename}"


@dataclass
class _ToolPublication:
    update: SemanticUpdate
    done: threading.Event = field(default_factory=threading.Event)


class DeepSessionStore:
    def __init__(self, memory: ResearchMemory, tasks: TaskQueue):
        self.memory = memory
        self.root = memory.root
        self.tasks = tasks

    def _path(self, session_id: str) -> Path:
        return workspace_path(self.root, _file(session_id, "session.yaml"))

    def _read(self, session_id: str) -> DeepState:
        if not any(item.deep_session_id == session_id for item in self.tasks.read().reservations):
            raise ResearchError(f"Unknown deep session: {session_id}")
        state = read_versioned(self.root, _file(session_id, "session.yaml"), DeepState)
        if state.session_id != session_id:
            raise ResearchError("Deep session identity changed")
        return state

    def _save(self, state: DeepState) -> None:
        atomic_write(self._path(state.session_id), yaml_text(state.model_dump(mode="json")))

    def _lock(self, session_id: str):
        return self.tasks._lock(f"deep-{session_id}", timeout=10)

    def begin(self, task: ReasoningTask) -> str | None:
        session_id = task.deep_session_id
        with self._lock(session_id):
            previous = self._read(session_id) if self._path(session_id).is_file() else None
            if previous and previous.attempt_id == task.attempt_id:
                return previous.native_session_id
            if previous and previous.problem_id != task.problem_id:
                raise ResearchError("Deep session belongs to another problem")
            risks = []
            if previous:
                risks.append(
                    "Provider internal state is unavailable; rebuilt from archived context."
                )
            risks.extend(f"Context budget omitted {item}" for item in task.omitted_context)
            self._save(
                DeepState(
                    session_id=session_id,
                    problem_id=task.problem_id,
                    research_goal=task.objective,
                    active_route=task.route_id,
                    attempt_id=task.attempt_id,
                    working_context=task.context,
                    continuity=Continuity.RECONSTRUCTED if previous else Continuity.FRESH,
                    last_checkpoint=task.checkpoint_id,
                    started_at=datetime.now(UTC),
                    strategy_version=task.strategy_version,
                    context_loss_risk=tuple(risks),
                    artifacts=previous.artifacts if previous else (),
                    semantic_events=previous.semantic_events if previous else (),
                )
            )
            return previous.native_session_id if previous else None

    def provider_started(self, task: ReasoningTask, native_id: str, resumed: bool) -> None:
        with self._lock(task.deep_session_id):
            state = self._read(task.deep_session_id)
            if state.attempt_id != task.attempt_id:
                raise ResearchError("Provider session belongs to a different attempt")
            updates = {"native_session_id": native_id}
            if resumed:
                updates["continuity"] = Continuity.NATIVE
                updates["context_loss_risk"] = tuple(
                    risk for risk in state.context_loss_risk if risk.startswith("Context budget")
                )
            self._save(state.model_copy(update=updates))

    def append(self, session_id: str, attempt_id: str, update: SemanticUpdate) -> str:
        with self._lock(session_id):
            state = self._read(session_id)
            if state.attempt_id != attempt_id:
                raise ResearchError("Semantic event belongs to a different attempt")
            return self._append(state, update)

    def _append(self, state: DeepState, update: SemanticUpdate) -> str:
        document = None
        significant = update.kind not in {SemanticKind.LOCAL_STEP, SemanticKind.STRATEGY_SWITCH}
        if significant:
            state, document = self._current(state)
        if update.kind == SemanticKind.INTERRUPTED:
            for relative in reversed(state.semantic_events):
                event = read_versioned(self.root, relative, SemanticRecord)
                if event.attempt_id != state.attempt_id:
                    break
                if event.update.kind == update.kind:
                    return self._checkpoint(state, document)
        event_id = f"SE-{uuid4().hex}"
        session_id = state.session_id
        source = (
            _file(session_id, f"{event_id}-{update.artifact.name}") if update.artifact else None
        )
        relative = _file(session_id, f"{event_id}.yaml")
        record = SemanticRecord(
            event_id=event_id, attempt_id=state.attempt_id, update=update, source_file=source
        )
        files = {relative: yaml_text(record.model_dump(mode="json")).encode()}
        if source:
            files[source] = update.artifact.text.encode()
        publish_immutable(self.root, files)
        updated = state.model_copy(
            update={
                "semantic_events": (*state.semantic_events, relative),
                "artifacts": (*state.artifacts, source) if source else state.artifacts,
            }
        )
        self._save(updated)
        return self._checkpoint(updated, document) if significant else state.last_checkpoint

    def _current(self, state: DeepState):
        document, _ = self.memory.research.snapshot()
        reference = document.contract(state.problem_id).checkpoint_reference
        if reference and reference.path == checkpoint_path(state.last_checkpoint):
            return state, document
        # A crash between publishing a checkpoint and updating session.yaml is recoverable.
        latest = self.memory.restore(state.problem_id)
        checkpoint = latest.checkpoint
        if (
            not latest.workspace_matches
            or checkpoint.deep_session_id != state.session_id
            or checkpoint.last_attempt is None
            or checkpoint.last_attempt.attempt_id != state.attempt_id
            or checkpoint.last_attempt.outcome is not None
            or not state.semantic_events
            or state.semantic_events[-1] not in {item.path for item in checkpoint.context_sources}
            or reference is None
            or reference.path != checkpoint_path(checkpoint.checkpoint_id)
        ):
            raise ResearchError("Session no longer owns the current checkpoint")
        state = state.model_copy(update={"last_checkpoint": checkpoint.checkpoint_id})
        self._save(state)
        return state, document

    def _checkpoint(self, state: DeepState, document) -> str:
        original = self.memory.restore(state.problem_id, state.last_checkpoint)
        if not original.workspace_matches:
            raise ResearchError("Research changed during semantic checkpoint")
        base = original.checkpoint
        sources = [item.model_dump(mode="json") for item in base.context_sources]
        existing = {item["path"] for item in sources}
        pending = [
            path for path in (*state.artifacts, *state.semantic_events) if path not in existing
        ]
        if not pending:
            return state.last_checkpoint
        action = base.next_research_action
        unresolved = list(base.unresolved_proof_obligations)
        literature = set()
        for relative in state.semantic_events:
            if relative not in pending:
                continue
            record = read_versioned(self.root, relative, SemanticRecord)
            if record.update.kind == SemanticKind.TOOL_RESULT and record.update.summary.startswith(
                "literature."
            ):
                literature.update((relative, record.source_file))
            if record.attempt_id == state.attempt_id:
                action = record.update.next_action or action
                unresolved.extend(record.update.unresolved)
        # Newest original derivations and semantic records receive priority during reconstruction.
        additions = [
            {"path": path, "kind": "LITERATURE" if path in literature else "DERIVATION"}
            for path in reversed(pending)
        ]
        checkpoint = self.memory.create_checkpoint(
            state.problem_id,
            {
                "active_route": state.active_route,
                "next_research_action": action,
                "unresolved_proof_obligations": tuple(dict.fromkeys(unresolved)),
                "strategy_version": state.strategy_version or base.strategy_version,
                "deep_session_id": state.session_id,
                "context_sources": [*additions, *sources],
                "established_results": base.established_results,
                "candidate_results": base.candidate_results,
                "usage": base.budget_snapshot.model_dump(
                    include={"tokens_used", "elapsed_seconds"}
                ),
            },
            expected=document,
        )
        self._save(state.model_copy(update={"last_checkpoint": checkpoint.checkpoint_id}))
        return checkpoint.checkpoint_id

    def checkpoint(self, session_id: str) -> str:
        with self._lock(session_id):
            return self._append(
                self._read(session_id),
                SemanticUpdate(
                    kind=SemanticKind.USER_CHECKPOINT,
                    summary="User requested a semantic checkpoint.",
                ),
            )

    def flush(self, session_id: str, attempt_id: str) -> None:
        with self._lock(session_id):
            state = self._read(session_id)
            if state.attempt_id != attempt_id:
                raise ResearchError("Semantic checkpoint belongs to a different attempt")
            if state.semantic_events:
                state, document = self._current(state)
                self._checkpoint(state, document)

    def finished(self, session_id: str, attempt_id: str, checkpoint_id: str) -> None:
        with self._lock(session_id):
            state = self._read(session_id)
            if state.attempt_id == attempt_id:
                self._save(state.model_copy(update={"last_checkpoint": checkpoint_id}))

    def tool_updates(self, task: ReasoningTask) -> tuple[SemanticUpdate, ...]:
        state = self._read(task.deep_session_id)
        return tuple(
            record.update
            for relative in state.semantic_events
            if (record := read_versioned(self.root, relative, SemanticRecord)).attempt_id
            == task.attempt_id
            and record.update.kind == SemanticKind.TOOL_RESULT
        )

    def inspect(self, session_id: str) -> DeepResearchSession:
        state = self._read(session_id)
        ledger = self.tasks.read()
        attempts = [item for item in ledger.reservations if item.deep_session_id == session_id]
        last = attempts[-1]
        job = ledger.jobs.get(ExecutionJob(problem_id=last.problem_id, program=last.program).key)
        elapsed = sum(item.settled_ms or 0 for item in attempts)
        reserved = sum(item.reserved_ms for item in attempts if item.settled_ms is None)
        limit = load_policy(self.root).deep.session_seconds * 1000

        def total(field: str) -> int | str:
            values = [getattr(item.usage, field) for item in attempts]
            return "UNKNOWN" if any(value is None for value in values) else sum(values)

        status = (
            job.state
            if job and job.deep_session_id == session_id
            else last.final_state or ExecutionState.RUNNING
        )
        live = 0
        if status == ExecutionState.RUNNING and state.started_at is not None:
            wall_ms = int((datetime.now(UTC) - state.started_at).total_seconds() * 1000)
            live = max(0, min(reserved, wall_ms))
        return DeepResearchSession(
            **state.model_dump(),
            session_status=status,
            elapsed_time=elapsed + live,
            reasoning_budget=ReasoningBudget(
                session_ms=limit,
                charged_ms=elapsed,
                reserved_ms=reserved,
                remaining_ms=max(0, limit - elapsed - reserved),
                tokens=total("tokens"),
                cost_microusd=total("cost_microusd"),
            ),
        )


class SemanticObserver:
    """Enqueue bounded model messages and publish snapshots off the model output thread."""

    def __init__(
        self,
        store: DeepSessionStore,
        task: ReasoningTask,
        *,
        max_events: int,
        max_bytes: int,
        known_assumptions: frozenset[str],
    ):
        self.store = store
        self.task = task
        self.events: queue.Queue[SemanticUpdate | _ToolPublication | tuple[str, bool] | None] = (
            queue.Queue()
        )
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.known_assumptions = known_assumptions
        self.count = self.size = 0
        self.failure = None
        self._warned = False
        self.started = time.monotonic()
        self.worker = threading.Thread(target=self._write, daemon=True)
        self.worker.start()

    def _write(self) -> None:
        while (update := self.events.get()) is not None:
            try:
                if isinstance(update, tuple):
                    self.store.provider_started(self.task, *update)
                else:
                    event = update.update if isinstance(update, _ToolPublication) else update
                    self.store.append(self.task.deep_session_id, self.task.attempt_id, event)
            except (ResearchError, OSError, ValueError):
                self.failure = Failure.UNAVAILABLE
            finally:
                if isinstance(update, _ToolPublication):
                    update.done.set()

    def archive_tool(self, update: SemanticUpdate, timeout: float) -> None:
        publication = _ToolPublication(update)
        self.events.put_nowait(publication)
        if not publication.done.wait(timeout):
            self.failure = Failure.TIMEOUT
            raise ResearchError("Session tool receipt exceeded the dispatch deadline")
        if self.failure:
            raise ResearchError("Session tool receipt could not be published")

    def emit(self, payload: object) -> None:
        try:
            update = validate(SemanticUpdate, payload, "semantic event")
            if update.kind not in MODEL_SEMANTIC_KINDS:
                raise ResearchError("Model submitted a reserved event kind")
            if not self.known_assumptions.issuperset(update.assumptions):
                raise ResearchError(
                    "Unregistered assumption: state proposed hypotheses in the artifact"
                )
            if update.artifact:
                update = update.model_copy(
                    update={
                        "artifact": update.artifact.model_copy(
                            update={"text": redact(update.artifact.text)}
                        )
                    }
                )
            update = update.model_copy(
                update={
                    "summary": redact(update.summary),
                    "next_action": redact(update.next_action) if update.next_action else None,
                    "unresolved": tuple(redact(item) for item in update.unresolved),
                }
            )
        except (ResearchError, ValueError):
            self.failure = Failure.INVALID_RESULT
            return
        self.size += len(update.model_dump_json().encode())
        self.count += 1
        if self.count > self.max_events or self.size > self.max_bytes:
            self.failure = Failure.OUTPUT_LIMIT
        elif self.failure is None:
            self.events.put_nowait(update)

    def provider_started(self, native_id: str, resumed: bool) -> None:
        self.events.put_nowait((native_id, resumed))

    def boundary(self, timeout: float) -> Failure | None:
        if not self._warned and time.monotonic() - self.started >= timeout * 0.9:
            self._warned = True
            self.events.put_nowait(
                SemanticUpdate(
                    kind=SemanticKind.BUDGET_BOUNDARY,
                    summary="Session approaching its hard deadline.",
                )
            )
        return self.failure

    def finish(self, failure: Failure | None) -> Failure | None:
        kind = {
            Failure.TIMEOUT: SemanticKind.BUDGET_BOUNDARY,
            Failure.TOKEN_LIMIT: SemanticKind.BUDGET_BOUNDARY,
            Failure.PAUSED: SemanticKind.USER_PAUSE,
            Failure.CANCELLED: SemanticKind.USER_STOP,
            Failure.INTERRUPTED: SemanticKind.INTERRUPTED,
        }.get(failure)
        if kind:
            self.events.put_nowait(
                SemanticUpdate(kind=kind, summary=f"Execution boundary: {failure}.")
            )
        self.events.put_nowait(None)
        self.worker.join()
        try:
            self.store.flush(self.task.deep_session_id, self.task.attempt_id)
        except (ResearchError, OSError, ValueError):
            self.failure = self.failure or Failure.UNAVAILABLE
        return self.failure


class DeepResearchRunner:
    def __init__(self, root: Path):
        from amre.execution.orchestrator import ResearchOrchestrator

        self.runner = ResearchOrchestrator(root)
        self.store = DeepSessionStore(self.runner.memory, self.runner.queue)

    def start(self, problem_id: str, adapter: ModelAdapter, **options) -> ExecutionJob:
        return self.runner.run(problem_id, adapter, deep_session_id=f"DS-{uuid4().hex}", **options)

    def resume(self, problem_id: str, adapter: ModelAdapter | None, **options) -> ExecutionJob:
        return self.runner.run(problem_id, adapter, resume=True, **options)

    def inspect(self, session_id: str) -> DeepResearchSession:
        return self.store.inspect(session_id)

    def checkpoint(self, session_id: str) -> str:
        return self.store.checkpoint(session_id)

    def _control(self, session_id: str, status: ExecutionState) -> ExecutionJob:
        session = self.store.inspect(session_id)
        ledger = self.runner.queue.read()
        reservation = next(
            item for item in ledger.reservations if item.attempt_id == session.attempt_id
        )
        job = ledger.jobs[
            ExecutionJob(problem_id=session.problem_id, program=reservation.program).key
        ]
        if job.deep_session_id != session_id:
            raise ResearchError("A newer job owns this problem")
        return self.runner.queue.control(job, status, session_id=session_id)

    def pause(self, session_id: str) -> ExecutionJob:
        return self._control(session_id, ExecutionState.PAUSED)

    def stop(self, session_id: str) -> ExecutionJob:
        return self._control(session_id, ExecutionState.CANCELLED)
