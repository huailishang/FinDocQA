"""Opt-in selective parser fallback policy for calibrated page-level anomalies.

The policy is deliberately qid-agnostic.  It only knows parser calibration,
document/page identity, and retrieval-window membership.  Callers may apply
additional question/document relevance gates before adding a fallback page to
an evidence packet.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
import json
import re
from typing import Any, Iterable, Mapping, Sequence

STATUS_FALLBACK_PYMUPDF_CANDIDATE = "FALLBACK_PYMUPDF_CANDIDATE"
BLOCKED_STATUSES = {"BOTH_WEAK", "UNALIGNED_REVIEW"}
_PAGE_RE = re.compile(r"page_(\d+)\.md$", re.IGNORECASE)
_FIN_REPORT_FAMILY_RE = re.compile(r"^(annual_[a-z0-9]+)_\d{4}_report$", re.IGNORECASE)
_GENERIC_ALIAS_RE = re.compile(r"^(?:19|20)\d{2}.*(?:年度报告|年报)?$|^年度报告$|^年报$")


@dataclass(frozen=True)
class SourceIdentity:
    domain: str
    doc_id: str
    page_number: int


@dataclass(frozen=True)
class FallbackRule:
    identity: SourceIdentity
    status: str
    mineru_page: str
    pymupdf_page: str
    source_file: str
    source_page_index: int | None
    nearest_heading: str
    alignment_basis: str
    evidence: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["identity"] = asdict(self.identity)
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True)
class RetrievalFallbackHit:
    source: str
    identity: SourceIdentity
    rule: FallbackRule

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "identity": asdict(self.identity),
            "rule": self.rule.to_dict(),
        }


def parse_source_identity(source: str | Path) -> SourceIdentity | None:
    """Parse ``.../<domain>/<doc_id>/page_NNNN.md`` on Windows or POSIX."""

    raw = str(source)
    # Frozen artifacts contain Windows paths even when inspected from WSL.
    path = PureWindowsPath(raw) if "\\" in raw else Path(raw)
    match = _PAGE_RE.search(path.name)
    if not match or len(path.parts) < 3:
        return None
    return SourceIdentity(
        domain=str(path.parent.parent.name),
        doc_id=str(path.parent.name),
        page_number=int(match.group(1)),
    )


def report_family(doc_id: str) -> str:
    match = _FIN_REPORT_FAMILY_RE.match(doc_id)
    return match.group(1) if match else doc_id


class DocumentEntityIndex:
    """Small catalog-backed entity gate used to reject cross-company scope noise."""

    def __init__(self, aliases_by_family: Mapping[str, Sequence[str]]) -> None:
        self.aliases_by_family = {
            str(family): tuple(str(alias) for alias in aliases if str(alias).strip())
            for family, aliases in aliases_by_family.items()
        }

    @classmethod
    def from_catalog_json(cls, path: Path) -> "DocumentEntityIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        grouped: dict[str, list[str]] = {}
        for row in payload.get("documents", []):
            if not isinstance(row, Mapping):
                continue
            doc_id = str(row.get("doc_id") or "").strip()
            if not doc_id:
                continue
            family = report_family(doc_id)
            values = [str(row.get("title") or ""), str(row.get("identity_text") or "")]
            values.extend(str(x) for x in row.get("title_aliases", []) if str(x).strip())
            grouped.setdefault(family, []).extend(values)

        cleaned: dict[str, tuple[str, ...]] = {}
        for family, values in grouped.items():
            seen: set[str] = set()
            aliases: list[str] = []
            for value in values:
                for line in re.split(r"[\n/]+", value):
                    alias = re.sub(r"\s+", "", line).strip(" ：:，,。")
                    alias = re.sub(r"(?:19|20)\d{2}年?年?度报告(?:全文)?.*$", "", alias)
                    alias = re.sub(r"(?:19|20)\d{2}年", "", alias)
                    alias = re.sub(r"年度报告(?:全文)?$", "", alias)
                    if len(alias) < 2 or _GENERIC_ALIAS_RE.match(alias):
                        continue
                    if alias.lower() in {family.lower(), family.replace("_", "").lower()}:
                        continue
                    derived = [alias]
                    current = alias
                    for suffix in (
                        "集团股份有限公司",
                        "股份有限公司",
                        "有限责任公司",
                        "集团有限公司",
                        "有限公司",
                        "股份",
                    ):
                        if current.endswith(suffix) and len(current) > len(suffix) + 1:
                            current = current[: -len(suffix)]
                            derived.append(current)
                            break
                    if current.endswith("集团") and len(current) > len("集团") + 1:
                        derived.append(current[: -len("集团")])
                    for suffix in ("新能源科技", "科技创新"):
                        if current.endswith(suffix) and len(current) > len(suffix) + 1:
                            derived.append(current[: -len(suffix)])
                    for candidate in derived:
                        if candidate not in seen:
                            seen.add(candidate)
                            aliases.append(candidate)
            cleaned[family] = tuple(aliases)
        return cls(cleaned)

    def matched_aliases(self, doc_id: str, question_surface: str) -> tuple[str, ...]:
        compact = re.sub(r"\s+", "", question_surface)
        aliases = self.aliases_by_family.get(report_family(doc_id), ())
        matches = [alias for alias in aliases if len(alias) >= 2 and alias in compact]
        return tuple(sorted(set(matches), key=lambda value: (-len(value), value)))

    def matches(self, doc_id: str, question_surface: str) -> bool:
        return bool(self.matched_aliases(doc_id, question_surface))


class SelectiveParserFallbackPolicy:
    """Load only explicitly calibrated PyMuPDF fallback candidates.

    ``enabled`` defaults to ``False`` so importing the module cannot alter the
    production evidence path.  The policy does not accept qids and therefore
    cannot contain per-question production special cases.
    """

    def __init__(self, rules: Mapping[SourceIdentity, FallbackRule], *, enabled: bool = False) -> None:
        self._rules = dict(rules)
        self.enabled = bool(enabled)

    @classmethod
    def from_comparison_json(cls, path: Path, *, enabled: bool = False) -> "SelectiveParserFallbackPolicy":
        payload = json.loads(path.read_text(encoding="utf-8"))
        rules: dict[SourceIdentity, FallbackRule] = {}
        for row in payload.get("pages", []):
            if not isinstance(row, Mapping):
                continue
            status = str(row.get("status") or "")
            if status != STATUS_FALLBACK_PYMUPDF_CANDIDATE:
                continue
            lineage = row.get("lineage") if isinstance(row.get("lineage"), Mapping) else {}
            identity = SourceIdentity(
                domain=str(row.get("domain") or lineage.get("domain") or ""),
                doc_id=str(row.get("doc_id") or lineage.get("doc_id") or ""),
                page_number=int(row.get("page_number") or lineage.get("page_number") or 0),
            )
            if not identity.domain or not identity.doc_id or identity.page_number <= 0:
                continue
            rules[identity] = FallbackRule(
                identity=identity,
                status=status,
                mineru_page=str(lineage.get("mineru_page") or ""),
                pymupdf_page=str(lineage.get("pymupdf_page") or ""),
                source_file=str(lineage.get("source_file") or ""),
                source_page_index=(int(lineage["source_page_index"]) if lineage.get("source_page_index") is not None else None),
                nearest_heading=str(lineage.get("nearest_heading") or ""),
                alignment_basis=str(lineage.get("alignment_basis") or ""),
                evidence=tuple(dict(item) for item in row.get("evidence", []) if isinstance(item, Mapping)),
            )
        return cls(rules, enabled=enabled)

    @property
    def candidate_count(self) -> int:
        return len(self._rules)

    def rule_for(self, identity: SourceIdentity) -> FallbackRule | None:
        if not self.enabled:
            return None
        rule = self._rules.get(identity)
        if rule is None or rule.status in BLOCKED_STATUSES:
            return None
        return rule

    def hits_for_retrieval_window(self, sources: Iterable[str | Path]) -> list[RetrievalFallbackHit]:
        if not self.enabled:
            return []
        hits: list[RetrievalFallbackHit] = []
        seen: set[SourceIdentity] = set()
        for source in sources:
            identity = parse_source_identity(source)
            if identity is None or identity in seen:
                continue
            rule = self.rule_for(identity)
            if rule is None:
                continue
            seen.add(identity)
            hits.append(RetrievalFallbackHit(source=str(source), identity=identity, rule=rule))
        return hits

    def all_rules(self) -> tuple[FallbackRule, ...]:
        return tuple(self._rules[key] for key in sorted(self._rules, key=lambda x: (x.domain, x.doc_id, x.page_number)))
