"""Strict YAML and atomic UTF-8 file operations for research data."""

import os
import tempfile
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from amre.errors import ResearchError

_Model = TypeVar("_Model", bound=BaseModel)


class _ResearchYamlLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[str, object]:
        values = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            line = key_node.start_mark.line + 1
            if not isinstance(key, str):
                raise ResearchError(f"Expected a string YAML key at line {line}")
            if key in values:
                raise ResearchError(f"Duplicate YAML key at line {line}")
            values[key] = self.construct_object(value_node, deep=deep)
        return values


def read_yaml(path: Path, *, missing_ok: bool = False) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ResearchError(f"Research YAML file not found: {path}") from None
    except (OSError, UnicodeError):
        raise ResearchError(f"Cannot read research YAML: {path}") from None

    try:
        values = yaml.load(text, Loader=_ResearchYamlLoader)
    except ResearchError as exc:
        raise ResearchError(f"{path}: {exc}") from None
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ResearchError(f"Invalid research YAML in {path}{location}") from None
    except (yaml.YAMLError, ValueError, OverflowError, RecursionError):
        raise ResearchError(f"Invalid research YAML in {path}") from None
    if not isinstance(values, dict):
        raise ResearchError(f"Expected a YAML mapping in {path}")
    return values


def validate(model: type[_Model], value: object, label: str) -> _Model:
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        details = [
            f"{'.'.join(map(str, error['loc'])) or label}: {error['msg']}"
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
        raise ResearchError(f"Invalid {label}. " + ". ".join(details)) from None


def yaml_text(values: dict[str, object]) -> str:
    return yaml.safe_dump(values, sort_keys=False, allow_unicode=True)


def workspace_path(root: Path, relative: str) -> Path:
    candidate = root / relative
    try:
        inside = candidate.resolve().is_relative_to(root)
    except (OSError, RuntimeError, ValueError):
        inside = False
    if not inside:
        raise ResearchError(f"Research path escapes the workspace: {relative}")
    return candidate


def atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, mode="wb", delete=False, suffix=".tmp"
    ) as file:
        temporary_path = Path(file.name)
        try:
            file.write(content.encode("utf-8") if isinstance(content, str) else content)
            file.flush()
            os.fsync(file.fileno())
            file.close()
            os.replace(temporary_path, path)
        finally:
            file.close()
            temporary_path.unlink(missing_ok=True)
