import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from amre.execution import adapters
from amre.execution.adapters import AdapterOutput, CodexCLIAdapter, MockModelAdapter, run_process
from amre.execution.deep import DeepResearchRunner
from amre.execution.deep_models import Continuity, SemanticKind, SemanticRecord
from amre.execution.models import ExecutionState, Failure
from amre.research.journal import read_versioned

P = "P-T"
DERIVATION = r"Under the real-domain hypothesis, \(x^2\geq 0\)."
MILESTONE = {
    "kind": "PROOF_PROGRESS",
    "summary": "First lemma reduced to a nonnegative square.",
    "assumptions": ["H-Real"],
    "unresolved": ["Prove the boundary implication."],
    "next_action": "Check the boundary implication.",
    "artifact": {"name": "first.tex", "text": DERIVATION},
}


def _wait_for(read):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if result := read():
            return result
        time.sleep(0.025)
    raise AssertionError("Timed out waiting for durable session state")


def _events(runner, session):
    return [
        read_versioned(runner.store.root, path, SemanticRecord).update
        for path in session.semantic_events
    ]


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "amre", *args],
        cwd=root,
        env={**os.environ, "AMRE_WORKSPACE_ROOT": str(root), "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=35,
        check=True,
    )


def test_cli_deep_default_and_explicit_short(example_store):
    root = example_store.root
    deep = json.loads(_cli(root, "run", "--problem", P).stdout)
    session_id = deep["deep_session_id"]
    assert deep["state"] == "COMPLETED" and session_id.startswith("DS-")
    session = json.loads(_cli(root, "session", "inspect", session_id).stdout)
    assert session["continuity"] == "FRESH"
    assert len(session["semantic_events"]) == 6
    short = json.loads(_cli(root, "run", "--problem", P, "--workflow", "short").stdout)
    assert short["state"] == "COMPLETED" and short["deep_session_id"] is None


def test_pause_and_reconstruct_keeps_one_reservation_per_continuous_invocation(example_store):
    runner = DeepResearchRunner(example_store.root)
    entered = Event()
    dispatched = []

    class PauseAfterMilestone(MockModelAdapter):
        def run_deep(self, task, *, on_event, interrupted, **options):
            dispatched.append(task)
            on_event(MILESTONE)
            on_event(
                {
                    "kind": "STRATEGY_SWITCH",
                    "summary": "Search for a counterexample at the boundary.",
                    "strategy": "COUNTEREXAMPLE_SEARCH",
                }
            )
            entered.set()
            if failure := _wait_for(interrupted):
                return AdapterOutput(failure=failure)

    class Complete(MockModelAdapter):
        def run_deep(self, task, *, on_event, **options):
            dispatched.append(task)
            on_event(
                {
                    "kind": "LOCAL_STEP",
                    "summary": "Trailing candidate algebra.",
                    "artifact": {"name": "tail.tex", "text": r"Keep \(a+b=c\) intact."},
                }
            )
            return self.run(task, **options)

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(runner.start, P, PauseAfterMilestone())
        assert entered.wait(15)
        session_id = dispatched[0].deep_session_id
        assert _wait_for(
            lambda: runner.inspect(session_id).last_checkpoint != dispatched[0].checkpoint_id
        )
        live = runner.inspect(session_id)
        assert not running.done()
        assert live.session_status == ExecutionState.RUNNING
        assert live.elapsed_time > 0
        assert len(runner.runner.queue.read().reservations) == 1
        assert runner.pause(session_id).request == ExecutionState.PAUSED
        assert running.result(30).state == ExecutionState.PAUSED

    restarted = DeepResearchRunner(example_store.root)
    paused = restarted.inspect(session_id)
    assert any(update.kind == SemanticKind.USER_PAUSE for update in _events(restarted, paused))
    assert any(update.assumptions == ("H-Real",) for update in _events(restarted, paused))
    assert restarted.resume(P, Complete(), expected_session_id=session_id).state == "COMPLETED"
    finished = restarted.inspect(session_id)
    assert finished.continuity == Continuity.RECONSTRUCTED
    assert finished.native_session_id is None and finished.context_loss_risk
    assert DERIVATION in dispatched[1].context
    assert "Prove the boundary implication." in dispatched[1].context
    restored = restarted.runner.memory.restore(P, finished.last_checkpoint)
    assert any(
        restored.read_text(item.path) == r"Keep \(a+b=c\) intact."
        for item in restored.checkpoint.context_sources
        if item.path.endswith("tail.tex")
    )
    assert restored.checkpoint.contract.claim_status == "PROOF_CANDIDATE"
    assert all(not evidence.properties for evidence in restored.graph.evidence_for(P))
    assert restarted.resume(P, Complete()).state == "COMPLETED"
    charges = restarted.runner.queue.read().reservations
    assert len(dispatched) == len(charges) == 2
    assert finished.reasoning_budget.charged_ms == sum(item.settled_ms for item in charges)
    assert finished.reasoning_budget.tokens == "UNKNOWN"


