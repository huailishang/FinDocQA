"""Auditable, default-off prompt registry for BB-P0-04C.

The registry never rewires production solvers.  Existing prompt builders are
registered by source path + symbol and fingerprinted read-only.  Offline prompt
candidates may be represented as append-only overlays whose base version points
to a registered production prompt.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import textwrap
from typing import Any, Iterable, Mapping, Sequence

REGISTRY_SCHEMA_VERSION = "prompt_registry_v1"
FEW_SHOT_SCHEMA_VERSION = "few_shot_asset_v1"


class PromptRegistryError(ValueError):
    """Raised when registry contents are ambiguous, stale or unsafe."""


def normalize_template_text(value: str) -> str:
    """Normalize source/template text before hashing.

    Newline and trailing-space normalization keeps hashes stable across Windows
    and WSL checkouts while still changing whenever executable prompt text or
    builder logic changes.
    """

    text = textwrap.dedent(str(value or "")).strip()
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def sha256_text(value: str) -> str:
    return hashlib.sha256(normalize_template_text(value).encode("utf-8")).hexdigest()


def _source_node_text(repo_root: Path, source_path: str, source_symbol: str) -> str:
    path = (repo_root / source_path).resolve()
    try:
        path.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise PromptRegistryError(f"source path escapes repository: {source_path}") from exc
    if not path.is_file():
        raise PromptRegistryError(f"prompt source missing: {source_path}")

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    parts = [part for part in str(source_symbol or "").split(".") if part]
    if not parts:
        raise PromptRegistryError(f"empty source symbol for {source_path}")

    nodes: Sequence[ast.AST] = tree.body
    found: ast.AST | None = None
    for index, part in enumerate(parts):
        found = None
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == part:
                found = node
                break
        if found is None:
            raise PromptRegistryError(f"prompt source symbol missing: {source_path}:{source_symbol}")
        if index < len(parts) - 1:
            if not isinstance(found, ast.ClassDef):
                raise PromptRegistryError(f"invalid nested prompt symbol: {source_path}:{source_symbol}")
            nodes = found.body

    segment = ast.get_source_segment(source, found) if found is not None else None
    if not segment:
        raise PromptRegistryError(f"cannot read prompt source segment: {source_path}:{source_symbol}")
    return normalize_template_text(segment)


@dataclass(frozen=True)
class FewShotAsset:
    id: str
    version: str
    question_kind: str
    domain: str
    failure_mode: str
    input_fixture: Mapping[str, Any]
    expected_output_fixture: Mapping[str, Any]
    source_note: str
    approved_for_production: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptRegistryEntry:
    id: str
    version: str
    question_kind: str
    domain: str
    model_profile: str
    template_hash: str
    change_note: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    source_symbol: str | None = None
    inline_template: str | None = None
    base_version: str | None = None
    template_mode: str = "source"
    production_enabled: bool = False
    few_shot_asset_ids: Sequence[str] = field(default_factory=tuple)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["few_shot_asset_ids"] = list(self.few_shot_asset_ids)
        return payload


@dataclass(frozen=True)
class PromptRegistry:
    repo_root: Path
    entries: Sequence[PromptRegistryEntry]
    few_shot_assets: Sequence[FewShotAsset]
    production_injection_enabled: bool = False

    def get(self, prompt_id: str, version: str) -> PromptRegistryEntry:
        for entry in self.entries:
            if entry.id == prompt_id and entry.version == version:
                return entry
        raise PromptRegistryError(f"prompt version not registered: {prompt_id}@{version}")

    def resolve_base(self, entry: PromptRegistryEntry) -> PromptRegistryEntry | None:
        if not entry.base_version:
            return None
        return self.get(entry.id, entry.base_version)

    def expected_hash(self, entry: PromptRegistryEntry) -> str:
        if entry.source_path and entry.source_symbol:
            return sha256_text(_source_node_text(self.repo_root, entry.source_path, entry.source_symbol))
        if entry.inline_template is not None:
            return sha256_text(entry.inline_template)
        raise PromptRegistryError(f"entry has no hashable template source: {entry.ref}")

    def validate(self) -> None:
        if self.production_injection_enabled:
            raise PromptRegistryError("BB-P0-04C registry must keep production injection disabled")

        seen: set[tuple[str, str]] = set()
        asset_ids: set[str] = set()
        for asset in self.few_shot_assets:
            if not all((asset.id, asset.version, asset.question_kind, asset.domain, asset.failure_mode, asset.source_note)):
                raise PromptRegistryError(f"incomplete few-shot asset: {asset.id or '<missing>'}")
            if asset.id in asset_ids:
                raise PromptRegistryError(f"duplicate few-shot asset id: {asset.id}")
            if asset.approved_for_production:
                raise PromptRegistryError(f"few-shot asset unexpectedly approved for production: {asset.id}")
            asset_ids.add(asset.id)

        for entry in self.entries:
            required = (
                entry.id,
                entry.version,
                entry.question_kind,
                entry.domain,
                entry.model_profile,
                entry.template_hash,
                entry.change_note,
            )
            if not all(required):
                raise PromptRegistryError(f"incomplete prompt registry entry: {entry.ref}")
            key = (entry.id, entry.version)
            if key in seen:
                raise PromptRegistryError(f"prompt version conflict: {entry.ref}")
            seen.add(key)

            has_source = bool(entry.source_path and entry.source_symbol)
            has_inline = entry.inline_template is not None
            if has_source == has_inline:
                raise PromptRegistryError(
                    f"entry must have exactly one template source (python symbol or inline asset): {entry.ref}"
                )
            if has_source and entry.template_mode != "source":
                raise PromptRegistryError(f"source entry must use template_mode=source: {entry.ref}")
            if has_inline and entry.template_mode not in {"inline", "append"}:
                raise PromptRegistryError(f"unsupported inline template mode: {entry.ref}:{entry.template_mode}")
            if entry.template_mode == "append" and not entry.base_version:
                raise PromptRegistryError(f"append entry requires base_version: {entry.ref}")
            if entry.production_enabled and entry.template_mode != "source":
                raise PromptRegistryError(f"offline template cannot be production-enabled: {entry.ref}")
            if entry.production_enabled and entry.base_version:
                raise PromptRegistryError(f"production entry cannot inherit offline prompt version: {entry.ref}")

            expected = self.expected_hash(entry)
            if expected != entry.template_hash:
                raise PromptRegistryError(
                    f"template hash mismatch: {entry.ref}: registered={entry.template_hash} actual={expected}"
                )
            missing_assets = [asset_id for asset_id in entry.few_shot_asset_ids if asset_id not in asset_ids]
            if missing_assets:
                raise PromptRegistryError(f"unknown few-shot asset reference on {entry.ref}: {missing_assets}")

        for entry in self.entries:
            if entry.base_version:
                base = self.get(entry.id, entry.base_version)
                if base.ref == entry.ref:
                    raise PromptRegistryError(f"prompt cannot inherit itself: {entry.ref}")

    def inventory(self) -> dict[str, Any]:
        discovered = discover_solver_prompt_builders(self.repo_root)
        registered_by_source: dict[tuple[str, str], list[PromptRegistryEntry]] = {}
        for entry in self.entries:
            if entry.source_path and entry.source_symbol:
                registered_by_source.setdefault((entry.source_path, entry.source_symbol), []).append(entry)

        registered_rows: list[dict[str, Any]] = []
        unregistered_rows: list[dict[str, str]] = []
        for item in discovered:
            key = (item["source_path"], item["source_symbol"])
            matches = registered_by_source.get(key, [])
            if not matches:
                unregistered_rows.append(item)
                continue
            actual_hash = sha256_text(_source_node_text(self.repo_root, *key))
            registered_rows.append(
                {
                    **item,
                    "actual_template_hash": actual_hash,
                    "registered_versions": [entry.ref for entry in matches],
                    "hash_match": all(entry.template_hash == actual_hash for entry in matches),
                }
            )

        return {
            "schema_version": "prompt_registry_inventory_v1",
            "registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "production_injection_enabled": self.production_injection_enabled,
            "discovered_solver_prompt_builders": len(discovered),
            "registered_solver_prompt_builders": len(registered_rows),
            "unregistered_solver_prompt_builders": len(unregistered_rows),
            "registered": registered_rows,
            "unregistered": unregistered_rows,
            "offline_prompt_assets": [entry.ref for entry in self.entries if not entry.source_path],
            "few_shot_assets": [asset.to_dict() for asset in self.few_shot_assets],
        }


def discover_solver_prompt_builders(repo_root: Path) -> list[dict[str, str]]:
    """Find actual solver prompt builder methods without importing solvers."""

    root = Path(repo_root).resolve()
    solver_dir = root / "src" / "solvers"
    result: list[dict[str, str]] = []
    for path in sorted(solver_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        rel = path.relative_to(root).as_posix()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                name = child.name.lower()
                if not name.startswith("_build") or "prompt" not in name:
                    continue
                result.append({"source_path": rel, "source_symbol": f"{node.name}.{child.name}"})
    return result


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PromptRegistryError(f"{context} must be an object")
    return value


def _build_entry(raw: Mapping[str, Any]) -> PromptRegistryEntry:
    return PromptRegistryEntry(
        id=str(raw.get("id") or ""),
        version=str(raw.get("version") or ""),
        question_kind=str(raw.get("question_kind") or ""),
        domain=str(raw.get("domain") or ""),
        model_profile=str(raw.get("model_profile") or ""),
        template_hash=str(raw.get("template_hash") or ""),
        change_note=str(raw.get("change_note") or ""),
        parameters=dict(_require_mapping(raw.get("parameters") or {}, "parameters")),
        source_path=str(raw.get("source_path")) if raw.get("source_path") else None,
        source_symbol=str(raw.get("source_symbol")) if raw.get("source_symbol") else None,
        inline_template=str(raw.get("inline_template")) if raw.get("inline_template") is not None else None,
        base_version=str(raw.get("base_version")) if raw.get("base_version") else None,
        template_mode=str(raw.get("template_mode") or ("source" if raw.get("source_path") else "inline")),
        production_enabled=bool(raw.get("production_enabled", False)),
        few_shot_asset_ids=tuple(str(item) for item in raw.get("few_shot_asset_ids") or ()),
    )


def _build_few_shot_asset(raw: Mapping[str, Any]) -> FewShotAsset:
    return FewShotAsset(
        id=str(raw.get("id") or ""),
        version=str(raw.get("version") or ""),
        question_kind=str(raw.get("question_kind") or ""),
        domain=str(raw.get("domain") or ""),
        failure_mode=str(raw.get("failure_mode") or ""),
        input_fixture=dict(_require_mapping(raw.get("input_fixture") or {}, "few-shot input_fixture")),
        expected_output_fixture=dict(
            _require_mapping(raw.get("expected_output_fixture") or {}, "few-shot expected_output_fixture")
        ),
        source_note=str(raw.get("source_note") or ""),
        approved_for_production=bool(raw.get("approved_for_production", False)),
    )


def load_prompt_registry(
    path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
) -> PromptRegistry:
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[2]
    registry_path = Path(path) if path is not None else root / "config" / "prompt_registry.json"
    if not registry_path.is_absolute():
        registry_path = root / registry_path
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    data = _require_mapping(payload, "prompt registry")
    if str(data.get("schema_version") or "") != REGISTRY_SCHEMA_VERSION:
        raise PromptRegistryError(
            f"unsupported prompt registry schema: {data.get('schema_version')!r}"
        )
    if str(data.get("few_shot_schema_version") or "") != FEW_SHOT_SCHEMA_VERSION:
        raise PromptRegistryError(
            f"unsupported few-shot schema: {data.get('few_shot_schema_version')!r}"
        )

    entries_raw = data.get("prompts") or []
    assets_raw = data.get("few_shot_assets") or []
    if not isinstance(entries_raw, list) or not isinstance(assets_raw, list):
        raise PromptRegistryError("prompts and few_shot_assets must be arrays")

    registry = PromptRegistry(
        repo_root=root,
        entries=tuple(_build_entry(_require_mapping(item, "prompt entry")) for item in entries_raw),
        few_shot_assets=tuple(
            _build_few_shot_asset(_require_mapping(item, "few-shot asset")) for item in assets_raw
        ),
        production_injection_enabled=bool(data.get("production_injection_enabled", False)),
    )
    registry.validate()
    return registry


def registered_refs(entries: Iterable[PromptRegistryEntry]) -> list[str]:
    return sorted(entry.ref for entry in entries)
