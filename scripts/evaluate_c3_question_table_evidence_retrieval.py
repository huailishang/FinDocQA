"""Run the frozen question-to-table-evidence retrieval baseline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from evaluation.external_benchmarks.table_evidence_retrieval import (  # noqa: E402
    build_report,
    canonical_json_bytes,
    sha256_file,
    validate_report,
)


DEFAULT_MANIFEST = (
    REPO_ROOT
    / "evaluation_artifacts"
    / "c3_question_table_evidence_retrieval_baseline_v1"
    / "case_manifest.json"
)
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "evaluation_artifacts"
    / "c3_question_table_evidence_retrieval_baseline_v1"
    / "report.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure blind question-to-table evidence retrieval coverage."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate an existing machine report without rescanning.",
    )
    return parser


def _print_summary(report: dict[str, object], output: Path) -> None:
    print(f"report={output}")
    print(f"sha256={sha256_file(output)}")
    print(f"case_count={report['case_count']}")
    print(f"unique_document_count={report['unique_document_count']}")
    print(f"adapted_source_object_count={report['adapted_source_object_count']}")
    print(
        "required_document_recall_at_5="
        f"{json.dumps(report['required_document_recall_at_5'], sort_keys=True)}"
    )
    print(
        "gold_table_source_recall_at_5="
        f"{json.dumps(report['gold_table_source_recall_at_5'], sort_keys=True)}"
    )
    print(
        "gold_member_coordinate_micro_coverage_at_5="
        f"{json.dumps(report['gold_member_coordinate_micro_coverage_at_5'], sort_keys=True)}"
    )
    print(f"binding_ready_case_count_at_5={report['binding_ready_case_count_at_5']}")
    print(
        "terminal_layer_counts="
        f"{json.dumps(report['terminal_layer_counts'], sort_keys=True)}"
    )
    print(
        "zero_calls="
        f"{report['provider_calls']}/{report['legacy_calls']}/"
        f"{report['network_calls']}/{report['total_tokens']}"
    )


def main() -> int:
    args = _parser().parse_args()
    output = args.output.resolve()
    if args.validate_only:
        report = json.loads(output.read_text(encoding="utf-8"))
        validate_report(report, enforce_frozen_counts=True)
        print(f"VALID report={output}")
        _print_summary(report, output)
        return 0

    report = build_report(args.manifest.resolve(), repo_root=REPO_ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(report))
    stored = json.loads(output.read_text(encoding="utf-8"))
    validate_report(stored, enforce_frozen_counts=True)
    _print_summary(stored, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
