"""Load AMRE settings from defaults, a YAML file, and the environment."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from amre.errors import ConfigError


class Settings(BaseModel):
    """Settings consumed by the phase 01 commands."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_root: Path = Path(".")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    codex_executable: str = Field(default="codex", min_length=1)


@dataclass(frozen=True)
class LoadedConfig:
    settings: Settings
    source: Path | None


def _read_yaml(path: Path, *, required: bool) -> dict[str, object] | None:
    try:
        contents = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        if required:
            raise ConfigError(f"Configuration file not found: {path}") from None
        return None
    except (OSError, UnicodeError):
        raise ConfigError(f"Cannot read configuration file as UTF-8: {path}") from None

    try:
        values = yaml.safe_load(contents)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        location = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise ConfigError(f"Invalid YAML in {path}{location}") from None
    except (yaml.YAMLError, ValueError, OverflowError, RecursionError):
        raise ConfigError(f"Invalid YAML in {path}") from None

    if values is None:
        return {}
    if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
        raise ConfigError(f"Expected a YAML mapping with string keys in {path}")
    return values


def load_config(path: Path | None = None) -> LoadedConfig:
    """Use an explicit file or amre.yaml in the current directory, if present."""
    try:
        config_path = (path if path is not None else Path.cwd() / "amre.yaml").resolve()
    except (OSError, RuntimeError, ValueError):
        raise ConfigError("Cannot resolve the configuration path") from None

    file_values = _read_yaml(config_path, required=path is not None)
    values = file_values if file_values is not None else {}
    for field in Settings.model_fields:
        variable = f"AMRE_{field.upper()}"
        if variable in os.environ:
            values[field] = os.environ[variable]

    try:
        settings = Settings.model_validate(values)
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            field = error["loc"][0]
            if field in Settings.model_fields:
                problems.append(f"{field}: {error['msg']}")
            else:
                problems.append("unknown setting")
        raise ConfigError("Invalid configuration: " + "; ".join(problems)) from None

    try:
        root = (config_path.parent / settings.workspace_root).resolve()
    except (OSError, RuntimeError, ValueError):
        raise ConfigError("Cannot resolve workspace_root") from None
    return LoadedConfig(
        settings.model_copy(update={"workspace_root": root}),
        config_path if file_values is not None else None,
    )
