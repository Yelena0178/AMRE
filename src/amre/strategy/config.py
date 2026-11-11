"""Read-only configuration overlay shared by execution and scheduling."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from amre.errors import ResearchError
from amre.execution.models import PromptTemplate
from amre.research.journal import read_versioned
from amre.research.models import Identifier, PositiveInt, VersionedRecord
from amre.research.storage import read_yaml, validate, workspace_path
from amre.scheduling.models import Strategy

ACTIVE_PATH = "research/strategies/activations.yaml"


class StrategyProfile(VersionedRecord):
    name: Identifier
    context_bytes: Annotated[int, Field(strict=True, ge=1024, le=131072)] = 16000
    retrieval_version: Literal["bounded-priority-v1"] = "bounded-priority-v1"
    prompt_template: PromptTemplate = "standard-v1"
    deep_target_percent: Annotated[int, Field(strict=True, ge=0, le=100)] = 85
    portfolio: Strategy = Strategy()
    problem_values: dict[Identifier, PositiveInt] = Field(default_factory=dict)


class StrategyVersion(VersionedRecord):
    version_id: Identifier
    profile: StrategyProfile


class Activation(VersionedRecord):
    operation: Literal["APPLY", "ROLLBACK"]
    version_id: Identifier | None
    previous: Identifier | None
    evaluation_id: Identifier | None = None


class ActivationLog(VersionedRecord):
    history: tuple[Activation, ...] = ()


def version_identity(profile: StrategyProfile) -> str:
    data = json.dumps(profile.model_dump(mode="json"), sort_keys=True)
    return "SV-" + sha256(data.encode()).hexdigest()[:24]


def version_path(version_id: str) -> str:
    if (
        not version_id.startswith("SV-")
        or len(version_id) != 27
        or any(item not in "0123456789abcdef" for item in version_id[3:])
    ):
        raise ResearchError("Invalid strategy version ID")
    return f"research/strategies/versions/{version_id}.yaml"


def load_version(root: Path, version_id: str) -> StrategyVersion:
    record = read_versioned(root, version_path(version_id), StrategyVersion)
    if record.version_id != version_id or version_identity(record.profile) != version_id:
        raise ResearchError("Strategy version does not match its content")
    return record


def activation_log(root: Path) -> ActivationLog:
    return validate(
        ActivationLog,
        read_yaml(workspace_path(root, ACTIVE_PATH), missing_ok=True) or {},
        "strategy activation log",
    )


def active_profile(root: Path) -> StrategyVersion | None:
    log = activation_log(root)
    if log.history and (version_id := log.history[-1].version_id):
        return load_version(root, version_id)
    return None
