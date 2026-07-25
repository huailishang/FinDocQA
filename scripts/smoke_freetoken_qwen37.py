from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.llm_client import LLMEndpoint, OpenAICompatibleClient


def load_local_env(path: Path) -> None:
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


def main() -> int:
    load_local_env(ROOT / ".env")
    key = os.getenv("FREETOKEN_API_KEY", "").strip()
    if not key:
        print(json.dumps({
            "status": "blocked",
            "reason": "FREETOKEN_API_KEY_missing",
            "credential_exposed": False,
        }, ensure_ascii=False))
        return 2

    endpoint = LLMEndpoint(
        provider="freetoken-secondary",
        api_key=key,
        base_url=os.getenv("FREETOKEN_BASE_URL", "https://freetokenfaucet.com/v1").rstrip("/"),
        model=os.getenv("FREETOKEN_MODEL", "qwen3.7-plus"),
        token_budget=max(0, int(os.getenv("FREETOKEN_TOKEN_BUDGET", "1000000"))),
        stop_on_exhaustion=True,
        usage_file=os.getenv("FREETOKEN_USAGE_FILE", "output/freetoken_qwen37_usage.json"),
    )
    client = OpenAICompatibleClient(
        endpoint.api_key,
        endpoint.base_url,
        endpoint.model,
        timeout_s=int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        temperature=0.0,
        endpoints=[endpoint],
    )
    result = client.chat(
        [{"role": "user", "content": "只回复OK"}],
        max_tokens=8,
    )
    payload = {
        "status": "pass" if result.content.strip() else "fail",
        "provider": result.provider,
        "model": result.model,
        "finish_reason": result.finish_reason,
        "non_empty_content": bool(result.content.strip()),
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
        "total_tokens": result.usage.total_tokens,
        "credential_exposed": False,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
