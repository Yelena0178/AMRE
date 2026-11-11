"""Fixtures shared by AMRE tests."""

import shutil
import subprocess
from pathlib import Path

import pytest

from amre.config import Settings
from amre.research.store import ResearchStore


@pytest.fixture(autouse=True)
def clean_amre_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(f"AMRE_{field.upper()}", raising=False)


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def example_store(tmp_path: Path) -> ResearchStore:
    source = Path(__file__).resolve().parents[1] / "examples" / "and_or" / "research"
    shutil.copytree(source, tmp_path / "research")
    return ResearchStore(tmp_path)


@pytest.fixture
def git_workspace(tmp_path: Path) -> Path:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is needed for tests using a real worktree")
    subprocess.run([git, "init", "--quiet"], cwd=tmp_path, check=True, timeout=10)
    return tmp_path
