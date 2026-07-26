"""Minimal health probe for optional SiliconFlow retrieval models.

Reads a local env file (default: .env.retrieval.local). Never prints API keys.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from contracts import EvidenceCandidate, Question
from retrieval.siliconflow_models import (
    SiliconFlowClient,
    SiliconFlowEmbeddingClient,
    SiliconFlowEvidenceReranker,
)


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env.retrieval.local")
    args = parser.parse_args()
    env = load_env(Path(args.env))
    client = SiliconFlowClient(
        api_key=env.get("SILICONFLOW_API_KEY", ""),
        base_url=env.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
    )
    embedding = SiliconFlowEmbeddingClient(
        client,
        model=env.get("SILICONFLOW_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"),
        dimensions=64,
    )
    vector = embedding.embed("财务报告检索测试")[0]
    print(f"embedding=PASS model={embedding.model} dimensions={len(vector)}")

    question = Question(
        qid="probe",
        domain="financial_reports",
        text="营业收入",
        options={},
        answer_format="freeform",
        doc_ids=(),
    )
    candidates = (
        EvidenceCandidate(domain="financial_reports", doc_id="a", source="probe://a", text="公司2025年营业收入为100亿元"),
        EvidenceCandidate(domain="financial_reports", doc_id="b", source="probe://b", text="公司2025年净利润为20亿元"),
    )
    reranker = SiliconFlowEvidenceReranker(
        client,
        model=env.get("SILICONFLOW_RERANK_MODEL", "Qwen/Qwen3-Reranker-8B"),
    )
    ranked = reranker.rerank(question, candidates, top_k=2)
    if not ranked:
        raise RuntimeError("rerank returned no candidates")
    print(f"rerank=PASS model={reranker.model} top_doc={ranked[0].doc_id} score={ranked[0].score:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
