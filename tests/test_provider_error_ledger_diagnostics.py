from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_safety import TokenLedger, set_attempt_context
from utils.llm_client import (
    LLMClientUnavailable,
    LLMEndpoint,
    LLMProviderCapabilityError,
    OpenAICompatibleClient,
)

SENTINEL = "PRIVATE_BODY_SENTINEL_DO_NOT_PERSIST_7F3A"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def _client() -> tuple[OpenAICompatibleClient, LLMEndpoint]:
    endpoint = LLMEndpoint(
        provider="synthetic-provider",
        api_key="synthetic-key",
        base_url="https://synthetic.invalid/v1",
        model="synthetic-model",
        stop_on_exhaustion=False,
    )
    client = OpenAICompatibleClient(
        endpoint.api_key,
        endpoint.base_url,
        endpoint.model,
        timeout_s=7,
        endpoints=[endpoint],
    )
    return client, endpoint


@pytest.fixture
def ledger_path(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "provider_ledger.jsonl"
    monkeypatch.setenv("SAFE_RUN_EXECUTION", "1")
    monkeypatch.setenv("LLM_TOKEN_LEDGER_PATH", str(path))
    monkeypatch.delenv("SAFE_RUN_PROVIDER_CALL_BUDGETS_JSON", raising=False)
    monkeypatch.delenv("SAFE_RUN_MAX_PROVIDER_CALL_BUDGET", raising=False)
    set_attempt_context("synthetic-qid", "llm_chat")
    return path


def _only_row(path: Path) -> dict:
    rows = TokenLedger(path).rows()
    assert len(rows) == 1
    return rows[0]


def _assert_sanitized(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert SENTINEL not in text
    row = _only_row(path)
    for forbidden in ("error_detail", "response_body", "raw_error"):
        assert forbidden not in row


def _raise_http(code: int, body: str):
    error = urllib.error.HTTPError(
        url="https://synthetic.invalid/v1/chat/completions",
        code=code,
        msg="synthetic",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )

    def _raiser(*args, **kwargs):
        raise error

    return _raiser


@pytest.mark.parametrize(
    ("code", "body", "max_completion_tokens", "expected_category", "expected_type", "expected_exc"),
    [
        (402, f'{{"error":"quota {SENTINEL}"}}', None, "PROVIDER_QUOTA", "HTTPError", LLMClientUnavailable),
        (403, f'{{"error":"insufficient_quota {SENTINEL}"}}', None, "PROVIDER_QUOTA", "HTTPError", LLMClientUnavailable),
        (429, f'{{"error":"quota {SENTINEL}"}}', None, "PROVIDER_QUOTA", "HTTPError", LLMClientUnavailable),
        (500, f'{{"error":"server {SENTINEL}"}}', None, "HTTP_ERROR", "HTTPError", LLMClientUnavailable),
        (
            400,
            f'{{"error":"max_completion_tokens is not supported {SENTINEL}"}}',
            64,
            "PROVIDER_CAPABILITY",
            "LLMProviderCapabilityError",
            LLMProviderCapabilityError,
        ),
        (
            422,
            f'{{"error":"max_completion_tokens unexpected {SENTINEL}"}}',
            64,
            "PROVIDER_CAPABILITY",
            "LLMProviderCapabilityError",
            LLMProviderCapabilityError,
        ),
    ],
)
def test_http_failures_are_classified_without_persisting_body(
    monkeypatch,
    ledger_path: Path,
    code: int,
    body: str,
    max_completion_tokens: int | None,
    expected_category: str,
    expected_type: str,
    expected_exc: type[BaseException],
):
    client, endpoint = _client()
    monkeypatch.setattr("urllib.request.urlopen", _raise_http(code, body))

    with pytest.raises(expected_exc):
        client._chat_endpoint(
            endpoint,
            [{"role": "user", "content": "synthetic"}],
            16,
            max_completion_tokens=max_completion_tokens,
        )

    row = _only_row(ledger_path)
    assert row["final_status"] == "ERROR"
    assert row["failure_category"] == expected_category
    assert row["error_type"] == expected_type
    assert row["http_status"] == code
    _assert_sanitized(ledger_path)


def test_timeout_is_classified(monkeypatch, ledger_path: Path):
    client, endpoint = _client()

    def _timeout(*args, **kwargs):
        raise TimeoutError(SENTINEL)

    monkeypatch.setattr("urllib.request.urlopen", _timeout)
    with pytest.raises(LLMClientUnavailable):
        client._chat_endpoint(endpoint, [{"role": "user", "content": "synthetic"}], 16)

    row = _only_row(ledger_path)
    assert row["final_status"] == "TIMEOUT"
    assert row["failure_category"] == "TIMEOUT"
    assert row["error_type"] == "TimeoutError"
    assert row["http_status"] is None
    _assert_sanitized(ledger_path)


def test_url_error_is_classified(monkeypatch, ledger_path: Path):
    client, endpoint = _client()

    def _connection_error(*args, **kwargs):
        raise urllib.error.URLError(SENTINEL)

    monkeypatch.setattr("urllib.request.urlopen", _connection_error)
    with pytest.raises(LLMClientUnavailable):
        client._chat_endpoint(endpoint, [{"role": "user", "content": "synthetic"}], 16)

    row = _only_row(ledger_path)
    assert row["final_status"] == "ERROR"
    assert row["failure_category"] == "CONNECTION_ERROR"
    assert row["error_type"] == "URLError"
    assert row["http_status"] is None
    _assert_sanitized(ledger_path)


def test_invalid_json_is_classified(monkeypatch, ledger_path: Path):
    client, endpoint = _client()
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _FakeResponse(f"not-json-{SENTINEL}".encode("utf-8")),
    )

    with pytest.raises(LLMClientUnavailable):
        client._chat_endpoint(endpoint, [{"role": "user", "content": "synthetic"}], 16)

    row = _only_row(ledger_path)
    assert row["failure_category"] == "INVALID_JSON"
    assert row["error_type"] == "JSONDecodeError"
    assert row["http_status"] is None
    _assert_sanitized(ledger_path)


@pytest.mark.parametrize(
    "payload",
    [
        [SENTINEL],
        {"choices": SENTINEL},
        {"choices": [{}]},
        {"choices": [{"message": SENTINEL}]},
    ],
)
def test_invalid_response_shape_is_classified(monkeypatch, ledger_path: Path, payload):
    client, endpoint = _client()
    body = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _FakeResponse(body))

    with pytest.raises(LLMClientUnavailable):
        client._chat_endpoint(endpoint, [{"role": "user", "content": "synthetic"}], 16)

    row = _only_row(ledger_path)
    assert row["failure_category"] == "INVALID_RESPONSE"
    assert row["error_type"] == "InvalidCompletionResponse"
    assert row["http_status"] is None
    _assert_sanitized(ledger_path)


def test_success_semantics_remain_completed_with_neutral_diagnostics(monkeypatch, ledger_path: Path):
    client, endpoint = _client()
    body = json.dumps(
        {
            "id": "req-synthetic-1",
            "model": "synthetic-model-resolved",
            "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }
    ).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _FakeResponse(body))

    result = client._chat_endpoint(endpoint, [{"role": "user", "content": "synthetic"}], 16)

    assert result.content == "answer"
    assert result.model == "synthetic-model-resolved"
    row = _only_row(ledger_path)
    assert row["final_status"] == "COMPLETED"
    assert row["provider"] == "synthetic-provider"
    assert row["model"] == "synthetic-model-resolved"
    assert row["provider_request_id"] == "req-synthetic-1"
    assert row["prompt_tokens"] == 11
    assert row["completion_tokens"] == 3
    assert row["total_tokens"] == 14
    assert row["failure_category"] == ""
    assert row["error_type"] == ""
    assert row["http_status"] is None
