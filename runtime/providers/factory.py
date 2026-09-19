"""Provider 工厂。

业务层通过 get_provider() 获取模型能力，不直接 new 具体类；
切换模型只改 .env 的 LLM_PROVIDER（§4.4：Provider 不含教学逻辑）。
"""
from __future__ import annotations

from config import settings

from .base import Provider
from .fake import FakeProvider


def get_provider(name: str = "", **overrides) -> Provider:
    """按名称返回 Provider。

    Args:
        name: deepseek | openai | qwen | fake，留空则读 settings.llm_provider
        overrides: 透传给 Provider 的覆盖参数（如测试注入 async_client）
    """
    provider = (name or settings.llm_provider).lower()

    if provider == "fake":
        return FakeProvider(**overrides)
    if provider == "deepseek":
        return _openai_compat(
            name="deepseek",
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            **overrides,
        )
    if provider == "openai":
        return _openai_compat(
            name="openai",
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            **overrides,
        )
    if provider == "qwen":
        return _openai_compat(
            name="qwen",
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            model=settings.qwen_model,
            **overrides,
        )
    raise ValueError(f"未知 LLM_PROVIDER: {provider!r}，支持 deepseek|openai|qwen|fake")


def _openai_compat(**kwargs) -> Provider:
    """延迟导入：只用 FakeProvider 的场景无需 openai SDK。"""
    from .openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(**kwargs)