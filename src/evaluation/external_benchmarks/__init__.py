"""External financial benchmark adapters for evaluation-only use."""
from evaluation.external_benchmarks.c3_oracle_baseline import (
    build_source_manifest,
    ensure_source_manifest,
    execute_c3_runtime,
    run_cases,
    run_external_oracle_baseline,
    verify_source_manifest,
)
from evaluation.external_benchmarks.contracts import (
    C3ExecutionObservation,
    ExternalCaseRecord,
    OracleCase,
    OracleLabel,
    OracleRuntime,
    RuntimeVariable,
    SourceManifestEntry,
    TerminalClassification,
)
from evaluation.external_benchmarks.financebench_adapter import (
    FINANCEBENCH_LICENSE_ID,
    RESEARCH_ONLY_USE_SCOPE,
    FinanceBenchCase,
    FinanceBenchDocumentRef,
    FinanceBenchEvidence,
    FinanceBenchGoldLabel,
    load_financebench_cases,
)
from evaluation.external_benchmarks.finqa_adapter import FinQASeriesOracleRuntime, load_finqa_cases
from evaluation.external_benchmarks.tatqa_adapter import (
    TATQAPredicateCardinalityOracleRuntime,
    TATQASectionCardinalityOracleRuntime,
    load_tatqa_cases,
)

__all__ = [
    "C3ExecutionObservation",
    "ExternalCaseRecord",
    "FINANCEBENCH_LICENSE_ID",
    "FinanceBenchCase",
    "FinanceBenchDocumentRef",
    "FinanceBenchEvidence",
    "FinanceBenchGoldLabel",
    "FinQASeriesOracleRuntime",
    "OracleCase",
    "OracleLabel",
    "OracleRuntime",
    "RESEARCH_ONLY_USE_SCOPE",
    "RuntimeVariable",
    "SourceManifestEntry",
    "TATQAPredicateCardinalityOracleRuntime",
    "TATQASectionCardinalityOracleRuntime",
    "TerminalClassification",
    "build_source_manifest",
    "ensure_source_manifest",
    "execute_c3_runtime",
    "load_financebench_cases",
    "load_finqa_cases",
    "load_tatqa_cases",
    "run_cases",
    "run_external_oracle_baseline",
    "verify_source_manifest",
]
