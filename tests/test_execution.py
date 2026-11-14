import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from amre.errors import ResearchError
from amre.execution import adapters
from amre.execution.adapters import (
    AdapterOutput,
    CodexCLIAdapter,
    MockModelAdapter,
    run_process,
)
from amre.execution.commands import budget_summary
from amre.execution.models import (
    AdapterName,
    AttemptReceipt,
    ExecutionJob,
    Failure,
    ReasoningTask,
    Usage,
)
from amre.execution.orchestrator import ResearchOrchestrator, receipt_path
from amre.execution.queue import TaskQueue
from amre.research.journal import read_versioned
from amre.research.store import ResearchStore

_PROBLEM = "P-T"


def _amre(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "amre", *args],
        cwd=root,
        env={**os.environ, "AMRE_WORKSPACE_ROOT": str(root), "PYTHONUTF8": "1"},
        capture_output=True,
        encoding="utf-8",
        timeout=40,
        check=False,
    )


def test_mock_runner_publishes_and_cold_resumes(example_store: ResearchStore) -> None:
    root = example_store.root
    created = _amre(
        root,
        "checkpoint",
        "create",
        _PROBLEM,
        str(Path(__file__).resolve().parents[1] / "examples" / "and_or" / "checkpoint-notes.yaml"),
    )
    assert created.returncode == 0, created.stderr
    result = _amre(root, "run", "--problem", _PROBLEM, "--adapter", "mock")
    assert result.returncode == 0, result.stderr
    job = json.loads(result.stdout)
    assert job["state"] == "COMPLETED"
    restored = ResearchOrchestrator(root).memory.restore(_PROBLEM)
    assert restored.checkpoint.last_attempt.attempt_id == job["attempt_id"]
    assert restored.checkpoint.next_research_action.startswith("Check each stated premise")
    assert restored.checkpoint.active_obstacles
    evidence = restored.graph.evidence_for(_PROBLEM)
    assert len(evidence) == 1 and evidence[0].source_type == "MODEL"
    assert not evidence[0].properties
    assert restored.checkpoint.contract.claim_status == "PROOF_CANDIDATE"
    assert b"Mock derivation" in restored.read_artifact(evidence[0].file_path)
    receipt = read_versioned(root, receipt_path(job["attempt_id"]), AttemptReceipt)
    assert receipt.task.checkpoint_id == created.stdout.strip()
    assert "REQUIRES_ALL L1" in receipt.task.context
    resumed = _amre(root, "resume", "--problem", _PROBLEM)
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout) == job
    assert budget_summary(root)["attempts"] == 1


