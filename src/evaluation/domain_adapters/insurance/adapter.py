"""Insurance product-clause-condition-formula graph adapter."""
from pathlib import Path
from typing import Any, Mapping
from evaluation.domain_adapters.base import DomainQuestionResult, evaluate_from_current_production
CAPABILITY = "insurance_product_clause_condition_formula_graph_v1"
def evaluate(*, repo_root: Path, question: Any, payload: Mapping[str, Any]) -> DomainQuestionResult:
    return evaluate_from_current_production(repo_root=repo_root, question=question, payload=payload, production_capability=CAPABILITY, require_payload_trust=True)
