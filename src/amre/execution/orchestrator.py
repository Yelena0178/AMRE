"""Run, record, and recover a single deterministic research dispatch."""

import json
import math
import time
from pathlib import Path

from amre.errors import ResearchError, WorkspaceChanged
from amre.execution.adapters import (
    AdapterOutput,
    CodexCLIAdapter,
    ModelAdapter,
    parse_result,
    redact,
)
from amre.execution.deep import DeepSessionStore, SemanticObserver
from amre.execution.deep_models import SemanticKind, SemanticUpdate
from amre.execution.dispatch_checkpoint import prepare_dispatch
from amre.execution.models import (
    AttemptReceipt,
    BranchDispatch,
    ExecutionJob,
    ExecutionState,
    Failure,
    ReasoningOutcome,
    ReasoningTask,
    ResearchProgram,
    Reservation,
    Usage,
)
from amre.execution.queue import BudgetManager, TaskQueue, load_policy
from amre.execution.session_tools import SessionToolCalls, publish_attempt_tools, workspace_tools
from amre.execution.skill_models import ReasoningMode
from amre.execution.skill_publication import Publication, draft_path, publish_skill
from amre.execution.skills import prepare_skill, validate_skill
from amre.research.context import build_context
from amre.research.graph import GraphNode, NodeKind
from amre.research.journal import publish_immutable, read_versioned, replay
from amre.research.memory import ResearchMemory, RestoredState
from amre.research.memory_models import AttemptOutcome, EventKind
from amre.research.models import ClaimStatus, EvidenceRecord, ResearchStatus, ReviewStatus, RunInfo
from amre.research.storage import workspace_path, yaml_text

_CLAIM = {
    ReasoningOutcome.PROOF_CANDIDATE: ClaimStatus.PROOF_CANDIDATE,
    ReasoningOutcome.COUNTEREXAMPLE_CANDIDATE: ClaimStatus.REFUTATION_CANDIDATE,
    ReasoningOutcome.VALID_REDUCTION_CANDIDATE: ClaimStatus.PARTIAL,
    ReasoningOutcome.PARTIAL_PROGRESS: ClaimStatus.PARTIAL,
}


def receipt_path(attempt_id: str) -> str:
    return f"research/attempts/{attempt_id}/receipt.yaml"


def _node_id(prefix: str, attempt_id: str, index: int) -> str:
    return f"{prefix}-{attempt_id}-{index}"


def _redact_inputs(value: object) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_inputs(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_inputs(item) for key, item in value.items()}
    return value


