from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from verification.replacement_qualification import qualify_replacement
from verification.scope_absence import (
    TrustedDocumentSource,
    build_scope_absence_proof,
    scan_local_windows,
    validate_scope_absence_proof,
)


def trusted_sources(root: Path, contents: dict[str, str]) -> dict[str, TrustedDocumentSource]:
    result: dict[str, TrustedDocumentSource] = {}
    for doc_id, text in contents.items():
        relpath = f"corpus/{doc_id}.md"
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        result[doc_id] = TrustedDocumentSource(
            canonical_doc_id=doc_id,
            source_root_identity="test_corpus.v1",
            source_root=str(root),
            source_relpath=relpath,
        )
    return result


def valid_bundle(tmp_path: Path) -> tuple[dict[str, TrustedDocumentSource], dict]:
    trusted = trusted_sources(
        tmp_path / "trusted",
        {"doc1": "alpha unrelated material\n", "doc2": "beta unrelated material\n"},
    )
    proof = build_scope_absence_proof(
        trusted_declared_documents=trusted,
        query_terms=["target", "claim"],
        query_alias_groups=[["year", "period"]],
        out_of_scope_match_doc_ids=["doc3"],
        scan_timestamp_or_run_id="test-run",
    ).to_dict()
    return trusted, proof


def oracle_with_scope_absence(proof: dict | None) -> dict:
    slot = {
        "verdict": "scope_absent",
        "claim_route": "scope_only",
        "source_document": ["fake-scan-ref"],
        "text_anchor": ["complete scan"],
    }
    if proof is not None:
        slot["scope_absence_proof"] = proof
    return {
        "status": "pass",
        "oracle_answer": "AC",
        "options": {
            "A": {"verdict": "supported", "claim_route": "direct_evidence", "source_document": ["doc1"], "text_anchor": ["a"]},
            "B": {"verdict": "contradicted", "claim_route": "contradiction", "source_document": ["doc1"], "text_anchor": ["b"]},
            "C": {"verdict": "supported", "claim_route": "direct_evidence", "source_document": ["doc2"], "text_anchor": ["c"]},
            "D": slot,
        },
    }


def qualify(oracle: dict, trusted: dict[str, TrustedDocumentSource] | None):
    return qualify_replacement(
        qid="fixture",
        baseline_answer="ABCD",
        proposed_answer="AC",
        record={
            "qid": "fixture",
            "answer": "",
            "error": "production_integrity:option_evidence_review_required",
            "metadata": {
                "final_state": "failed",
                "attempted_answer": "AC",
                "answer_source": "error",
                "blocking_reasons": ["option_evidence_review_required"],
            },
        },
        answer_format="multi",
        option_texts={label: f"option {label}" for label in "ABCD"},
        independent_oracle=oracle,
        trusted_declared_documents=trusted,
    )


def test_fake_reference_without_proof_is_blocked(tmp_path: Path):
    trusted, _ = valid_bundle(tmp_path)
    result = qualify(oracle_with_scope_absence(None), trusted)
    assert result.replacement_allowed is False
    assert result.removed_option_contradiction_complete is False


def test_missing_trusted_mapping_is_blocked(tmp_path: Path):
    _, proof = valid_bundle(tmp_path)
    validation = validate_scope_absence_proof(proof)
    result = qualify(oracle_with_scope_absence(proof), None)
    assert "trusted_declared_documents_missing" in validation.errors
    assert result.replacement_allowed is False


def test_unrelated_path_binding_is_blocked(tmp_path: Path):
    trusted, _ = valid_bundle(tmp_path)
    attacker = trusted_sources(
        tmp_path / "attacker",
        {"doc1": "unrelated one\n", "doc2": "unrelated two\n"},
    )
    attacker_proof = build_scope_absence_proof(
        trusted_declared_documents=attacker,
        query_terms=["target", "claim"],
        query_alias_groups=[["year", "period"]],
        scan_timestamp_or_run_id="attacker-run",
    ).to_dict()
    validation = validate_scope_absence_proof(attacker_proof, trusted_declared_documents=trusted)
    result = qualify(oracle_with_scope_absence(attacker_proof), trusted)
    assert validation.valid is False
    assert "source_hash_mismatch" in validation.errors
    assert result.replacement_allowed is False


def test_trusted_mapping_missing_one_document_is_blocked(tmp_path: Path):
    trusted, proof = valid_bundle(tmp_path)
    partial = {"doc1": trusted["doc1"]}
    validation = validate_scope_absence_proof(proof, trusted_declared_documents=partial)
    assert "trusted_declared_doc_ids_mismatch" in validation.errors
    assert qualify(oracle_with_scope_absence(proof), partial).replacement_allowed is False


