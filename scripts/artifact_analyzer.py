#!/usr/bin/env python3
"""Artifact analyzer CLI (Lane D).

Reads ``output/debug_results.json`` (+ optional ``submission.csv``) and prints:

- P7E metadata rollup: answer_source distribution, high_risk / truncation_risk
  / fallback / calculation-incomplete / unsupported-guess qids;
- error taxonomy report (markdown);
- token / answer consistency vs submission.csv (when provided).

Read-only. No LLM, no pipeline run, no API. Standard-library only.

Usage:
    python scripts/artifact_analyzer.py --debug-json output/debug_results.json
    python scripts/artifact_analyzer.py --debug-json output/debug_results.json \\
        --submission output/submission.csv --experiment-id A0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiments.artifact_loader import (  # noqa: E402
    build_manifest_from_artifact,
    check_token_consistency,
    load_debug_artifact,
    load_submission_csv,
    summarize_p7e_metadata,
)
from experiments.report import generate_error_taxonomy_report  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze a debug_results.json artifact (P7E metadata + taxonomy)."
    )
    parser.add_argument("--debug-json", default="output/debug_results.json",
                        help="path to debug_results.json (default: output/debug_results.json)")
    parser.add_argument("--submission", default=None,
                        help="optional path to submission.csv for token/answer consistency")
    parser.add_argument("--experiment-id", default="A0",
                        help="experiment id label for the taxonomy report")
    args = parser.parse_args(argv)

    debug_path = Path(args.debug_json)
    if not debug_path.is_file():
        print(f"LIMITATION: {debug_path} not found (output/ is gitignored).")
        print("This is expected in a clean Git tree. Run the pipeline locally to")
        print("generate the artifact, then re-run this script.")
        return 0

    results = load_debug_artifact(debug_path)
    print(f"Artifact analyzer — {args.experiment_id}")
    print(f"debug_json: {debug_path.resolve()}")
    print(f"questions: {len(results)}")
    print()

    summary = summarize_p7e_metadata(results)
    print("== P7E metadata rollup ==")
    print(f"  answer_source distribution: {summary['answer_source_distribution']}")
    print(f"  high_risk qids: {summary['high_risk_qids']}")
    print(f"  truncation_risk qids: {summary['truncation_risk_qids']}")
    print(f"  fallback qids: {summary['fallback_qids']}")
    print(f"  calculation_incomplete qids: {summary['calculation_incomplete_qids']}")
    print(f"  unsupported_guess qids: {summary['unsupported_guess_qids']}")
    print(f"  total_tokens: {summary['total_tokens']}")
    print()

    er = build_manifest_from_artifact(
        debug_path, experiment_id=args.experiment_id,
        commit="local", config="config.yaml", corpus_root="local",
    )
    tax = generate_error_taxonomy_report(er)
    print(tax.text)

    if args.submission:
        sub_path = Path(args.submission)
        if sub_path.is_file():
            sub = load_submission_csv(sub_path)
            cons = check_token_consistency(results, sub)
            print("== token / answer consistency vs submission ==")
            print(f"  debug_total_tokens: {cons['debug_total_tokens']}")
            print(f"  submission_qid_count: {cons['submission_qid_count']}")
            print(f"  answer_mismatches: {cons['answer_mismatches']}")
            print(f"  qids_only_in_debug: {cons['qids_only_in_debug']}")
            print(f"  qids_only_in_submission: {cons['qids_only_in_submission']}")
        else:
            print(f"submission csv not found: {sub_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