def test_hard_worker_loss_restores_original_milestone_once(example_store):
    script = f"""
import os
import time
from pathlib import Path
from amre.execution.adapters import MockModelAdapter
from amre.execution.deep import DeepResearchRunner

runner = DeepResearchRunner(Path.cwd())
class Interrupt(MockModelAdapter):
    def run_deep(self, task, *, on_event, **options):
        on_event({MILESTONE!r})
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if runner.inspect(task.deep_session_id).last_checkpoint != task.checkpoint_id:
                os._exit(77)
            time.sleep(0.025)
        os._exit(78)

runner.start({P!r}, Interrupt())
"""
    child = subprocess.run(
        [sys.executable, "-c", script],
        cwd=example_store.root,
        capture_output=True,
        timeout=25,
        check=False,
    )
    assert child.returncode == 77, child.stderr
    runner = DeepResearchRunner(example_store.root)
    original = runner.runner.queue.read().reservations[0]
    job = runner.resume(P, None, expected_session_id=original.deep_session_id)
    assert job.state == ExecutionState.PAUSED and job.detail == Failure.INTERRUPTED
    session = runner.inspect(original.deep_session_id)
    restored = runner.runner.memory.restore(P, session.last_checkpoint)
    assert restored.checkpoint.next_research_action == MILESTONE["next_action"]
    assert MILESTONE["unresolved"][0] in restored.checkpoint.unresolved_proof_obligations
    assert any(restored.read_text(path) == DERIVATION for path in session.artifacts)
    assert sum(update.kind == SemanticKind.INTERRUPTED for update in _events(runner, session)) == 1
    charges = runner.runner.queue.read().reservations
    assert len(charges) == 1 and charges[0].settled_ms == original.reserved_ms


def test_hard_deadline_limits_process_and_session_resume(example_store):
    root = example_store.root
    (root / "research" / "execution.yaml").write_text(
        "deep:\n  session_seconds: 1\nattempt:\n  attempts: 1\n  seconds: 30\n",
        encoding="utf-8",
    )
    runner = DeepResearchRunner(root)
    dispatched = []

    class Slow(MockModelAdapter):
        def run_deep(self, task, *, on_event, **options):
            dispatched.append(task)
            return run_process(
                [sys.executable, "-c", "import time; time.sleep(20)"],
                cwd=root,
                env=dict(os.environ),
                data=b"",
                **options,
            )

    job = runner.start(P, Slow())
    assert job.state == ExecutionState.FAILED and job.detail == Failure.TIMEOUT
    session = runner.inspect(job.deep_session_id)
    assert session.reasoning_budget.session_ms == 1000
    assert session.reasoning_budget.charged_ms >= 1000
    assert any(update.kind == SemanticKind.BUDGET_BOUNDARY for update in _events(runner, session))
    resumed = runner.resume(P, Slow(), expected_session_id=job.deep_session_id)
    assert resumed.state == ExecutionState.BLOCKED and "session" in resumed.detail
    assert len(dispatched) == len(runner.runner.queue.read().reservations) == 1


