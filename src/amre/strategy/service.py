"""Persist experiments, validate comparative results, and activate reviewed configurations."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Self

from filelock import FileLock, Timeout
from pydantic import AwareDatetime, model_validator

from amre.benchmarks.models import BenchmarkResult
from amre.benchmarks.service import TrialPolicy, run_benchmark
from amre.errors import ResearchError
from amre.execution.models import ExecutionState
from amre.execution.queue import TaskQueue, load_policy
from amre.research.journal import publish_immutable, read_versioned
from amre.research.models import Identifier, Sha256, VersionedRecord
from amre.research.storage import atomic_write, workspace_path, yaml_text
from amre.scheduling.portfolio import load_settings
from amre.strategy.config import (
    ACTIVE_PATH,
    Activation,
    StrategyProfile,
    StrategyVersion,
    activation_log,
    active_profile,
    load_version,
    version_identity,
    version_path,
)


class StrategyEvaluation(VersionedRecord):
    evaluation_id: Identifier
    candidate: Identifier
    baseline: Identifier | None = None
    baseline_profile: Identifier
    seed: int
    fixture_digest: Sha256
    baseline_digest: Sha256
    candidate_digest: Sha256
    gates: dict[str, bool]
    eligible: bool
    timestamp: AwareDatetime

    @model_validator(mode="after")
    def _verify(self) -> Self:
        if self.eligible != all(self.gates.values()) or set(self.gates) != _GATE_NAMES:
            raise ValueError("strategy evaluation gate summary is inconsistent")
        material = self.model_dump(mode="json", exclude={"evaluation_id", "timestamp"})
        if _evaluation_id(material) != self.evaluation_id:
            raise ValueError("strategy evaluation ID does not match its recorded evidence")
        return self


class StrategyProposal(VersionedRecord):
    proposed: StrategyVersion
    trajectory_digest: Sha256
    reasons: tuple[str, ...]


_GATE_NAMES = frozenset(
    {
        "comparable",
        "known_mathematical_checks",
        "within_hard_budget",
        "no_completion_regression",
        "no_restore_regression",
        "no_repeat_regression",
    }
)


def _evaluation_path(evaluation_id: str) -> str:
    if (
        not evaluation_id.startswith("EV-")
        or len(evaluation_id) != 27
        or any(item not in "0123456789abcdef" for item in evaluation_id[3:])
    ):
        raise ResearchError("Invalid strategy evaluation ID")
    return f"research/strategies/evaluations/{evaluation_id}.yaml"


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _evaluation_id(material: dict) -> str:
    return "EV-" + _digest(json.dumps(material, sort_keys=True).encode())[:24]


def _gates(baseline: BenchmarkResult, candidate: BenchmarkResult) -> dict[str, bool]:
    old = {(case.case_id, item.variant): item for case in baseline.cases for item in case.workflows}
    new = {
        (case.case_id, item.variant): item for case in candidate.cases for item in case.workflows
    }
    old_ablations = {
        (case.case_id, item.dimension): item for case in baseline.cases for item in case.ablations
    }
    new_ablations = {
        (case.case_id, item.dimension): item for case in candidate.cases for item in case.ablations
    }
    return {
        "comparable": old.keys() == new.keys() and bool(new),
        "known_mathematical_checks": all(all(case.checked.values()) for case in candidate.cases),
        "within_hard_budget": all(
            item.elapsed_ms <= item.budget_ms and item.reserved_peak_ms <= item.budget_ms
            for item in new.values()
        ),
        "no_completion_regression": all(
            key in new and (not item.completed or new[key].completed) for key, item in old.items()
        ),
        "no_restore_regression": all(
            key in new and (not item.state_restored or new[key].state_restored)
            for key, item in old.items()
        ),
        "no_repeat_regression": all(
            key in new_ablations
            and new_ablations[key].repeated_failures[1] <= item.repeated_failures[1]
            for key, item in old_ablations.items()
        ),
    }


def _workspace_profile(root: Path) -> StrategyProfile:
    policy = load_policy(root)
    portfolio = load_settings(root)
    return StrategyProfile(
        name="WorkspaceBaseline",
        context_bytes=policy.max_context_bytes,
        prompt_template=policy.prompt_template,
        deep_target_percent=policy.deep.budget_target_percent,
        portfolio=portfolio.strategy,
        problem_values=portfolio.problem_values,
    )


class StrategyRegistry:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def propose(self, profile: StrategyProfile) -> StrategyVersion:
        version_id = version_identity(profile)
        record = StrategyVersion(version_id=version_id, profile=profile)
        path = version_path(version_id)
        publish_immutable(self.root, {path: yaml_text(record.model_dump(mode="json")).encode()})
        return record

    def recommend(self, trajectory: Path, base: StrategyProfile) -> StrategyProposal:
        with trajectory.open("rb") as file:
            data = file.read(2_000_001)
        if len(data) > 2_000_000:
            raise ResearchError("Benchmark trajectory exceeds the strategy analysis limit")
        try:
            result = BenchmarkResult.model_validate_json(data)
        except ValueError:
            raise ResearchError("Invalid offline benchmark trajectory") from None
        if result.engine != "OFFLINE_MOCK" or not all(
            all(case.checked.values()) for case in result.cases
        ):
            raise ResearchError(
                "Strategy analysis needs an offline trajectory with checked fixtures"
            )
        changes = {}
        reasons = []
        enabled = [
            row
            for case in result.cases
            for row in case.workflows
            if row.variant in {"deep", "short"}
        ]
        if any(row.context_loss_events for row in enabled) and base.context_bytes < 131072:
            changes["context_bytes"] = min(131072, base.context_bytes * 5 // 4)
            reasons.append("Increase the bounded context allowance for observed source omissions.")
        for case in result.cases:
            for ablation in case.ablations:
                first, second = ablation.repeated_failures
                if ablation.dimension == "Linear versus obstruction-driven" and first > second:
                    changes["prompt_template"] = "explicit-gaps-v2"
                    reasons.append(
                        "Test an explicit gap prompt after repeated fixture obstructions."
                    )
                if (
                    ablation.dimension == "Fixed versus adaptive routing"
                    and first > second
                    and base.portfolio.stagnation_window > 2
                ):
                    changes["portfolio"] = base.portfolio.model_copy(
                        update={"stagnation_window": 2}
                    )
                    reasons.append("Test the earlier structured stagnation window in the fixture.")
        if not reasons:
            raise ResearchError("No supported strategy proposal emerged from this trajectory")
        digest = _digest(data)
        profile = base.model_copy(update={**changes, "name": f"Experiment-{digest[:12]}"})
        proposed = self.propose(profile)
        proposal = StrategyProposal(
            proposed=proposed,
            trajectory_digest=digest,
            reasons=tuple(dict.fromkeys(reasons)),
        )
        path = f"research/strategies/proposals/{proposed.version_id}.yaml"
        publish_immutable(self.root, {path: yaml_text(proposal.model_dump(mode="json")).encode()})
        return proposal

    def compare(self, before: str, after: str) -> dict:
        old = load_version(self.root, before).profile.model_dump(mode="json")
        new = load_version(self.root, after).profile.model_dump(mode="json")
        return {
            field: {"before": old[field], "after": new[field]}
            for field in new
            if new[field] != old[field]
        }

    def evaluate(self, version_id: str, fixture: Path, output: Path, seed: int, *, baseline=None):
        record = load_version(self.root, version_id)
        if baseline is None and (active := active_profile(self.root)):
            baseline = active.version_id
        reference = (
            load_version(self.root, baseline).profile if baseline else _workspace_profile(self.root)
        )
        output.mkdir(parents=True, exist_ok=False)
        results = []
        digests = []
        for name, profile in (("baseline", reference), ("candidate", record.profile)):
            policy = TrialPolicy(
                context_bytes=profile.context_bytes,
                prompt_template=profile.prompt_template,
                deep_target_percent=profile.deep_target_percent,
                strategy=profile.portfolio.model_dump(mode="json"),
                problem_values=profile.problem_values,
            )
            results.append(run_benchmark(fixture, output / name, seed, policy=policy))
            digests.append(_digest((output / name / "benchmark.json").read_bytes()))
        fixture_digest = results[0].fixture_digest
        if results[1].fixture_digest != fixture_digest:
            raise ResearchError("Benchmark fixtures changed between baseline and candidate")
        gates = _gates(*results)
        material = {
            "schema_version": 1,
            "candidate": version_id,
            "baseline": baseline,
            "baseline_profile": version_identity(reference),
            "seed": seed,
            "fixture_digest": fixture_digest,
            "baseline_digest": digests[0],
            "candidate_digest": digests[1],
            "gates": gates,
            "eligible": all(gates.values()),
        }
        result = StrategyEvaluation(
            evaluation_id=_evaluation_id(material),
            **material,
            timestamp=datetime.now(UTC),
        )
        path = _evaluation_path(result.evaluation_id)
        publish_immutable(self.root, {path: yaml_text(result.model_dump(mode="json")).encode()})
        return result

    def _update(
        self, action: str, version_id=None, evaluation_id=None, baseline=None, baseline_profile=None
    ) -> Activation:
        lock = workspace_path(self.root, ".amre/strategy.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        queue = TaskQueue(self.root)
        try:
            with queue.worker(), FileLock(lock, timeout=5):
                if any(item.state == ExecutionState.RUNNING for item in queue.read().jobs.values()):
                    raise ResearchError("Recover the running execution before changing strategy")
                journal = activation_log(self.root)
                current = journal.history[-1] if journal.history else None
                active = current.version_id if current else None
                if action == "ROLLBACK":
                    stack = []
                    for item in journal.history:
                        if item.operation == "APPLY":
                            stack.append(item.version_id)
                        elif stack:
                            stack.pop()
                    if not stack:
                        raise ResearchError("No prior strategy activation")
                    version_id = stack[-2] if len(stack) > 1 else None
                elif active == version_id:
                    return current
                elif baseline != active:
                    raise ResearchError("Evaluation baseline differs from the active strategy")
                elif (
                    baseline is None
                    and version_identity(_workspace_profile(self.root)) != baseline_profile
                ):
                    raise ResearchError("Workspace strategy changed after evaluation")
                if version_id is not None:
                    load_version(self.root, version_id)
                result = Activation(
                    operation=action,
                    version_id=version_id,
                    previous=active,
                    evaluation_id=evaluation_id,
                )
                updated = journal.model_copy(update={"history": (*journal.history, result)})
                atomic_write(
                    workspace_path(self.root, ACTIVE_PATH),
                    yaml_text(updated.model_dump(mode="json")),
                )
                return result
        except Timeout:
            raise ResearchError("Strategy activation is busy") from None

    def apply(self, version_id: str, evaluation_id: str) -> Activation:
        evidence = read_versioned(self.root, _evaluation_path(evaluation_id), StrategyEvaluation)
        if (
            evidence.evaluation_id != evaluation_id
            or evidence.candidate != version_id
            or not evidence.eligible
            or not evidence.gates
            or not all(evidence.gates.values())
        ):
            raise ResearchError(
                "Applying a strategy requires a passing evaluation for that version"
            )
        return self._update(
            "APPLY", version_id, evaluation_id, evidence.baseline, evidence.baseline_profile
        )

    def rollback(self) -> Activation:
        return self._update("ROLLBACK")