def test_proof_relpath_mismatch_is_blocked(tmp_path: Path):
    trusted, proof = valid_bundle(tmp_path)
    proof["source_relpaths"]["doc2"] = "corpus/not_doc2.md"
    validation = validate_scope_absence_proof(proof, trusted_declared_documents=trusted)
    assert "proof_trusted_source_relpath_mismatch" in validation.errors
    assert qualify(oracle_with_scope_absence(proof), trusted).replacement_allowed is False


def test_source_hash_change_is_blocked(tmp_path: Path):
    trusted, proof = valid_bundle(tmp_path)
    trusted["doc2"].resolved_path.write_text("changed after scan\n", encoding="utf-8")
    validation = validate_scope_absence_proof(proof, trusted_declared_documents=trusted)
    assert "source_hash_mismatch" in validation.errors
    assert qualify(oracle_with_scope_absence(proof), trusted).replacement_allowed is False


def test_valid_v2_proof_passes_with_scope_metadata(tmp_path: Path):
    trusted, proof = valid_bundle(tmp_path)
    validation = validate_scope_absence_proof(proof, trusted_declared_documents=trusted)
    result = qualify(oracle_with_scope_absence(proof), trusted)
    slot = result.per_option_verdicts["D"]
    assert validation.valid is True
    assert result.replacement_allowed is True
    assert result.removed_option_contradiction_complete is True
    assert slot["claim_route"] == "scope_only"
    assert slot["question_scope_binding"] == "scope_absent"
    assert slot["factual_statement_true"] is None
    assert slot["scope_absence_proof_valid"] is True


def test_v1_proof_is_diagnostic_only(tmp_path: Path):
    trusted, proof = valid_bundle(tmp_path)
    proof["proof_version"] = "scope_absence_proof.v1"
    validation = validate_scope_absence_proof(proof, trusted_declared_documents=trusted)
    assert "proof_version_invalid" in validation.errors
    assert qualify(oracle_with_scope_absence(proof), trusted).replacement_allowed is False


@pytest.mark.parametrize(
    ("text", "expected_kind"),
    [
        ("2025年金融信创市场规模预计接近\n2500亿元。\n", "adjacent_2_lines"),
        ("2025年\n金融信创市场规模预计接近\n2500亿元。\n", "adjacent_3_lines"),
    ],
)
def test_two_and_three_line_facts_are_detected(text: str, expected_kind: str):
    matches = scan_local_windows(
        text,
        query_terms=["2025年", "金融信创市场规模", "2500亿元"],
    )
    assert matches
    assert any(expected_kind in row["window_kinds"] for row in matches)


def test_markdown_table_local_block_is_detected():
    text = "| 主体 | 年份 | 指标 |\n|---|---|---|\n| 金融信创市场 | 2025年 | 市场规模 |\n| 数值 | 2500亿元 | 预计接近 |\n"
    matches = scan_local_windows(
        text,
        query_terms=["金融信创市场", "2025年", "市场规模", "2500亿元"],
    )
    assert matches
    assert any("markdown_table_local_block" in row["window_kinds"] for row in matches)


def test_html_table_local_block_is_detected():
    text = "<table>\n<tr><td>金融信创市场</td><td>2025年</td></tr>\n<tr><td>市场规模</td><td>2500亿元</td></tr>\n</table>\n"
    matches = scan_local_windows(
        text,
        query_terms=["金融信创市场", "2025年", "市场规模", "2500亿元"],
    )
    assert matches
    assert any("html_table_local_block" in row["window_kinds"] for row in matches)


def test_distant_terms_do_not_form_false_positive(tmp_path: Path):
    trusted = trusted_sources(
        tmp_path,
        {
            "doc1": "金融信创市场\n\n# unrelated middle\n2025年\n\n# another section\n市场规模\n\n# final unrelated\n2500亿元\n",
        },
    )
    proof = build_scope_absence_proof(
        trusted_declared_documents=trusted,
        query_terms=["金融信创市场", "2025年", "市场规模", "2500亿元"],
        scan_timestamp_or_run_id="distant-negative",
    ).to_dict()
    assert proof["coherent_match_count"] == 0
    assert validate_scope_absence_proof(proof, trusted_declared_documents=trusted).valid is True


def test_matched_local_window_invalidates_absence_proof(tmp_path: Path):
    trusted = trusted_sources(
        tmp_path,
        {"doc1": "2025年金融信创市场规模预计接近\n2500亿元。\n"},
    )
    proof = build_scope_absence_proof(
        trusted_declared_documents=trusted,
        query_terms=["2025年", "金融信创市场规模", "2500亿元"],
        scan_timestamp_or_run_id="two-line-positive",
    ).to_dict()
    validation = validate_scope_absence_proof(proof, trusted_declared_documents=trusted)
    assert proof["coherent_match_count"] >= 1
    assert "coherent_match_count_nonzero" in validation.errors
