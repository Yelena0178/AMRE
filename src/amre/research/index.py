"""Rebuild a disposable SQLite view of validated research files."""

from __future__ import annotations

import os
import tempfile
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from amre.errors import ResearchError
from amre.research.journal import checkpoint_path, event_path
from amre.research.memory import MemoryInventory, ResearchMemory
from amre.research.models import ResearchStatus
from amre.research.storage import workspace_path

if TYPE_CHECKING:
    import sqlite3

_INDEX = ".amre/research-index.sqlite3"


def summarize(memory: ResearchMemory, inventory: MemoryInventory) -> dict[str, object]:
    problems = {}
    for contract in inventory.document.contracts:
        state = inventory.states.get(contract.problem_id)
        problems[contract.problem_id] = {
            "status": ResearchStatus.PAUSED if state and state.paused else contract.research_status,
            "active_route": state.active_route if state else None,
            "latest_checkpoint": state.latest_checkpoint if state else None,
            "last_attempt": state.last_attempt.model_dump(mode="json")
            if state and state.last_attempt
            else None,
        }
    return {
        "problems": problems,
        "nodes": len(inventory.graph.document.nodes),
        "events": len(inventory.events),
        "checkpoints": len(inventory.checkpoints),
        "index_present": workspace_path(memory.root, _INDEX).is_file(),
    }


def _populate(
    db: sqlite3.Connection, inventory: MemoryInventory, summary: dict[str, object]
) -> None:
    db.executescript("""
        CREATE TABLE problems (
            id TEXT PRIMARY KEY, status TEXT, active_route TEXT, latest_checkpoint TEXT
        );
        CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, revision INTEGER, review_status TEXT);
        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY, id TEXT UNIQUE, kind TEXT, problem_id TEXT,
            target_id TEXT, timestamp TEXT, path TEXT
        );
        CREATE TABLE checkpoints (
            id TEXT PRIMARY KEY, problem_id TEXT, sequence INTEGER, schema_version INTEGER,
            repository_revision TEXT, timestamp TEXT, path TEXT
        );
        CREATE TABLE artifacts (
            original_path TEXT, sha256 TEXT, archive_path TEXT, size INTEGER,
            PRIMARY KEY (original_path, sha256)
        );
    """)
    db.executemany(
        "INSERT INTO problems VALUES (?, ?, ?, ?)",
        (
            (identifier, state["status"], state["active_route"], state["latest_checkpoint"])
            for identifier, state in summary["problems"].items()
        ),
    )
    db.executemany(
        "INSERT INTO nodes VALUES (?, ?, ?, ?)",
        (
            (
                node.id,
                node.kind,
                inventory.graph.review(node.id).revision,
                inventory.graph.review(node.id).status,
            )
            for node in inventory.graph.document.nodes
        ),
    )
    db.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            (
                event.sequence,
                event.event_id,
                event.data.kind,
                event.data.problem_id,
                event.data.target_id,
                event.timestamp.isoformat(),
                event_path(event.event_id),
            )
            for event in inventory.events
        ),
    )
    db.executemany(
        "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            (
                item.checkpoint_id,
                item.problem_id,
                item.event_sequence,
                item.schema_version,
                item.repository_revision,
                item.timestamp.isoformat(),
                checkpoint_path(item.checkpoint_id),
            )
            for item in inventory.checkpoints
        ),
    )
    db.executemany(
        "INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?)",
        (
            (ref.path, ref.sha256, ref.blob_path, ref.size)
            for record in (*inventory.events, *inventory.checkpoints)
            for ref in record.artifacts
        ),
    )


def rebuild_index(memory: ResearchMemory) -> dict[str, object]:
    try:
        import sqlite3
    except ImportError:
        raise ResearchError("SQLite is unavailable in this Python runtime") from None

    with memory.research._writing():
        inventory = memory.inventory()
        summary = summarize(memory, inventory)
        target = workspace_path(memory.root, _INDEX)
        fd, name = tempfile.mkstemp(dir=target.parent, suffix=".sqlite3")
        os.close(fd)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary)) as db, db:
                _populate(db, inventory, summary)
            os.replace(temporary, target)
        except sqlite3.Error as exc:
            raise ResearchError(f"Could not rebuild research index: {exc}") from None
        finally:
            temporary.unlink(missing_ok=True)
    return {**summary, "index_present": True}
