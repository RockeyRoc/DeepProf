"""本地模型 Adapter：Ollama / 本地 OpenAI-compatible Server。

本地模型不要求凭据，沿用同一兼容协议，因此直接继承兼容适配器并改写默认项。
"""

from __future__ import annotations

from typing import Any

from runtime.providers.openai_compatible import OpenAICompatibleProvider
from runtime.providers.profiles import PROTOCOL_LOCAL, ProviderProfile

DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:11434/v1"


class LocalProvider(OpenAICompatibleProvider):
    """本地 OpenAI-compatible Server（默认 Ollama 端口）。"""

    protocol = PROTOCOL_LOCAL

    def __init__(self, profile: ProviderProfile, secret_store: Any = None, **kwargs: Any) -> None:
        profile = _with_local_defaults(profile)
        super().__init__(profile, secret_store or _NoSecret(), **kwargs)

    def _headers(self, *, require_key: bool = True) -> dict[str, str]:
        return super()._headers(require_key=False)


class _NoSecret:
    def get(self, ref: str) -> str | None:
        return None

    def set(self, ref: str, value: str) -> None:  # pragma: no cover - 本地模型无密钥
        raise NotImplementedError("local provider 不支持写入密钥")

    def delete(self, ref: str) -> None:  # pragma: no cover
        return None


def _with_local_defaults(profile: ProviderProfile) -> ProviderProfile:
    if not profile.base_url:
        profile.base_url = DEFAULT_LOCAL_BASE_URL
    if not profile.api_mode:
        profile.api_mode = "auto"
    return profile