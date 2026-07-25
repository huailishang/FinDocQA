from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from openai import OpenAI

from utils.freetoken_model_profiles import FreetokenModelProfile, resolve_freetoken_profile


def load_dotenv_if_present(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def create_profile_client(profile_name: str, *, usage_dir: Path | None = None) -> tuple[FreetokenModelProfile, OpenAI]:
    profile = resolve_freetoken_profile(profile_name, usage_dir=usage_dir)
    key_name = "FREETOKEN_" + "API" + "_" + "KEY"
    base_name = "FREETOKEN_" + "BASE" + "_" + "URL"
    token = os.environ.get(key_name, "").strip()
    base_url = os.environ.get(base_name, "").strip()
    if not token or not base_url:
        raise RuntimeError("shared FREETOKEN credentials are missing from the environment")
    return profile, OpenAI(base_url=base_url, api_key=token)


def chat_completion(profile_name: str, messages: Sequence[dict[str, str]], *, usage_dir: Path | None = None, max_tokens: int = 700, temperature: float = 0.0) -> dict[str, Any]:
    profile, client = create_profile_client(profile_name, usage_dir=usage_dir)
    completion = client.chat.completions.create(
        model=profile.model,
        messages=list(messages),
        max_tokens=max_tokens,
        temperature=temperature,
    )
    choice = completion.choices[0]
    usage = completion.usage
    return {
        "profile": profile.name,
        "requested_model": profile.model,
        "resolved_model": completion.model,
        "content": choice.message.content or "",
        "finish_reason": choice.finish_reason,
        "usage": {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        },
    }
