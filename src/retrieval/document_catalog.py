"""Deterministic document catalog for multi-slot document-scope discovery.

The catalog is built only from corpus metadata that is independent of question
answers: retrieval directories, source paths, and high-confidence identity text
from the first parsed page.  It deliberately contains no qid -> doc_id mapping.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Sequence


_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_HEADING_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$")
_ANNUAL_REPORT_SUFFIX_RE = re.compile(
    r"(?:19|20)\d{2}\s*年?\s*年?度报告(?:全文)?(?:.*)?$",
    re.IGNORECASE,
)

_COMPANY_SUFFIXES = (
    # Strip generic legal suffixes before group-specific suffixes so useful
    # intermediate aliases survive, e.g. 美的集团股份有限公司 -> 美的集团 -> 美的.
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团股份有限公司",
    "集团有限公司",
    "股份",
    "集团",
)
_ENTITY_NOISE_SUFFIXES = (
    "新能源科技",
    "科技创新",
)


@dataclass(frozen=True)
class DocumentCatalogEntry:
    doc_id: str
    domain: str
    retrieval_dir: str
    source_paths: tuple[str, ...]
    title: str
    title_aliases: tuple[str, ...]
    identity_text: str
    # Deterministic document-wide lexical profile. It is intentionally omitted
    # from the serialized catalog artifact to avoid duplicating large corpus text.
    lexical_profile: str = ""
    lexical_profile_sha256: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "domain": self.domain,
            "retrieval_dir": self.retrieval_dir,
            "source_paths": list(self.source_paths),
            "title": self.title,
            "title_aliases": list(self.title_aliases),
            "identity_text": self.identity_text,
            "lexical_profile_sha256": self.lexical_profile_sha256,
        }


class DocumentCatalog:
    """Immutable domain-indexed catalog of retrievable documents."""

    def __init__(self, entries: Sequence[DocumentCatalogEntry]) -> None:
        ordered = sorted(entries, key=lambda item: (item.domain, item.doc_id))
        self._entries = tuple(ordered)
        by_domain: Dict[str, list[DocumentCatalogEntry]] = {}
        duplicate_groups: Dict[tuple[str, str], list[str]] = {}
        for entry in ordered:
            by_domain.setdefault(entry.domain, []).append(entry)
            if entry.lexical_profile_sha256:
                duplicate_groups.setdefault((entry.domain, entry.lexical_profile_sha256), []).append(entry.doc_id)
        self._by_domain = {key: tuple(value) for key, value in by_domain.items()}
        self._duplicate_groups = {
            key: tuple(sorted(values))
            for key, values in duplicate_groups.items()
            if len(values) >= 2
        }

    @property
    def entries(self) -> tuple[DocumentCatalogEntry, ...]:
        return self._entries

    def entries_for_domain(self, domain: str) -> tuple[DocumentCatalogEntry, ...]:
        return self._by_domain.get(str(domain), ())

    def duplicate_doc_ids(self, entry: DocumentCatalogEntry) -> tuple[str, ...]:
        if not entry.lexical_profile_sha256:
            return ()
        return self._duplicate_groups.get((entry.domain, entry.lexical_profile_sha256), ())

    def to_dict(self) -> dict[str, object]:
        return {
            "count": len(self._entries),
            "domains": {
                domain: len(entries)
                for domain, entries in sorted(self._by_domain.items())
            },
            "duplicate_lexical_profile_groups": [
                {"domain": domain, "lexical_profile_sha256": digest, "doc_ids": list(doc_ids)}
                for (domain, digest), doc_ids in sorted(self._duplicate_groups.items())
            ],
            "documents": [entry.to_dict() for entry in self._entries],
        }

    @classmethod
    def from_roots(
        cls,
        primary_root: Path,
        *,
        fallback_roots: Sequence[Path] = (),
        raw_root: Path | None = None,
        max_identity_chars: int = 8000,
        max_lexical_chars: int = 80000,
        lexical_chars_per_page: int = 2200,
    ) -> "DocumentCatalog":
        """Build a catalog from retrieval roots without answer-side metadata.

        The first root that contains a document wins.  Fallback roots may add
        documents absent from the primary root, but cannot replace the primary
        representation.  Only directories containing a non-empty ``page_*.md``
        file become catalog entries.
        """
        roots = tuple(Path(root) for root in (primary_root, *fallback_roots))
        primary_domains = (
            {path.name for path in Path(primary_root).iterdir() if path.is_dir()}
            if Path(primary_root).is_dir()
            else set()
        )
        seen: set[tuple[str, str]] = set()
        entries: list[DocumentCatalogEntry] = []

        for root_index, root in enumerate(roots):
            if not root.is_dir():
                continue
            for domain_dir in sorted(path for path in root.iterdir() if path.is_dir()):
                domain = domain_dir.name
                # A fallback parser may have helper trees such as ``attachments``.
                # It can supplement a canonical primary domain but must not create
                # a new business domain that the primary retrieval corpus does not
                # expose.
                if root_index > 0 and primary_domains and domain not in primary_domains:
                    continue
                for doc_dir in sorted(path for path in domain_dir.iterdir() if path.is_dir()):
                    key = (domain, doc_dir.name)
                    if key in seen:
                        continue
                    first_page = _first_nonempty_page(doc_dir)
                    if first_page is None:
                        continue
                    seen.add(key)
                    identity_text = _clean_identity_text(
                        first_page.read_text(encoding="utf-8", errors="ignore"),
                        max_chars=max_identity_chars,
                    )
                    lexical_profile, lexical_profile_sha256 = _build_document_profile(
                        doc_dir,
                        max_chars=max_lexical_chars,
                        chars_per_page=lexical_chars_per_page,
                    )
                    title, aliases = _extract_title_and_aliases(
                        doc_id=doc_dir.name,
                        identity_text=identity_text,
                    )
                    source_paths = _source_paths(
                        domain=domain,
                        doc_id=doc_dir.name,
                        retrieval_dir=doc_dir,
                        raw_root=raw_root,
                    )
                    entries.append(
                        DocumentCatalogEntry(
                            doc_id=doc_dir.name,
                            domain=domain,
                            retrieval_dir=str(doc_dir),
                            source_paths=source_paths,
                            title=title,
                            title_aliases=aliases,
                            identity_text=identity_text,
                            lexical_profile=lexical_profile,
                            lexical_profile_sha256=lexical_profile_sha256,
                        )
                    )
        return cls(entries)


def _first_nonempty_page(doc_dir: Path) -> Path | None:
    for page in sorted(doc_dir.glob("page_*.md")):
        try:
            if page.is_file() and page.stat().st_size > 0:
                return page
        except OSError:
            continue
    return None


def _build_document_profile(
    doc_dir: Path,
    *,
    max_chars: int,
    chars_per_page: int,
) -> tuple[str, str]:
    """Build a bounded, document-wide lexical profile and profile fingerprint.

    The profile uses only deterministic corpus text: up to ``chars_per_page``
    cleaned characters from successive parsed pages. This broadens recall for
    research reports whose cover page is generic without creating any
    question-oriented summary. Only a bounded prefix of each page is read, so
    catalog construction does not scan the full corpus on every process start.
    """
    snippets: list[str] = []
    used = 0
    for page in sorted(doc_dir.glob("page_*.md")):
        if used >= max_chars:
            break
        if not page.is_file():
            continue
        # Markdown image URLs and boilerplate can expand raw input substantially;
        # read a bounded multiple of the desired cleaned excerpt instead of the
        # entire page file.
        raw_read_chars = max(chars_per_page * 4, chars_per_page + 2000)
        with page.open("r", encoding="utf-8", errors="ignore") as handle:
            raw_text = handle.read(raw_read_chars)
        if not raw_text:
            continue
        cleaned = _clean_identity_text(
            raw_text,
            max_chars=min(chars_per_page, max_chars - used),
        )
        if cleaned:
            snippets.append(cleaned)
            used += len(cleaned)
    profile = "\n".join(snippets)[:max_chars]
    fingerprint = hashlib.sha256(profile.encode("utf-8")).hexdigest() if profile else ""
    return profile, fingerprint


def _clean_identity_text(text: str, *, max_chars: int) -> str:
    text = _IMAGE_RE.sub(" ", text)
    text = _HTML_RE.sub(" ", text)
    text = text.replace("<!-- page 0: no renderable text (image-only or empty blocks) -->", " ")
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            line = heading.group(1).strip()
        if line:
            lines.append(line)
        if sum(len(item) for item in lines) >= max_chars:
            break
    return "\n".join(lines)[:max_chars]


def _extract_title_and_aliases(*, doc_id: str, identity_text: str) -> tuple[str, tuple[str, ...]]:
    lines = [line.strip() for line in identity_text.splitlines() if line.strip()]
    title_lines: list[str] = []
    for line in lines[:18]:
        compact = _compact(line)
        if not compact:
            continue
        # Very long prose is useful identity text but a poor title alias.
        if len(compact) <= 80:
            title_lines.append(line)
        if len(title_lines) >= 6:
            break

    title = " / ".join(title_lines[:3]) if title_lines else doc_id
    raw_aliases: list[str] = [doc_id, doc_id.replace("_", " ")]
    raw_aliases.extend(title_lines)

    # Strict-v3 doc ids already contain a high-confidence official title.
    if "（" in doc_id and "）" in doc_id:
        raw_aliases.append(doc_id.split("（", 1)[1].rsplit("）", 1)[0])

    # Add compact company/product aliases derived only from catalog titles.
    # Annual-report cover lines often fuse entity + year + report title; strip
    # that generic suffix first so the entity survives OCR/layout variation.
    for value in list(raw_aliases):
        compact = _compact(value)
        if not compact:
            continue
        without_report = _ANNUAL_REPORT_SUFFIX_RE.sub("", compact).strip()
        if len(without_report) >= 2 and without_report != compact:
            raw_aliases.append(without_report)

    for value in list(raw_aliases):
        compact = _compact(value)
        if not compact:
            continue
        current = compact
        changed = True
        while changed:
            changed = False
            for suffix in _COMPANY_SUFFIXES:
                if current.endswith(suffix) and len(current) > len(suffix) + 1:
                    current = current[: -len(suffix)]
                    raw_aliases.append(current)
                    changed = True
                    break
        for suffix in _ENTITY_NOISE_SUFFIXES:
            if current.endswith(suffix) and len(current) > len(suffix) + 1:
                raw_aliases.append(current[: -len(suffix)])

    aliases: list[str] = []
    seen: set[str] = set()
    for value in raw_aliases:
        compact = _compact(value)
        if len(compact) < 2 or compact in seen:
            continue
        seen.add(compact)
        aliases.append(value.strip())
    return title, tuple(aliases)


def _source_paths(*, domain: str, doc_id: str, retrieval_dir: Path, raw_root: Path | None) -> tuple[str, ...]:
    paths = [str(retrieval_dir)]
    if raw_root is not None:
        domain_root = Path(raw_root) / domain
        if domain_root.is_dir():
            for source in sorted(domain_root.glob(f"{doc_id}.*")):
                if source.is_file():
                    paths.append(str(source))
    return tuple(dict.fromkeys(paths))


def _compact(value: str) -> str:
    value = str(value or "").lower()
    return _SPACE_RE.sub("", re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value))
