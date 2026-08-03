#!/usr/bin/env python3
"""Run the complete FinQA + TAT-QA C3 Oracle-program baseline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evaluation.external_benchmarks import ensure_source_manifest, run_external_oracle_baseline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--finqa-root",
        default="evaluation_artifacts/external_benchmarks/finqa",
    )
    parser.add_argument(
        "--tatqa-root",
        default="evaluation_artifacts/external_benchmarks/tatqa",
    )
    parser.add_argument(
        "--output-dir",
        default=(
            "evaluation_artifacts/c3_external_oracle_baseline_v1/"
            "c3o_source_bound_table_section_cardinality_v1"
        ),
    )
    parser.add_argument(
        "--manifest",
        default="evaluation_artifacts/c3_external_oracle_baseline_v1/source_manifest.json",
    )
    parser.add_argument("--manifest-only", action="store_true")
    parser.add_argument(
        "--disable-section-cardinality",
        action="store_true",
        help="Reproduce the frozen C3-N baseline without section cardinality.",
    )
    parser.add_argument(
        "--disable-predicate-cardinality",
        action="store_true",
        help="Reproduce the frozen C3-M baseline without predicate cardinality.",
    )
    parser.add_argument(
        "--disable-series-aggregation",
        action="store_true",
        help="Reproduce the frozen pre-C3-M baseline without the new product capability.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = ensure_source_manifest(args.manifest, args.finqa_root, args.tatqa_root)
    if args.manifest_only:
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        return 0
    _records, report = run_external_oracle_baseline(
        finqa_root=args.finqa_root,
        tatqa_root=args.tatqa_root,
        output_dir=args.output_dir,
        manifest_path=args.manifest,
        enable_series_aggregation=not args.disable_series_aggregation,
        enable_predicate_cardinality=not args.disable_predicate_cardinality,
        enable_section_cardinality=not args.disable_section_cardinality,
    )
    combined = report["datasets"]["combined"]
    print(
        "C3_EXTERNAL_ORACLE_BASELINE",
        f"measurement_valid={str(report['measurement_valid']).lower()}",
        f"source_cases={report['source_case_count']}",
        f"terminal_records={report['terminal_record_count']}",
        f"numeric_eligible={combined['numeric_eligible_count']}",
        f"representable={combined['c3_representable_count']}",
        f"supported_accuracy={combined['supported_subset_execution_exact_match_rate']['value']:.6f}",
        f"effective_accuracy={combined['effective_oracle_execution_accuracy']['value']:.6f}",
        f"rerun_equal={str(report['rerun_record_hash_equal']).lower()}",
        f"provider_calls={report['actual_provider_call_count']}",
        f"network_calls={report['actual_network_call_count_during_evaluation']}",
    )
    print(f"NEXT_PRIMARY_BOTTLENECK = {report['NEXT_PRIMARY_BOTTLENECK']}")
    return 0 if report["measurement_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
