from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verification.evidence_workspace import EvidenceWorkspaceScope  # noqa: E402

TASK = Path(__file__).resolve().parent
CURRENT = TASK.parent / "state" / "CURRENT.md"
H58 = TASK.parent / "FDQA-B03-FRESH12-WORKSPACE-GENERALIZATION-READINESS-WAVE-V1"
H58_COHORT = H58 / "FRESH12_COHORT_MANIFEST.json"
H58_LEXICAL = H58 / "LEXICAL_BASELINE.json"
INPUTS = TASK / "H60_RUNTIME_INPUTS.jsonl"
RUNTIME_TEXT_PREFLIGHT = TASK / "RUNTIME_TEXT_PREFLIGHT.json"
LEDGER = TASK / "API_ATTEMPT_LEDGER.jsonl"
CACHE = TASK / "embedding_cache_1024d"
PREFLIGHT = TASK / "CLOUDFLARE_PREFLIGHT.json"
SEMANTIC_MANIFEST = TASK / "SEMANTIC_EMBEDDING_MANIFEST.json"
SEMANTIC_TOP5 = TASK / "SEMANTIC_TOP5.jsonl"
LANE_HASHES = TASK / "LANE_INPUT_HASHES.json"
BASELINE_RESULTS = TASK / "BASELINE_RRF60_RESULTS.jsonl"
CANDIDATE_RESULTS = TASK / "CANDIDATE_WORKSPACE_RESULTS.jsonl"
RUNTIME_FREEZE = TASK / "RUNTIME_OUTPUT_FREEZE.json"
BOUNDARY_SUMMARY = TASK / "BOUNDARY_CAPABILITY_SUMMARY.json"
DOWNSTREAM_ROWS = TASK / "DOWNSTREAM_TRUSTED_REACH.jsonl"
DOWNSTREAM_SUMMARY = TASK / "DOWNSTREAM_TRUSTED_SUMMARY.json"
COST_SUMMARY = TASK / "RUN_COST_SUMMARY.json"

