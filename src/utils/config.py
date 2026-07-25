"""Configuration loading for enhanced-baseline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def load_config(path: Path) -> Dict[str, Any]:
    """Load a YAML config file.

    Uses PyYAML when available. Falls back to a tiny parser that supports the
    config shapes used by this project (top-level section + one optional nested
    mapping level + scalar values), so architecture tests and no-PyYAML
    environments do not require dependency installation.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return _parse_simple_yaml(text)

    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Parse the limited YAML shapes used by this project without PyYAML.

    Supports section mappings, nested mappings, scalar values, and one-level
    scalar lists such as fallback_processed_docs.
    """
    config: Dict[str, Any] = {}
    current_section: Dict[str, Any] = {}
    nested_under: str | None = None
    nested_indent: int | None = None

    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    for raw_line in text.splitlines():
        code = raw_line.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        depth = _indent(code)
        stripped = code.strip()

        if depth == 0:
            if not stripped.endswith(":"):
                continue
            name = stripped[:-1].strip()
            current_section = config.setdefault(name, {})
            if not isinstance(current_section, dict):
                current_section = {}
                config[name] = current_section
            nested_under = None
            nested_indent = None
            continue

        if stripped.startswith("- "):
            if nested_under is not None and isinstance(current_section.get(nested_under), list):
                current_section[nested_under].append(_coerce_scalar(stripped[2:].strip()))
            continue

        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        if value == "" and key:
            # Delay deciding map vs list until the next meaningful line.
            current_section[key] = []
            nested_under = key
            nested_indent = depth
            continue

        # A scalar at the same indentation as the active nested header closes it.
        if nested_under is not None and nested_indent is not None and depth <= nested_indent:
            nested_under = None
            nested_indent = None

        if nested_under is not None:
            target = current_section.get(nested_under)
            if isinstance(target, list) and not target:
                target = {}
                current_section[nested_under] = target
            if isinstance(target, dict):
                target[key] = _coerce_scalar(value)
                continue

        current_section[key] = _coerce_scalar(value)

    return config

def _coerce_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    try:
        return int(value)
    except ValueError:
        return value.strip('"\'')