@pytest.mark.parametrize("matching", [True, False])
def test_codex_native_resume_checks_provider_identity_offline(example_store, monkeypatch, matching):
    for name in ("AMRE_MODEL_CHILD", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TOOL_CALL_ID"):
        monkeypatch.delenv(name, raising=False)
    root = example_store.root
    (root / "research" / "execution.yaml").write_text(
        "deep:\n  native_codex_sessions: true\n", encoding="utf-8"
    )
    expected = "73de78ce-3a9f-4d84-823b-b62345bc6079"
    launches = []

    def fake_transport(argv, *, data, stdout_line, interrupted, **kwargs):
        if "--help" in argv:
            return AdapterOutput(
                stdout=b"--sandbox --json --ignore-user-config --ignore-rules "
                b"--skip-git-repo-check --ephemeral --disable --strict-config --config SESSION_ID",
                exit_code=0,
            )
        if argv[1:3] == ["features", "list"]:
            return AdapterOutput(
                stdout=b"shell_tool\nhooks\nmulti_agent\napps\nplugins\nskill_mcp_dependency_install",
                exit_code=0,
            )
        launches.append(argv)
        assert "--ephemeral" not in argv
        assert 'approval_policy="never"' in argv and "mcp_servers={}" in argv
        assert b'"problem_id":"P-T"' in data
        assert len(launches) <= 2
        resume = len(launches) == 2
        if resume:
            assert argv[1:3] == ["exec", "resume"]
            assert argv[-2:] == [expected, "-"]
            assert 'sandbox_mode="read-only"' in argv
        identity = expected if matching or not resume else "32f81cd0-9b98-426a-9cc5-24d312ae98e9"
        events = [
            {"type": "thread.started", "thread_id": identity},
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps({"semantic_event": MILESTONE}),
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {"outcome": "NO_PROGRESS", "summary": "Fixture", "next_action": "Continue"}
                    ),
                },
            },
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
        ]
        lines = []
        for event in events:
            lines.append(json.dumps(event).encode())
            stdout_line(lines[-1])
            if failure := interrupted():
                return AdapterOutput(failure=failure)
        if not resume:
            return AdapterOutput(failure=Failure.PAUSED)
        return AdapterOutput(stdout=b"\n".join(lines), exit_code=0)

    monkeypatch.setattr(adapters, "run_process", fake_transport)
    adapter = CodexCLIAdapter(sys.executable, live=True)
    runner = DeepResearchRunner(root)
    started = runner.start(P, adapter)
    assert started.state == "PAUSED"
    assert runner.inspect(started.deep_session_id).native_session_id == expected
    resumed = runner.resume(P, adapter, expected_session_id=started.deep_session_id)
    session = runner.inspect(started.deep_session_id)
    assert len(launches) == len(runner.runner.queue.read().reservations) == 2
    if matching:
        assert resumed.state == "COMPLETED" and session.continuity == Continuity.NATIVE
        assert session.native_session_id == expected
    else:
        assert resumed.detail == Failure.INVALID_RESULT
        assert session.continuity == Continuity.RECONSTRUCTED and session.native_session_id is None


def test_session_can_checkpoint_and_stop_without_a_second_dispatch(example_store):
    runner = DeepResearchRunner(example_store.root)
    tasks = []

    class Waiting(MockModelAdapter):
        def run_deep(self, task, *, interrupted, **options):
            tasks.append(task)
            return AdapterOutput(failure=_wait_for(interrupted))

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.start, P, Waiting())
        task = _wait_for(lambda: tasks[0] if tasks else None)
        session_id = task.deep_session_id
        cp = runner.checkpoint(session_id)
        assert cp != task.checkpoint_id and not future.done()
        assert runner.stop(session_id).request == ExecutionState.CANCELLED
        assert future.result(30).state == ExecutionState.CANCELLED
    session = runner.inspect(session_id)
    assert [event.kind for event in _events(runner, session)] == [
        SemanticKind.USER_CHECKPOINT,
        SemanticKind.USER_STOP,
    ]
    assert runner.resume(P, Waiting()).state == ExecutionState.CANCELLED
    assert len(tasks) == len(runner.runner.queue.read().reservations) == 1
