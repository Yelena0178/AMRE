from pathlib import Path

import pytest

from amre.config import load_config
from amre.errors import ConfigError


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.source is None
    assert config.settings.workspace_root == tmp_path.resolve()
    assert config.settings.log_level == "INFO"
    assert config.settings.codex_executable == "codex"


def test_yaml_and_environment_precedence(
    fixture_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    file = fixture_dir / "config" / "layered.yaml"
    config = load_config(file)
    assert config.source == file.resolve()
    assert config.settings.workspace_root == file.parent.resolve() / "data"
    assert config.settings.log_level == "WARNING"
    assert config.settings.codex_executable == "fixture-codex"

    monkeypatch.setenv("AMRE_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AMRE_WORKSPACE_ROOT", "override")
    config = load_config(file)
    assert config.settings.log_level == "DEBUG"
    assert config.settings.workspace_root == file.parent.resolve() / "override"
    assert config.settings.codex_executable == "fixture-codex"


def test_implicit_yaml_with_utf8_bom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    file = tmp_path / "amre.yaml"
    file.write_text("log_level: ERROR\n", encoding="utf-8-sig")
    config = load_config()
    assert config.source == file.resolve()
    assert config.settings.log_level == "ERROR"


def test_explicit_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Configuration file not found"):
        load_config(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[invalid", "Invalid YAML"),
        ("[INFO]", "Expected a YAML mapping"),
        ("unknown_option: example", "unknown setting"),
        ("log_level: INVALID", "log_level"),
        ("workspace_root: []", "workspace_root"),
    ],
)
def test_invalid_yaml_or_settings(tmp_path: Path, content: str, message: str) -> None:
    file = tmp_path / "amre.yaml"
    file.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(file)
