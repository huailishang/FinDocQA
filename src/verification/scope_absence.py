"""Fail-closed, cross-platform proof of absence inside declared documents.

The proof records stable source identities and local-window scan results.  It is
valid only when an external trusted document mapping binds each declared doc ID
to the canonical corpus file on the current platform.  The proof never proves
that a statement is globally false; it proves only that no coherent local match
was found in the question-declared documents.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

DECLARED_DOCUMENT_SCOPE = "question_declared_documents_only"
PROOF_VERSION = "scope_absence_proof.v2"
SCAN_METHOD = "normalized_local_window_coherence_scan.v2"
DEFAULT_MAX_WINDOW_LINES = 3
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HTML_ROW_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _alias_groups(value: Any) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    groups: list[tuple[str, ...]] = []
    for group in value:
        items = _strings(group)
        if items:
            groups.append(items)
    return tuple(groups)


def _normalise_relpath(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix().lstrip("./")


@dataclass(frozen=True)
class TrustedDocumentSource:
    """External trust input, never derived from a proof artifact."""

    canonical_doc_id: str
    source_root_identity: str
    source_root: str
    source_relpath: str

    @property
    def resolved_path(self) -> Path:
        return Path(self.source_root) / Path(self.source_relpath)

    def proof_identity(self) -> dict[str, str]:
        return {
            "canonical_doc_id": self.canonical_doc_id,
            "source_root_identity": self.source_root_identity,
            "source_relpath": _normalise_relpath(self.source_relpath),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TrustedDocumentSource":
        return cls(
            canonical_doc_id=str(payload.get("canonical_doc_id") or ""),
            source_root_identity=str(payload.get("source_root_identity") or ""),
            source_root=str(payload.get("source_root") or ""),
            source_relpath=_normalise_relpath(payload.get("source_relpath")),
        )


def normalize_trusted_documents(
    trusted_declared_documents: Mapping[str, TrustedDocumentSource | Mapping[str, Any]] | None,
) -> dict[str, TrustedDocumentSource]:
    if not isinstance(trusted_declared_documents, Mapping):
        return {}
    result: dict[str, TrustedDocumentSource] = {}
    for raw_doc_id, raw_source in trusted_declared_documents.items():
        doc_id = str(raw_doc_id or "").strip()
        if not doc_id:
            continue
        if isinstance(raw_source, TrustedDocumentSource):
            source = raw_source
        elif isinstance(raw_source, Mapping):
            source = TrustedDocumentSource.from_mapping(raw_source)
        else:
            continue
        result[doc_id] = source
    return result


@dataclass(frozen=True)
class ScopeAbsenceProof:
    required_doc_ids: tuple[str, ...]
    scanned_doc_ids: tuple[str, ...]
    missing_required_doc_ids: tuple[str, ...]
    scan_complete: bool
    query_terms: tuple[str, ...]
    query_alias_groups: tuple[tuple[str, ...], ...]
    match_counts_by_doc: dict[str, int]
    coherent_match_count: int
    matched_windows_by_doc: dict[str, list[dict[str, Any]]]
    out_of_scope_match_doc_ids: tuple[str, ...]
    scan_method: str
    scan_window_policy: str
    max_window_lines: int
    canonical_doc_ids_by_doc: dict[str, str]
    source_root_identity: str
    source_relpaths: dict[str, str]
    source_sha256_by_doc: dict[str, str]
    scan_timestamp_or_run_id: str
    corpus_scope: str = DECLARED_DOCUMENT_SCOPE
    proof_version: str = PROOF_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_doc_ids": list(self.required_doc_ids),
            "scanned_doc_ids": list(self.scanned_doc_ids),
            "missing_required_doc_ids": list(self.missing_required_doc_ids),
            "scan_complete": self.scan_complete,
            "query_terms": list(self.query_terms),
            "query_alias_groups": [list(group) for group in self.query_alias_groups],
            "match_counts_by_doc": dict(self.match_counts_by_doc),
            "coherent_match_count": self.coherent_match_count,
            "matched_windows_by_doc": self.matched_windows_by_doc,
            "out_of_scope_match_doc_ids": list(self.out_of_scope_match_doc_ids),
            "scan_method": self.scan_method,
            "scan_window_policy": self.scan_window_policy,
            "max_window_lines": self.max_window_lines,
            "canonical_doc_ids_by_doc": dict(self.canonical_doc_ids_by_doc),
            "source_root_identity": self.source_root_identity,
            "source_relpaths": dict(self.source_relpaths),
            "source_sha256_by_doc": dict(self.source_sha256_by_doc),
            "scan_timestamp_or_run_id": self.scan_timestamp_or_run_id,
            "corpus_scope": self.corpus_scope,
            "proof_version": self.proof_version,
        }


@dataclass(frozen=True)
class ScopeAbsenceValidation:
    valid: bool
    errors: tuple[str, ...]
    normalized_proof: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_scope_absence_proof(
    proof: Mapping[str, Any] | ScopeAbsenceProof | None,
) -> dict[str, Any]:
    if isinstance(proof, ScopeAbsenceProof):
        raw = proof.to_dict()
    elif isinstance(proof, Mapping):
        raw = dict(proof)
    else:
        raw = {}

    def mapping(name: str) -> dict[str, Any]:
        value = raw.get(name)
        return {str(key): item for key, item in dict(value or {}).items()} if isinstance(value, Mapping) else {}

    matched_raw = mapping("matched_windows_by_doc")
    matched_windows = {
        doc_id: [dict(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []
        for doc_id, value in matched_raw.items()
    }
    return {
        "required_doc_ids": list(_strings(raw.get("required_doc_ids"))),
        "scanned_doc_ids": list(_strings(raw.get("scanned_doc_ids"))),
        "missing_required_doc_ids": list(_strings(raw.get("missing_required_doc_ids"))),
        "scan_complete": raw.get("scan_complete") is True,
        "query_terms": list(_strings(raw.get("query_terms"))),
        "query_alias_groups": [list(group) for group in _alias_groups(raw.get("query_alias_groups"))],
        "match_counts_by_doc": mapping("match_counts_by_doc"),
        "coherent_match_count": raw.get("coherent_match_count"),
        "matched_windows_by_doc": matched_windows,
        "out_of_scope_match_doc_ids": list(_strings(raw.get("out_of_scope_match_doc_ids"))),
        "scan_method": str(raw.get("scan_method") or "").strip(),
        "scan_window_policy": str(raw.get("scan_window_policy") or "").strip(),
        "max_window_lines": raw.get("max_window_lines"),
        "canonical_doc_ids_by_doc": {str(key): str(value) for key, value in mapping("canonical_doc_ids_by_doc").items()},
        "source_root_identity": str(raw.get("source_root_identity") or "").strip(),
        "source_relpaths": {str(key): _normalise_relpath(value) for key, value in mapping("source_relpaths").items()},
        "source_sha256_by_doc": {str(key): str(value).lower() for key, value in mapping("source_sha256_by_doc").items()},
        "scan_timestamp_or_run_id": str(raw.get("scan_timestamp_or_run_id") or "").strip(),
        "corpus_scope": str(raw.get("corpus_scope") or "").strip(),
        "proof_version": str(raw.get("proof_version") or "").strip(),
    }


def validate_scope_absence_proof(
    proof: Mapping[str, Any] | ScopeAbsenceProof | None,
    *,
    trusted_declared_documents: Mapping[str, TrustedDocumentSource | Mapping[str, Any]] | None = None,
) -> ScopeAbsenceValidation:
    normalized = normalize_scope_absence_proof(proof)
    trusted = normalize_trusted_documents(trusted_declared_documents)
    errors: list[str] = []
    required = normalized["required_doc_ids"]
    scanned = normalized["scanned_doc_ids"]
    missing = normalized["missing_required_doc_ids"]

    if normalized["proof_version"] != PROOF_VERSION:
        errors.append("proof_version_invalid")
    if not trusted:
        errors.append("trusted_declared_documents_missing")
    if not required:
        errors.append("required_doc_ids_empty")
    if len(required) != len(set(required)):
        errors.append("required_doc_ids_not_unique")
    if len(scanned) != len(set(scanned)):
        errors.append("scanned_doc_ids_not_unique")
    if set(scanned) != set(required) or len(scanned) != len(required):
        errors.append("required_scanned_doc_ids_mismatch")
    if trusted and set(trusted) != set(required):
        errors.append("trusted_declared_doc_ids_mismatch")
    if missing:
        errors.append("missing_required_doc_ids_present")
    if normalized["scan_complete"] is not True:
        errors.append("scan_not_complete")
    if not normalized["query_terms"] and not normalized["query_alias_groups"]:
        errors.append("query_definition_empty")

    coherent = normalized["coherent_match_count"]
    if not isinstance(coherent, int) or isinstance(coherent, bool):
        errors.append("coherent_match_count_invalid")
    elif coherent != 0:
        errors.append("coherent_match_count_nonzero")

    if normalized["scan_method"] != SCAN_METHOD:
        errors.append("scan_method_invalid")
    if not normalized["scan_window_policy"]:
        errors.append("scan_window_policy_empty")
    max_lines = normalized["max_window_lines"]
    if not isinstance(max_lines, int) or isinstance(max_lines, bool) or max_lines < 3:
        errors.append("max_window_lines_invalid")

    counts = normalized["match_counts_by_doc"]
    windows = normalized["matched_windows_by_doc"]
    if set(counts) != set(required):
        errors.append("match_counts_doc_ids_mismatch")
    elif any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()):
        errors.append("match_counts_invalid")
    if set(windows) != set(required):
        errors.append("matched_windows_doc_ids_mismatch")
    elif set(counts) == set(required):
        if any(counts[doc_id] != len(windows[doc_id]) for doc_id in required):
            errors.append("matched_windows_count_mismatch")
        if isinstance(coherent, int) and sum(len(windows[doc_id]) for doc_id in required) != coherent:
            errors.append("coherent_match_total_mismatch")

    canonical_ids = normalized["canonical_doc_ids_by_doc"]
    relpaths = normalized["source_relpaths"]
    hashes = normalized["source_sha256_by_doc"]
    if set(canonical_ids) != set(required):
        errors.append("canonical_doc_ids_incomplete")
    if set(relpaths) != set(required) or any(not relpaths.get(doc_id) for doc_id in required):
        errors.append("source_relpaths_incomplete")
    if set(hashes) != set(required):
        errors.append("source_hashes_incomplete")
    elif any(not _SHA256_RE.fullmatch(hashes.get(doc_id, "")) for doc_id in required):
        errors.append("source_hash_invalid")

    if trusted and set(trusted) == set(required):
        root_ids = {source.source_root_identity for source in trusted.values()}
        if len(root_ids) != 1 or "" in root_ids:
            errors.append("trusted_source_root_identity_invalid")
        else:
            expected_root_identity = next(iter(root_ids))
            if normalized["source_root_identity"] != expected_root_identity:
                errors.append("source_root_identity_mismatch")
        for doc_id in required:
            source = trusted[doc_id]
            expected_relpath = _normalise_relpath(source.source_relpath)
            if source.canonical_doc_id != doc_id:
                errors.append("trusted_canonical_doc_id_mismatch")
            if canonical_ids.get(doc_id) != source.canonical_doc_id:
                errors.append("proof_canonical_doc_id_mismatch")
            if relpaths.get(doc_id) != expected_relpath:
                errors.append("proof_trusted_source_relpath_mismatch")
            path = source.resolved_path
            if not path.is_file():
                errors.append("trusted_source_path_missing")
                continue
            actual_hash = sha256(path.read_bytes()).hexdigest()
            if hashes.get(doc_id) != actual_hash:
                errors.append("source_hash_mismatch")

    if not normalized["scan_timestamp_or_run_id"]:
        errors.append("scan_run_id_empty")
    if normalized["corpus_scope"] != DECLARED_DOCUMENT_SCOPE:
        errors.append("corpus_scope_invalid")

    return ScopeAbsenceValidation(
        valid=not errors,
        errors=tuple(sorted(set(errors))),
        normalized_proof=normalized,
    )


def _window_is_coherent(
    text: str,
    *,
    query_terms: Sequence[str],
    query_alias_groups: Sequence[Sequence[str]],
) -> bool:
    compact = _compact(text)
    terms = [_compact(term) for term in query_terms if _compact(term)]
    groups = [[_compact(alias) for alias in group if _compact(alias)] for group in query_alias_groups if group]
    if not terms and not groups:
        return False
    return all(term in compact for term in terms) and all(any(alias in compact for alias in group) for group in groups)


def _line_number(text: str, char_index: int) -> int:
    return text.count("\n", 0, max(0, char_index)) + 1


def _local_windows(text: str, *, max_window_lines: int) -> list[dict[str, Any]]:
    lines = text.splitlines()
    windows: dict[tuple[int, int, str], dict[str, Any]] = {}

    def add(kind: str, start: int, end: int, snippet: str) -> None:
        normalized = _compact(snippet)
        if not normalized:
            return
        key = (start, end, normalized)
        existing = windows.get(key)
        if existing is None:
            windows[key] = {
                "window_kind": kind,
                "window_kinds": [kind],
                "start_line": start,
                "end_line": end,
                "snippet": snippet.strip()[:1800],
            }
        elif kind not in existing["window_kinds"]:
            existing["window_kinds"].append(kind)

    # Contiguous non-empty line windows. Blank lines are hard local boundaries.
    segment_start = 0
    while segment_start < len(lines):
        while segment_start < len(lines) and not lines[segment_start].strip():
            segment_start += 1
        if segment_start >= len(lines):
            break
        segment_end = segment_start
        while segment_end < len(lines) and lines[segment_end].strip():
            segment_end += 1
        segment = lines[segment_start:segment_end]
        for offset in range(len(segment)):
            for size in range(1, min(max_window_lines, len(segment) - offset) + 1):
                kind = "single_line" if size == 1 else f"adjacent_{size}_lines"
                add(kind, segment_start + offset + 1, segment_start + offset + size, "\n".join(segment[offset:offset + size]))
        if len(segment) <= max_window_lines:
            add("markdown_paragraph", segment_start + 1, segment_end, "\n".join(segment))
        segment_start = segment_end + 1

    # Markdown table rows, bounded to adjacent rows only.
    index = 0
    while index < len(lines):
        if "|" not in lines[index]:
            index += 1
            continue
        end = index
        while end < len(lines) and "|" in lines[end] and lines[end].strip():
            end += 1
        rows = lines[index:end]
        for offset in range(len(rows)):
            for size in range(1, min(max_window_lines, len(rows) - offset) + 1):
                add("markdown_table_local_block", index + offset + 1, index + offset + size, "\n".join(rows[offset:offset + size]))
        index = end

    # HTML table rows, also bounded to adjacent row blocks.
    html_rows = list(_HTML_ROW_RE.finditer(text))
    for offset in range(len(html_rows)):
        for size in range(1, min(max_window_lines, len(html_rows) - offset) + 1):
            selected = html_rows[offset:offset + size]
            start_line = _line_number(text, selected[0].start())
            end_line = _line_number(text, selected[-1].end())
            add("html_table_local_block", start_line, end_line, "\n".join(match.group(0) for match in selected))

    return list(windows.values())


def scan_local_windows(
    text: str,
    *,
    query_terms: Sequence[str] = (),
    query_alias_groups: Sequence[Sequence[str]] = (),
    max_window_lines: int = DEFAULT_MAX_WINDOW_LINES,
) -> list[dict[str, Any]]:
    return [
        window for window in _local_windows(text, max_window_lines=max_window_lines)
        if _window_is_coherent(
            window["snippet"],
            query_terms=query_terms,
            query_alias_groups=query_alias_groups,
        )
    ]


def build_scope_absence_proof(
    *,
    trusted_declared_documents: Mapping[str, TrustedDocumentSource | Mapping[str, Any]],
    query_terms: Sequence[str] = (),
    query_alias_groups: Sequence[Sequence[str]] = (),
    out_of_scope_match_doc_ids: Sequence[str] = (),
    scan_timestamp_or_run_id: str,
    max_window_lines: int = DEFAULT_MAX_WINDOW_LINES,
) -> ScopeAbsenceProof:
    """Scan trusted declared documents and return a portable v2 proof."""
    trusted = normalize_trusted_documents(trusted_declared_documents)
    required = tuple(trusted)
    scanned: list[str] = []
    missing: list[str] = []
    counts: dict[str, int] = {}
    matched_windows: dict[str, list[dict[str, Any]]] = {}
    canonical_ids: dict[str, str] = {}
    relpaths: dict[str, str] = {}
    hashes: dict[str, str] = {}
    root_ids = {source.source_root_identity for source in trusted.values()}
    root_identity = next(iter(root_ids)) if len(root_ids) == 1 else ""

    for doc_id, source in trusted.items():
        canonical_ids[doc_id] = source.canonical_doc_id
        relpaths[doc_id] = _normalise_relpath(source.source_relpath)
        path = source.resolved_path
        if not path.is_file():
            missing.append(doc_id)
            counts[doc_id] = 0
            matched_windows[doc_id] = []
            continue
        data = path.read_bytes()
        text = data.decode("utf-8-sig", errors="replace")
        matches = scan_local_windows(
            text,
            query_terms=query_terms,
            query_alias_groups=query_alias_groups,
            max_window_lines=max_window_lines,
        )
        scanned.append(doc_id)
        counts[doc_id] = len(matches)
        matched_windows[doc_id] = matches
        hashes[doc_id] = sha256(data).hexdigest()

    coherent_total = sum(counts.values())
    return ScopeAbsenceProof(
        required_doc_ids=required,
        scanned_doc_ids=tuple(scanned),
        missing_required_doc_ids=tuple(missing),
        scan_complete=not missing and len(scanned) == len(required),
        query_terms=tuple(str(term) for term in query_terms if str(term).strip()),
        query_alias_groups=tuple(
            tuple(str(alias) for alias in group if str(alias).strip())
            for group in query_alias_groups
            if any(str(alias).strip() for alias in group)
        ),
        match_counts_by_doc=counts,
        coherent_match_count=coherent_total,
        matched_windows_by_doc=matched_windows,
        out_of_scope_match_doc_ids=tuple(str(doc_id) for doc_id in out_of_scope_match_doc_ids),
        scan_method=SCAN_METHOD,
        scan_window_policy="single_line+adjacent_2_3_lines+markdown_paragraph+markdown_table_local_block+html_table_local_block;blank_lines_and_nonadjacent_sections_are_hard_boundaries",
        max_window_lines=max_window_lines,
        canonical_doc_ids_by_doc=canonical_ids,
        source_root_identity=root_identity,
        source_relpaths=relpaths,
        source_sha256_by_doc=hashes,
        scan_timestamp_or_run_id=scan_timestamp_or_run_id,
    )
