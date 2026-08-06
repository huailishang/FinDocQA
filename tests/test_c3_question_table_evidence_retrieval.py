from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from contracts import EvidenceCandidate
from evaluation.external_benchmarks import table_evidence_retrieval as baseline


MANIFEST_PATH = (
    REPO_ROOT
    / "evaluation_artifacts"
    / "c3_question_table_evidence_retrieval_baseline_v1"
    / "case_manifest.json"
)
BASELINE_REPORT_PATH = (
    REPO_ROOT
    / "evaluation_artifacts"
    / "c3_question_table_evidence_retrieval_baseline_v1"
    / "report.json"
)
BASELINE_REPORT_SHA256 = "33edc54487162e6b2f5cd7ed30c82c7087002bae0e2cdaf5d3fa7086f0539998"


@pytest.fixture(scope="module")
def manifest() -> dict[str, object]:
    payload, digest = baseline.load_and_validate_manifest(
        MANIFEST_PATH, repo_root=REPO_ROOT
    )
    assert digest == baseline.EXPECTED_MANIFEST_SHA256
    return payload


@pytest.fixture(scope="module")
def corpus(manifest: dict[str, object]) -> baseline.AdaptedCorpus:
    return baseline.adapt_corpus(manifest, repo_root=REPO_ROOT)


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    assert baseline.sha256_file(BASELINE_REPORT_PATH) == BASELINE_REPORT_SHA256
    return json.loads(BASELINE_REPORT_PATH.read_text(encoding="utf-8"))


def _entry(payload: dict[str, object], dataset: str) -> dict[str, object]:
    return next(
        deepcopy(item)
        for item in payload["entries"]
        if item["dataset"] == dataset
    )


def test_frozen_manifest_and_official_sources_are_complete(
    manifest: dict[str, object], corpus: baseline.AdaptedCorpus
) -> None:
    assert manifest["case_count"] == 54
    assert manifest["dataset_counts"] == {"finqa": 34, "tatqa": 20}
    assert manifest["unique_document_count"] == 46
    assert len(corpus.documents) == 46
    assert len(corpus.document_id_by_source_key) == 46
    assert corpus.source_object_count == 98
    assert sum(
        len(document.pages)
        for document in corpus.documents
        if document.metadata.get("dataset") == "finqa"
    ) == 79
    assert sum(
        len(document.pages)
        for document in corpus.documents
        if document.metadata.get("dataset") == "tatqa"
    ) == 19


def test_each_gold_source_object_occurs_once_in_store(
    manifest: dict[str, object], corpus: baseline.AdaptedCorpus
) -> None:
    all_source_ids = [
        str(page.metadata["source_object_id"])
        for document in corpus.documents
        for page in document.pages
    ]
    assert len(all_source_ids) == len(set(all_source_ids)) == 98
    gold_source_ids = {
        source_id
        for entry in manifest["entries"]
        for source_id in entry["bound_source_object_ids"]
    }
    assert gold_source_ids <= set(all_source_ids)


def test_finqa_canonical_page_is_exact_official_context(
    manifest: dict[str, object], corpus: baseline.AdaptedCorpus
) -> None:
    entry = _entry(manifest, "finqa")
    official = json.loads(
        (
            REPO_ROOT
            / "evaluation_artifacts/external_benchmarks/finqa/dataset/dev.json"
        ).read_text(encoding="utf-8")
    )[entry["document_index"]]
    source_id = entry["bound_source_object_ids"][0]
    page = corpus.pages_by_source_object[source_id]
    expected, coordinates, _ = baseline._render_page_text(
        paragraphs_before=official["pre_text"],
        table=official["table"],
        paragraphs_after=official["post_text"],
        source_object_id=source_id,
    )
    assert page.text == expected
    assert page.metadata["coordinate_spans"] == {
        key: [value[0], value[1]] for key, value in sorted(coordinates.items())
    }
    assert entry["question"] not in page.metadata["included_official_fields"]
    assert "question" in page.metadata["excluded_gold_fields"]
    assert "answer" in page.metadata["excluded_gold_fields"]


@pytest.mark.parametrize(
    ("dataset", "mutation", "message"),
    [
        (
            "finqa",
            lambda entry: entry.__setitem__("source_document_key", "wrong/file.pdf"),
            "filename mismatch",
        ),
        (
            "tatqa",
            lambda entry: entry.__setitem__("table_uid", "wrong-table-uid"),
            "table UID mismatch",
        ),
        (
            "finqa",
            lambda entry: entry.__setitem__("bound_source_object_ids", ["finqa://dev/duplicate"]),
            "source object mismatch",
        ),
        (
            "tatqa",
            lambda entry: entry.__setitem__("bound_member_or_value_coordinates", []),
            "no gold coordinates",
        ),
    ],
)
def test_adapter_tampering_fails_closed(
    manifest: dict[str, object], dataset: str, mutation, message: str
) -> None:
    tampered = deepcopy(manifest)
    target = next(item for item in tampered["entries"] if item["dataset"] == dataset)
    mutation(target)
    with pytest.raises(baseline.RetrievalBaselineValidationError, match=message):
        baseline.adapt_corpus(tampered, repo_root=REPO_ROOT)


def test_existing_retrievers_run_all_cases_without_gold_scope(
    report: dict[str, object], manifest: dict[str, object]
) -> None:
    assert report["case_count"] == 54
    assert len(report["per_case_records"]) == 54
    assert len(report["retriever_input_audit"]) == 54
    expected_question_hashes = {
        baseline._sha256_text(entry["question"])
        for entry in manifest["entries"]
    }
    observed_question_hashes = {
        audit["question_text_sha256"] for audit in report["retriever_input_audit"]
    }
    assert observed_question_hashes == expected_question_hashes
    for audit in report["retriever_input_audit"]:
        assert audit["call_count"] == 2
        assert audit["gold_scope_injected"] is False
        assert audit["violations"] == []
        for call in audit["calls"]:
            assert call["doc_ids"] == []
            assert call["candidate_doc_ids"] == []
            assert call["options"] == {}
            assert call["raw"] == {}
            assert call["classification_labels"] == ["default"]


