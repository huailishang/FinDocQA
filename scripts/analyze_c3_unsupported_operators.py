#!/usr/bin/env python3
"""Build the deterministic C3 unsupported-operator capability triage."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evaluation.external_benchmarks.unsupported_operator_triage import write_triage_outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default="evaluation_artifacts/c3_unsupported_operator_triage_v1",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    rows, aggregate, decision = write_triage_outputs(
        root=ROOT,
        output_dir=args.output_dir,
    )
    print(
        "C3_UNSUPPORTED_OPERATOR_TRIAGE",
        f"cases={len(rows)}",
        f"finqa={aggregate['dataset_totals']['finqa']}",
        f"tatqa={aggregate['dataset_totals']['tatqa']}",
        f"selected={decision['selected_capability']}",
        f"projected_recoverable={decision['projected_recoverable_case_count']}",
        f"projected_representable={decision['projected_combined_representable_count']}",
        "provider_calls=0",
        "model_calls=0",
        "network_calls=0",
        "tokens=0",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
