import logging
from importlib.metadata import version
from pathlib import Path

import pytest

from amre.cli import main


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert all(command in output for command in ("version", "doctor", "status"))


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == version("amre")


def test_doctor_reports_optional_codex(
    git_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(git_workspace)
    monkeypatch.setenv("AMRE_CODEX_EXECUTABLE", str(git_workspace / "missing-codex.exe"))
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "[OK] SQLite:" in output
    assert "[OK] Git worktree:" in output
    assert "[OPTIONAL] Codex CLI:" in output
    assert sorted(path.name for path in git_workspace.iterdir()) == [".git"]


def test_doctor_failure_and_status_on_missing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMRE_WORKSPACE_ROOT", "missing")
    assert main(["doctor"]) == 1
    assert "[FAIL] Workspace:" in capsys.readouterr().out
    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert "missing (missing)" in output
    assert "Git worktree: unconfirmed" in output
    assert not (tmp_path / "missing").exists()


def test_config_error_logs_without_raw_yaml(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    file = tmp_path / "amre.yaml"
    file.write_text("log_level: [private-input", encoding="utf-8")
    assert main(["--config", str(file), "status"]) == 2
    assert "Invalid YAML" in caplog.text
    assert "private-input" not in caplog.text
    assert capsys.readouterr().out == ""


def test_status_applies_log_level(
    git_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(git_workspace)
    monkeypatch.setenv("AMRE_LOG_LEVEL", "DEBUG")
    logger = logging.getLogger("amre")
    original_level = logger.level
    try:
        assert main(["status"]) == 0
        assert "Log level: DEBUG" in capsys.readouterr().out
        assert logger.getEffectiveLevel() == logging.DEBUG
    finally:
        logger.setLevel(original_level)
