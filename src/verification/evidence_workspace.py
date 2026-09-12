"""Immutable page-scope contract for financial evidence evaluation.

The workspace uses one-based canonical physical pages. Financial facts keep the
MinerU zero-based ``source_page`` index, so callers must normalize them through
``financial_page_key`` or ``audit_source_fact`` before membership checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import re
from typing import Any, Iterable, Mapping


IN_SCOPE = "IN_SCOPE"
OUTSIDE_SCOPE = "OUTSIDE_SCOPE"
UNKNOWN_PAGE = "UNKNOWN_PAGE"
IDENTITY_CONFLICT = "IDENTITY_CONFLICT"

PageKey = tuple[str, int]
_ALLOWED_PROVENANCE = frozenset({"lexical", "semantic", "both", "structure"})
_CANONICAL_PAGE_RE = re.compile(r"^canonical://[^/]+/(?P<doc>[^/]+)/page/(?P<page>\d+)$")
_PAGE_IDX_RE = re.compile(r"(?:[#&?]|^)page_idx=(?P<page>\d+)(?:[&#]|$)")


def _normalize_page_key(value: tuple[Any, Any]) -> PageKey:
    doc_id = str(value[0] or "").strip()
    try:
        page = int(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid workspace page: {value!r}") from exc
    if not doc_id or page <= 0:
        raise ValueError(f"invalid workspace page: {value!r}")
    return doc_id, page


def canonical_page_from_source(source: str) -> tuple[str | None, int | None]:
    """Return canonical doc/page when the source encodes deterministic page identity."""
    text = str(source or "").strip()
    if not text:
        return None, None
    match = _CANONICAL_PAGE_RE.match(text)
    if match:
        page = int(match.group("page"))
        return match.group("doc"), page if page > 0 else None
    match = _PAGE_IDX_RE.search(text)
    if match:
        return None, int(match.group("page")) + 1
    return None, None


@dataclass(frozen=True)
class EvidenceWorkspaceScope:
    """One immutable allowed-page set shared by all scoped evidence paths."""

    allowed_pages: frozenset[PageKey]
    provenance_by_page: Mapping[PageKey, frozenset[str]]
    max_unique_pages: int = 10
    page_index_semantics: str = "ONE_BASED_CANONICAL_PHYSICAL_PAGE"
    fail_closed_on_unknown_page: bool = True
    fail_closed_on_outside_page: bool = True
    full_document_fallback_allowed: bool = False

    def __post_init__(self) -> None:
        if int(self.max_unique_pages) != 10:
            raise ValueError("EvidenceWorkspaceScope max_unique_pages is fixed at 10")
        normalized = frozenset(_normalize_page_key(tuple(page)) for page in self.allowed_pages)
        if not normalized:
            raise ValueError("workspace must contain at least one page")
        if len(normalized) > 10:
            raise ValueError(f"workspace contains {len(normalized)} unique pages; max is 10")
        normalized_provenance: dict[PageKey, frozenset[str]] = {}
        for raw_key, raw_values in dict(self.provenance_by_page or {}).items():
            key = _normalize_page_key(tuple(raw_key))
            if key not in normalized:
                raise ValueError(f"provenance references page outside workspace: {key!r}")
            values = frozenset(str(value).strip().lower() for value in raw_values if str(value).strip())
            if not values:
                raise ValueError(f"empty provenance for page: {key!r}")
            unknown = values - _ALLOWED_PROVENANCE
            if unknown:
                raise ValueError(f"unsupported provenance values: {sorted(unknown)}")
            if "both" in values and len(values) > 1:
                values = frozenset({"both"})
            elif {"lexical", "semantic"} <= values:
                values = frozenset({"both"})
            normalized_provenance[key] = values
        for key in normalized:
            normalized_provenance.setdefault(key, frozenset({"structure"}))
        object.__setattr__(self, "allowed_pages", normalized)
        object.__setattr__(
            self,
            "provenance_by_page",
            MappingProxyType(dict(sorted(normalized_provenance.items()))),
        )

    @classmethod
    def from_pages(
        cls,
        pages: Iterable[tuple[Any, Any]],
        *,
        provenance_by_page: Mapping[tuple[Any, Any], Iterable[str]] | None = None,
        max_unique_pages: int = 10,
    ) -> "EvidenceWorkspaceScope":
        if int(max_unique_pages) != 10:
            raise ValueError("EvidenceWorkspaceScope max_unique_pages is fixed at 10")
        normalized = frozenset(_normalize_page_key(tuple(page)) for page in pages)
        if not normalized:
            raise ValueError("workspace must contain at least one page")
        if len(normalized) > int(max_unique_pages):
            raise ValueError(
                f"workspace contains {len(normalized)} unique pages; max is {max_unique_pages}"
            )

        raw_provenance = provenance_by_page or {}
        normalized_provenance: dict[PageKey, frozenset[str]] = {}
        for raw_key, raw_values in raw_provenance.items():
            key = _normalize_page_key(tuple(raw_key))
            if key not in normalized:
                raise ValueError(f"provenance references page outside workspace: {key!r}")
            values = frozenset(str(value).strip().lower() for value in raw_values if str(value).strip())
            if not values:
                raise ValueError(f"empty provenance for page: {key!r}")
            unknown = values - _ALLOWED_PROVENANCE
            if unknown:
                raise ValueError(f"unsupported provenance values: {sorted(unknown)}")
            if "both" in values and len(values) > 1:
                values = frozenset({"both"})
            elif {"lexical", "semantic"} <= values:
                values = frozenset({"both"})
            normalized_provenance[key] = values

        for key in normalized:
            normalized_provenance.setdefault(key, frozenset({"structure"}))

        return cls(
            allowed_pages=normalized,
            provenance_by_page=MappingProxyType(dict(sorted(normalized_provenance.items()))),
            max_unique_pages=int(max_unique_pages),
        )

    def contains(self, doc_id: str, canonical_physical_page: int) -> bool:
        try:
            key = _normalize_page_key((doc_id, canonical_physical_page))
        except ValueError:
            return False
        return key in self.allowed_pages

    def classify(self, doc_id: str, canonical_physical_page: int | None) -> str:
        if canonical_physical_page is None:
            return UNKNOWN_PAGE
        try:
            key = _normalize_page_key((doc_id, canonical_physical_page))
        except ValueError:
            return UNKNOWN_PAGE
        return IN_SCOPE if key in self.allowed_pages else OUTSIDE_SCOPE

    def financial_page_key(self, doc_id: str, source_page: int | None) -> PageKey | None:
        if source_page is None or isinstance(source_page, bool):
            return None
        try:
            zero_based = int(source_page)
        except (TypeError, ValueError):
            return None
        if zero_based < 0:
            return None
        try:
            return _normalize_page_key((doc_id, zero_based + 1))
        except ValueError:
            return None

    def classify_financial_page(self, doc_id: str, source_page: int | None) -> str:
        key = self.financial_page_key(doc_id, source_page)
        if key is None:
            return UNKNOWN_PAGE
        return IN_SCOPE if key in self.allowed_pages else OUTSIDE_SCOPE

    def classify_source(self, doc_id: str, canonical_source: str) -> str:
        source_doc, page = canonical_page_from_source(canonical_source)
        if source_doc and source_doc != str(doc_id or "").strip():
            return IDENTITY_CONFLICT
        return self.classify(doc_id, page)

    def audit_source_fact(self, fact: Any) -> dict[str, Any]:
        """Resolve all available page identities and fail closed on disagreement."""
        if isinstance(fact, Mapping):
            payload = fact
            doc_id = str(payload.get("doc_id") or payload.get("document_id") or "").strip()
            canonical_source = str(payload.get("canonical_source") or "")
            metadata = payload.get("metadata") or {}
            source_page = payload.get("source_page")
        else:
            doc_id = str(
                getattr(fact, "doc_id", "") or getattr(fact, "document_id", "") or ""
            ).strip()
            canonical_source = str(getattr(fact, "canonical_source", "") or "")
            metadata = getattr(fact, "metadata", {}) or {}
            source_page = getattr(fact, "source_page", None)

        if isinstance(metadata, Mapping) and source_page is None:
            source_page = metadata.get("source_page")

        identities: list[PageKey] = []
        explicit_key = self.financial_page_key(doc_id, source_page)
        if source_page is not None and explicit_key is None:
            return {
                "status": UNKNOWN_PAGE,
                "page_key": None,
                "identities": [],
                "reason": "invalid_financial_source_page",
            }
        if explicit_key is not None:
            identities.append(explicit_key)

        source_doc, source_page_one_based = canonical_page_from_source(canonical_source)
        if source_doc and source_doc != doc_id:
            return {
                "status": IDENTITY_CONFLICT,
                "page_key": None,
                "identities": [list(key) for key in identities],
                "reason": "canonical_source_document_conflict",
            }
        if source_page_one_based is not None:
            try:
                identities.append(_normalize_page_key((doc_id, source_page_one_based)))
            except ValueError:
                return {
                    "status": UNKNOWN_PAGE,
                    "page_key": None,
                    "identities": [list(key) for key in identities],
                    "reason": "invalid_canonical_source_page",
                }

        unique = tuple(dict.fromkeys(identities))
        if len(unique) > 1:
            return {
                "status": IDENTITY_CONFLICT,
                "page_key": None,
                "identities": [list(key) for key in unique],
                "reason": "page_identity_conflict",
            }
        if not unique:
            return {
                "status": UNKNOWN_PAGE,
                "page_key": None,
                "identities": [],
                "reason": "page_identity_missing",
            }

        key = unique[0]
        return {
            "status": IN_SCOPE if key in self.allowed_pages else OUTSIDE_SCOPE,
            "page_key": list(key),
            "identities": [list(key)],
            "reason": "workspace_membership",
        }

    def audit_source_facts(self, facts: Iterable[Any]) -> dict[str, Any]:
        rows = tuple(self.audit_source_fact(fact) for fact in facts)
        counts = {
            status: sum(1 for row in rows if row["status"] == status)
            for status in (IN_SCOPE, OUTSIDE_SCOPE, UNKNOWN_PAGE, IDENTITY_CONFLICT)
        }
        return {
            "schema_version": "evidence_workspace_audit_v1",
            "rows": list(rows),
            "counts": counts,
            "all_contributing_facts_in_scope": bool(rows) and counts[IN_SCOPE] == len(rows),
            "safe_for_trusted_evidence": counts[OUTSIDE_SCOPE] == 0
            and counts[UNKNOWN_PAGE] == 0
            and counts[IDENTITY_CONFLICT] == 0,
        }


__all__ = [
    "EvidenceWorkspaceScope",
    "PageKey",
    "IN_SCOPE",
    "OUTSIDE_SCOPE",
    "UNKNOWN_PAGE",
    "IDENTITY_CONFLICT",
    "canonical_page_from_source",
]
