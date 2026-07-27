"""Optional SiliconFlow embedding/rerank adapters.

These adapters are shadow/backup only. They never run unless instantiated and
called explicitly. Secrets are read from environment variables by the caller.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate, Question


class SiliconFlowApiError(RuntimeError):
    """Raised when an optional SiliconFlow retrieval request fails."""


class SiliconFlowClient:
    def __init__(self, *, api_key: str, base_url: str = "https://api.siliconflow.cn/v1", timeout: float = 30.0) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("api_key is required")
        self._api_key = key
        self.base_url = str(base_url or "https://api.siliconflow.cn/v1").rstrip("/")
        self.timeout = float(timeout)

    def post_json(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = urllib.request.Request(
            self.base_url + "/" + path.lstrip("/"),
            data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:800]
            raise SiliconFlowApiError(f"SiliconFlow HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise SiliconFlowApiError(f"SiliconFlow connection failed: {exc.reason}") from exc


class SiliconFlowEmbeddingClient:
    """Thin client for the SiliconFlow /embeddings endpoint."""

    def __init__(
        self,
        client: SiliconFlowClient,
        *,
        model: str = "Qwen/Qwen3-Embedding-8B",
        dimensions: int | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: str | Sequence[str]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.dimensions is not None:
            payload["dimensions"] = int(self.dimensions)
        body = self.client.post_json("embeddings", payload)
        rows = body.get("data")
        if not isinstance(rows, list) or not rows:
            raise SiliconFlowApiError("embedding response contains no data")
        vectors: list[list[float]] = []
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("embedding"), list):
                raise SiliconFlowApiError("embedding response has invalid row")
            vectors.append([float(value) for value in row["embedding"]])
        return vectors


class SiliconFlowEvidenceReranker:
    """Optional EvidenceReranker backed by Qwen3-Reranker."""

    name = "siliconflow_qwen3_reranker"

    def __init__(
        self,
        client: SiliconFlowClient,
        *,
        model: str = "Qwen/Qwen3-Reranker-8B",
        instruction: str | None = None,
        max_query_chars: int = 8000,
        max_document_chars: int = 12000,
    ) -> None:
        if max_query_chars < 1:
            raise ValueError("max_query_chars must be >= 1")
        if max_document_chars < 1:
            raise ValueError("max_document_chars must be >= 1")
        self.client = client
        self.model = model
        self.instruction = instruction
        self.max_query_chars = int(max_query_chars)
        self.max_document_chars = int(max_document_chars)

    def rerank(
        self,
        question: Question,
        candidates: Sequence[EvidenceCandidate],
        *,
        top_k: int | None = None,
    ) -> Sequence[EvidenceCandidate]:
        items = tuple(candidates)
        if not items:
            return ()
        query = "\n".join([question.text, *question.options.values()]).strip()
        payload: dict[str, Any] = {
            "model": self.model,
            "query": query[: self.max_query_chars],
            "documents": [candidate.text[: self.max_document_chars] for candidate in items],
            "return_documents": False,
            "top_n": min(len(items), int(top_k)) if top_k is not None else len(items),
        }
        if self.instruction:
            payload["instruction"] = self.instruction
        body = self.client.post_json("rerank", payload)
        rows = body.get("results")
        if not isinstance(rows, list):
            raise SiliconFlowApiError("rerank response contains no results")
        ranked: list[EvidenceCandidate] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            index = int(row.get("index", -1))
            if index < 0 or index >= len(items):
                continue
            score = float(row.get("relevance_score", 0.0))
            candidate = items[index]
            metadata = dict(candidate.metadata or {})
            metadata.update({
                "reranker": self.name,
                "rerank_model": self.model,
                "pre_rerank_score": candidate.score,
            })
            ranked.append(replace(candidate, score=score, retriever=f"{candidate.retriever}+rerank", metadata=metadata))
        return tuple(ranked)
