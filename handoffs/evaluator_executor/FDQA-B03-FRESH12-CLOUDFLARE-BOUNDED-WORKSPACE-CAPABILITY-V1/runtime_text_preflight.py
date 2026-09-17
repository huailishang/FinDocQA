from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
TASK = Path(__file__).resolve().parent
H58 = TASK.parent / "FDQA-B03-FRESH12-WORKSPACE-GENERALIZATION-READINESS-WAVE-V1"
H59 = TASK.parent / "FDQA-B03-FRESH12-BOUNDED-WORKSPACE-CAPABILITY-V1"
COHORT = H58 / "FRESH12_COHORT_MANIFEST.json"
H59_INPUT = H59 / "H59_PAGE_TEXTS_FOR_TOKEN_PREFLIGHT.jsonl"
FB_REPO = REPO / "evaluation_artifacts/external_benchmarks/financebench/github"
OUTPUT = TASK / "RUNTIME_TEXT_PREFLIGHT.json"
RUNTIME_INPUT = TASK / "H60_RUNTIME_INPUTS.jsonl"

EXPECTED_REVISION = "cc39aeb4afdf33909ee1412188bf89035950c2eb"
EXPECTED_EXTRACTOR = "PyMuPDF"
EXPECTED_EXTRACTOR_VERSION = "1.28.0"
EXPECTED_PAGES = 1019
EXPECTED_QUERIES = 12
EXPECTED_CANONICAL_HASH_CHECKS = 15


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8", newline="\n")


def h59_mismatch_summary(cohort: dict[str, Any]) -> tuple[int, int]:
    if not H59_INPUT.exists():
        return 0, 0
    rows = read_jsonl(H59_INPUT)
    page_map = {
        (str(row.get("document_family")), int(row.get("page"))): str(row.get("text") or "")
        for row in rows
        if row.get("kind") == "page"
    }
    checked = 0
    mismatches = 0
    for case in cohort["cases"]:
        doc = str(case["document_family"])
        for page, expected_hash in zip(
            case["canonical_evidence_pages"], case["canonical_evidence_page_text_sha256"]
        ):
            checked += 1
            text = page_map.get((doc, int(page)))
            if text is None or sha256_text(text) != str(expected_hash):
                mismatches += 1
    return checked, mismatches


def probe_pymupdf() -> tuple[Any | None, str, str]:
    try:
        pymupdf = importlib.import_module("pymupdf")
    except Exception as exc:  # local capability probe only
        return None, "", f"{type(exc).__name__}: {exc}"
    version = str(getattr(pymupdf, "VersionBind", ""))
    return pymupdf, version, ""


def frozen_pdf_bytes(identity: dict[str, Any]) -> bytes:
    revision = str(identity.get("financebench_revision") or EXPECTED_REVISION)
    git_path = str(identity["git_path"])
    blob = str(identity["git_blob"])
    if revision != EXPECTED_REVISION:
        raise RuntimeError(f"unexpected FinanceBench revision: {revision}")
    observed = subprocess.check_output(
        ["git", "-C", str(FB_REPO), "ls-tree", revision, git_path], text=True, encoding="utf-8"
    ).strip().split()
    if len(observed) < 3 or observed[1] != "blob" or observed[2] != blob:
        raise RuntimeError(f"frozen git blob identity changed: {git_path}")
    data = subprocess.check_output(["git", "-C", str(FB_REPO), "cat-file", "blob", blob])
    if sha256_bytes(data) != str(identity["pdf_sha256"]):
        raise RuntimeError(f"frozen PDF sha256 changed: {git_path}")
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"frozen source is not PDF: {git_path}")
    return data


