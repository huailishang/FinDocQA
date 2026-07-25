from __future__ import annotations

import json

import pytest

from evaluation.formal_submission import FormalRouteError, assert_formal_route, model_family
from utils.llm_client import (
    LLMEndpoint,
    OpenAICompatibleClient,
    build_response_shape_audit,
    extract_visible_message_content,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def _client(*, provider: str, model: str) -> OpenAICompatibleClient:
    endpoint = LLMEndpoint(provider, "test-key", "https://example.invalid/v1", model)
    return OpenAICompatibleClient(
        "test-key",
        "https://example.invalid/v1",
        model,
        endpoints=[endpoint],
    )


def _payload(*, model: str, content, reasoning_content: str | None = None) -> dict:
    message = {"content": content}
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    return {
        "id": "chat-test",
        "model": model,
        "choices": [{"finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def test_model_family_accepts_qwen35_qwen36_qwen37_and_blocks_qwen38() -> None:
    assert model_family("qwen3.7-plus") == "qwen3.7"
    assert model_family("qwen3.7-max") == "qwen3.7"
    assert model_family("Qwen3.6-plus") == "qwen3.6"
    assert model_family("Qwen/Qwen3.5-122B-A10B") == "qwen3.5"
    assert model_family("qwen3.8-max-preview") == ""


def test_provider_must_be_exactly_evaluator_approved() -> None:
    assert assert_formal_route(
        provider="organizer-api",
        model="qwen3.7-plus",
        approved_providers=("organizer-api",),
    )["model_family"] == "qwen3.7"

    with pytest.raises(FormalRouteError, match="provider is not evaluator-approved"):
        assert_formal_route(
            provider="modelscope-1",
            model="Qwen/Qwen3.5-122B-A10B",
            approved_providers=("organizer-api",),
        )


def test_qwen37_plus_and_max_allowed_but_qwen38_blocked_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINDOCQA_FORMAL_EXECUTION", "1")
    monkeypatch.setenv("FINDOCQA_FORMAL_PROVIDER_ALLOWLIST", "organizer-api")
    called = {"http": 0}

    def _fake(request, *args, **kwargs):
        called["http"] += 1
        body = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(_payload(model=body["model"], content="{}"))

    monkeypatch.setattr("urllib.request.urlopen", _fake)
    for model in ("qwen3.7-plus", "qwen3.7-max"):
        result = _client(provider="organizer-api", model=model).chat(
            [{"role": "user", "content": "test"}]
        )
        assert result.model == model
    assert called["http"] == 2

    with pytest.raises(FormalRouteError, match="not Qwen3.7/Qwen3.6/Qwen3.5"):
        _client(provider="organizer-api", model="qwen3.8-max-preview").chat(
            [{"role": "user", "content": "test"}]
        )
    assert called["http"] == 2


def test_qwen35_on_unapproved_provider_is_blocked_before_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDOCQA_FORMAL_EXECUTION", "1")
    monkeypatch.setenv("FINDOCQA_FORMAL_PROVIDER_ALLOWLIST", "organizer-api")
    called = {"http": 0}

    def _unexpected(*args, **kwargs):
        called["http"] += 1
        raise AssertionError("HTTP must not be reached")

    monkeypatch.setattr("urllib.request.urlopen", _unexpected)
    with pytest.raises(FormalRouteError, match="provider is not evaluator-approved"):
        _client(provider="modelscope-1", model="Qwen/Qwen3.5-122B-A10B").chat(
            [{"role": "user", "content": "test"}]
        )
    assert called["http"] == 0


def test_visible_string_content_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDOCQA_FORMAL_EXECUTION", "1")
    monkeypatch.setenv("FINDOCQA_FORMAL_PROVIDER_ALLOWLIST", "organizer-api")
    visible = '{"answers":["A"],"reasoning":"具体规定支持A并排除其他选项，因此最终答案为A。"}'
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _FakeResponse(_payload(model="qwen3.7-plus", content=visible)),
    )
    result = _client(provider="organizer-api", model="qwen3.7-plus").chat(
        [{"role": "user", "content": "test"}]
    )
    assert result.content == visible


def test_visible_content_parts_only_join_explicit_visible_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDOCQA_FORMAL_EXECUTION", "1")
    monkeypatch.setenv("FINDOCQA_FORMAL_PROVIDER_ALLOWLIST", "organizer-api")
    parts = [
        {"type": "text", "text": '{"answers":["A"],'},
        {"type": "reasoning", "text": "hidden-part-must-not-leak"},
        {"type": "output_text", "text": '"reasoning":"条款明确支持A并排除其他项，因此最终答案为A。"}'},
    ]
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _FakeResponse(_payload(model="qwen3.7-plus", content=parts)),
    )
    result = _client(provider="organizer-api", model="qwen3.7-plus").chat(
        [{"role": "user", "content": "test"}]
    )
    assert "hidden-part-must-not-leak" not in result.content
    assert result.content.startswith('{"answers"')
    assert result.content.endswith("}")


def test_reasoning_content_never_replaces_empty_visible_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDOCQA_FORMAL_EXECUTION", "1")
    monkeypatch.setenv("FINDOCQA_FORMAL_PROVIDER_ALLOWLIST", "organizer-api")
    payload = _payload(
        model="qwen3.7-plus",
        content="",
        reasoning_content="hidden reasoning must stay hidden",
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _FakeResponse(payload))

    result = _client(provider="organizer-api", model="qwen3.7-plus").chat(
        [{"role": "user", "content": "test"}]
    )
    assert result.content == ""
    assert result.raw["choices"][0]["message"]["reasoning_content"]


def test_response_shape_audit_is_sanitized_and_counts_hidden_reasoning() -> None:
    hidden = "SECRET-THINKING-BODY"
    raw = _payload(model="qwen3.7-plus", content="", reasoning_content=hidden)
    audit = build_response_shape_audit(raw, provider="organizer-api", requested_model="qwen3.7-plus")

    assert audit["content_type"] == "string"
    assert audit["content_present"] is False
    assert audit["content_char_count"] == 0
    assert audit["content_part_count"] == 0
    assert audit["reasoning_content_present"] is True
    assert audit["reasoning_content_char_count"] == len(hidden)
    assert audit["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert hidden not in json.dumps(audit, ensure_ascii=False)


def test_extract_visible_content_fails_closed_for_non_visible_parts() -> None:
    assert extract_visible_message_content({"content": [{"type": "reasoning", "text": "hidden"}]}) == ""
    assert extract_visible_message_content({"content": {"text": "not-an-array-part"}}) == ""


def test_resolved_model_is_rechecked_after_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDOCQA_FORMAL_EXECUTION", "1")
    monkeypatch.setenv("FINDOCQA_FORMAL_PROVIDER_ALLOWLIST", "organizer-api")
    payload = _payload(model="qwen3.8-max-preview", content="{}")
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _FakeResponse(payload))

    with pytest.raises(FormalRouteError, match="not Qwen3.7/Qwen3.6/Qwen3.5"):
        _client(provider="organizer-api", model="qwen3.7-plus").chat(
            [{"role": "user", "content": "test"}]
        )
