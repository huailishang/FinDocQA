"""Inspection helper: dump HighRiskVerifier output for a small question subset.

Used to satisfy the Stage 4 Final Reviewer requirement that the evaluation
include an actual ``verification_result`` sample from a real ``--no-write`` run.

Usage (from enhanced-baseline/):
    $env:LLM_API_KEY=''; $env:DASHSCOPE_API_KEY=''
    python scripts/inspect_verifier.py --domain insurance --limit 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.factory import PipelineFactory
from contracts import VerificationResult
from utils.config import load_config


def _load_local_env(path: Path) -> None:
    """Load .env so OpenAICompatibleClient.from_env can read real API keys."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _verification_dict(v: VerificationResult | None) -> dict:
    if v is None:
        return {"verifier": None, "present": False}
    return {
        "present": True,
        "qid": v.qid,
        "verifier": v.verifier,
        "answer": v.answer,
        "changed": v.changed,
        "notes": list(v.notes),
        "metadata": dict(v.metadata),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect verifier output.")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--domain", default="insurance")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    config_path = (ROOT / args.config).resolve()
    config = load_config(config_path)
    _load_local_env(ROOT / ".env")
    factory = PipelineFactory(config=config, project_root=ROOT)
    workflow = factory.build_workflow(writer=None)

    loader = factory.build_loader()
    questions = list(loader.load())
    if args.domain:
        questions = [q for q in questions if q.domain == args.domain]
    questions = questions[: args.limit]

    print("=== Verifier Inspection (dry-run, no API keys) ===")
    for q in questions:
        result = workflow.process_one(q)
        m = result.metadata
        out = {
            "qid": result.qid,
            "domain": m.get("domain"),
            "doc_ids": m.get("doc_ids"),
            "classifier_labels": m.get("classifier_labels"),
            "solver": result.solver_result.solver,
            "evidence_count": m.get("evidence_count", 0),
            "missing_doc_ids": m.get("missing_doc_ids", []),
            "degraded": m.get("degraded", False),
            "solver_answer": result.solver_result.answer,
            "solver_dry_run": result.solver_result.metadata.get("dry_run", False),
            "verification_result": _verification_dict(result.verification_result),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