TASK_ID = "FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1"
PROFILE_ID = "qwen3-0.6b-cloudflare-v1"
MODEL = "@cf/qwen/qwen3-embedding-0.6b"
PROVIDER = "cloudflare-workers-ai"
PROVIDER_ROUTE = "Cloudflare Workers AI"
BATCH_SIZE = 16
TOP_K = 5
RRF_K = 60
MAX_WORKSPACE = 10
EXPECTED_DIMENSION = 1024
EXPECTED_PAGES = 1019
EXPECTED_QUERIES = 12
EXPECTED_FAMILIES = 9
EXPECTED_PAGE_CALLS = 68
EXPECTED_RUNTIME_CALLS = 80
MAX_SUCCESSFUL_CALLS = 81
MAX_PHYSICAL_ATTEMPTS = 162
MAX_ATTEMPTS_PER_UNIT = 2
MAX_PARALLELISM_USED = 1
RETRY_SLEEP_SECONDS = 2.0
PREFLIGHT_TEXT = "financial document retrieval h60 cloudflare profile preflight"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def stable_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def stable_hash(value: Any) -> str:
    return sha256_bytes(stable_bytes(value))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assert_router_authority() -> None:
    text = CURRENT.read_text(encoding="utf-8")
    required = (
        f"task_id: {TASK_ID}",
        "state: EXECUTING",
        "current_role: Executor",
        "authorization_api_call: true",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(f"H-60 router/API authorization invalid; missing={missing}")


def load_local_env() -> None:
    env_file = REPO / ".env.retrieval.local"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def cloudflare_credentials() -> tuple[str, str]:
    load_local_env()
    account_id = os.getenv("CF_ACCOUNT_ID", "").strip()
    auth_value = os.getenv("CF_AI_AUTH", "").strip()
    if not account_id or not auth_value:
        raise RuntimeError("CF_ACCOUNT_ID / CF_AI_AUTH are not configured")
    return account_id, auth_value


def assert_runtime_text_gate() -> None:
    if not RUNTIME_TEXT_PREFLIGHT.exists():
        raise RuntimeError("H60 runtime-text preflight is missing; Phase 0 must pass before Cloudflare calls")
    gate = json.loads(RUNTIME_TEXT_PREFLIGHT.read_text(encoding="utf-8"))
    required = {
        "status": "PASS",
        "no_dependency_install_policy": "ENFORCED",
        "dependency_install_attempts": 0,
        "dependency_download_attempts": 0,
        "extractor": "PyMuPDF",
        "extractor_version": "1.28.0",
        "financebench_revision": "cc39aeb4afdf33909ee1412188bf89035950c2eb",
        "page_count": EXPECTED_PAGES,
        "query_count": EXPECTED_QUERIES,
        "canonical_hash_checks": 15,
        "canonical_hash_mismatches": 0,
    }
    mismatched = {key: (gate.get(key), expected) for key, expected in required.items() if gate.get(key) != expected}
    if mismatched:
        raise RuntimeError(f"H60 runtime-text gate not PASS: {mismatched}")
    if not INPUTS.exists():
        raise RuntimeError("H60 runtime input file is missing after runtime-text preflight PASS")
    if gate.get("runtime_input_sha256") != sha256_file(INPUTS):
        raise RuntimeError("H60 runtime input hash differs from runtime-text preflight")


def load_runtime_inputs() -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    assert_runtime_text_gate()
    rows = read_jsonl(INPUTS)
    pages_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    queries: list[dict[str, Any]] = []
    for row in rows:
        kind = row.get("kind")
        if kind == "page":
            if set(row) != {"kind", "document_family", "page", "text"}:
                raise RuntimeError(f"unexpected page runtime fields: {sorted(row)}")
            doc = str(row["document_family"])
            page = int(row["page"])
            text = str(row["text"])
            if not doc or page <= 0:
                raise RuntimeError(f"invalid runtime page row: {doc}/{page}")
            pages_by_doc[doc].append({"document_family": doc, "page": page, "text": text})
        elif kind == "query":
            if set(row) != {"kind", "qid", "text"}:
                raise RuntimeError(f"unexpected query runtime fields: {sorted(row)}")
            qid = str(row["qid"])
            text = str(row["text"])
            if not qid or not text.strip():
                raise RuntimeError(f"invalid runtime query row: {qid}")
            queries.append({"qid": qid, "text": text})
        else:
            raise RuntimeError(f"unexpected runtime input kind: {kind}")

    cohort = json.loads(H58_COHORT.read_text(encoding="utf-8"))
    if int(cohort["case_count"]) != EXPECTED_QUERIES or len(cohort["document_families"]) != EXPECTED_FAMILIES:
        raise RuntimeError("H58 cohort count/family identity changed")
    expected_queries = {str(row["qid"]): str(row["question"]) for row in cohort["cases"]}
    observed_queries = {str(row["qid"]): str(row["text"]) for row in queries}
    if expected_queries != observed_queries:
        raise RuntimeError("H60 runtime query texts do not match H58 frozen questions")
    if set(pages_by_doc) != set(map(str, cohort["document_families"])):
        raise RuntimeError("H60 runtime document families do not match H58 cohort")

    source_identities = cohort["source_identities"]
    for doc, pages in pages_by_doc.items():
        observed = [int(row["page"]) for row in pages]
        expected_count = int(source_identities[doc]["pdf_page_count"])
        if observed != list(range(1, expected_count + 1)):
            raise RuntimeError(f"page order/supply changed for {doc}")
    for case in cohort["cases"]:
        doc = str(case["document_family"])
        page_map = {int(row["page"]): str(row["text"]) for row in pages_by_doc[doc]}
        for page, expected_hash in zip(case["canonical_evidence_pages"], case["canonical_evidence_page_text_sha256"]):
            if sha256_text(page_map[int(page)]) != str(expected_hash):
                raise RuntimeError(f"H58 canonical page text hash mismatch: {case['qid']}/{page}")

    if sum(len(doc_rows) for doc_rows in pages_by_doc.values()) != EXPECTED_PAGES:
        raise RuntimeError("H60 page input count changed")
    if len(queries) != EXPECTED_QUERIES or len(observed_queries) != EXPECTED_QUERIES:
        raise RuntimeError("H60 query input count/identity changed")
    return dict(pages_by_doc), queries


def page_units(pages_by_doc: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for doc, pages in pages_by_doc.items():
        for batch_index, start in enumerate(range(0, len(pages), BATCH_SIZE), start=1):
            batch = list(pages[start : start + BATCH_SIZE])
            text_hashes = [sha256_text(str(row["text"])) for row in batch]
            units.append(
                {
                    "logical_unit_id": f"H60:PAGE_BATCH:{doc}:{batch_index:03d}",
                    "unit_type": "PAGE_BATCH",
                    "document_family": doc,
                    "batch_index": batch_index,
                    "texts": [str(row["text"]) for row in batch],
                    "pages": [int(row["page"]) for row in batch],
                    "input_count": len(batch),
                    "input_sha256": stable_hash(text_hashes),
                    "cache_path": CACHE / "pages" / doc / f"batch_{batch_index:03d}.json.gz",
                    "metadata_path": CACHE / "pages" / doc / f"batch_{batch_index:03d}.meta.json",
                }
            )
    if len(units) != EXPECTED_PAGE_CALLS:
        raise RuntimeError(f"H60 page batch count changed: {len(units)}")
    return units


def query_units(queries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "logical_unit_id": f"H60:QUERY:{row['qid']}",
            "unit_type": "QUERY",
            "qid": str(row["qid"]),
            "texts": [str(row["text"])],
            "input_count": 1,
            "input_sha256": sha256_text(str(row["text"])),
            "cache_path": CACHE / "queries" / f"{row['qid']}.json.gz",
            "metadata_path": CACHE / "queries" / f"{row['qid']}.meta.json",
        }
        for row in queries
    ]


def ledger_rows() -> list[dict[str, Any]]:
    if not LEDGER.exists():
        return []
    return read_jsonl(LEDGER)


def append_ledger(row: Mapping[str, Any]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def completed_runtime_units() -> set[str]:
    return {
        str(row["logical_unit_id"])
        for row in ledger_rows()
        if row.get("operation") == "embedding" and row.get("terminal_status") == "COMPLETED"
    }


def vector_payload_hash(vectors: Sequence[Sequence[float]]) -> str:
    return sha256_bytes(stable_bytes([[float(v) for v in vector] for vector in vectors]))


def save_vectors(unit: Mapping[str, Any], vectors: Sequence[Sequence[float]]) -> dict[str, Any]:
    cache_path = Path(unit["cache_path"])
    meta_path = Path(unit["metadata_path"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [[float(v) for v in vector] for vector in vectors]
    with gzip.open(cache_path, "wt", encoding="utf-8", newline="\n") as handle:
        json.dump(normalized, handle, ensure_ascii=False, separators=(",", ":"))
    vector_hash = vector_payload_hash(normalized)
    meta: dict[str, Any] = {
        "logical_unit_id": str(unit["logical_unit_id"]),
        "unit_type": str(unit["unit_type"]),
        "provider": PROVIDER,
        "provider_route": PROVIDER_ROUTE,
        "profile_id": PROFILE_ID,
        "model": MODEL,
        "input_count": int(unit["input_count"]),
        "input_sha256": str(unit["input_sha256"]),
        "embedding_dimension": EXPECTED_DIMENSION,
        "vector_sha256": vector_hash,
        "cache_sha256": sha256_file(cache_path),
    }
    if unit["unit_type"] == "PAGE_BATCH":
        meta.update(
            {
                "document_family": str(unit["document_family"]),
                "batch_index": int(unit["batch_index"]),
                "pages": list(unit["pages"]),
            }
        )
    else:
        meta["qid"] = str(unit["qid"])
    write_json(meta_path, meta)
    return meta


def load_vectors(unit: Mapping[str, Any]) -> list[list[float]]:
    meta = cache_metadata(unit)
    if meta is None:
        raise RuntimeError(f"missing cache: {unit['logical_unit_id']}")
    with gzip.open(Path(unit["cache_path"]), "rt", encoding="utf-8") as handle:
        vectors = json.load(handle)
    return [[float(v) for v in vector] for vector in vectors]


def cache_metadata(unit: Mapping[str, Any]) -> dict[str, Any] | None:
    cache_path = Path(unit["cache_path"])
    meta_path = Path(unit["metadata_path"])
    if not cache_path.exists() and not meta_path.exists():
        return None
    if not cache_path.exists() or not meta_path.exists():
        raise RuntimeError(f"partial H60 cache for {unit['logical_unit_id']}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("logical_unit_id") != unit["logical_unit_id"]:
        raise RuntimeError(f"cache unit mismatch: {unit['logical_unit_id']}")
    if meta.get("model") != MODEL or meta.get("provider") != PROVIDER or meta.get("input_sha256") != unit["input_sha256"]:
        raise RuntimeError(f"cache provider/model/input mismatch: {unit['logical_unit_id']}")
    if int(meta.get("embedding_dimension", 0)) != EXPECTED_DIMENSION:
        raise RuntimeError(f"cache dimension mismatch: {unit['logical_unit_id']}")
    if sha256_file(cache_path) != meta.get("cache_sha256"):
        raise RuntimeError(f"cache file hash mismatch: {unit['logical_unit_id']}")
    with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
        vectors = json.load(handle)
    normalize_vectors(vectors, int(unit["input_count"]))
    if vector_payload_hash(vectors) != meta.get("vector_sha256"):
        raise RuntimeError(f"cache vector hash mismatch: {unit['logical_unit_id']}")
    return meta


def verify_checkpoint_consistency(units: Sequence[Mapping[str, Any]]) -> None:
    completed = completed_runtime_units()
    for unit in units:
        cached = cache_metadata(unit) is not None
        in_ledger = str(unit["logical_unit_id"]) in completed
        if cached != in_ledger:
            raise RuntimeError(f"cache/ledger mismatch: {unit['logical_unit_id']} cache={cached} completed={in_ledger}")


def normalize_vectors(value: Any, input_count: int) -> list[list[float]]:
    if input_count == 1 and isinstance(value, list) and value and isinstance(value[0], (int, float)):
        value = [value]
    if not isinstance(value, list) or len(value) != input_count:
        raise RuntimeError(f"unexpected embedding response count: {type(value).__name__}/{len(value) if isinstance(value,list) else 'na'}")
    normalized: list[list[float]] = []
    for vector in value:
        if not isinstance(vector, list) or len(vector) != EXPECTED_DIMENSION:
            raise RuntimeError(f"unexpected embedding dimension: {len(vector) if isinstance(vector,list) else 'na'}")
        converted = [float(v) for v in vector]
        if not all(math.isfinite(v) for v in converted):
            raise RuntimeError("embedding response contains non-finite values")
        normalized.append(converted)
    return normalized


def cf_embed(texts: Sequence[str]) -> list[list[float]]:
    account_id, auth_value = cloudflare_credentials()
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{MODEL}"
    body = json.dumps({"text": list(texts)}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {auth_value}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
        status = int(response.status)
    if status != 200 or not payload.get("success"):
        raise RuntimeError(f"Cloudflare response status={status} success={payload.get('success')}")
    result = payload.get("result") or {}
    data = result.get("data")
    return normalize_vectors(data, len(texts))


def classify_error(exc: BaseException) -> tuple[str, int | None, bool]:
    status = getattr(exc, "code", None)
    if not isinstance(status, int):
        match = re.search(r"(?:HTTP|status)[^0-9]{0,8}(\d{3})", str(exc), flags=re.I)
        status = int(match.group(1)) if match else None
    if status == 429 or (status is not None and 500 <= status <= 599):
        return "HTTP_RETRYABLE", status, True
    if status is not None:
        return "HTTP_TERMINAL", status, False
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if any(token in name for token in ("timeout", "connection", "urlerror")) or any(
        token in text for token in ("timed out", "connection reset", "connection aborted", "temporarily unavailable")
    ):
        return "TRANSPORT_RETRYABLE", None, True
    return "TERMINAL_OTHER", None, False


def ledger_attempt_count(logical: str) -> int:
    return sum(str(row.get("logical_unit_id")) == logical for row in ledger_rows())


def assert_external_budget() -> None:
    rows = ledger_rows()
    successes = sum(row.get("terminal_status") == "COMPLETED" for row in rows)
    if successes >= MAX_SUCCESSFUL_CALLS:
        raise RuntimeError("H60 successful-call ceiling exhausted")
    if len(rows) >= MAX_PHYSICAL_ATTEMPTS:
        raise RuntimeError("H60 physical-attempt ceiling exhausted")


def preflight() -> None:
    assert_router_authority()
    assert_runtime_text_gate()
    logical = "H60:PREFLIGHT:001"
    prior = [row for row in ledger_rows() if str(row.get("logical_unit_id")) == logical]
    if any(row.get("terminal_status") == "COMPLETED" for row in prior):
        if not PREFLIGHT.exists():
            raise RuntimeError("preflight completed in ledger but artifact missing")
        print(PREFLIGHT.read_text(encoding="utf-8").strip())
        return
    if any(row.get("terminal_status") == "TERMINAL_ERROR" for row in prior):
        raise RuntimeError("terminal Cloudflare preflight failure already recorded")
    while len(prior) < MAX_ATTEMPTS_PER_UNIT:
        assert_external_budget()
        all_rows = ledger_rows()
        attempt_number = len(prior) + 1
        attempt_id = f"A{len(all_rows)+1:03d}"
        started_at = now()
        try:
            vectors = cf_embed([PREFLIGHT_TEXT])
            append_ledger(
                {
                    "attempt_id": attempt_id,
                    "operation": "embedding_preflight",
                    "logical_unit_id": logical,
                    "unit_type": "PREFLIGHT",
                    "provider": PROVIDER,
                    "provider_route": PROVIDER_ROUTE,
                    "profile_id": PROFILE_ID,
                    "model": MODEL,
                    "attempt_number": attempt_number,
                    "input_count": 1,
                    "input_sha256": sha256_text(PREFLIGHT_TEXT),
                    "started_at": started_at,
                    "finished_at": now(),
                    "terminal_status": "COMPLETED",
                    "http_status": 200,
                    "response_item_count": 1,
                    "embedding_dimension": len(vectors[0]),
                    "vector_sha256": vector_payload_hash(vectors),
                    "error_family": None,
                    "retryable": False,
                    "paid_fallback_used": False,
                    "raw_provider_response_persisted": False,
                    "credential_persisted": False,
                }
            )
            output = {
                "schema": "fdqa-h60-cloudflare-preflight/v1",
                "task_id": TASK_ID,
                "provider": PROVIDER,
                "provider_route": PROVIDER_ROUTE,
                "profile_id": PROFILE_ID,
                "model": MODEL,
                "embedding_dimension": len(vectors[0]),
                "probe_text_sha256": sha256_text(PREFLIGHT_TEXT),
                "credential_values_persisted_in_output": False,
                "raw_provider_response_persisted": False,
                "paid_fallback_used": False,
                "status": "PASS",
            }
            write_json(PREFLIGHT, output)
            print(json.dumps(output, ensure_ascii=False, sort_keys=True))
            return
        except BaseException as exc:
            family, status, retryable = classify_error(exc)
            append_ledger(
                {
                    "attempt_id": attempt_id,
                    "operation": "embedding_preflight",
                    "logical_unit_id": logical,
                    "unit_type": "PREFLIGHT",
                    "provider": PROVIDER,
                    "provider_route": PROVIDER_ROUTE,
                    "profile_id": PROFILE_ID,
                    "model": MODEL,
                    "attempt_number": attempt_number,
                    "input_count": 1,
                    "input_sha256": sha256_text(PREFLIGHT_TEXT),
                    "started_at": started_at,
                    "finished_at": now(),
                    "terminal_status": "RETRYABLE_ERROR" if retryable else "TERMINAL_ERROR",
                    "http_status": status,
                    "response_item_count": None,
                    "embedding_dimension": None,
                    "vector_sha256": None,
                    "error_family": family,
                    "error_class": type(exc).__name__,
                    "retryable": retryable,
                    "paid_fallback_used": False,
                    "raw_provider_response_persisted": False,
                    "credential_persisted": False,
                }
            )
            prior = [row for row in ledger_rows() if str(row.get("logical_unit_id")) == logical]
            if not retryable or len(prior) >= MAX_ATTEMPTS_PER_UNIT:
                raise
            time.sleep(RETRY_SLEEP_SECONDS)


def call_unit(unit: Mapping[str, Any]) -> None:
    logical = str(unit["logical_unit_id"])
    prior = [row for row in ledger_rows() if str(row.get("logical_unit_id")) == logical]
    if any(row.get("terminal_status") == "COMPLETED" for row in prior):
        if cache_metadata(unit) is None:
            raise RuntimeError(f"completed unit lacks cache: {logical}")
        return
    if any(row.get("terminal_status") == "TERMINAL_ERROR" for row in prior):
        raise RuntimeError(f"terminal provider failure already recorded for {logical}; no retry authorized")
    while len(prior) < MAX_ATTEMPTS_PER_UNIT:
        assert_external_budget()
        all_rows = ledger_rows()
        attempt_number = len(prior) + 1
        attempt_id = f"A{len(all_rows)+1:03d}"
        started_at = now()
        try:
            vectors = cf_embed(list(unit["texts"]))
            meta = save_vectors(unit, vectors)
            append_ledger(
                {
                    "attempt_id": attempt_id,
                    "operation": "embedding",
                    "logical_unit_id": logical,
                    "unit_type": unit["unit_type"],
                    "provider": PROVIDER,
                    "provider_route": PROVIDER_ROUTE,
                    "profile_id": PROFILE_ID,
                    "model": MODEL,
                    "attempt_number": attempt_number,
                    "input_count": int(unit["input_count"]),
                    "input_sha256": unit["input_sha256"],
                    "started_at": started_at,
                    "finished_at": now(),
                    "terminal_status": "COMPLETED",
                    "http_status": 200,
                    "response_item_count": int(unit["input_count"]),
                    "embedding_dimension": EXPECTED_DIMENSION,
                    "vector_sha256": meta["vector_sha256"],
                    "error_family": None,
                    "retryable": False,
                    "paid_fallback_used": False,
                    "raw_provider_response_persisted": False,
                    "credential_persisted": False,
                }
            )
            return
        except BaseException as exc:
            family, status, retryable = classify_error(exc)
            append_ledger(
                {
                    "attempt_id": attempt_id,
                    "operation": "embedding",
                    "logical_unit_id": logical,
                    "unit_type": unit["unit_type"],
                    "provider": PROVIDER,
                    "provider_route": PROVIDER_ROUTE,
                    "profile_id": PROFILE_ID,
                    "model": MODEL,
                    "attempt_number": attempt_number,
                    "input_count": int(unit["input_count"]),
                    "input_sha256": unit["input_sha256"],
                    "started_at": started_at,
                    "finished_at": now(),
                    "terminal_status": "RETRYABLE_ERROR" if retryable else "TERMINAL_ERROR",
                    "http_status": status,
                    "response_item_count": None,
                    "embedding_dimension": None,
                    "vector_sha256": None,
                    "error_family": family,
                    "error_class": type(exc).__name__,
                    "retryable": retryable,
                    "paid_fallback_used": False,
                    "raw_provider_response_persisted": False,
                    "credential_persisted": False,
                }
            )
            prior = [row for row in ledger_rows() if str(row.get("logical_unit_id")) == logical]
            if not retryable or len(prior) >= MAX_ATTEMPTS_PER_UNIT:
                raise
            time.sleep(RETRY_SLEEP_SECONDS)


def runtime_qid_to_doc() -> dict[str, str]:
    payload = json.loads(H58_LEXICAL.read_text(encoding="utf-8"))
    rows = payload.get("cases") or []
    projection = {str(row["qid"]): str(row["document_family"]) for row in rows}
    if len(projection) != EXPECTED_QUERIES:
        raise RuntimeError("H58 lexical qid/document projection changed")
    return projection


def lexical_runtime_projection() -> list[dict[str, Any]]:
    payload = json.loads(H58_LEXICAL.read_text(encoding="utf-8"))
    rows = payload.get("cases") or []
    result = []
    for row in rows:
        top5 = [int(page) for page in row["lexical_top5_physical_pages"]]
        if len(top5) != TOP_K or len(set(top5)) != TOP_K:
            raise RuntimeError(f"H58 lexical Top5 invalid: {row.get('qid')}")
        result.append(
            {
                "qid": str(row["qid"]),
                "document_family": str(row["document_family"]),
                "lexical_top5": top5,
                "retriever": str(row["retriever"]),
                "retriever_settings": dict(row["retriever_settings"]),
            }
        )
    if len(result) != EXPECTED_QUERIES:
        raise RuntimeError("H58 lexical projection count changed")
    return result


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    an = math.sqrt(sum(x * x for x in a))
    bn = math.sqrt(sum(y * y for y in b))
    if an <= 0 or bn <= 0:
        raise RuntimeError("zero vector norm")
    return dot / (an * bn)


def semantic_rankings(
    pages_by_doc: Mapping[str, Sequence[Mapping[str, Any]]],
    page_units_: Sequence[Mapping[str, Any]],
    query_units_: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    doc_vectors: dict[str, list[tuple[int, list[float]]]] = defaultdict(list)
    for unit in page_units_:
        vectors = load_vectors(unit)
        for page, vector in zip(unit["pages"], vectors):
            doc_vectors[str(unit["document_family"])].append((int(page), vector))
    for doc, expected_pages in pages_by_doc.items():
        rows = sorted(doc_vectors[doc], key=lambda pair: pair[0])
        if [page for page, _ in rows] != [int(row["page"]) for row in expected_pages]:
            raise RuntimeError(f"semantic page-vector identity mismatch: {doc}")
        doc_vectors[doc] = rows

    qid_to_doc = runtime_qid_to_doc()
    results: list[dict[str, Any]] = []
    for unit in query_units_:
        qid = str(unit["qid"])
        doc = qid_to_doc[qid]
        qvec = load_vectors(unit)[0]
        scored = [(page, cosine(qvec, vector)) for page, vector in doc_vectors[doc]]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        top = scored[:TOP_K]
        results.append(
            {
                "qid": qid,
                "document_family": doc,
                "query_text_sha256": unit["input_sha256"],
                "provider": PROVIDER,
                "profile_id": PROFILE_ID,
                "model": MODEL,
                "retrieval_unit": "one_based_canonical_physical_page",
                "similarity": "cosine",
                "top_k": TOP_K,
                "semantic_top5": [page for page, _ in top],
                "semantic_scores": [round(score, 12) for _, score in top],
                "gold_reference_joined": False,
            }
        )
    return results


def rrf60_top5(lexical: Sequence[int], semantic: Sequence[int]) -> tuple[list[int], list[dict[str, Any]]]:
    scores: dict[int, float] = {}
    lane_hits: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for lane, pages in (("lexical", lexical), ("semantic", semantic)):
        for rank, page in enumerate(pages, start=1):
            page = int(page)
            scores[page] = scores.get(page, 0.0) + 1.0 / (RRF_K + rank)
            lane_hits[page].append({"lane": lane, "rank": rank, "weight": 1.0})
    ordered = sorted(scores, key=lambda page: (-scores[page], page))[:TOP_K]
    return ordered, [{"page": page, "rrf60_score": scores[page], "lane_hits": lane_hits[page]} for page in ordered]


def candidate_union(lexical: Sequence[int], semantic: Sequence[int]) -> tuple[list[int], dict[int, list[str]]]:
    ordered: list[int] = []
    provenance: dict[int, list[str]] = {}
    for lane, pages in (("lexical", lexical), ("semantic", semantic)):
        for raw_page in pages:
            page = int(raw_page)
            if page not in provenance:
                ordered.append(page)
                provenance[page] = []
            if lane not in provenance[page]:
                provenance[page].append(lane)
    if len(ordered) > MAX_WORKSPACE:
        raise RuntimeError(f"candidate union exceeds max workspace: {len(ordered)}")
    return ordered, provenance


def materialize_runtime_outputs(
    pages_by_doc: Mapping[str, Sequence[Mapping[str, Any]]],
    page_units_: Sequence[Mapping[str, Any]],
    query_units_: Sequence[Mapping[str, Any]],
) -> None:
    semantic = semantic_rankings(pages_by_doc, page_units_, query_units_)
    if len(semantic) != EXPECTED_QUERIES:
        raise RuntimeError("semantic ranking output count changed")
    write_jsonl(SEMANTIC_TOP5, semantic)
    semantic_by_qid = {str(row["qid"]): row for row in semantic}
    lexical = lexical_runtime_projection()
    baseline_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for lex in lexical:
        qid = str(lex["qid"])
        sem = semantic_by_qid[qid]
        if str(lex["document_family"]) != str(sem["document_family"]):
            raise RuntimeError(f"lane document mismatch: {qid}")
        lexical_top5 = [int(page) for page in lex["lexical_top5"]]
        semantic_top5 = [int(page) for page in sem["semantic_top5"]]
        lex_hash = stable_hash(lexical_top5)
        sem_hash = stable_hash(semantic_top5)
        baseline_pages, details = rrf60_top5(lexical_top5, semantic_top5)
        union_pages, provenance = candidate_union(lexical_top5, semantic_top5)
        doc = str(lex["document_family"])
        scope_provenance = {
            (doc, page): ["both"] if set(provenance[page]) == {"lexical", "semantic"} else provenance[page]
            for page in union_pages
        }
        scope = EvidenceWorkspaceScope.from_pages(
            [(doc, page) for page in union_pages],
            provenance_by_page=scope_provenance,
            max_unique_pages=MAX_WORKSPACE,
        )
        if len(scope.allowed_pages) != len(union_pages):
            raise RuntimeError(f"workspace scope dedupe mismatch: {qid}")
        baseline_rows.append(
            {
                "qid": qid,
                "document_family": doc,
                "lexical_top5": lexical_top5,
                "semantic_top5": semantic_top5,
                "lexical_top5_hash": lex_hash,
                "semantic_top5_hash": sem_hash,
                "fusion": "rrf60_equal_weight",
                "rrf_k": RRF_K,
                "weights": {"lexical": 1.0, "semantic": 1.0},
                "verification_pages": baseline_pages,
                "fused_details": details,
                "gold_reference_joined": False,
            }
        )
        candidate_rows.append(
            {
                "qid": qid,
                "document_family": doc,
                "lexical_top5": lexical_top5,
                "semantic_top5": semantic_top5,
                "lexical_top5_hash": lex_hash,
                "semantic_top5_hash": sem_hash,
                "workspace_kind": "deduplicated_union_no_score_reweighting_before_verification",
                "verification_workspace_pages": union_pages,
                "provenance_by_page": {str(page): provenance[page] for page in union_pages},
                "evidence_workspace_scope": {
                    "max_unique_pages": scope.max_unique_pages,
                    "page_index_semantics": scope.page_index_semantics,
                    "fail_closed_on_unknown_page": scope.fail_closed_on_unknown_page,
                    "fail_closed_on_outside_page": scope.fail_closed_on_outside_page,
                    "full_document_fallback_allowed": scope.full_document_fallback_allowed,
                },
                "gold_reference_joined": False,
            }
        )
    write_jsonl(BASELINE_RESULTS, baseline_rows)
    write_jsonl(CANDIDATE_RESULTS, candidate_rows)

    cache_metas = [cache_metadata(unit) for unit in [*page_units_, *query_units_]]
    cache_metas = [meta for meta in cache_metas if meta is not None]
    embedding_hash = stable_hash(
        [
            {
                "logical_unit_id": meta["logical_unit_id"],
                "input_sha256": meta["input_sha256"],
                "vector_sha256": meta["vector_sha256"],
            }
            for meta in cache_metas
        ]
    )
    write_json(
        SEMANTIC_MANIFEST,
        {
            "task_id": TASK_ID,
            "provider": PROVIDER,
            "provider_route": PROVIDER_ROUTE,
            "profile_id": PROFILE_ID,
            "model": MODEL,
            "embedding_dimension": EXPECTED_DIMENSION,
            "document_embedding_batch_size": BATCH_SIZE,
            "query_embedding_unit": "one query vector per case",
            "top_k_per_doc": TOP_K,
            "case_count": EXPECTED_QUERIES,
            "document_family_count": EXPECTED_FAMILIES,
            "indexed_page_count": EXPECTED_PAGES,
            "page_batch_call_count": EXPECTED_PAGE_CALLS,
            "query_call_count": EXPECTED_QUERIES,
            "runtime_successful_call_target": EXPECTED_RUNTIME_CALLS,
            "persisted_embedding_unit_count": len(cache_metas),
            "persisted_embedding_manifest_sha256": embedding_hash,
            "runtime_input_file_sha256": sha256_file(INPUTS),
            "h59_8b_cache_reused": False,
            "cache_profile": "task_local_1024d_cloudflare_only",
            "raw_provider_responses_persisted": False,
            "credential_persisted": False,
        },
    )

    lane_hashes = {
        "task_id": TASK_ID,
        "h58_cohort_sha256": sha256_file(H58_COHORT),
        "h58_lexical_baseline_sha256": sha256_file(H58_LEXICAL),
        "runtime_input_file_sha256": sha256_file(INPUTS),
        "lexical_runtime_projection_sha256": stable_hash(lexical),
        "semantic_top5_sha256": sha256_file(SEMANTIC_TOP5),
        "semantic_embedding_manifest_sha256": sha256_file(SEMANTIC_MANIFEST),
        "baseline_lane_pair_sha256": stable_hash([(r["qid"], r["lexical_top5_hash"], r["semantic_top5_hash"]) for r in baseline_rows]),
        "candidate_lane_pair_sha256": stable_hash([(r["qid"], r["lexical_top5_hash"], r["semantic_top5_hash"]) for r in candidate_rows]),
        "same_runtime_lane_inputs": all(
            b["qid"] == c["qid"]
            and b["lexical_top5_hash"] == c["lexical_top5_hash"]
            and b["semantic_top5_hash"] == c["semantic_top5_hash"]
            for b, c in zip(baseline_rows, candidate_rows)
        ),
        "gold_reference_in_runtime_inputs": False,
        "runtime_input_allowed_fields": {
            "page": ["kind", "document_family", "page", "text"],
            "query": ["kind", "qid", "text"],
        },
        "h59_8b_vectors_used": False,
    }
    write_json(LANE_HASHES, lane_hashes)
    runtime_files = [SEMANTIC_MANIFEST, SEMANTIC_TOP5, LANE_HASHES, BASELINE_RESULTS, CANDIDATE_RESULTS]
    write_json(
        RUNTIME_FREEZE,
        {
            "task_id": TASK_ID,
            "frozen_at": now(),
            "runtime_outputs_frozen_before_gold_scoring": True,
            "gold_reference_joined_in_frozen_runtime_outputs": False,
            "files": {path.name: sha256_file(path) for path in runtime_files},
        },
    )


def boundary_scoring() -> dict[str, Any]:
    freeze = json.loads(RUNTIME_FREEZE.read_text(encoding="utf-8"))
    for name, expected in freeze["files"].items():
        if sha256_file(TASK / name) != expected:
            raise RuntimeError(f"runtime output changed before Gold scoring: {name}")
    cohort = json.loads(H58_COHORT.read_text(encoding="utf-8"))
    gold = {
        str(row["qid"]): {
            "document_family": str(row["document_family"]),
            "canonical_gold_pages": [int(page) for page in row["canonical_evidence_pages"]],
        }
        for row in cohort["cases"]
    }
    semantic = {str(row["qid"]): row for row in read_jsonl(SEMANTIC_TOP5)}
    baseline = {str(row["qid"]): row for row in read_jsonl(BASELINE_RESULTS)}
    candidate = {str(row["qid"]): row for row in read_jsonl(CANDIDATE_RESULTS)}
    if set(gold) != set(semantic) or set(gold) != set(baseline) or set(gold) != set(candidate):
        raise RuntimeError("runtime/scoring qid identity mismatch")
    cases: list[dict[str, Any]] = []
    recovered_families: set[str] = set()
    for cohort_row in cohort["cases"]:
        qid = str(cohort_row["qid"])
        canonical_gold_pages = gold[qid]["canonical_gold_pages"]
        base_pages = [int(page) for page in baseline[qid]["verification_pages"]]
        workspace = [int(page) for page in candidate[qid]["verification_workspace_pages"]]
        base_reach = bool(set(canonical_gold_pages).intersection(base_pages))
        candidate_reach = bool(set(canonical_gold_pages).intersection(workspace))
        recovered = candidate_reach and not base_reach
        protected_loss = base_reach and not candidate_reach
        if recovered:
            recovered_families.add(str(cohort_row["document_family"]))
        cases.append(
            {
                "qid": qid,
                "document_family": str(cohort_row["document_family"]),
                "lexical_top5": baseline[qid]["lexical_top5"],
                "semantic_top5": semantic[qid]["semantic_top5"],
                "baseline_rrf60_top5": base_pages,
                "candidate_union_workspace": workspace,
                "canonical_gold_pages": canonical_gold_pages,
                "baseline_gold_reach": base_reach,
                "candidate_gold_reach": candidate_reach,
                "recovered_case": recovered,
                "protected_loss": protected_loss,
            }
        )
    recovered_cases = sum(bool(row["recovered_case"]) for row in cases)
    protected_loss_count = sum(bool(row["protected_loss"]) for row in cases)
    max_workspace = max(len(row["candidate_union_workspace"]) for row in cases)
    rule = {
        "min_recovered_cases": 3,
        "min_recovered_document_families": 2,
        "protected_loss_max": 0,
        "workspace_max_unique_pages": 10,
    }
    qualified = (
        recovered_cases >= rule["min_recovered_cases"]
        and len(recovered_families) >= rule["min_recovered_document_families"]
        and protected_loss_count <= rule["protected_loss_max"]
        and max_workspace <= rule["workspace_max_unique_pages"]
    )
    summary = {
        "task_id": TASK_ID,
        "case_count": EXPECTED_QUERIES,
        "runtime_outputs_frozen_before_gold_scoring": True,
        "runtime_output_freeze_sha256": sha256_file(RUNTIME_FREEZE),
        "qualification_rule": rule,
        "baseline_gold_reach_cases": sum(bool(row["baseline_gold_reach"]) for row in cases),
        "candidate_gold_reach_cases": sum(bool(row["candidate_gold_reach"]) for row in cases),
        "recovered_cases": recovered_cases,
        "recovered_document_families": len(recovered_families),
        "recovered_document_family_ids": sorted(recovered_families),
        "protected_loss": protected_loss_count,
        "workspace_max_unique_pages": max_workspace,
        "qualified": qualified,
        "cases": cases,
    }
    write_json(BOUNDARY_SUMMARY, summary)
    return summary


def downstream_support_measurement() -> dict[str, Any]:
    baseline = {str(row["qid"]): row for row in read_jsonl(BASELINE_RESULTS)}
    candidate = {str(row["qid"]): row for row in read_jsonl(CANDIDATE_RESULTS)}
    rows: list[dict[str, Any]] = []
    for qid in baseline:
        rows.append(
            {
                "qid": qid,
                "document_family": baseline[qid]["document_family"],
                "baseline_status": "VERIFIER_UNSUPPORTED",
                "candidate_status": "VERIFIER_UNSUPPORTED",
                "support_probe": {
                    "existing_path": "FinancialClaimSpec -> ClaimFactBinding -> FinancialEvidenceSufficiency",
                    "required_runtime_input": "candidate_answer_claim",
                    "available_runtime_input": "freeform_question_only",
                    "gold_reference_answer_allowed": False,
                    "generative_solver_allowed": False,
                    "reason": "no_non_gold_candidate_answer_claim_for_existing_option_claim_verifier",
                },
                "baseline_verification_pages": baseline[qid]["verification_pages"],
                "candidate_workspace_pages": candidate[qid]["verification_workspace_pages"],
                "candidate_late_contraction_pages": None,
            }
        )
    write_jsonl(DOWNSTREAM_ROWS, rows)
    summary = {
        "task_id": TASK_ID,
        "case_count": len(rows),
        "verifier_supported_case_count": 0,
        "verifier_unsupported_case_count": len(rows),
        "unsupported_counted_as_workspace_failure": False,
        "baseline_supported_trusted_cases": 0,
        "candidate_trusted_losses_from_baseline_supported_trusted": 0,
        "candidate_trusted_recoveries": 0,
        "secondary_improvement_claim_supported": False,
        "reason": "current deterministic claim verifier requires a non-Gold candidate answer claim; H-60 has freeform questions only",
    }
    write_json(DOWNSTREAM_SUMMARY, summary)
    return summary


def write_cost_summary() -> dict[str, Any]:
    rows = ledger_rows()
    completed = [row for row in rows if row.get("terminal_status") == "COMPLETED"]
    attempts_by_unit = Counter(str(row["logical_unit_id"]) for row in rows)
    successful_ids = {str(row["logical_unit_id"]) for row in completed}
    max_attempts_for_success = max((attempts_by_unit[unit] for unit in successful_ids), default=0)
    summary = {
        "task_id": TASK_ID,
        "provider": PROVIDER,
        "provider_route": PROVIDER_ROUTE,
        "profile_id": PROFILE_ID,
        "model": MODEL,
        "successful_calls": len(completed),
        "physical_attempts": len(rows),
        "preflight_calls": sum(row.get("operation") == "embedding_preflight" for row in rows),
        "runtime_embedding_calls": sum(row.get("operation") == "embedding" and row.get("terminal_status") == "COMPLETED" for row in rows),
        "successful_page_batch_calls": sum(row.get("operation") == "embedding" and row.get("unit_type") == "PAGE_BATCH" and row.get("terminal_status") == "COMPLETED" for row in rows),
        "successful_query_calls": sum(row.get("operation") == "embedding" and row.get("unit_type") == "QUERY" and row.get("terminal_status") == "COMPLETED" for row in rows),
        "retryable_failures": sum(row.get("terminal_status") == "RETRYABLE_ERROR" for row in rows),
        "terminal_failures": sum(row.get("terminal_status") == "TERMINAL_ERROR" for row in rows),
        "max_attempts_per_successful_call": max_attempts_for_success,
        "max_parallelism": MAX_PARALLELISM_USED,
        "successful_call_ceiling": MAX_SUCCESSFUL_CALLS,
        "physical_attempt_ceiling": MAX_PHYSICAL_ATTEMPTS,
        "configured_max_attempts_per_successful_call": MAX_ATTEMPTS_PER_UNIT,
        "paid_fallback_used": False,
        "unauthorized_generative_calls": 0,
        "judge_calls": 0,
        "solver_calls": 0,
        "reranker_calls": 0,
        "raw_provider_responses_persisted": False,
        "credentials_persisted": False,
    }
    write_json(COST_SUMMARY, summary)
    return summary


def finalize(
    units: Sequence[Mapping[str, Any]],
    pages_by_doc: Mapping[str, Sequence[Mapping[str, Any]]],
    page_units_: Sequence[Mapping[str, Any]],
    query_units_: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    verify_checkpoint_consistency(units)
    if not all(cache_metadata(unit) is not None for unit in units):
        raise RuntimeError("cannot finalize before all H60 runtime embedding units are complete")
    materialize_runtime_outputs(pages_by_doc, page_units_, query_units_)
    boundary = boundary_scoring()
    downstream = downstream_support_measurement()
    cost = write_cost_summary()
    return {"boundary": boundary, "downstream": downstream, "cost": cost}


def status_payload() -> dict[str, Any]:
    pages_by_doc, queries = load_runtime_inputs()
    pages = page_units(pages_by_doc)
    q_units = query_units(queries)
    units = [*pages, *q_units]
    verify_checkpoint_consistency(units)
    rows = ledger_rows()
    return {
        "task_id": TASK_ID,
        "preflight_pass": PREFLIGHT.exists() and json.loads(PREFLIGHT.read_text(encoding="utf-8")).get("status") == "PASS",
        "page_count": sum(len(rows_) for rows_ in pages_by_doc.values()),
        "document_family_count": len(pages_by_doc),
        "page_units": len(pages),
        "query_units": len(q_units),
        "total_runtime_units": len(units),
        "completed_runtime_units": sum(cache_metadata(unit) is not None for unit in units),
        "completed_page_units": sum(cache_metadata(unit) is not None for unit in pages),
        "completed_query_units": sum(cache_metadata(unit) is not None for unit in q_units),
        "physical_attempts": len(rows),
        "successful_calls": sum(row.get("terminal_status") == "COMPLETED" for row in rows),
        "retryable_failures": sum(row.get("terminal_status") == "RETRYABLE_ERROR" for row in rows),
        "terminal_failures": sum(row.get("terminal_status") == "TERMINAL_ERROR" for row in rows),
        "finalized": all(path.exists() for path in (SEMANTIC_TOP5, BOUNDARY_SUMMARY, DOWNSTREAM_SUMMARY, COST_SUMMARY)),
    }


def run_online(max_new: int | None) -> None:
    assert_router_authority()
    if not PREFLIGHT.exists() or json.loads(PREFLIGHT.read_text(encoding="utf-8")).get("status") != "PASS":
        raise RuntimeError("H60 task-local Cloudflare preflight must PASS before runtime embeddings")
    pages_by_doc, queries = load_runtime_inputs()
    pages = page_units(pages_by_doc)
    q_units = query_units(queries)
    units = [*pages, *q_units]
    if len(units) != EXPECTED_RUNTIME_CALLS:
        raise RuntimeError(f"H60 runtime successful-call plan changed: {len(units)}")
    verify_checkpoint_consistency(units)
    completed_at_start = sum(cache_metadata(unit) is not None for unit in units)
    new_completed = 0
    for unit in units:
        if cache_metadata(unit) is not None:
            continue
        if max_new is not None and new_completed >= max_new:
            break
        call_unit(unit)
        new_completed += 1
        print(
            json.dumps(
                {
                    "completed_runtime": completed_at_start + new_completed,
                    "total_runtime": len(units),
                    "last_unit": unit["logical_unit_id"],
                    "physical_attempts": len(ledger_rows()),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    verify_checkpoint_consistency(units)
    completed_now = sum(cache_metadata(unit) is not None for unit in units)
    result: dict[str, Any] = {
        "completed_at_start": completed_at_start,
        "new_completed": new_completed,
        "completed_now": completed_now,
        "total_runtime_units": len(units),
        "physical_attempts": len(ledger_rows()),
        "successful_calls_total": sum(row.get("terminal_status") == "COMPLETED" for row in ledger_rows()),
    }
    if completed_now == len(units):
        result["final"] = finalize(units, pages_by_doc, pages, q_units)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def offline_replay(out: Path) -> None:
    pages_by_doc, queries = load_runtime_inputs()
    pages = page_units(pages_by_doc)
    q_units = query_units(queries)
    units = [*pages, *q_units]
    verify_checkpoint_consistency(units)
    if not all(cache_metadata(unit) is not None for unit in units):
        raise RuntimeError("offline replay requires complete persisted H60 embeddings")
    attempts_before = len(ledger_rows())
    final = finalize(units, pages_by_doc, pages, q_units)
    attempts_after = len(ledger_rows())
    if attempts_after != attempts_before:
        raise RuntimeError("offline replay unexpectedly changed API attempt ledger")
    output_paths = [
        SEMANTIC_MANIFEST,
        SEMANTIC_TOP5,
        LANE_HASHES,
        BASELINE_RESULTS,
        CANDIDATE_RESULTS,
        BOUNDARY_SUMMARY,
        DOWNSTREAM_ROWS,
        DOWNSTREAM_SUMMARY,
        COST_SUMMARY,
    ]
    payload = {
        "task_id": TASK_ID,
        "offline_replay": "PASS",
        "api_attempt_count_before": attempts_before,
        "api_attempt_count_after": attempts_after,
        "external_api_calls_during_replay": 0,
        "output_hashes": {path.name: sha256_file(path) for path in output_paths},
        "primary_qualified": bool(final["boundary"]["qualified"]),
        "recovered_cases": int(final["boundary"]["recovered_cases"]),
        "recovered_document_families": int(final["boundary"]["recovered_document_families"]),
        "protected_loss": int(final["boundary"]["protected_loss"]),
        "workspace_max_unique_pages": int(final["boundary"]["workspace_max_unique_pages"]),
        "candidate_trusted_losses": int(final["downstream"]["candidate_trusted_losses_from_baseline_supported_trusted"]),
    }
    write_json(out, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--online", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--offline-replay", type=Path)
    parser.add_argument("--max-new", type=int, default=None)
    args = parser.parse_args()
    if args.max_new is not None and args.max_new < 1:
        raise SystemExit("--max-new must be >=1")
    if args.preflight:
        preflight()
        return 0
    if args.status:
        print(json.dumps(status_payload(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.online:
        run_online(args.max_new)
        return 0
    offline_replay(args.offline_replay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
