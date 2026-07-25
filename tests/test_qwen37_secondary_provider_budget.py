from __future__ import annotations

import os

import pytest

from utils.llm_client import (
    LLMEndpoint,
    LLMProviderBudgetExhausted,
    OpenAICompatibleClient,
)


_PROVIDER_ENV_KEYS = (
    "FREETOKEN_API_KEY",
    "FREETOKEN_BASE_URL",
    "FREETOKEN_MODEL",
    "FREETOKEN_TOKEN_BUDGET",
    "FREETOKEN_USAGE_FILE",
    "QWEN37_API_KEY",
    "QWEN37_BASE_URL",
    "QWEN37_MODEL",
    "LLM_API_KEY",
    "DASHSCOPE_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL_ID",
    "MODELSCOPE_API_KEY",
    "SILICONFLOW_API_KEY",
)


def _clear_provider_env(monkeypatch):
    for key in _PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_freetoken_route_is_first_priority_even_if_retired_qwen37_env_exists(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("QWEN37_API_KEY", "retired-primary-key")
    monkeypatch.setenv("QWEN37_BASE_URL", "https://retired.example/v1")
    monkeypatch.setenv("QWEN37_MODEL", "retired-model")
    monkeypatch.setenv("FREETOKEN_API_KEY", "primary-key")
    monkeypatch.setenv("FREETOKEN_BASE_URL", "https://freetoken.example/v1")
    monkeypatch.setenv("FREETOKEN_MODEL", "qwen3.7-plus")
    monkeypatch.setenv("FREETOKEN_TOKEN_BUDGET", "1000000")
    monkeypatch.setenv("FREETOKEN_USAGE_FILE", str(tmp_path / "usage.json"))

    client = OpenAICompatibleClient.from_env({})

    assert client is not None
    assert [endpoint.provider for endpoint in client.endpoints] == ["freetoken-primary"]
    primary = client.endpoints[0]
    assert primary.base_url == "https://freetoken.example/v1"
    assert primary.model == "qwen3.7-plus"
    assert primary.token_budget == 1_000_000
    assert primary.stop_on_exhaustion is True


def test_retired_qwen37_env_alone_is_ignored(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("QWEN37_API_KEY", "retired-key")
    monkeypatch.setenv("QWEN37_BASE_URL", "https://retired.example/v1")
    monkeypatch.setenv("QWEN37_MODEL", "retired-model")

    client = OpenAICompatibleClient.from_env({})

    assert client is None


def test_primary_budget_exhaustion_stops_before_network_call():
    endpoint = LLMEndpoint(
        provider="freetoken-primary",
        api_key="key",
        base_url="https://freetoken.invalid/v1",
        model="qwen3.7-plus",
        token_budget=100,
        stop_on_exhaustion=True,
    )
    client = OpenAICompatibleClient(
        endpoint.api_key,
        endpoint.base_url,
        endpoint.model,
        endpoints=[endpoint],
    )
    client._endpoint_usage[endpoint.provider] = 100

    with pytest.raises(LLMProviderBudgetExhausted) as exc_info:
        client._chat_endpoint(endpoint, [{"role": "user", "content": "hello"}], 8)

    assert exc_info.value.used_tokens == 100
    assert exc_info.value.token_budget == 100


def test_persisted_freetoken_usage_is_loaded_across_client_restarts(tmp_path):
    usage_file = tmp_path / "usage.json"
    usage_file.write_text(
        '{"used_tokens": 750000, "token_budget": 1000000}\n',
        encoding="utf-8",
    )
    endpoint = LLMEndpoint(
        provider="freetoken-primary",
        api_key="key",
        base_url="https://freetoken.invalid/v1",
        model="qwen3.7-plus",
        token_budget=1_000_000,
        stop_on_exhaustion=True,
        usage_file=str(usage_file),
    )

    client = OpenAICompatibleClient(
        endpoint.api_key,
        endpoint.base_url,
        endpoint.model,
        endpoints=[endpoint],
    )

    assert client._endpoint_usage[endpoint.provider] == 750_000


def test_freetoken_http_403_insufficient_quota_is_hard_stop(monkeypatch):
    import io
    import urllib.error

    endpoint = LLMEndpoint(
        provider="freetoken-primary",
        api_key="key",
        base_url="https://freetoken.example/v1",
        model="qwen3.7-plus",
        token_budget=1_000_000,
        stop_on_exhaustion=True,
    )
    client = OpenAICompatibleClient(
        endpoint.api_key, endpoint.base_url, endpoint.model, endpoints=[endpoint]
    )

    error = urllib.error.HTTPError(
        url="https://freetoken.example/v1/chat/completions",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=io.BytesIO(b'{"error":{"code":"insufficient_quota"}}'),
    )

    def raise_quota(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", raise_quota)
    with pytest.raises(LLMProviderBudgetExhausted):
        client._chat_endpoint(endpoint, [{"role": "user", "content": "hello"}], 8)


def test_freetoken_http_403_auth_error_is_not_mislabeled_as_quota(monkeypatch):
    import io
    import urllib.error

    endpoint = LLMEndpoint(
        provider="freetoken-primary",
        api_key="bad-key",
        base_url="https://freetoken.example/v1",
        model="qwen3.7-plus",
        token_budget=1_000_000,
        stop_on_exhaustion=True,
    )
    client = OpenAICompatibleClient(
        endpoint.api_key, endpoint.base_url, endpoint.model, endpoints=[endpoint]
    )
    error = urllib.error.HTTPError(
        url="https://freetoken.example/v1/chat/completions",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=io.BytesIO(b'{"error":{"code":"invalid_api_key"}}'),
    )

    def raise_auth(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", raise_auth)
    from utils.llm_client import LLMClientUnavailable
    with pytest.raises(LLMClientUnavailable, match="HTTP 403"):
        client._chat_endpoint(endpoint, [{"role": "user", "content": "hello"}], 8)


def test_stdlib_env_fallback_loads_without_overriding_existing_values(monkeypatch, tmp_path):
    from utils.llm_client import _load_local_env_fallback

    env_file = tmp_path / ".env"
    env_file.write_text(
        chr(10).join([
            "FREETOKEN_API_KEY=local-key",
            "FREETOKEN_TOKEN_BUDGET=1000000",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.delenv("FREETOKEN_API_KEY", raising=False)
    monkeypatch.setenv("FREETOKEN_TOKEN_BUDGET", "777")

    assert _load_local_env_fallback(str(env_file)) is True
    assert os.environ["FREETOKEN_API_KEY"] == "local-key"
    assert os.environ["FREETOKEN_TOKEN_BUDGET"] == "777"