def test_validate_report_rejects_gold_document_injection(
    report: dict[str, object]
) -> None:
    tampered = deepcopy(report)
    audit = tampered["retriever_input_audit"][0]
    required_document_id = tampered["per_case_records"][0]["required_document_id"]
    audit["calls"][0]["doc_ids"] = [required_document_id]
    audit["gold_scope_injected"] = True
    audit["violations"] = ["call[0].doc_ids"]
    with pytest.raises(baseline.RetrievalBaselineValidationError, match="gold scope injected"):
        baseline.validate_report(tampered, enforce_frozen_counts=True)


@pytest.mark.parametrize(
    ("adapter_ok", "document_rank", "source_rank", "covered", "total", "expected"),
    [
        (False, None, None, 0, 1, "SOURCE_ADAPTER_ERROR"),
        (True, None, None, 0, 1, "DOCUMENT_MISS"),
        (True, 2, None, 0, 1, "TABLE_SOURCE_MISS"),
        (True, 2, 3, 1, 2, "MEMBER_RANGE_INCOMPLETE"),
        (True, 2, 3, 2, 2, "BINDING_READY"),
    ],
)
def test_terminal_layer_precedence(
    adapter_ok: bool,
    document_rank: int | None,
    source_rank: int | None,
    covered: int,
    total: int,
    expected: str,
) -> None:
    assert baseline.classify_terminal_layer(
        adapter_ok=adapter_ok,
        document_hit_rank=document_rank,
        gold_source_hit_ranks={"source": source_rank},
        covered_coordinate_count=covered,
        gold_coordinate_count=total,
    ) == expected


def test_same_value_in_wrong_source_does_not_cover_coordinate(
    manifest: dict[str, object], corpus: baseline.AdaptedCorpus
) -> None:
    entry = _entry(manifest, "finqa")
    correct_source = entry["bound_source_object_ids"][0]
    coordinate = baseline._gold_coordinates(entry)[0]
    correct_page = corpus.pages_by_source_object[correct_source]
    wrong_source = next(source for source in corpus.pages_by_source_object if source != correct_source)
    candidate = EvidenceCandidate(
        domain=baseline.DOMAIN,
        doc_id="wrong-doc",
        source="canonical://wrong",
        text=correct_page.text,
        score=100.0,
        metadata={"lineage": {"source_path": wrong_source}},
    )
    assert baseline._covered_coordinates(
        candidates=(candidate,),
        pages_by_source_object=corpus.pages_by_source_object,
        gold_source_object_ids=(correct_source,),
        gold_coordinates=(coordinate,),
        top_k=5,
    ) == ()


def test_partial_coordinate_hit_is_member_range_incomplete() -> None:
    assert baseline.classify_terminal_layer(
        adapter_ok=True,
        document_hit_rank=1,
        gold_source_hit_ranks={"source": 1},
        covered_coordinate_count=2,
        gold_coordinate_count=3,
    ) == "MEMBER_RANGE_INCOMPLETE"


def test_missing_lineage_cannot_remain_binding_ready(report: dict[str, object]) -> None:
    tampered = deepcopy(report)
    record = next(
        item for item in tampered["per_case_records"] if item["terminal_layer"] == "BINDING_READY"
    )
    record["evidence_top25"][0]["lineage"]["source_path"] = ""
    with pytest.raises(baseline.RetrievalBaselineValidationError, match="missing lineage"):
        baseline.validate_report(tampered, enforce_frozen_counts=True)


def test_report_aggregates_recompute_and_have_unique_terminal_layers(
    report: dict[str, object]
) -> None:
    baseline.validate_report(report, enforce_frozen_counts=True)
    assert sum(report["terminal_layer_counts"].values()) == 54
    assert report["terminal_layer_counts"] == {
        "BINDING_READY": 21,
        "DOCUMENT_MISS": 18,
        "MEMBER_RANGE_INCOMPLETE": 12,
        "TABLE_SOURCE_MISS": 3,
    }
    assert report["required_document_recall_at_5"] == {
        "numerator": 36,
        "denominator": 54,
        "value": 36 / 54,
    }
    assert report["gold_table_source_recall_at_5"] == {
        "numerator": 33,
        "denominator": 54,
        "value": 33 / 54,
    }
    assert report["gold_member_coordinate_micro_coverage_at_5"] == {
        "numerator": 67,
        "denominator": 185,
        "value": 67 / 185,
    }


def test_validate_report_rejects_aggregate_tampering(report: dict[str, object]) -> None:
    tampered = deepcopy(report)
    tampered["binding_ready_case_count_at_5"] += 1
    with pytest.raises(baseline.RetrievalBaselineValidationError, match="aggregate mismatch"):
        baseline.validate_report(tampered, enforce_frozen_counts=True)


def test_evidence_report_does_not_copy_large_text(report: dict[str, object]) -> None:
    for record in report["per_case_records"]:
        for hit in record["evidence_top25"]:
            assert "text" not in hit
            assert "before_text" not in hit
            assert "after_text" not in hit
            assert hit["lineage"]["source_path"]


def test_canonical_machine_report_is_deterministic(report: dict[str, object]) -> None:
    assert baseline.canonical_json_bytes(report) == baseline.canonical_json_bytes(
        deepcopy(report)
    )
