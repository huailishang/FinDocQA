"""Corpus validator for adapted MinerU output (Lane A, remote-offline).

Reads an *adapted* corpus directory (the output of ``adapt_corpus`` /
``adapt_document``) and validates the structural invariants the retrieval
pipeline depends on:

- **page continuity**: ``page_0001.md`` .. ``page_NNNN.md`` with no gaps;
- **image-only pages**: pages whose only content is an image placeholder /
  reference (so they are preserved but flagged for retrieval recall risk);
- **table blocks**: count of GFM tables per document (financial answers often
  depend on table row/column binding);
- **formula blocks**: count of ``$$ ... $$`` formula blocks;
- **doc-id mapping**: ``document_structure.json`` ``doc_id`` matches the
  directory name;
- **degraded flags**: ``document_structure.json`` ``degraded=True`` documents
  (markdown-fallback path used, page boundaries unreliable).

This module is **read-only** and standard-library only. It does not modify the
corpus, call any LLM, or touch the live retrieval pipeline. It is exercised by
synthetic fixtures under ``tests/fixtures/mineru/`` and by the CLI in
``scripts/validate_corpus.py``.

See ``handoffs/executor_task_stage5_remote_parallel_offline_dispatch.md`` Lane A.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple


# page_XXXX.md filename pattern (1-based, zero-padded to 4 digits).
_PAGE_FILE_RE = re.compile(r"^page_(\d+)\.md$")
# GFM table separator row, e.g. "| --- | --- |" or "| :---: | ---: |".
_GFM_SEPARATOR_RE = re.compile(r"^\s*\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
# A fenced formula block opens/closes with a line that is just "$$".
_FORMULA_FENCE_RE = re.compile(r"^\s*\$\$\s*$")


def _extract_page_number(name: str) -> Optional[int]:
    m = _PAGE_FILE_RE.match(name)
    return int(m.group(1)) if m else None


def _is_image_only_page(text: str) -> bool:
    """True when a page file carries no substantive text beyond image markup.

    Strips HTML comments (image-only placeholders), ``![alt](src)`` image
    references and ``*caption*`` lines. If nothing substantive remains, the
    page is image-only. This flags retrieval-recall risk without dropping the
    page (the adapter already preserves it for page-number continuity).
    """
    no_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    substantive: List[str] = []
    for line in no_comments.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("![") and "]" in s and "(" in s:  # image reference
            continue
        if s.startswith("*") and s.endswith("*") and len(s) > 2:  # caption
            continue
        substantive.append(s)
    return len(substantive) == 0


def _count_tables_and_formulas(text: str) -> Tuple[int, int]:
    """Return (gfm_table_count, formula_block_count) found in ``text``.

    A GFM table is counted once per separator row (each table has exactly one
    separator). A formula block is a ``$$``-fenced region; each opening ``$$``
    on its own line starts one block.
    """
    tables = 0
    formulas = 0
    in_formula = False
    for line in text.splitlines():
        if _GFM_SEPARATOR_RE.match(line):
            tables += 1
        if _FORMULA_FENCE_RE.match(line):
            if not in_formula:
                formulas += 1
            in_formula = not in_formula
    return tables, formulas


@dataclass(frozen=True)
class DocValidation:
    """Validation result for one adapted document directory.

    Attributes:
        doc_id: the directory name (expected doc id).
        domain: the domain label.
        doc_dir: path to the document directory.
        structure_found: True when ``document_structure.json`` exists.
        reconstruction_mode: from structure (``content_list_v2`` /
            ``markdown_fallback``); empty when structure is missing.
        degraded: from structure; False when structure is missing.
        declared_page_count: ``page_count`` from structure; 0 when missing.
        actual_page_files: number of ``page_XXXX.md`` files on disk.
        page_numbers: sorted tuple of actual page numbers found on disk.
        page_gaps: expected page numbers (1..declared_page_count) that have no
            ``page_XXXX.md`` file — a continuity break.
        image_only_pages: page numbers whose content is image-only.
        table_count: total GFM tables across all page files.
        formula_count: total ``$$`` formula blocks across all page files.
        doc_id_matches: True iff structure ``doc_id`` equals the directory name.
        warnings: human-readable validation warnings.
    """

    doc_id: str
    domain: str
    doc_dir: str
    structure_found: bool
    reconstruction_mode: str
    degraded: bool
    declared_page_count: int
    actual_page_files: int
    page_numbers: Tuple[int, ...]
    page_gaps: Tuple[int, ...]
    image_only_pages: Tuple[int, ...]
    table_count: int
    formula_count: int
    doc_id_matches: bool
    warnings: Tuple[str, ...]


@dataclass(frozen=True)
class CorpusValidationReport:
    """Aggregate validation report for one domain corpus.

    Attributes:
        domain: the domain label.
        corpus_root: path to the ``<target_root>/<domain>`` directory.
        doc_count: number of document directories validated.
        docs: per-document validation results.
        total_page_gaps: sum of per-doc page gaps.
        total_image_only_pages: count of image-only pages across the corpus.
        total_degraded_docs: count of documents with ``degraded=True``.
        docs_with_warnings: count of documents carrying >= 1 warning.
        text: rendered markdown report.
    """

    domain: str
    corpus_root: str
    doc_count: int
    docs: Tuple[DocValidation, ...]
    total_page_gaps: int
    total_image_only_pages: int
    total_degraded_docs: int
    docs_with_warnings: int
    text: str


def _load_structure(doc_dir: Path) -> Optional[Mapping[str, Any]]:
    struct_path = doc_dir / "document_structure.json"
    if not struct_path.is_file():
        return None
    try:
        return json.loads(struct_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def validate_doc(doc_dir: Path, *, domain: str, doc_id: str) -> DocValidation:
    """Validate one adapted document directory.

    Args:
        doc_dir: directory containing ``page_XXXX.md`` + ``document_structure.json``.
        domain: domain label for the report.
        doc_id: expected doc id (normally the directory name).

    Returns:
        DocValidation with all structural invariants checked.
    """
    doc_dir = Path(doc_dir)
    warnings: List[str] = []
    structure = _load_structure(doc_dir)

    structure_found = structure is not None
    reconstruction_mode = ""
    degraded = False
    declared_page_count = 0
    doc_id_matches = False
    if structure is not None:
        reconstruction_mode = str(structure.get("reconstruction_mode", "") or "")
        degraded = bool(structure.get("degraded", False))
        declared_page_count = int(structure.get("page_count", 0) or 0)
        struct_doc_id = str(structure.get("doc_id", "") or "")
        doc_id_matches = struct_doc_id == doc_id
        if not doc_id_matches:
            warnings.append(
                f"doc_id mismatch: structure says '{struct_doc_id}', dir is '{doc_id}'"
            )
        if degraded:
            warnings.append("degraded=True (markdown_fallback; page boundaries unreliable)")
        if not reconstruction_mode:
            warnings.append("document_structure.json missing reconstruction_mode")
    else:
        warnings.append("document_structure.json not found")

    # Enumerate page files on disk.
    page_files: List[Tuple[int, Path]] = []
    for entry in sorted(doc_dir.iterdir()) if doc_dir.is_dir() else []:
        num = _extract_page_number(entry.name)
        if num is not None and entry.is_file():
            page_files.append((num, entry))
    page_numbers = tuple(n for n, _ in page_files)
    actual_page_files = len(page_files)

    # Page continuity: declared pages 1..declared_page_count must all exist.
    if declared_page_count > 0:
        expected = set(range(1, declared_page_count + 1))
    else:
        expected = set(page_numbers)
    actual_set = set(page_numbers)
    page_gaps = tuple(sorted(expected - actual_set))
    if page_gaps:
        warnings.append(f"page continuity gap: missing pages {list(page_gaps)}")
    extra = sorted(actual_set - expected)
    if extra and declared_page_count > 0:
        warnings.append(f"unexpected page files beyond declared count: {extra}")

    # Per-page content analysis.
    image_only_pages: List[int] = []
    table_count = 0
    formula_count = 0
    for num, p in page_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            warnings.append(f"page_{num:04d}.md: could not read")
            continue
        if _is_image_only_page(text):
            image_only_pages.append(num)
        t, f = _count_tables_and_formulas(text)
        table_count += t
        formula_count += f

    if image_only_pages:
        warnings.append(f"image-only pages: {image_only_pages}")

    return DocValidation(
        doc_id=doc_id,
        domain=domain,
        doc_dir=str(doc_dir),
        structure_found=structure_found,
        reconstruction_mode=reconstruction_mode,
        degraded=degraded,
        declared_page_count=declared_page_count,
        actual_page_files=actual_page_files,
        page_numbers=page_numbers,
        page_gaps=page_gaps,
        image_only_pages=tuple(image_only_pages),
        table_count=table_count,
        formula_count=formula_count,
        doc_id_matches=doc_id_matches,
        warnings=tuple(warnings),
    )


def validate_corpus(target_root: Path, *, domain: str) -> CorpusValidationReport:
    """Validate every document under ``<target_root>/<domain>/``.

    Args:
        target_root: root containing the adapted ``<domain>/<doc_id>/`` tree.
        domain: domain subdirectory to validate.

    Returns:
        CorpusValidationReport covering every doc_id directory found.
    """
    target_root = Path(target_root)
    domain_dir = target_root / domain
    docs: List[DocValidation] = []
    if domain_dir.is_dir():
        for entry in sorted(domain_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            docs.append(validate_doc(entry, domain=domain, doc_id=entry.name))

    total_gaps = sum(len(d.page_gaps) for d in docs)
    total_image_only = sum(len(d.image_only_pages) for d in docs)
    total_degraded = sum(1 for d in docs if d.degraded)
    docs_with_warnings = sum(1 for d in docs if d.warnings)

    text = _render_markdown(
        domain=domain,
        corpus_root=str(domain_dir),
        docs=tuple(docs),
        total_gaps=total_gaps,
        total_image_only=total_image_only,
        total_degraded=total_degraded,
        docs_with_warnings=docs_with_warnings,
    )
    return CorpusValidationReport(
        domain=domain,
        corpus_root=str(domain_dir),
        doc_count=len(docs),
        docs=tuple(docs),
        total_page_gaps=total_gaps,
        total_image_only_pages=total_image_only,
        total_degraded_docs=total_degraded,
        docs_with_warnings=docs_with_warnings,
        text=text,
    )


def _render_markdown(
    *,
    domain: str,
    corpus_root: str,
    docs: Tuple[DocValidation, ...],
    total_gaps: int,
    total_image_only: int,
    total_degraded: int,
    docs_with_warnings: int,
) -> str:
    lines: List[str] = [
        f"# Corpus Validation Report — {domain}",
        "",
        f"- corpus_root: `{corpus_root}`",
        f"- documents: {len(docs)}",
        f"- total page gaps: {total_gaps}",
        f"- total image-only pages: {total_image_only}",
        f"- degraded documents: {total_degraded}",
        f"- documents with warnings: {docs_with_warnings}",
        "",
        "## Per-document summary",
        "",
        "| doc_id | mode | degraded | pages (declared/actual) | gaps | image-only | tables | formulas | doc_id match |",
        "| --- | --- | :---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for d in docs:
        lines.append(
            f"| {d.doc_id} | {d.reconstruction_mode or '—'} | {'yes' if d.degraded else 'no'} | "
            f"{d.declared_page_count}/{d.actual_page_files} | {len(d.page_gaps)} | "
            f"{len(d.image_only_pages)} | {d.table_count} | {d.formula_count} | "
            f"{'yes' if d.doc_id_matches else 'NO'} |"
        )
    if not docs:
        lines.append("| _no documents found_ | | | | | | | | |")

    flagged = [d for d in docs if d.warnings]
    if flagged:
        lines += ["", "## Warnings", ""]
        for d in flagged:
            lines.append(f"### {d.doc_id}")
            for w in d.warnings:
                lines.append(f"- {w}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point: validate an adapted corpus directory."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate an adapted MinerU corpus (page continuity, image-only, tables, formulas, doc-id, degraded)."
    )
    parser.add_argument("target_root", help="Root containing the adapted <domain>/<doc_id>/ tree.")
    parser.add_argument("--domain", required=True, help="Domain subdirectory to validate.")
    args = parser.parse_args(argv)

    report = validate_corpus(Path(args.target_root), domain=args.domain)
    print(report.text)
    # Non-zero exit only when structural invariants are broken (gaps or doc-id
    # mismatches); image-only / degraded are reported as risk, not failure.
    broken = any(d.page_gaps or not d.doc_id_matches for d in report.docs)
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
