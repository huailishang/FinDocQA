"""Cross-platform, corpus-root-bounded evidence source resolution.

The diagnostic artifacts may contain WSL absolute paths, Windows drive paths, or
project-relative corpus paths.  This module maps those references to the current
data/processed_mineru_retrieval root without permitting reads outside that
approved root.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

CORPUS_RELATIVE_ROOT = Path("data") / "processed_mineru_retrieval"
_CORPUS_MARKER_RE = re.compile(r"(?:^|/)data/processed_mineru_retrieval/(.*)$", re.IGNORECASE)
_CORPUS_SHORT_MARKER_RE = re.compile(r"(?:^|/)processed_mineru_retrieval/(.*)$", re.IGNORECASE)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\/]")


@dataclass(frozen=True)
class SourceResolution:
    original_ref: str
    canonical_ref: str
    resolved_path: str
    exists: bool
    resolution_route: str
    failure_reason: str
    mappable_corpus_ref: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normalise_ref(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _dedupe(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_source_refs(payload: Mapping[str, Any] | None) -> list[str]:
    """Collect all corpus references carried by one option-evidence payload."""
    raw = dict(payload or {})
    refs: list[str] = []
    for key in ("evidence_refs", "source_refs", "refs", "resolved_evidence_refs"):
        value = raw.get(key)
        if isinstance(value, str):
            refs.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                if isinstance(item, Mapping):
                    candidate = item.get("source") or item.get("resolved_path") or item.get("canonical_ref")
                    if candidate:
                        refs.append(str(candidate))
                elif item:
                    refs.append(str(item))
    matches = raw.get("evidence_matches")
    if isinstance(matches, Sequence) and not isinstance(matches, (str, bytes, bytearray)):
        for match in matches:
            if isinstance(match, Mapping) and match.get("source"):
                refs.append(str(match["source"]))
    coherent_source = raw.get("coherent_source")
    if coherent_source:
        refs.append(str(coherent_source))
    source_counts = raw.get("source_term_counts")
    if isinstance(source_counts, Mapping):
        refs.extend(str(key) for key in source_counts if key)
    return _dedupe(refs)


def evidence_snippets(payload: Mapping[str, Any] | None) -> list[str]:
    raw = dict(payload or {})
    snippets: list[str] = []
    matches = raw.get("evidence_matches")
    if isinstance(matches, Sequence) and not isinstance(matches, (str, bytes, bytearray)):
        for match in matches:
            if isinstance(match, Mapping):
                text = str(match.get("snippet") or "").strip()
                if text:
                    snippets.append(text)
    return _dedupe(snippets)


def evidence_terms(payload: Mapping[str, Any] | None) -> list[str]:
    raw = dict(payload or {})
    terms: list[str] = []
    for key in ("required_anchors", "coherent_terms", "matched_terms"):
        value = raw.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            terms.extend(str(item).strip() for item in value if str(item).strip())
    return _dedupe(terms)


class EvidenceSourceResolver:
    """Resolve evidence refs inside one approved corpus root."""

    def __init__(self, *, project_root: str | Path | None = None, corpus_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        configured = corpus_root or os.getenv("FINDOCQA_CORPUS_ROOT") or os.getenv("AFAC_CORPUS_ROOT")
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured))
        candidates.extend(
            [
                self.project_root.parent / CORPUS_RELATIVE_ROOT,
                self.project_root / CORPUS_RELATIVE_ROOT,
            ]
        )
        selected = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        self.corpus_root = selected.resolve()

    def _suffix(self, normalised_ref: str) -> str:
        match = _CORPUS_MARKER_RE.search(normalised_ref)
        if not match:
            match = _CORPUS_SHORT_MARKER_RE.search(normalised_ref)
        return match.group(1).lstrip("/") if match else ""

    def _canonical(self, suffix: str) -> str:
        if not suffix:
            return ""
        return (CORPUS_RELATIVE_ROOT / Path(suffix)).as_posix()

    def _candidate_paths(self, original_ref: str, normalised_ref: str, suffix: str) -> list[tuple[str, Path]]:
        candidates: list[tuple[str, Path]] = []
        if suffix:
            if normalised_ref.startswith("/mnt/"):
                route = "wsl_absolute_suffix_map"
            elif _WINDOWS_DRIVE_RE.match(original_ref) or _WINDOWS_DRIVE_RE.match(normalised_ref):
                route = "windows_drive_suffix_map"
            elif normalised_ref.startswith("../") or normalised_ref.startswith("./"):
                route = "project_relative_suffix_map"
            else:
                route = "corpus_suffix_map"
            candidates.append((route, self.corpus_root / Path(suffix)))
        path_ref = Path(normalised_ref)
        if normalised_ref.startswith("/mnt/"):
            candidates.append(("wsl_absolute_direct", path_ref))
        elif normalised_ref.startswith("../") or normalised_ref.startswith("./"):
            candidates.append(("project_relative_direct", self.project_root / path_ref))
        elif not _WINDOWS_DRIVE_RE.match(normalised_ref) and not path_ref.is_absolute():
            candidates.append(("project_or_corpus_relative", self.project_root / path_ref))
            candidates.append(("corpus_relative", self.corpus_root / path_ref))
        unique: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for route, candidate in candidates:
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                unique.append((route, candidate))
        return unique

    def resolve(self, original_ref: Any) -> SourceResolution:
        original = str(original_ref or "").strip()
        normalised = _normalise_ref(original)
        if not normalised:
            return SourceResolution(original, "", "", False, "none", "empty_ref", False)
        suffix = self._suffix(normalised)
        canonical = self._canonical(suffix)
        mappable = bool(suffix)
        outside_seen = False
        candidates = self._candidate_paths(original, normalised, suffix)
        for route, candidate in candidates:
            resolved = candidate.resolve(strict=False)
            if not _is_relative_to(resolved, self.corpus_root):
                outside_seen = True
                continue
            if resolved.is_file():
                if not canonical:
                    canonical = self._canonical(resolved.relative_to(self.corpus_root).as_posix())
                return SourceResolution(original, canonical, str(resolved), True, route, "", True)
        safe_candidate = ""
        for _, candidate in candidates:
            resolved = candidate.resolve(strict=False)
            if _is_relative_to(resolved, self.corpus_root):
                safe_candidate = str(resolved)
                break
        if outside_seen and not safe_candidate:
            reason = "outside_approved_corpus_root"
        elif mappable:
            reason = "corpus_file_not_found"
        else:
            reason = "not_a_mappable_corpus_ref"
        return SourceResolution(original, canonical, safe_candidate, False, "unresolved", reason, mappable)

    def read_bounded_context(
        self,
        original_ref: Any,
        *,
        snippet: str = "",
        terms: Sequence[str] | None = None,
        radius_chars: int = 900,
        max_chars: int = 2200,
    ) -> dict[str, Any]:
        resolution = self.resolve(original_ref)
        result = resolution.to_dict()
        result.update({"page_or_lineage": "", "bounded_context": "", "read_status": "not_read"})
        if not resolution.exists:
            result["read_status"] = "missing" if resolution.failure_reason == "corpus_file_not_found" else "unresolved"
            return result
        path = Path(resolution.resolved_path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result["read_status"] = "unreadable"
            result["failure_reason"] = f"read_error:{exc.__class__.__name__}"
            return result
        anchors = [str(snippet or "").strip()] + [str(term).strip() for term in (terms or []) if str(term).strip()]
        index = -1
        anchor_len = 0
        for anchor in anchors:
            if not anchor:
                continue
            probe = anchor if len(anchor) <= 240 else anchor[:240]
            index = text.find(probe)
            if index >= 0:
                anchor_len = len(probe)
                break
        if index < 0:
            index = 0
            anchor_len = 0
        start = max(0, index - radius_chars)
        end = min(len(text), index + anchor_len + radius_chars)
        context = text[start:end].strip()
        if len(context) > max_chars:
            context = context[:max_chars].rstrip()
        result["bounded_context"] = context
        result["page_or_lineage"] = path.relative_to(self.corpus_root).as_posix()
        result["read_status"] = "read"
        return result

    def enrich_option_payload(
        self,
        payload: Mapping[str, Any] | None,
        *,
        option_label: str,
        option_text: str,
        selected_or_unselected: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = dict(payload or {})
        refs = extract_source_refs(raw)
        snippets = evidence_snippets(raw)
        terms = evidence_terms(raw)
        matches = raw.get("evidence_matches")
        snippet_by_source: dict[str, str] = {}
        if isinstance(matches, Sequence) and not isinstance(matches, (str, bytes, bytearray)):
            for match in matches:
                if isinstance(match, Mapping) and match.get("source"):
                    snippet_by_source[str(match["source"])] = str(match.get("snippet") or "")
        contexts: list[dict[str, Any]] = []
        for ref in refs:
            contexts.append(
                self.read_bounded_context(
                    ref,
                    snippet=snippet_by_source.get(ref, snippets[0] if snippets else ""),
                    terms=terms,
                )
            )
        passages = _dedupe([str(item.get("bounded_context") or "") for item in contexts if item.get("read_status") == "read"])
        resolved_refs = _dedupe([str(item.get("canonical_ref") or "") for item in contexts if item.get("exists")])
        enriched = dict(raw)
        enriched["option_text"] = str(option_text or raw.get("option_text") or "")
        enriched["original_source_refs"] = refs
        enriched["resolved_evidence_refs"] = resolved_refs
        enriched["source_resolution"] = contexts
        enriched["full_passage_or_bounded_context"] = passages
        if refs and not enriched.get("evidence_refs"):
            enriched["evidence_refs"] = refs
        resolution_status = "no_source_ref"
        if refs:
            if all(item.get("read_status") == "read" for item in contexts):
                resolution_status = "resolved_and_read"
            elif any(item.get("read_status") == "read" for item in contexts):
                resolution_status = "partially_resolved"
            elif any(item.get("read_status") == "unreadable" for item in contexts):
                resolution_status = "resolved_but_unreadable"
            else:
                resolution_status = "unresolved"
        audit_row = {
            "option_label": str(option_label).upper(),
            "option_text": str(option_text or ""),
            "selected_or_unselected": selected_or_unselected,
            "original_source_ref": refs[0] if refs else "",
            "original_source_refs": refs,
            "canonical_source_ref": resolved_refs[0] if resolved_refs else "",
            "canonical_source_refs": resolved_refs,
            "page_or_lineage": [item.get("page_or_lineage") for item in contexts if item.get("page_or_lineage")],
            "short_snippet": " ".join(snippets)[:500],
            "full_passage_or_bounded_context": passages,
            "resolution_status": resolution_status,
            "source_resolution": contexts,
        }
        return enriched, audit_row