class ResearchOrchestrator:
    def __init__(self, root: Path):
        self.memory = ResearchMemory(root)
        self.root = self.memory.root
        self.queue = TaskQueue(self.root)
        self.policy = load_policy(self.root)

    def _current(
        self,
        problem_id: str,
        *,
        resume: bool,
        dispatch: BranchDispatch | None = None,
    ) -> RestoredState:
        inventory = self.memory.inventory()
        contract = inventory.document.contract(problem_id)
        state = inventory.states.get(problem_id)
        if state and state.last_attempt and state.last_attempt.outcome is None:
            raise ResearchError("A research attempt is already open for this problem")
        if contract.research_status == ResearchStatus.CLOSED:
            raise ResearchError("The research contract is closed")
        if not resume and (
            contract.research_status == ResearchStatus.PAUSED
            or (state is not None and state.paused)
        ):
            raise ResearchError("The research problem is paused. Use amre resume")
        if dispatch is not None:
            return prepare_dispatch(self.memory, inventory, dispatch)
        if state is None or state.latest_checkpoint is None:
            self.memory.create_checkpoint(
                problem_id,
                {
                    "next_research_action": contract.acceptance_criteria[0].description,
                },
            )
        restored = self.memory.restore(problem_id)
        if not restored.workspace_matches:
            raise ResearchError("Research changed since the checkpoint. Save a fresh checkpoint")
        return restored

    def _context(
        self, restored: RestoredState, mode: ReasoningMode | None, obstacle_id: str | None
    ):
        budget = self.policy.max_context_bytes
        fields = {}
        if mode is not None:
            skill, inputs, obstacle_id = prepare_skill(restored, mode, obstacle_id)
            inputs = _redact_inputs(inputs)
            text = json.dumps(inputs, ensure_ascii=False)
            instructions = skill.instructions
            budget -= len(text.encode()) + len(instructions.encode())
            fields = {
                "reasoning_mode": mode,
                "skill_version": skill.version,
                "skill_instructions": instructions,
                "skill_inputs": inputs,
                "obstacle_id": obstacle_id,
            }
            if budget <= 0:
                raise ResearchError("Scoped skill inputs exceed the context budget")
        return build_context(restored, max_bytes=budget), fields

    def _task(self, restored: RestoredState, reservation: Reservation) -> ReasoningTask:
        checkpoint = restored.checkpoint
        context, skill_fields = self._context(
            restored, reservation.reasoning_mode, reservation.obstacle_id
        )
        return ReasoningTask(
            attempt_id=reservation.attempt_id,
            problem_id=checkpoint.problem_id,
            route_id=checkpoint.active_route,
            checkpoint_id=checkpoint.checkpoint_id,
            assertion_revision=restored.graph.review(checkpoint.problem_id).revision,
            strategy_version=reservation.strategy_version or checkpoint.strategy_version,
            objective=redact(checkpoint.next_research_action),
            context=redact(context.text),
            dispatch_id=reservation.dispatch_id,
            deep_session_id=reservation.deep_session_id,
            omitted_context=context.omitted,
            tools=reservation.tools,
            prompt_template=reservation.prompt_template,
            **skill_fields,
        )

    def run(
        self,
        problem_id: str,
        adapter: ModelAdapter | None,
        *,
        program: ResearchProgram | None = None,
        resume: bool = False,
        mode: ReasoningMode | None = None,
        obstacle_id: str | None = None,
        dispatch: BranchDispatch | None = None,
        deep_session_id: str | None = None,
        expected_session_id: str | None = None,
    ) -> ExecutionJob:
        if dispatch is not None and dispatch.problem_id != problem_id:
            raise ResearchError("Scheduler decision refers to a different problem")
        job = ExecutionJob(problem_id=problem_id, program=program.name if program else None)
        with self.queue.worker():
            ledger = self.queue.read()
            job = ledger.jobs.get(job.key, job)
            if expected_session_id is not None and job.deep_session_id != expected_session_id:
                raise ResearchError("A newer job owns this problem")
            if job.state == ExecutionState.RUNNING:
                if dispatch is not None and not any(
                    item.attempt_id == job.attempt_id and item.dispatch_id == dispatch.decision_id
                    for item in ledger.reservations
                ):
                    raise ResearchError("Running attempt belongs to a different dispatch")
                return self._recover(job)
            if any(item.state == ExecutionState.RUNNING for item in ledger.jobs.values()):
                raise ResearchError("Another run requires recovery before dispatch")
            if resume and job.state in {ExecutionState.COMPLETED, ExecutionState.CANCELLED}:
                return job
            if job.state == ExecutionState.CANCELLED or (
                job.state == ExecutionState.PAUSED and not resume
            ):
                return job
            if adapter is None:
                raise ResearchError("Select an adapter to dispatch a new attempt")
            if resume and deep_session_id is None:
                deep_session_id = job.deep_session_id
            if resume and mode is None and job.attempt_id:
                previous = next(
                    item for item in ledger.reservations if item.attempt_id == job.attempt_id
                )
                mode = previous.reasoning_mode
                obstacle_id = obstacle_id or previous.obstacle_id
            if obstacle_id is not None and mode is None:
                raise ResearchError("--obstacle requires a reasoning mode")
            tools = self.policy.deep.tools if deep_session_id else ()
            if tools:
                workspace_tools(self.root).select(tools)
                if not callable(getattr(adapter, "run_deep_with_tools", None)):
                    raise ResearchError("This adapter has no session tool transport")
            ready = job.model_copy(update={"state": ExecutionState.READY, "detail": None})

            def prepare(state):
                current = state.jobs.get(job.key, job)
                if current != job:
                    return state
                return TaskQueue.with_job(state, ready)

            current = self.queue.edit(prepare).jobs[job.key]
            if current.state != ExecutionState.READY:
                return current
            try:
                restored = self._current(problem_id, resume=resume, dispatch=dispatch)
                checkpoint = restored.checkpoint
                # Check context before reserving an attempt.
                self._context(restored, mode, obstacle_id)
                previous_routes = (
                    {
                        item.checkpoint_id: item.active_route
                        for item in self.memory.inventory().checkpoints
                    }
                    if dispatch
                    else None
                )

                def reserve(state):
                    if state.jobs[job.key].state != ExecutionState.READY:
                        return state
                    return BudgetManager(self.policy).reserve(
                        state,
                        ready,
                        checkpoint.contract,
                        program,
                        checkpoint.checkpoint_id,
                        adapter.name,
                        prior_seconds=checkpoint.budget_snapshot.elapsed_seconds,
                        prior_tokens=checkpoint.budget_snapshot.tokens_used,
                        reasoning_mode=mode,
                        obstacle_id=obstacle_id,
                        route_id=checkpoint.active_route,
                        dispatch=dispatch,
                        route_by_checkpoint=previous_routes,
                        deep_session_id=deep_session_id,
                        tools=tools,
                    )[0]

                ledger = self.queue.edit(reserve)
            except ResearchError as exc:
                blocked = ready.model_copy(
                    update={"state": ExecutionState.BLOCKED, "detail": str(exc)}
                )
                return self.queue.edit(
                    lambda state: TaskQueue.with_job(
                        state,
                        blocked
                        if state.jobs[job.key].state == ExecutionState.READY
                        else state.jobs[job.key],
                    )
                ).jobs[job.key]
            running = ledger.jobs[job.key]
            if running.state != ExecutionState.RUNNING:
                return running
            reservation = ledger.reservations[-1]
            task = self._task(restored, reservation)
            self._start(task)
            receipt = self._dispatch(task, restored, running, reservation, adapter)
            self._save_receipt(receipt)
            return self._finalize(running, receipt)

    def _interruption(self, job: ExecutionJob) -> Failure | None:
        request = self.queue.read().jobs[job.key].request
        return {
            ExecutionState.PAUSED: Failure.PAUSED,
            ExecutionState.CANCELLED: Failure.CANCELLED,
        }.get(request)

    def _dispatch(
        self,
        task: ReasoningTask,
        restored: RestoredState,
        job: ExecutionJob,
        reservation: Reservation,
        adapter: ModelAdapter,
    ) -> AttemptReceipt:
        timeout = reservation.reserved_ms / 1000
        observer = None
        native_id = None
        tool_calls = None
        if task.deep_session_id:
            sessions = DeepSessionStore(self.memory, self.queue)
            native_id = sessions.begin(task)
            observer = SemanticObserver(
                sessions,
                task,
                max_events=self.policy.deep.max_semantic_events,
                max_bytes=self.policy.max_output_bytes,
                known_assumptions=frozenset(
                    node.id
                    for node in restored.graph.document.nodes
                    if node.kind == NodeKind.ASSUMPTION
                ),
            )

        def interrupted():
            return self._interruption(job) or (observer.boundary(timeout) if observer else None)

        started = time.monotonic()
        try:
            options = {
                "timeout": timeout,
                "max_bytes": self.policy.max_output_bytes,
                "interrupted": interrupted,
            }
            run_deep = getattr(adapter, "run_deep", None)
            if observer and isinstance(adapter, CodexCLIAdapter):
                options.update(
                    persist_native=self.policy.deep.native_codex_sessions,
                    resume_native=native_id,
                    on_native=observer.provider_started,
                )
            if observer and task.tools:
                tool_calls = SessionToolCalls(
                    task,
                    workspace_tools(self.root),
                    reservation.tool_cap,
                    started + timeout,
                    interrupted,
                    lambda update: observer.archive_tool(
                        update, max(0, started + timeout - time.monotonic())
                    ),
                )
                output = adapter.run_deep_with_tools(
                    task,
                    on_event=observer.emit,
                    call_tool=tool_calls,
                    **options,
                )
            elif observer and callable(run_deep):
                output = run_deep(task, on_event=observer.emit, **options)
            else:
                output = adapter.run(task, **options)
        except KeyboardInterrupt:
            output = AdapterOutput(failure=Failure.INTERRUPTED)
        except (OSError, ResearchError):
            output = AdapterOutput(failure=Failure.UNAVAILABLE)
        except ValueError:
            output = AdapterOutput(failure=Failure.INVALID_RESULT)
        elapsed = math.ceil((time.monotonic() - started) * 1000)
        failure = (
            self._interruption(job) or output.failure or (observer.failure if observer else None)
        )
        result = None
        limit = self.policy.max_output_bytes
        raw = output.stdout + output.stderr
        if failure is None and (
            len(raw) > limit or (output.result_json and len(output.result_json.encode()) > limit)
        ):
            failure = Failure.OUTPUT_LIMIT
        if failure is None and output.exit_code != 0:
            failure = Failure.NONZERO_EXIT
        if failure is None and output.usage.tool_calls != 0:
            failure = Failure.TOOL_LIMIT
        budget = restored.checkpoint.budget_snapshot
        if (
            failure is None
            and output.usage.tokens is not None
            and (budget.tokens_used + output.usage.tokens > budget.max_tokens)
        ):
            failure = Failure.TOKEN_LIMIT
        if failure is None and elapsed > reservation.reserved_ms:
            failure = Failure.TIMEOUT
        if failure is None:
            try:
                result = parse_result(redact(output.result_json or ""))
                allowed = restored.graph.assumption_lattice.closure(
                    restored.graph.assumptions_for(task.problem_id)
                )
                if not allowed.issuperset(result.assumptions_used):
                    raise ResearchError("Reasoning result used assumptions outside the contract")
                validate_skill(task, result, restored)
            except ResearchError:
                result = None
                failure = Failure.INVALID_RESULT
        if observer:
            publication_failure = observer.finish(failure)
            if publication_failure and failure is None:
                failure, result = publication_failure, None
        stdout = output.stdout[:limit]
        stderr = output.stderr[: limit - len(stdout)]
        return AttemptReceipt(
            task=task,
            adapter=adapter.name,
            model=redact(output.model) if output.model else None,
            elapsed_ms=elapsed,
            exit_code=output.exit_code,
            usage=output.usage.model_copy(update={"tool_calls": tool_calls.count})
            if tool_calls is not None
            else output.usage,
            stdout=redact(stdout.decode("utf-8", "replace")),
            stderr=redact(stderr.decode("utf-8", "replace")),
            failure=failure,
            result=result,
        )

    def _start(self, task: ReasoningTask) -> None:
        if not self._attempt_events(task, EventKind.ATTEMPT_STARTED):
            self.memory.append_event(
                {
                    "kind": EventKind.ATTEMPT_STARTED,
                    "problem_id": task.problem_id,
                    "target_id": task.attempt_id,
                    "summary": task.objective,
                }
            )

    def _attempt_events(self, task: ReasoningTask, kind: EventKind) -> bool:
        document = self.memory.research.read()
        return any(
            event.data.kind == kind
            and event.data.problem_id == task.problem_id
            and event.data.target_id == task.attempt_id
            for event in self.memory._events(document)
        )

    def _save_receipt(self, receipt: AttemptReceipt) -> None:
        attempt_id = receipt.task.attempt_id
        files = (
            {
                draft_path(attempt_id, item.name): item.text.encode()
                for item in receipt.result.all_artifacts
            }
            if receipt.result
            else {}
        )
        files[receipt_path(attempt_id)] = yaml_text(receipt.model_dump(mode="json")).encode()
        try:
            publish_immutable(self.root, files)
        except OSError as exc:
            raise ResearchError(f"Could not persist attempt receipt: {exc.strerror}") from None

    def _recover(self, job: ExecutionJob) -> ExecutionJob:
        reservation = next(
            item for item in self.queue.read().reservations if item.attempt_id == job.attempt_id
        )
        path = receipt_path(reservation.attempt_id)
        if workspace_path(self.root, path).is_file():
            receipt = read_versioned(self.root, path, AttemptReceipt)
            if (
                receipt.task.attempt_id != reservation.attempt_id
                or receipt.task.problem_id != reservation.problem_id
                or receipt.task.checkpoint_id != reservation.checkpoint_id
                or receipt.adapter != reservation.adapter
                or receipt.task.dispatch_id != reservation.dispatch_id
                or receipt.task.deep_session_id != reservation.deep_session_id
            ):
                raise ResearchError("Attempt receipt does not match its reservation")
        else:
            restored = self.memory.restore(job.problem_id, reservation.checkpoint_id)
            receipt = AttemptReceipt(
                task=self._task(restored, reservation),
                adapter=reservation.adapter,
                elapsed_ms=reservation.reserved_ms,
                failure=self._interruption(job) or Failure.INTERRUPTED,
                usage=Usage(tool_calls=reservation.tool_cap or None),
            )
            if receipt.task.deep_session_id:
                self._start(receipt.task)
                sessions = DeepSessionStore(self.memory, self.queue)
                sessions.begin(receipt.task)
                sessions.append(
                    receipt.task.deep_session_id,
                    receipt.task.attempt_id,
                    SemanticUpdate(
                        kind=SemanticKind.INTERRUPTED,
                        summary="Recovered a lost worker without repeating the reservation.",
                    ),
                )
            self._save_receipt(receipt)
        self._start(receipt.task)
        return self._finalize(job, receipt)

    def _apply_result(self, receipt: AttemptReceipt, original: RestoredState) -> tuple[str, ...]:
        result = receipt.result
        task = receipt.task
        store = self.memory.research
        document, graph = store.snapshot()
        contract = document.contract(task.problem_id)
        evidence_ids = tuple(
            _node_id("Evidence", task.attempt_id, index) for index, _ in enumerate(result.artifacts)
        )
        obstacle_ids = tuple(
            _node_id("Obstacle", task.attempt_id, index) for index, _ in enumerate(result.obstacles)
        )
        existing = {node.id for node in graph.document.nodes}
        outputs = set(evidence_ids + obstacle_ids)
        if outputs and outputs.issubset(existing):
            return evidence_ids
        if not original.workspace_matches or graph.review(task.problem_id).revision != (
            task.assertion_revision
        ):
            raise WorkspaceChanged("Research changed during the attempt")
        for artifact in result.artifacts:
            path = workspace_path(self.root, draft_path(task.attempt_id, artifact.name))
            try:
                matches = path.read_bytes() == artifact.text.encode()
            except OSError:
                matches = False
            if not matches:
                raise ResearchError(f"Attempt artifact differs from its receipt: {artifact.name}")
        nodes = list(document.graph.nodes)
        for evidence_id, artifact in zip(evidence_ids, result.artifacts, strict=True):
            record = EvidenceRecord(
                evidence_id=evidence_id,
                source_type="MODEL",
                file_path=draft_path(task.attempt_id, artifact.name),
                git_revision=original.checkpoint.repository_revision,
                run=RunInfo(tool=receipt.adapter, version=receipt.model, run_id=task.attempt_id),
                check_scope=f"{task.problem_id}, revision {task.assertion_revision} (candidate)",
                check_result="INCONCLUSIVE",
                coverage="PARTIAL",
                assertion_id=task.problem_id,
                assertion_revision=task.assertion_revision,
                direction="REFUTE"
                if result.outcome == ReasoningOutcome.COUNTEREXAMPLE_CANDIDATE
                else "SUPPORT",
            )
            nodes.append(
                GraphNode(
                    id=evidence_id, kind=NodeKind.EVIDENCE, label=artifact.name, evidence=record
                )
            )
        nodes.extend(
            GraphNode(id=obstacle_id, kind=NodeKind.OBSTACLE, label=label)
            for obstacle_id, label in zip(obstacle_ids, result.obstacles, strict=True)
        )
        patch = {
            "obstacles": (*contract.obstacles, *obstacle_ids),
            "evidence_references": (*contract.evidence_references, *evidence_ids),
            "claim_status": _CLAIM.get(result.outcome, contract.claim_status),
        }
        store.update(
            task.problem_id,
            patch,
            expected=document,
            graph=document.graph.model_copy(update={"nodes": tuple(nodes)}),
        )
        return evidence_ids

    def _record_outputs(self, receipt: AttemptReceipt, new_obstacles: tuple[str, ...]) -> None:
        task = receipt.task
        result = receipt.result
        path = receipt_path(task.attempt_id)
        events = self.memory._events(self.memory.research.read())
        outputs = [(EventKind.OBSTACLE_DISCOVERED, obstacle_id) for obstacle_id in new_obstacles]
        if result.outcome in _CLAIM:
            outputs.append((EventKind.CLAIM_PROPOSED, task.problem_id))
        for kind, subject in outputs:
            if not any(
                event.data.kind == kind
                and event.data.target_id == subject
                and path in event.data.artifact_paths
                for event in events
            ):
                self.memory.append_event(
                    {
                        "kind": kind,
                        "problem_id": task.problem_id,
                        "target_id": subject,
                        "artifact_paths": [path],
                    }
                )

    def _finalize(self, job: ExecutionJob, receipt: AttemptReceipt) -> ExecutionJob:
        task = receipt.task
        original = self.memory.restore(task.problem_id, task.checkpoint_id)
        evidence_ids = ()
        progress = None
        if receipt.result is not None:
            try:
                publication = (
                    publish_skill(self.memory.research, receipt, original)
                    if task.reasoning_mode
                    else Publication(
                        self._apply_result(receipt, original),
                        tuple(
                            _node_id("Obstacle", task.attempt_id, index)
                            for index, _ in enumerate(receipt.result.obstacles)
                        ),
                    )
                )
                evidence_ids = publication.root_evidence
                progress = publication.progress
            except WorkspaceChanged:
                receipt = receipt.model_copy(
                    update={
                        "result": None,
                        "failure": Failure.STALE_RESEARCH,
                    }
                )
            else:
                self._record_outputs(receipt, publication.new_obstacles)
        if task.tools and receipt.failure != Failure.STALE_RESEARCH:
            updates = DeepSessionStore(self.memory, self.queue).tool_updates(task)
            publish_attempt_tools(self.root, task, updates)
        path = receipt_path(task.attempt_id)
        result = receipt.result
        if not self._attempt_events(task, EventKind.ATTEMPT_COMPLETED):
            self.memory.append_event(
                {
                    "kind": EventKind.ATTEMPT_COMPLETED,
                    "problem_id": task.problem_id,
                    "target_id": task.attempt_id,
                    "outcome": AttemptOutcome.SUCCEEDED if result else AttemptOutcome.FAILED,
                    "summary": result.summary if result else str(receipt.failure),
                    "artifact_paths": [
                        path,
                        *(draft_path(task.attempt_id, item.name) for item in result.all_artifacts),
                        *([progress] if progress else []),
                    ]
                    if result
                    else [path],
                }
            )
        if receipt.failure in {Failure.PAUSED, Failure.INTERRUPTED}:
            events = self.memory._events(self.memory.research.read())
            if not any(
                event.data.kind == EventKind.PROBLEM_PAUSED and path in event.data.artifact_paths
                for event in events
            ):
                self.memory.append_event(
                    {
                        "kind": EventKind.PROBLEM_PAUSED,
                        "problem_id": task.problem_id,
                        "artifact_paths": [path],
                    }
                )
        if receipt.failure != Failure.STALE_RESEARCH and (result or original.workspace_matches):
            checkpoint_id = self._checkpoint_result(receipt, original, evidence_ids)
            if task.deep_session_id:
                DeepSessionStore(self.memory, self.queue).finished(
                    task.deep_session_id, task.attempt_id, checkpoint_id
                )
        if result:
            state = (
                ExecutionState.BLOCKED
                if result.outcome == ReasoningOutcome.BLOCKED
                else ExecutionState.COMPLETED
            )
        else:
            state = {
                Failure.PAUSED: ExecutionState.PAUSED,
                Failure.CANCELLED: ExecutionState.CANCELLED,
                Failure.INTERRUPTED: ExecutionState.PAUSED,
                Failure.UNAVAILABLE: ExecutionState.BLOCKED,
                Failure.STALE_RESEARCH: ExecutionState.BLOCKED,
            }.get(receipt.failure, ExecutionState.FAILED)
        return self.queue.settle(job, receipt, state)

    def _checkpoint_result(
        self,
        receipt: AttemptReceipt,
        original: RestoredState,
        evidence_ids: tuple[str, ...],
    ) -> str:
        task = receipt.task
        events = self.memory._events(self.memory.research.read())
        state = replay(events)[task.problem_id]
        checkpoint = original.checkpoint
        if state.latest_checkpoint:
            latest = self.memory.restore(task.problem_id, state.latest_checkpoint).checkpoint
            if latest.last_attempt and latest.last_attempt.attempt_id == task.attempt_id:
                if latest.last_attempt.outcome is not None:
                    return latest.checkpoint_id
                if task.deep_session_id and latest.deep_session_id == task.deep_session_id:
                    checkpoint = latest
        result = receipt.result
        candidates = [item.model_dump() for item in checkpoint.candidate_results]
        established = []
        if checkpoint.established_results:
            graph = self.memory.research.graph()
            for item in checkpoint.established_results:
                review = graph.review(item.assertion_id)
                records = graph.evidence_for(item.assertion_id)
                if (
                    review.status == ReviewStatus.CURRENT
                    and any(
                        evidence.evidence_id in item.evidence_references
                        and evidence.applies(review.revision, "SUPPORT")
                        for evidence in records
                    )
                    and not any(evidence.applies(review.revision, "REFUTE") for evidence in records)
                ):
                    established.append(item)
                else:
                    candidates.append(item.model_dump())
        if evidence_ids:
            candidates.append(
                {
                    "assertion_id": task.problem_id,
                    "summary": result.summary,
                    "evidence_references": evidence_ids,
                }
            )
        final_checkpoint = self.memory.create_checkpoint(
            task.problem_id,
            {
                "active_route": task.route_id,
                "next_research_action": result.next_action
                if result
                else checkpoint.next_research_action,
                "unresolved_proof_obligations": tuple(
                    dict.fromkeys(
                        (*checkpoint.unresolved_proof_obligations, *result.unresolved_steps)
                    )
                )
                if result and task.deep_session_id
                else result.unresolved_steps
                if result
                else checkpoint.unresolved_proof_obligations,
                "strategy_version": task.strategy_version,
                "dispatch_id": task.dispatch_id,
                "dispatch_phase": "AFTER_ATTEMPT" if task.dispatch_id else None,
                "deep_session_id": task.deep_session_id,
                "context_sources": checkpoint.context_sources,
                "established_results": established,
                "candidate_results": candidates,
                "usage": {
                    "tokens_used": checkpoint.budget_snapshot.tokens_used
                    + (receipt.usage.tokens or 0),
                    "elapsed_seconds": checkpoint.budget_snapshot.elapsed_seconds
                    + math.ceil(receipt.elapsed_ms / 1000),
                },
            },
        )
        return final_checkpoint.checkpoint_id