def materialize_inputs(cohort: dict[str, Any], pymupdf: Any) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    canonical_checks = 0
    canonical_mismatches = 0
    extracted: dict[str, list[str]] = {}

    for doc_name in cohort["document_families"]:
        identity = cohort["source_identities"][doc_name]
        if str(identity.get("extractor")) != EXPECTED_EXTRACTOR:
            raise RuntimeError(f"frozen extractor identity changed: {doc_name}")
        if str(identity.get("extractor_version")) != EXPECTED_EXTRACTOR_VERSION:
            raise RuntimeError(f"frozen extractor version changed: {doc_name}")
        data = frozen_pdf_bytes(identity)
        document = pymupdf.open(stream=data, filetype="pdf")
        expected_count = int(identity["pdf_page_count"])
        if int(document.page_count) != expected_count:
            document.close()
            raise RuntimeError(f"page count changed: {doc_name}")
        texts: list[str] = []
        for idx in range(expected_count):
            text = document[idx].get_text("text", sort=True).replace("\r\n", "\n").replace("\r", "\n")
            if text and not text.endswith("\n"):
                text += "\n"
            texts.append(text)
            rows.append({"kind": "page", "document_family": doc_name, "page": idx + 1, "text": text})
        document.close()
        extracted[doc_name] = texts

    for case in cohort["cases"]:
        doc_name = str(case["document_family"])
        for page, expected_hash in zip(
            case["canonical_evidence_pages"], case["canonical_evidence_page_text_sha256"]
        ):
            canonical_checks += 1
            text = extracted[doc_name][int(page) - 1]
            if sha256_text(text) != str(expected_hash):
                canonical_mismatches += 1

    for case in cohort["cases"]:
        rows.append({"kind": "query", "qid": str(case["qid"]), "text": str(case["question"])})

    return rows, canonical_checks, canonical_mismatches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materialize", action="store_true")
    args = parser.parse_args()

    cohort = json.loads(COHORT.read_text(encoding="utf-8"))
    h59_checked, h59_mismatches = h59_mismatch_summary(cohort)
    pymupdf, version, import_error = probe_pymupdf()

    payload: dict[str, Any] = {
        "schema": "fdqa-h60-runtime-text-preflight/v1",
        "status": "BLOCKED",
        "no_dependency_install_policy": "ENFORCED",
        "dependency_install_attempts": 0,
        "dependency_download_attempts": 0,
        "h59_runtime_source_accepted": False,
        "h59_canonical_refs_checked": h59_checked,
        "h59_canonical_hash_mismatches": h59_mismatches,
        "runtime_text_source": None,
        "extractor": EXPECTED_EXTRACTOR,
        "extractor_version": version or None,
        "financebench_revision": str(cohort.get("financebench_revision") or ""),
        "page_count": 0,
        "query_count": 0,
        "canonical_hash_checks": 0,
        "canonical_hash_mismatches": None,
        "runtime_input_sha256": None,
        "current_interpreter_has_exact_extractor": bool(pymupdf is not None and version == EXPECTED_EXTRACTOR_VERSION),
        "extractor_import_error": import_error or None,
        "reason": None,
    }

    if h59_checked != EXPECTED_CANONICAL_HASH_CHECKS or h59_mismatches == 0:
        payload["reason"] = "H59 authority comparison did not reproduce the expected rejected-source condition"
        write_json(OUTPUT, payload)
        return 2

    if not args.materialize:
        payload["reason"] = (
            "Exact preinstalled PyMuPDF 1.28.0 is available; rerun this script with --materialize"
            if payload["current_interpreter_has_exact_extractor"]
            else "No exact preinstalled PyMuPDF 1.28.0 in this interpreter; do not install dependencies"
        )
        write_json(OUTPUT, payload)
        return 0 if payload["current_interpreter_has_exact_extractor"] else 2

    if pymupdf is None or version != EXPECTED_EXTRACTOR_VERSION:
        payload["reason"] = "Materialization forbidden without an already installed exact PyMuPDF 1.28.0"
        write_json(OUTPUT, payload)
        return 2

    rows, canonical_checks, canonical_mismatches = materialize_inputs(cohort, pymupdf)
    page_count = sum(1 for row in rows if row["kind"] == "page")
    query_count = sum(1 for row in rows if row["kind"] == "query")

    if page_count != EXPECTED_PAGES or query_count != EXPECTED_QUERIES:
        payload.update(
            page_count=page_count,
            query_count=query_count,
            canonical_hash_checks=canonical_checks,
            canonical_hash_mismatches=canonical_mismatches,
            reason="Materialized runtime input counts do not match the frozen H-60 contract",
        )
        write_json(OUTPUT, payload)
        return 2

    if canonical_checks != EXPECTED_CANONICAL_HASH_CHECKS or canonical_mismatches != 0:
        payload.update(
            page_count=page_count,
            query_count=query_count,
            canonical_hash_checks=canonical_checks,
            canonical_hash_mismatches=canonical_mismatches,
            reason="Materialized runtime text does not reproduce H-58 canonical page authority",
        )
        write_json(OUTPUT, payload)
        return 2

    write_jsonl(RUNTIME_INPUT, rows)
    payload.update(
        status="PASS",
        runtime_text_source="reextracted_from_frozen_financebench_git_blobs",
        extractor=EXPECTED_EXTRACTOR,
        extractor_version=EXPECTED_EXTRACTOR_VERSION,
        financebench_revision=EXPECTED_REVISION,
        page_count=page_count,
        query_count=query_count,
        canonical_hash_checks=canonical_checks,
        canonical_hash_mismatches=0,
        runtime_input_sha256=sha256_bytes(RUNTIME_INPUT.read_bytes()),
        reason=None,
    )
    write_json(OUTPUT, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
