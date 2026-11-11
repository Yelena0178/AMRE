"""Immutable event files and content-addressed research artifacts."""

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from amre.errors import ResearchError
from amre.research.graph import GraphNode
from amre.research.memory_models import (
    ArchiveRef,
    AttemptRecord,
    EventInput,
    EventKind,
    ResearchEvent,
)
from amre.research.models import ResearchContract, VersionedRecord
from amre.research.storage import atomic_write, read_yaml, validate, workspace_path, yaml_text

_Versioned = TypeVar("_Versioned", bound=VersionedRecord)


def event_path(event_id: str) -> str:
    return f"research/events/{event_id}.yaml"


def checkpoint_path(checkpoint_id: str) -> str:
    return f"research/checkpoints/{checkpoint_id}.yaml"


def repository_revision(root: Path) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, "rev-parse", "--verify", "HEAD"],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip()
    if result.returncode == 0 and re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
        return revision.decode("ascii")
    return None


def read_versioned(root: Path, relative: str, model: type[_Versioned]) -> _Versioned:
    raw = read_yaml(workspace_path(root, relative))
    if raw is None or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ResearchError(f"Unsupported or missing schema_version in {relative}")
    return validate(model, raw, relative)


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        raise ResearchError(f"Cannot read research artifact: {path}") from None


class ArchiveBatch:
    """Stage only referenced artifacts and immutable records for publication."""

    def __init__(self, root: Path, pending: Mapping[str, str] | None = None):
        self.root = root
        self.pending = pending if pending is not None else {}
        self.files: dict[str, bytes] = {}
        self._refs: dict[str, ArchiveRef] = {}

    def include(self, path: str) -> ArchiveRef:
        if path not in self._refs:
            source = workspace_path(self.root, path)
            contents = self.files.get(path)
            if contents is None:
                text = self.pending.get(path)
                contents = _read_bytes(source) if source.exists() or text is None else text.encode()
            ref = ArchiveRef(
                path=path, sha256=hashlib.sha256(contents).hexdigest(), size=len(contents)
            )
            self.files[ref.blob_path] = contents
            self._refs[path] = ref
        return self._refs[path]

    def record(self, path: str, record: VersionedRecord) -> None:
        self.files[path] = yaml_text(record.model_dump(mode="json")).encode("utf-8")

    def event(
        self,
        data: EventInput,
        *,
        sequence: int,
        anchor: str,
        revision: str | None,
        subject: ResearchContract | GraphNode | None = None,
    ) -> ResearchEvent:
        paths = dict.fromkeys((anchor, *data.artifact_paths))
        event = ResearchEvent(
            event_id=f"EV-{uuid4().hex}",
            sequence=sequence,
            timestamp=datetime.now(UTC),
            repository_revision=revision,
            data=data,
            artifacts=tuple(self.include(path) for path in paths),
            subject=subject,
        )
        self.record(event_path(event.event_id), event)
        return event


def publish_immutable(root: Path, files: Mapping[str, bytes]) -> None:
    for relative, data in files.items():
        path = workspace_path(root, relative)
        if not path.exists():
            atomic_write(path, data)
        elif _read_bytes(path) != data:
            raise ResearchError(
                f"Immutable research file already has different content: {relative}"
            )


def verify_blob(root: Path, ref: ArchiveRef) -> None:
    path = workspace_path(root, ref.blob_path)
    try:
        with path.open("rb") as file:
            size = os.fstat(file.fileno()).st_size
            digest = hashlib.file_digest(file, "sha256").hexdigest()
    except OSError:
        raise ResearchError(f"Missing or unreadable archive artifact: {ref.path}") from None
    if digest != ref.sha256 or size != ref.size:
        raise ResearchError(f"Archive digest or size mismatch: {ref.path}")


def archived_bytes(root: Path, ref: ArchiveRef) -> bytes:
    data = _read_bytes(workspace_path(root, ref.blob_path))
    if len(data) != ref.size or hashlib.sha256(data).hexdigest() != ref.sha256:
        raise ResearchError(f"Archive digest or size mismatch: {ref.path}")
    return data


def matches_live_artifact(root: Path, ref: ArchiveRef) -> bool:
    try:
        with workspace_path(root, ref.path).open("rb") as file:
            return (
                os.fstat(file.fileno()).st_size == ref.size
                and hashlib.file_digest(file, "sha256").hexdigest() == ref.sha256
            )
    except (OSError, ResearchError):
        return False


def load_events(root: Path, event_ids: tuple[str, ...]) -> tuple[ResearchEvent, ...]:
    events = []
    for sequence, event_id in enumerate(event_ids, start=1):
        event = read_versioned(root, event_path(event_id), ResearchEvent)
        if event.event_id != event_id or event.sequence != sequence:
            raise ResearchError(f"Event ID or sequence mismatch: {event_id}")
        events.append(event)
    return tuple(events)


@dataclass
class ReplayState:
    created: bool = False
    paused: bool = False
    active_route: str | None = None
    latest_checkpoint: str | None = None
    last_attempt: AttemptRecord | None = None
    failed_attempts: list[ResearchEvent] = field(default_factory=list)


def replay(events: Iterable[ResearchEvent]) -> dict[str, ReplayState]:
    states: dict[str, ReplayState] = {}
    seen_attempts: set[tuple[str, str]] = set()
    seen_checkpoints: set[str] = set()
    event_ids = set()
    for sequence, event in enumerate(events, start=1):
        if event.sequence != sequence or event.event_id in event_ids:
            raise ResearchError(f"Event ID or sequence mismatch: {event.event_id}")
        event_ids.add(event.event_id)
        data = event.data
        state = states.setdefault(data.problem_id, ReplayState())
        match data.kind:
            case EventKind.PROBLEM_CREATED:
                if state.created:
                    raise ResearchError(f"Duplicate creation event for {data.problem_id}")
                state.created = True
            case EventKind.ATTEMPT_STARTED:
                key = (data.problem_id, data.target_id)
                if key in seen_attempts or (state.last_attempt and not state.last_attempt.outcome):
                    raise ResearchError(f"Repeated or overlapping attempt: {data.target_id}")
                seen_attempts.add(key)
                state.last_attempt = AttemptRecord(
                    attempt_id=data.target_id, started_event=event.event_id
                )
                state.paused = False
            case EventKind.ATTEMPT_COMPLETED:
                attempt = state.last_attempt
                if attempt is None or attempt.attempt_id != data.target_id or attempt.outcome:
                    raise ResearchError(f"No matching active attempt: {data.target_id}")
                state.last_attempt = AttemptRecord(
                    attempt_id=attempt.attempt_id,
                    started_event=attempt.started_event,
                    completed_event=event.event_id,
                    outcome=data.outcome,
                    summary=data.summary,
                )
                if data.outcome == "FAILED":
                    state.failed_attempts.append(event)
            case EventKind.ROUTE_SWITCHED:
                state.active_route = data.target_id
            case EventKind.PROBLEM_PAUSED:
                state.paused = True
            case EventKind.CHECKPOINT_CREATED:
                if data.target_id in seen_checkpoints:
                    raise ResearchError(f"Duplicate checkpoint event: {data.target_id}")
                seen_checkpoints.add(data.target_id)
                state.latest_checkpoint = data.target_id
                state.active_route = data.active_route
    return states
