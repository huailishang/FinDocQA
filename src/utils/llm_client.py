"""OpenAI-compatible LLM client with ordered multi-provider fallback."""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from evaluation.formal_submission import assert_formal_route, formal_execution_enabled

try:
    from runtime_safety import ProviderCallBudgetExceeded, begin_provider_attempt, finalize_provider_attempt
except Exception:  # pragma: no cover - safety hooks are optional outside repo runtime
    ProviderCallBudgetExceeded = RuntimeError
    begin_provider_attempt = None
    finalize_provider_attempt = None


def _write_resolved_runtime_config(endpoints: Sequence["LLMEndpoint"], timeout_s: int, temperature: float) -> None:
    path_raw = os.getenv("LLM_RESOLVED_CONFIG_PATH", "").strip()
    if not path_raw:
        return
    path = Path(path_raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timeout_s": timeout_s,
        "temperature": temperature,
        "endpoint_count": len(endpoints),
        "endpoints": [
            {
                "provider": endpoint.provider,
                "base_url": endpoint.base_url,
                "model": endpoint.model,
                "token_budget": endpoint.token_budget,
                "stop_on_exhaustion": endpoint.stop_on_exhaustion,
            }
            for endpoint in endpoints
        ],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _ledger_path_from_env() -> Optional[Path]:
    raw = os.getenv("LLM_TOKEN_LEDGER_PATH", "").strip()
    if not raw or os.getenv("SAFE_RUN_EXECUTION", "").strip() != "1":
        return None
    return Path(raw)

def _load_local_env_fallback(path: str = ".env") -> bool:
    """Load simple KEY=VALUE pairs when python-dotenv is unavailable."""
    env_path = Path(path)
    if not env_path.exists():
        return False
    loaded = False
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value
        loaded = True
    return loaded


try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    _load_local_env_fallback()


@dataclass(frozen=True)
class ChatUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    finish_reason: str = ""
    usage: ChatUsage = ChatUsage()
    raw: Dict[str, Any] | None = None
    provider: str = ""


@dataclass(frozen=True)
class LLMEndpoint:
    provider: str
    api_key: str
    base_url: str
    model: str
    token_budget: int = 0
    stop_on_exhaustion: bool = False
    usage_file: str = ""


def extract_visible_message_content(message: Any) -> str:
    """Return only submission-visible text from OpenAI-compatible message.content.

    Hidden reasoning fields are intentionally ignored. Array content is accepted
    only for explicit visible text parts; unknown/proprietary part types fail
    closed by contributing no text.
    """

    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    visible_parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").strip().lower()
        if part_type not in {"text", "output_text"}:
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            visible_parts.append(text)
    return "".join(visible_parts)


def _hidden_char_count(value: Any) -> int:
    """Count hidden reasoning characters without returning/persisting its body."""

    if isinstance(value, str):
        return len(value)
    if value is None:
        return 0
    if isinstance(value, list):
        total = 0
        for part in value:
            if isinstance(part, str):
                total += len(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    total += len(text)
        return total
    return 0


def build_response_shape_audit(
    raw: Any, *, provider: str, requested_model: str
) -> dict[str, Any]:
    """Build a sanitized provider response-shape record for visible-output audit."""

    payload = raw if isinstance(raw, dict) else {}
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    if not isinstance(message, dict):
        message = {}

    content = message.get("content")
    visible = extract_visible_message_content(message)
    if isinstance(content, str):
        content_type = "string"
        part_count = 0
    elif isinstance(content, list):
        content_type = "array"
        part_count = len(content)
    elif content is None:
        content_type = "null"
        part_count = 0
    else:
        content_type = type(content).__name__
        part_count = 0

    reasoning_content = message.get("reasoning_content")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    usage_safe = {
        key: int(usage.get(key, 0) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    return {
        "provider": str(provider or ""),
        "requested_model": str(requested_model or ""),
        "resolved_model": str(payload.get("model") or requested_model or ""),
        "top_level_keys": sorted(str(key) for key in payload.keys()),
        "choice_keys": sorted(str(key) for key in choice.keys()),
        "message_keys": sorted(str(key) for key in message.keys()),
        "content_type": content_type,
        "content_present": bool(visible),
        "content_char_count": len(visible),
        "content_part_count": int(part_count),
        "reasoning_content_present": "reasoning_content" in message and reasoning_content is not None,
        "reasoning_content_char_count": _hidden_char_count(reasoning_content),
        "finish_reason": str(choice.get("finish_reason") or ""),
        "usage": usage_safe,
    }


def build_request_parameter_audit(
    *,
    enable_thinking: bool,
    max_tokens: int,
    max_completion_tokens: int | None,
    resolved_timeout_s: int,
) -> dict[str, Any]:
    """Return a sanitized audit record for the exact output-control request."""

    return {
        "requested_enable_thinking": bool(enable_thinking),
        "requested_max_tokens": int(max_tokens),
        "requested_max_completion_tokens": (
            None if max_completion_tokens is None else int(max_completion_tokens)
        ),
        "resolved_timeout_s": int(resolved_timeout_s),
    }


def classify_provider_output_guard(
    *,
    request_audit: dict[str, Any],
    response_shape_audit: dict[str, Any],
    wall_seconds: float,
    latency_ms: float = 0.0,
    formal_output_valid: bool = True,
    provider_capability_failure: bool = False,
    provider_error: bool = False,
) -> dict[str, Any]:
    """Classify thinking leakage and completion-cap compliance without hidden text."""

    usage = dict(response_shape_audit.get("usage") or {})
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    cap_raw = request_audit.get("requested_max_completion_tokens")
    cap = None if cap_raw is None else int(cap_raw)
    reasoning_present = bool(response_shape_audit.get("reasoning_content_present"))
    requested_enable_thinking = bool(request_audit.get("requested_enable_thinking"))
    thinking_leak = (not requested_enable_thinking) and reasoning_present
    cap_respected = (
        None
        if cap is None or provider_error or provider_capability_failure
        else completion_tokens <= cap
    )

    if provider_capability_failure:
        status = "FAIL_PROVIDER_CAPABILITY"
    elif provider_error:
        status = "FAIL_PROVIDER_ERROR"
    elif cap is None:
        status = "FAIL_COMPLETION_CAP_NOT_REQUESTED"
    elif cap_respected is False:
        status = "FAIL_COMPLETION_CAP_EXCEEDED"
    elif not formal_output_valid:
        status = "FAIL_FORMAL_OUTPUT_INVALID"
    elif thinking_leak:
        status = "PASS_THINKING_PRESENT_BUT_BOUNDED"
    else:
        status = "PASS_NON_THINKING"

    return {
        **request_audit,
        "completion_cap_requested": cap,
        "completion_cap_respected": cap_respected,
        "provider_thinking_leak": thinking_leak,
        "visible_content_char_count": int(response_shape_audit.get("content_char_count", 0) or 0),
        "reasoning_content_present": reasoning_present,
        "reasoning_content_char_count": int(response_shape_audit.get("reasoning_content_char_count", 0) or 0),
        "completion_tokens": completion_tokens,
        "finish_reason": str(response_shape_audit.get("finish_reason") or ""),
        "wall_seconds": round(float(wall_seconds), 3),
        "latency_ms": round(float(latency_ms), 3),
        "output_guard_status": status,
    }


class LLMClientUnavailable(RuntimeError):
    pass


class LLMProviderCapabilityError(LLMClientUnavailable):
    """Raised when a provider explicitly rejects a requested runtime capability."""


class LLMProviderBudgetExhausted(RuntimeError):
    """Raised when a hard-stop provider reaches its configured token budget."""

    def __init__(self, provider: str, model: str, used_tokens: int, token_budget: int) -> None:
        self.provider = provider
        self.model = model
        self.used_tokens = used_tokens
        self.token_budget = token_budget
        super().__init__(
            f"provider token budget exhausted: {provider}/{model} "
            f"used={used_tokens} budget={token_budget}"
        )


class OpenAICompatibleClient:
    """Minimal standard-library client that tries endpoints in order."""

    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout_s: int = 120, temperature: float = 0.0,
                 endpoints: Optional[Sequence[LLMEndpoint]] = None) -> None:
        self.endpoints = list(endpoints or [LLMEndpoint("primary", api_key, base_url.rstrip("/"), model)])
        self.api_key = self.endpoints[0].api_key
        self.base_url = self.endpoints[0].base_url.rstrip("/")
        self.model = self.endpoints[0].model
        self.timeout_s = timeout_s
        self.temperature = temperature
        self._endpoint_usage: dict[str, int] = {endpoint.provider: 0 for endpoint in self.endpoints}
        for endpoint in self.endpoints:
            if endpoint.usage_file:
                self._endpoint_usage[endpoint.provider] = self._load_persisted_usage(endpoint)
        _write_resolved_runtime_config(self.endpoints, self.timeout_s, self.temperature)

    @classmethod
    def from_env(cls, config: Optional[Dict[str, Any]] = None) -> Optional["OpenAICompatibleClient"]:
        config = config or {}
        model_cfg = config.get("model", {}) if isinstance(config.get("model", {}), dict) else {}
        timeout_s = int(os.getenv("LLM_TIMEOUT_SECONDS") or model_cfg.get("timeout_s", 180))
        temperature = float(model_cfg.get("temperature", 0.0))
        endpoints: list[LLMEndpoint] = []

        freetoken_key = os.getenv("FREETOKEN_API_KEY")
        if freetoken_key:
            endpoints.append(LLMEndpoint(
                "freetoken-primary",
                freetoken_key,
                os.getenv("FREETOKEN_BASE_URL", "https://freetokenfaucet.com/v1").rstrip("/"),
                os.getenv("FREETOKEN_MODEL", "qwen3.7-plus"),
                token_budget=max(0, int(os.getenv("FREETOKEN_TOKEN_BUDGET", "1000000"))),
                stop_on_exhaustion=True,
                usage_file=os.getenv(
                    "FREETOKEN_USAGE_FILE",
                    "output/freetoken_qwen37_usage.json",
                ),
            ))
            first = endpoints[0]
            return cls(first.api_key, first.base_url, first.model,
                       timeout_s=timeout_s, temperature=temperature, endpoints=endpoints)

        # Prefer the configured primary model only when the explicit FREETOKEN
        # first-priority route is absent. QWEN37_* environment variables are
        # intentionally ignored; that retired direct route should not preempt
        # FREETOKEN or silently change provider order.
        primary_key = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        primary_url = os.getenv("LLM_BASE_URL") or model_cfg.get("base_url") or model_cfg.get("api_base")
        primary_model = os.getenv("LLM_MODEL_ID") or model_cfg.get("id") or model_cfg.get("name")
        if primary_key and primary_url and primary_model:
            endpoints.append(LLMEndpoint(
                "configured-primary",
                primary_key,
                str(primary_url).rstrip("/"),
                str(primary_model),
            ))

        ms_key = os.getenv("MODELSCOPE_API_KEY")
        if ms_key:
            ms_url = os.getenv("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1").rstrip("/")
            models = [
                os.getenv("MODELSCOPE_MODEL_1", "Qwen/Qwen3.5-397B-A17B"),
                os.getenv("MODELSCOPE_MODEL_2", "Qwen/Qwen3.5-122B-A10B"),
                os.getenv("MODELSCOPE_MODEL_3", "Qwen/Qwen3.5-35B-A3B"),
            ]
            endpoints.extend(LLMEndpoint(f"modelscope-{i}", ms_key, ms_url, model)
                             for i, model in enumerate(models, 1) if model)

        sf_key = os.getenv("SILICONFLOW_API_KEY")
        if sf_key:
            endpoints.append(LLMEndpoint(
                "siliconflow",
                sf_key,
                os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/"),
                os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen3.5-35B-A3B"),
            ))

        if not endpoints:
            api_key = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
            base_url = os.getenv("LLM_BASE_URL") or model_cfg.get("base_url") or model_cfg.get("api_base")
            model = os.getenv("LLM_MODEL_ID") or model_cfg.get("id") or model_cfg.get("name")
            if not api_key or not base_url or not model:
                return None
            endpoints.append(LLMEndpoint("legacy-primary", api_key, str(base_url).rstrip("/"), str(model)))

        first = endpoints[0]
        return cls(first.api_key, first.base_url, first.model,
                   timeout_s=timeout_s, temperature=temperature, endpoints=endpoints)

    def _load_persisted_usage(self, endpoint: LLMEndpoint) -> int:
        path = Path(endpoint.usage_file)
        if not path.exists():
            return 0
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return max(0, int(payload.get("used_tokens", 0) or 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    def _persist_endpoint_usage(self, endpoint: LLMEndpoint) -> None:
        if not endpoint.usage_file:
            return
        path = Path(endpoint.usage_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        payload = {
            "provider": endpoint.provider,
            "model": endpoint.model,
            "used_tokens": self._endpoint_usage.get(endpoint.provider, 0),
            "token_budget": endpoint.token_budget,
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        temporary.replace(path)

    def _chat_endpoint(
        self,
        endpoint: LLMEndpoint,
        messages: Sequence[Dict[str, str]],
        max_tokens: int,
        *,
        max_completion_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> ChatResult:
        if formal_execution_enabled():
            assert_formal_route(provider=endpoint.provider, model=endpoint.model)
        used_tokens = self._endpoint_usage.get(endpoint.provider, 0)
        if endpoint.token_budget and used_tokens >= endpoint.token_budget:
            raise LLMProviderBudgetExhausted(
                endpoint.provider, endpoint.model, used_tokens, endpoint.token_budget
            )
        url = f"{endpoint.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": endpoint.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "max_tokens": int(max_tokens),
            "enable_thinking": bool(enable_thinking),
        }
        if max_completion_tokens is not None:
            payload["max_completion_tokens"] = int(max_completion_tokens)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {endpoint.api_key}", "Content-Type": "application/json", "User-Agent": os.getenv("FREETOKEN_USER_AGENT", "Mozilla/5.0")},
        )
        ledger_path = _ledger_path_from_env()
        attempt_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        if ledger_path is not None and begin_provider_attempt is not None:
            begin_provider_attempt(
                path=ledger_path,
                attempt_id=attempt_id,
                provider=endpoint.provider,
                model=endpoint.model,
            )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if ledger_path is not None and finalize_provider_attempt is not None:
                finalize_provider_attempt(path=ledger_path, attempt_id=attempt_id, final_status="ERROR")
            quota_markers = (
                "insufficient_quota",
                "quota exhausted",
                "quota_exhausted",
                "free quota has been exhausted",
                "token budget exhausted",
            )
            quota_exhausted = (
                exc.code in {402, 429}
                or (exc.code == 403 and any(marker in detail.lower() for marker in quota_markers))
            )
            detail_lower = detail.lower()
            capability_markers = (
                "unsupported", "not supported", "unknown", "unrecognized",
                "not allowed", "not permitted", "unexpected", "extra_forbidden",
                "extra inputs",
            )
            explicit_completion_cap_rejection = bool(
                max_completion_tokens is not None
                and exc.code in {400, 422}
                and "max_completion_tokens" in detail_lower
                and any(marker in detail_lower for marker in capability_markers)
            )
            if explicit_completion_cap_rejection:
                raise LLMProviderCapabilityError(
                    f"provider rejected max_completion_tokens capability: HTTP {exc.code}"
                ) from exc
            if endpoint.stop_on_exhaustion and quota_exhausted:
                raise LLMProviderBudgetExhausted(
                    endpoint.provider, endpoint.model,
                    self._endpoint_usage.get(endpoint.provider, 0), endpoint.token_budget
                ) from exc
            raise LLMClientUnavailable(f"HTTP {exc.code}: {detail[:500]}") from exc
        except TimeoutError as exc:
            if ledger_path is not None and finalize_provider_attempt is not None:
                finalize_provider_attempt(path=ledger_path, attempt_id=attempt_id, final_status="TIMEOUT")
            raise LLMClientUnavailable(f"connection error: {exc}") from exc
        except urllib.error.URLError as exc:
            if ledger_path is not None and finalize_provider_attempt is not None:
                finalize_provider_attempt(path=ledger_path, attempt_id=attempt_id, final_status="ERROR")
            raise LLMClientUnavailable(f"connection error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000
        try:
            raw = json.loads(body)
        except json.JSONDecodeError as exc:
            if ledger_path is not None and finalize_provider_attempt is not None:
                finalize_provider_attempt(path=ledger_path, attempt_id=attempt_id, final_status="ERROR")
            raise LLMClientUnavailable(f"invalid JSON response: {body[:500]}") from exc
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            if ledger_path is not None and finalize_provider_attempt is not None:
                finalize_provider_attempt(path=ledger_path, attempt_id=attempt_id, final_status="ERROR")
            raise LLMClientUnavailable(f"invalid completion response: choices={choices!r}")
        choice = choices[0]
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            if ledger_path is not None and finalize_provider_attempt is not None:
                finalize_provider_attempt(path=ledger_path, attempt_id=attempt_id, final_status="ERROR")
            raise LLMClientUnavailable(f"invalid completion response: message={message!r}")
        usage_raw = raw.get("usage", {}) or {}
        resolved_model = str(raw.get("model") or endpoint.model)
        total_tokens = int(usage_raw.get("total_tokens", 0) or 0)
        self._endpoint_usage[endpoint.provider] = (
            self._endpoint_usage.get(endpoint.provider, 0) + total_tokens
        )
        self._persist_endpoint_usage(endpoint)
        if ledger_path is not None and finalize_provider_attempt is not None:
            finalize_provider_attempt(
                path=ledger_path,
                attempt_id=attempt_id,
                final_status="COMPLETED",
                provider_request_id=str(raw.get("id") or ""),
                resolved_model=resolved_model,
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
                total_tokens=total_tokens,
            )
        if formal_execution_enabled():
            assert_formal_route(provider=endpoint.provider, model=resolved_model)
        return ChatResult(
            content=extract_visible_message_content(message),
            model=resolved_model,
            finish_reason=str(choice.get("finish_reason") or ""),
            usage=ChatUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
                total_tokens=total_tokens,
                latency_ms=latency_ms,
            ),
            raw=raw,
            provider=endpoint.provider,
        )

    def chat(
        self,
        messages: Sequence[Dict[str, str]],
        max_tokens: int = 256,
        *,
        max_completion_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> ChatResult:
        errors = []
        for index, endpoint in enumerate(self.endpoints):
            try:
                return self._chat_endpoint(
                    endpoint,
                    messages,
                    max_tokens,
                    max_completion_tokens=max_completion_tokens,
                    enable_thinking=enable_thinking,
                )
            except LLMProviderCapabilityError:
                raise
            except LLMClientUnavailable as exc:
                errors.append(f"{endpoint.provider}/{endpoint.model}: {exc}")
                if index < len(self.endpoints) - 1:
                    nxt = self.endpoints[index + 1]
                    print(f"[llm_client] {endpoint.provider}/{endpoint.model} failed; "
                          f"trying {nxt.provider}/{nxt.model}", file=sys.stderr)
        raise LLMClientUnavailable("all LLM endpoints failed: " + " | ".join(errors))


def build_fallback_client(config: Dict[str, Any]) -> Optional[OpenAICompatibleClient]:
    """Legacy second client; new multi-endpoint env already contains all fallbacks."""
    if os.getenv("MODELSCOPE_API_KEY") or os.getenv("SILICONFLOW_API_KEY"):
        return None
    fb = config.get("model", {}).get("fallback_model")
    if not fb:
        return None
    api_key = os.getenv(fb.get("api_key_env", "LLM_API_KEY2"))
    if not api_key:
        return None
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=str(os.getenv("LLM_BASE_URL2") or fb.get("base_url", "")),
        model=str(os.getenv("LLM_MODEL_ID2") or fb.get("name", "")),
        timeout_s=int(fb.get("timeout_s", 120)),
        temperature=float(fb.get("temperature", 0.0)),
    )


def chat_with_fallback(primary: Optional[OpenAICompatibleClient],
                       fallback: Optional[OpenAICompatibleClient],
                       messages: Sequence[Dict[str, str]], max_tokens: int = 256) -> ChatResult:
    first = primary or fallback
    if first is None:
        raise LLMClientUnavailable("No LLM client configured.")
    try:
        return first.chat(messages, max_tokens=max_tokens)
    except LLMClientUnavailable:
        if fallback is None or fallback is first:
            raise
        print(f"[llm_client] primary chain failed; trying legacy fallback ({fallback.model})", file=sys.stderr)
        return fallback.chat(messages, max_tokens=max_tokens)