def test_program_dry_run_leaves_workspace_unchanged(example_store: ResearchStore) -> None:
    previous = example_store.manifest.read_bytes()
    result = _amre(example_store.root, "run", "--program", "Algebra", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["problems"] == [_PROBLEM]
    assert example_store.manifest.read_bytes() == previous
    assert not TaskQueue(example_store.root).path.exists()


class FixedAdapter:
    name = AdapterName.MOCK

    def __init__(self, output: AdapterOutput):
        self.output = output

    def run(self, task, **kwargs):
        return self.output


@pytest.mark.parametrize(
    ("text", "failure"),
    [
        ("{", Failure.INVALID_RESULT),
        ('{"outcome":"NO_PROGRESS","summary":"x","summary":"y"}', Failure.INVALID_RESULT),
        (
            json.dumps(
                {
                    "outcome": "PROOF_CANDIDATE",
                    "summary": "x",
                    "next_action": "x",
                    "artifacts": [{"name": "../escape.md", "text": "x"}],
                }
            ),
            Failure.INVALID_RESULT,
        ),
    ],
)
def test_invalid_output_is_archived_without_applying_it(
    example_store: ResearchStore,
    text: str,
    failure: Failure,
) -> None:
    runner = ResearchOrchestrator(example_store.root)
    job = runner.run(
        _PROBLEM,
        FixedAdapter(
            AdapterOutput(
                stdout=text.encode(), exit_code=0, result_json=text, usage=Usage(tool_calls=0)
            )
        ),
    )
    assert job.state == "FAILED"
    receipt = read_versioned(runner.root, receipt_path(job.attempt_id), AttemptReceipt)
    assert receipt.failure == failure and receipt.stdout == text
    assert not runner.memory.research.graph().evidence_for(_PROBLEM)
    assert not (runner.root / "escape.md").exists()
    assert runner.memory.restore(_PROBLEM).checkpoint.last_attempt.outcome == "FAILED"


def test_failure_redacts_credentials_and_reports_unknown_usage(
    example_store: ResearchStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "a-fixture-secret-784"
    monkeypatch.setenv("AMRE_TEST_TOKEN", secret)
    runner = ResearchOrchestrator(example_store.root)
    job = runner.run(
        _PROBLEM,
        FixedAdapter(
            AdapterOutput(
                stderr=f"Authorization: Bearer {secret}".encode(),
                exit_code=7,
            )
        ),
    )
    record = runner.root / receipt_path(job.attempt_id)
    assert job.detail == "NONZERO_EXIT"
    assert secret not in record.read_text(encoding="utf-8")
    assert budget_summary(runner.root)["tokens"] == "UNKNOWN"


@pytest.mark.parametrize(
    ("code", "timeout", "limit", "failure"),
    [
        ("import time; time.sleep(5)", 0.15, 8192, Failure.TIMEOUT),
        ("raise SystemExit(7)", 5, 8192, Failure.NONZERO_EXIT),
        ("import sys; sys.stdout.buffer.write(b'x'*1000000)", 5, 1024, Failure.OUTPUT_LIMIT),
    ],
)
def test_bounded_subprocess(
    tmp_path: Path,
    code: str,
    timeout: float,
    limit: int,
    failure: Failure,
) -> None:
    result = run_process(
        [sys.executable, "-c", code],
        cwd=tmp_path.resolve(),
        env=os.environ.copy(),
        data=b"",
        timeout=timeout,
        max_bytes=limit,
        interrupted=lambda: None,
    )
    assert result.failure == failure
    assert len(result.stdout) + len(result.stderr) <= limit


def test_codex_live_gate_recursion_and_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ResearchError, match="requires --adapter"):
        CodexCLIAdapter("codex", live=False)
    monkeypatch.setenv("AMRE_MODEL_CHILD", "1")
    with pytest.raises(ResearchError, match="Nested"):
        CodexCLIAdapter("codex", live=True)
    for name in ("AMRE_MODEL_CHILD", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TOOL_CALL_ID"):
        monkeypatch.delenv(name, raising=False)
    adapter = CodexCLIAdapter("amre-nonexistent-codex", live=True)
    result = adapter.run(None, timeout=1, max_bytes=1024, interrupted=lambda: None)
    assert result.failure == Failure.UNAVAILABLE


@pytest.mark.parametrize("purpose", ["research", "intake"])
def test_codex_arguments_and_jsonl_with_fake_transport(
    monkeypatch: pytest.MonkeyPatch, purpose: str
) -> None:
    for name in ("AMRE_MODEL_CHILD", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TOOL_CALL_ID"):
        monkeypatch.delenv(name, raising=False)
    calls = []
    options = (
        b"--sandbox --json --ignore-user-config --ignore-rules --skip-git-repo-check "
        b"--ephemeral --disable --strict-config"
    )
    features = b"shell_tool\nhooks\nmulti_agent\napps\nplugins\nskill_mcp_dependency_install"
    content = '{"outcome":"NO_PROGRESS","summary":"fixture","next_action":"review"}'
    stream = "\n".join(
        json.dumps(item)
        for item in (
            {"type": "item.completed", "item": {"type": "agent_message", "text": content}},
            {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 3}},
        )
    ).encode()

    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        data = (options, features, stream)[len(calls) - 1]
        return AdapterOutput(stdout=data, exit_code=0)

    monkeypatch.setattr(adapters, "run_process", fake)
    result = CodexCLIAdapter(sys.executable, live=True).run(
        ReasoningTask(
            purpose=purpose,
            attempt_id="AT-Test",
            problem_id="P-T",
            checkpoint_id="CP-Test",
            assertion_revision=1,
            strategy_version="test",
            objective="Review",
            context="Math",
        ),
        timeout=2,
        max_bytes=4096,
        interrupted=lambda: None,
    )
    assert result.result_json == content
    assert result.usage.tokens == 7 and result.usage.cost_microusd is None
    argv, options = calls[-1]
    assert argv[1] == "exec" and "read-only" in argv and "--ignore-user-config" in argv
    assert "multi_agent" in argv and "shell_tool" in argv
    assert argv[-1] == "-" and b'"objective":"Review"' in options["data"]
    assert options["env"]["AMRE_MODEL_CHILD"] == "1"
    if purpose == "intake":
        assert options["data"].startswith(b"Prepare or revise an intake draft.")
        assert b"Do not perform research or solve proofs." in options["data"]
    else:
        assert options["data"].startswith(b"Perform one mathematical research attempt")


@pytest.mark.parametrize("command", ["pause", "stop"])
def test_duplicate_start_and_requested_interruption(
    example_store: ResearchStore,
    command: str,
) -> None:
    entered = threading.Event()

    class WaitingAdapter:
        name = AdapterName.MOCK

        def run(self, task, *, timeout, max_bytes, interrupted):
            entered.set()
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if failure := interrupted():
                    return AdapterOutput(failure=failure)
                time.sleep(0.02)
            return AdapterOutput(failure=Failure.TIMEOUT)

    runner = ResearchOrchestrator(example_store.root)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run, _PROBLEM, WaitingAdapter())
        assert entered.wait(12)
        with pytest.raises(ResearchError, match="lock is busy"):
            ResearchOrchestrator(runner.root).run(_PROBLEM, MockModelAdapter())
        control = _amre(runner.root, command, "--problem", _PROBLEM)
        assert control.returncode == 0, control.stderr
        result = future.result(timeout=15)
    assert result.state == ("PAUSED" if command == "pause" else "CANCELLED")
    assert len(runner.queue.read().reservations) == 1
    assert runner.queue.read().reservations[0].settled_ms is not None
    if command == "pause":
        resumed = runner.run(_PROBLEM, MockModelAdapter(), resume=True)
        assert resumed.state == "COMPLETED"


@pytest.mark.parametrize("recoverable", [True, False])
def test_interrupted_worker_recovery_does_not_dispatch_again(
    example_store: ResearchStore,
    recoverable: bool,
) -> None:
    script = """
import os
import sys
from pathlib import Path
from amre.execution.adapters import MockModelAdapter
from amre.execution.orchestrator import ResearchOrchestrator

class Worker(ResearchOrchestrator):
    def _finalize(self, *args):
        os._exit(75)

class LostAdapter(MockModelAdapter):
    def run(self, *args, **kwargs):
        os._exit(75)

Worker(Path.cwd()).run('P-T', MockModelAdapter() if sys.argv[1] == 'yes' else LostAdapter())
"""
    child = subprocess.run(
        [sys.executable, "-c", script, "yes" if recoverable else "no"],
        cwd=example_store.root,
        capture_output=True,
        timeout=35,
        check=False,
    )
    assert child.returncode == 75, child.stderr
    runner = ResearchOrchestrator(example_store.root)
    before = runner.queue.read().reservations[0]
    result = runner.run(_PROBLEM, None, resume=True)
    assert result.attempt_id == before.attempt_id
    assert result.state == ("COMPLETED" if recoverable else "PAUSED")
    settled = runner.queue.read().reservations
    assert len(settled) == 1
    if not recoverable:
        assert settled[0].settled_ms == before.reserved_ms
    else:
        assert runner.run(_PROBLEM, None, resume=True) == result


def test_budget_exhaustion_and_workspace_io(example_store: ResearchStore, tmp_path: Path) -> None:
    path = example_store.root / "research" / "execution.yaml"
    path.write_text("problem:\n  attempts: 1\n  seconds: 200\n", encoding="utf-8")
    runner = ResearchOrchestrator(example_store.root)
    assert runner.run(_PROBLEM, MockModelAdapter()).state == "COMPLETED"
    exhausted = runner.run(_PROBLEM, MockModelAdapter())
    assert exhausted.state == "BLOCKED"
    assert exhausted.detail == "problem attempt budget exhausted"
    assert len(runner.queue.read().reservations) == 1
    locked_root = tmp_path / "invalid"
    locked_root.mkdir()
    (locked_root / ".amre").write_text("not a directory", encoding="utf-8")
    with pytest.raises(ResearchError, match="workspace I/O failed"):
        TaskQueue(locked_root).control(ExecutionJob(problem_id=_PROBLEM), "PAUSED")
