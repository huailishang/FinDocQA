#!/usr/bin/env python3
"""Build a reproducibility manifest for an adapted MinerU corpus.

This script is read-only with respect to the corpus: it records source PDF
hashes, adapted-output hashes, raw MinerU-output hashes when available, page
mapping metadata and validator summaries. The manifest is meant to close the
M1-A follow-up that required formal metadata before A0/A1/B1 focused
comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from structure.corpus_validator import validate_corpus  # noqa: E402


FOCUSED_DOC_IDS: Mapping[str, Sequence[str]] = {
    "insurance": ("3", "5", "6"),
    "financial_contracts": ("text02", "text03"),
    "financial_reports": ("annual_catl_2025_report", "annual_midea_2024_report"),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    h = hashlib.sha256()
    if not root.is_dir():
        return {"exists": False, "sha256": "", "file_count": 0, "bytes": 0, "files": []}

    total_bytes = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        digest = sha256_file(path)
        size = path.stat().st_size
        total_bytes += size
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("ascii"))
        h.update(b"\0")
        files.append({"path": rel, "bytes": size, "sha256": digest})

    return {
        "exists": True,
        "sha256": h.hexdigest(),
        "file_count": len(files),
        "bytes": total_bytes,
        "files": files,
    }


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def find_source_pdf(raw_root: Path, domain: str, doc_id: str) -> Path | None:
    domain_dir = raw_root / domain
    if not domain_dir.is_dir():
        return None
    for suffix in (".pdf", ".PDF"):
        candidate = domain_dir / f"{doc_id}{suffix}"
        if candidate.is_file():
            return candidate
    matches = sorted(
        p for p in domain_dir.iterdir() if p.is_file() and p.stem.lower() == doc_id.lower()
    )
    return matches[0] if matches else None


def raw_output_candidates(processed_mineru_root: Path, domain: str, doc_id: str) -> list[Path]:
    return [
        processed_mineru_root / "focused_raw" / domain / doc_id,
        processed_mineru_root / domain / doc_id,
    ]


def structure_page_summary(structure: Mapping[str, Any]) -> dict[str, Any]:
    pages = structure.get("pages", [])
    if not isinstance(pages, list):
        pages = []
    page_numbers: list[int] = []
    page_files: list[str] = []
    for page in pages:
        if not isinstance(page, Mapping):
            continue
        try:
            page_numbers.append(int(page.get("page", 0) or 0))
        except (TypeError, ValueError):
            page_numbers.append(0)
        file_name = str(page.get("file", "") or "")
        if file_name:
            page_files.append(file_name)
    positive = [n for n in page_numbers if n > 0]
    explicit_mapping_mode = str(
        structure.get("reconstruction_mode")
        or structure.get("page_mapping_mode")
        or ""
    )
    inferred_mapping_mode = "document_structure_pages" if page_files else "unknown"
    return {
        "page_count": int(structure.get("page_count", 0) or len(positive)),
        "page_numbers_min": min(positive) if positive else 0,
        "page_numbers_max": max(positive) if positive else 0,
        "page_files": page_files,
        "page_mapping_mode": explicit_mapping_mode or inferred_mapping_mode,
        "degraded": bool(structure.get("degraded", False)),
        "warnings": list(structure.get("warnings", []) or []),
    }


def iter_doc_dirs(corpus_root: Path, domains: Iterable[str]) -> Iterable[tuple[str, str, Path]]:
    for domain in domains:
        domain_dir = corpus_root / domain
        expected = FOCUSED_DOC_IDS.get(domain)
        doc_ids: Sequence[str]
        if expected:
            doc_ids = expected
        elif domain_dir.is_dir():
            doc_ids = tuple(sorted(p.name for p in domain_dir.iterdir() if p.is_dir()))
        else:
            doc_ids = ()
        for doc_id in doc_ids:
            yield domain, doc_id, domain_dir / doc_id


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    corpus_root = Path(args.corpus_root).resolve()
    raw_root = Path(args.raw_root).resolve()
    processed_mineru_root = Path(args.processed_mineru_root).resolve()
    domains = tuple(args.domain or FOCUSED_DOC_IDS.keys())

    docs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for domain, doc_id, doc_dir in iter_doc_dirs(corpus_root, domains):
        structure_path = doc_dir / "document_structure.json"
        structure = load_json(structure_path)
        source_pdf = find_source_pdf(raw_root, domain, doc_id)
        raw_dirs = [p for p in raw_output_candidates(processed_mineru_root, domain, doc_id) if p.is_dir()]
        adapted_hash = sha256_tree(doc_dir)
        raw_hash = sha256_tree(raw_dirs[0]) if raw_dirs else {
            "exists": False,
            "sha256": "",
            "file_count": 0,
            "bytes": 0,
            "files": [],
        }
        page_summary = structure_page_summary(structure)
        status = "completed"
        warnings: list[str] = []
        if not doc_dir.is_dir():
            status = "missing_adapted_doc"
            failures.append({"domain": domain, "doc_id": doc_id, "reason": status})
        if not source_pdf:
            warnings.append("source PDF not found")
        if not structure:
            warnings.append("document_structure.json missing or unreadable")
        if not raw_dirs:
            warnings.append("raw MinerU output directory not found")

        docs.append(
            {
                "domain": domain,
                "doc_id": doc_id,
                "status": status,
                "source_pdf": rel(source_pdf) if source_pdf else "",
                "source_pdf_sha256": sha256_file(source_pdf) if source_pdf else "",
                "source_pdf_bytes": source_pdf.stat().st_size if source_pdf else 0,
                "adapted_dir": rel(doc_dir),
                "adapted_tree_sha256": adapted_hash["sha256"],
                "adapted_file_count": adapted_hash["file_count"],
                "adapted_bytes": adapted_hash["bytes"],
                "raw_mineru_dir": rel(raw_dirs[0]) if raw_dirs else "",
                "raw_mineru_tree_sha256": raw_hash["sha256"],
                "raw_mineru_file_count": raw_hash["file_count"],
                "raw_mineru_bytes": raw_hash["bytes"],
                "parser": str(structure.get("parser", "mineru") or "mineru"),
                "page_count": page_summary["page_count"],
                "page_mapping_mode": page_summary["page_mapping_mode"],
                "degraded": page_summary["degraded"],
                "page_numbers_min": page_summary["page_numbers_min"],
                "page_numbers_max": page_summary["page_numbers_max"],
                "page_files": page_summary["page_files"],
                "structure_warnings": page_summary["warnings"],
                "manifest_warnings": warnings,
            }
        )

    validation: dict[str, Any] = {}
    for domain in domains:
        report = validate_corpus(corpus_root, domain=domain)
        validation[domain] = {
            "doc_count": report.doc_count,
            "total_page_gaps": report.total_page_gaps,
            "total_image_only_pages": report.total_image_only_pages,
            "total_degraded_docs": report.total_degraded_docs,
            "docs_with_warnings": report.docs_with_warnings,
        }

    return {
        "manifest_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage": "M1_A_MINERU_FOCUSED_CORPUS",
        "decision_context": "formal manifest for focused A0/A1/B1 comparison",
        "git_head": git_head(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "mineru_version": args.mineru_version,
        "backend": args.backend,
        "device": args.device,
        "corpus_root": rel(corpus_root),
        "raw_root": rel(raw_root),
        "processed_mineru_root": rel(processed_mineru_root),
        "domains": list(domains),
        "summary": {
            "documents_expected": len(docs),
            "documents_completed": sum(1 for d in docs if d["status"] == "completed"),
            "documents_failed": len(failures),
            "page_count_total": sum(int(d["page_count"]) for d in docs),
            "degraded_documents": sum(1 for d in docs if d["degraded"]),
            "source_pdf_hashes_recorded": all(bool(d["source_pdf_sha256"]) for d in docs),
            "adapted_hashes_recorded": all(bool(d["adapted_tree_sha256"]) for d in docs),
            "raw_mineru_hashes_recorded": all(bool(d["raw_mineru_tree_sha256"]) for d in docs),
            "page_mapping_recorded": all(bool(d["page_mapping_mode"]) for d in docs),
            "reproducibility_metadata_recorded": True,
        },
        "validation": validation,
        "documents": docs,
        "failures": failures,
    }


def render_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest["summary"]
    lines = [
        "# MinerU Focused Corpus Manifest",
        "",
        f"- stage: `{manifest['stage']}`",
        f"- generated_at: `{manifest['generated_at']}`",
        f"- git_head: `{manifest['git_head']}`",
        f"- corpus_root: `{manifest['corpus_root']}`",
        f"- raw_root: `{manifest['raw_root']}`",
        f"- mineru_version: `{manifest['mineru_version']}`",
        f"- backend/device: `{manifest['backend']}` / `{manifest['device']}`",
        "",
        "## Summary",
        "",
        f"- documents expected: {summary['documents_expected']}",
        f"- documents completed: {summary['documents_completed']}",
        f"- documents failed: {summary['documents_failed']}",
        f"- page count total: {summary['page_count_total']}",
        f"- degraded documents: {summary['degraded_documents']}",
        f"- source PDF hashes recorded: {summary['source_pdf_hashes_recorded']}",
        f"- adapted output hashes recorded: {summary['adapted_hashes_recorded']}",
        f"- raw MinerU hashes recorded: {summary['raw_mineru_hashes_recorded']}",
        f"- page mapping recorded: {summary['page_mapping_recorded']}",
        f"- REPRODUCIBILITY_METADATA_RECORDED: {summary['reproducibility_metadata_recorded']}",
        "",
        "## Per-document",
        "",
        "| domain | doc_id | pages | mode | degraded | source sha256 | adapted sha256 | raw sha256 | warnings |",
        "| --- | --- | ---: | --- | :---: | --- | --- | --- | --- |",
    ]
    for doc in manifest["documents"]:
        warnings = "; ".join(doc["manifest_warnings"] + doc["structure_warnings"])
        lines.append(
            "| {domain} | {doc_id} | {page_count} | {page_mapping_mode} | {degraded} | "
            "`{source}` | `{adapted}` | `{raw}` | {warnings} |".format(
                domain=doc["domain"],
                doc_id=doc["doc_id"],
                page_count=doc["page_count"],
                page_mapping_mode=doc["page_mapping_mode"],
                degraded="yes" if doc["degraded"] else "no",
                source=(doc["source_pdf_sha256"] or "")[:12],
                adapted=(doc["adapted_tree_sha256"] or "")[:12],
                raw=(doc["raw_mineru_tree_sha256"] or "")[:12],
                warnings=warnings or "",
            )
        )

    lines += ["", "## Validator Summary", ""]
    for domain, report in manifest["validation"].items():
        lines.append(
            "- `{}`: docs={}, gaps={}, image_only={}, degraded={}, docs_with_warnings={}".format(
                domain,
                report["doc_count"],
                report["total_page_gaps"],
                report["total_image_only_pages"],
                report["total_degraded_docs"],
                report["docs_with_warnings"],
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-root",
        default=str(PROJECT_ROOT / "data" / "processed_mineru" / "focused"),
        help="Adapted MinerU corpus root containing <domain>/<doc_id>/ directories.",
    )
    parser.add_argument(
        "--processed-mineru-root",
        default=str(PROJECT_ROOT / "data" / "processed_mineru"),
        help="Root containing focused_raw and raw MinerU outputs.",
    )
    parser.add_argument(
        "--raw-root",
        default=str(PROJECT_ROOT / "data" / "raw_dataset" / "raw"),
        help="Raw PDF root containing <domain>/<doc_id>.pdf files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data" / "processed_mineru" / "_reports"),
        help="Directory where manifest JSON and Markdown summary are written.",
    )
    parser.add_argument("--domain", action="append", choices=tuple(FOCUSED_DOC_IDS.keys()))
    parser.add_argument("--mineru-version", default=os.environ.get("MINERU_VERSION", "3.4.0"))
    parser.add_argument("--backend", default="pipeline")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args(argv)

    manifest = build_manifest(args)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "focused_manifest.json"
    summary_path = output_dir / "focused_manifest.md"
    failures_path = output_dir / "focused_failed_documents.json"

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(render_markdown(manifest), encoding="utf-8")
    failures_path.write_text(
        json.dumps(manifest["failures"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"manifest={manifest_path}")
    print(f"summary={summary_path}")
    print(f"failures={failures_path}")
    print(
        "documents={documents_completed}/{documents_expected} pages={page_count_total} "
        "reproducibility={reproducibility_metadata_recorded}".format(**manifest["summary"])
    )
    return 1 if manifest["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
