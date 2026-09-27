"""Shared directory of OpenAI-compatible provider presets."""

from __future__ import annotations

from typing import Any


PROVIDERS: tuple[dict[str, Any], ...] = (
    {"id": "deepseek", "name": "DeepSeek", "name_en": "DeepSeek", "aliases": ["深度求索", "deep seek"], "base_url": "https://api.deepseek.com", "protocol": "openai_compatible", "key_required": True},
    {"id": "qwen", "name": "通义百炼", "name_en": "Alibaba Bailian / Qwen", "aliases": ["阿里云", "百炼", "通义千问", "qwen", "dashscope"], "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "protocol": "openai_compatible", "key_required": True},
    {"id": "glm", "name": "智谱 GLM", "name_en": "Zhipu GLM", "aliases": ["智谱", "bigmodel", "zhipu", "glm"], "base_url": "https://open.bigmodel.cn/api/paas/v4", "protocol": "openai_compatible", "key_required": True},
    {"id": "kimi", "name": "月之暗面 Kimi", "name_en": "Moonshot Kimi", "aliases": ["月之暗面", "moonshot", "kimi"], "base_url": "https://api.moonshot.cn/v1", "protocol": "openai_compatible", "key_required": True},
    {"id": "volcengine", "name": "火山方舟 / 豆包", "name_en": "Volcengine Ark / Doubao", "aliases": ["火山引擎", "豆包", "ark", "volcengine", "doubao"], "base_url": "https://ark.cn-beijing.volces.com/api/v3", "protocol": "openai_compatible", "key_required": True},
    {"id": "siliconflow", "name": "硅基流动", "name_en": "SiliconFlow", "aliases": ["硅基", "silicon flow", "siliconflow"], "base_url": "https://api.siliconflow.cn/v1", "protocol": "openai_compatible", "key_required": True},
    {"id": "openrouter", "name": "OpenRouter", "name_en": "OpenRouter", "aliases": ["open router", "路由"], "base_url": "https://openrouter.ai/api/v1", "protocol": "openai_compatible", "key_required": True},
    {"id": "ollama", "name": "Ollama（本机）", "name_en": "Ollama", "aliases": ["本地模型", "ollama local"], "base_url": "http://127.0.0.1:11434/v1", "protocol": "local", "key_required": False},
    {"id": "custom", "name": "自定义 OpenAI 兼容接口", "name_en": "Custom OpenAI-compatible", "aliases": ["自定义", "openai compatible", "兼容接口"], "base_url": "", "protocol": "openai_compatible", "key_required": True},
)


def search_providers(query: str = "") -> list[dict[str, Any]]:
    needle = query.strip().casefold()
    rows = []
    for item in PROVIDERS:
        haystack = " ".join([str(item["id"]), str(item["name"]), str(item["name_en"]), *item["aliases"]]).casefold()
        if needle and needle not in haystack:
            continue
        rows.append(dict(item))
    return rows


def get_provider(provider_id: str) -> dict[str, Any] | None:
    return next((dict(item) for item in PROVIDERS if item["id"] == provider_id), None)
