"""Research source-attribution, forecast-scope and cross-document adapter."""
from pathlib import Path
from typing import Any, Mapping
from evaluation.domain_adapters.base import DomainQuestionResult, evaluate_from_current_production
CAPABILITY = "research_source_attribution_forecast_scope_v1"
def evaluate(*, repo_root: Path, question: Any, payload: Mapping[str, Any]) -> DomainQuestionResult:
    return evaluate_from_current_production(repo_root=repo_root, question=question, payload=payload, production_capability=CAPABILITY, require_payload_trust=True)
