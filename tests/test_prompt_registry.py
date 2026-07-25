from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompts.registry import PromptRegistryError, load_prompt_registry  # noqa: E402
from scripts.evaluate_prompt_ab import evaluate_fixture, run  # noqa: E402

REGISTRY = ROOT / "config" / "prompt_registry.json"
FIXTURE = ROOT / "tests" / "fixtures" / "prompt_registry_ab_fixture.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_registry_covers_all_discovered_solver_prompt_builders_and_hashes_are_stable():
    registry = load_prompt_registry(REGISTRY, repo_root=ROOT)
    inventory = registry.inventory()

    assert inventory["discovered_solver_prompt_builders"] == 7
    assert inventory["registered_solver_prompt_builders"] == 7
    assert inventory["unregistered_solver_prompt_builders"] == 0
    assert all(row["hash_match"] is True for row in inventory["registered"])
    assert registry.production_injection_enabled is False

    for entry in registry.entries:
        assert registry.expected_hash(entry) == entry.template_hash
        assert registry.expected_hash(entry) == entry.template_hash


def test_registry_detects_duplicate_prompt_version(tmp_path: Path):
    payload = _json(REGISTRY)
    payload["prompts"].append(copy.deepcopy(payload["prompts"][0]))
    path = tmp_path / "duplicate_registry.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="version conflict"):
        load_prompt_registry(path, repo_root=ROOT)


def test_registry_detects_template_hash_drift(tmp_path: Path):
    payload = _json(REGISTRY)
    payload["prompts"][0]["template_hash"] = "0" * 64
    path = tmp_path / "stale_registry.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="template hash mismatch"):
        load_prompt_registry(path, repo_root=ROOT)


def test_few_shot_asset_schema_is_validated_and_cannot_be_production_approved(tmp_path: Path):
    payload = _json(REGISTRY)
    asset = {
        "id": "multi_choice.negation_example",
        "version": "1.0.0",
        "question_kind": "multi_choice",
        "domain": "regulatory",
        "failure_mode": "negation_scope",
        "input_fixture": {"question": "不得执行X，以下何项正确？"},
        "expected_output_fixture": {"answer": "B"},
        "source_note": "Synthetic schema-validation-only example; not a production few-shot.",
        "approved_for_production": False,
    }
    payload["few_shot_assets"] = [asset]
    path = tmp_path / "few_shot_registry.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    registry = load_prompt_registry(path, repo_root=ROOT)
    assert registry.few_shot_assets[0].approved_for_production is False

    payload["few_shot_assets"][0]["approved_for_production"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PromptRegistryError, match="approved for production"):
        load_prompt_registry(path, repo_root=ROOT)


def test_frozen_ab_fixture_is_immutable_prompt_only_and_reproducible():
    registry = load_prompt_registry(REGISTRY, repo_root=ROOT)
    fixture = _json(FIXTURE)
    before = copy.deepcopy(fixture)
    file_hash_before = _sha256(FIXTURE)

    report = evaluate_fixture(registry, fixture, fixture_path=FIXTURE)

    assert fixture == before
    assert _sha256(FIXTURE) == file_hash_before
    assert report["provider_calls"] == 0
    assert report["fixture_immutable"] is True
    assert report["production_prompt_modified"] is False
    assert report["production_injection_enabled"] is False
    assert report["quality_claim"] == "NOT_EVALUATED_FIXTURE_ONLY"
    assert len(report["cases"]) == 2

    for case in report["cases"]:
        assert case["input_same_across_arms"] is True
        assert case["model_and_parameters_same_across_arms"] is True
        assert case["prompt_changed"] is True
        assert case["non_prompt_changes"] == []
        assert case["change_attribution"] == "PROMPT_ONLY_OFFLINE_FIXTURE"

    multi = report["cases"][0]
    multi_a, multi_b = multi["arms"]
    assert multi_a["output_metrics"]["output_parseable"] is True
    assert multi_b["output_metrics"]["answer_contract_valid"] is True
    assert multi_a["output_metrics"]["parsed_answer"] == "AC"
    assert multi_b["output_metrics"]["parsed_answer"] == "AC"
    assert multi_a["output_metrics"]["per_option_structure"]["coverage"] == 1.0
    assert multi_b["output_metrics"]["per_option_structure"]["coverage"] == 1.0
    assert multi_a["output_metrics"]["per_option_evidence_reference_completeness"] == 0.0
    assert multi_b["output_metrics"]["per_option_evidence_reference_completeness"] == 1.0

    calc = report["cases"][1]
    calc_a, calc_b = calc["arms"]
    assert calc_a["output_metrics"]["answer_contract_valid"] is True
    assert calc_b["output_metrics"]["answer_contract_valid"] is True
    assert calc_a["output_metrics"]["calculation_formula_structure"]["coverage"] == 0.0
    assert calc_b["output_metrics"]["calculation_formula_structure"]["coverage"] == 1.0
    assert calc_a["output_metrics"]["per_slot_evidence_reference_completeness"] == 0.0
    assert calc_b["output_metrics"]["per_slot_evidence_reference_completeness"] == 1.0
    assert calc_b["prompt_metrics"]["prompt_chars"] > calc_a["prompt_metrics"]["prompt_chars"]


def test_ab_harness_rejects_arm_level_non_prompt_override():
    registry = load_prompt_registry(REGISTRY, repo_root=ROOT)
    fixture = _json(FIXTURE)
    fixture["cases"][0]["arms"][1]["generation_parameters"] = {"temperature": 1}

    with pytest.raises(PromptRegistryError, match="non-prompt input override"):
        evaluate_fixture(registry, fixture)


def test_run_writes_required_audit_artifacts(tmp_path: Path):
    report = run(registry_path=REGISTRY, fixture_path=FIXTURE, output_dir=tmp_path)

    assert report["provider_calls"] == 0
    inventory = _json(tmp_path / "prompt_registry_inventory.json")
    baseline = _json(tmp_path / "prompt_ab_baseline.json")
    unregistered = _json(tmp_path / "unregistered_prompt_paths.json")
    assert inventory["registered_solver_prompt_builders"] == 7
    assert inventory["unregistered_solver_prompt_builders"] == 0
    assert baseline["provider_calls"] == 0
    assert baseline["quality_claim"] == "NOT_EVALUATED_FIXTURE_ONLY"
    assert unregistered == {
        "schema_version": "unregistered_prompt_paths_v1",
        "count": 0,
        "items": [],
    }
