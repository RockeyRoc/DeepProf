"""Provider-specific reasoning controls derived from official API contracts.

Unknown models deliberately stay unknown: a provider family name alone is not
enough evidence that every model accepts the same non-standard parameters.
"""
from __future__ import annotations

from typing import Any


DEEPSEEK_DOC = "https://api-docs.deepseek.com/api/create-chat-completion/"
BAILIAN_DOC = "https://help.aliyun.com/zh/model-studio/deep-thinking"
SILICONFLOW_DOC = "https://docs.siliconflow.cn/docs/api/chat-completions-post"
OLLAMA_DOC = "https://docs.ollama.com/capabilities/thinking"
OPENROUTER_DOC = "https://openrouter.ai/docs/guides/best-practices/reasoning-tokens"
GLM_DOC = "https://docs.z.ai/guides/capabilities/thinking"
KIMI_DOC = "https://platform.kimi.ai/docs/guide/use-thinking-models"
ARK_DOC = "https://docs.volcengine.com/docs/ark/chat-api?lang=zh"
CHECKED_ON = "2026-09-30"


def model_options(vendor_id: str, model: str) -> dict[str, Any]:
    vendor = vendor_id.casefold()
    name = model.casefold()
    unknown: dict[str, Any] = {"reasoning_mode": "unknown", "reasoning": None,
                               "checked_on": CHECKED_ON}

    if vendor == "ollama":
        return {**unknown, "options_source": "ollama_api_show", "official_docs": OLLAMA_DOC}

    if vendor == "deepseek":
        if "deepseek-v4" in name or name in {"deepseek-flash", "deepseek-v4-pro"}:
            return {"reasoning_mode": "toggle", "reasoning": True,
                    "thinking_levels": ["none", "low", "high", "max"],
                    "thinking_enabled_default": True,
                    "thinking_default": "high", "thinking_parameter": "reasoning_effort",
                    "official_docs": DEEPSEEK_DOC, "checked_on": CHECKED_ON}
        if "reasoner" in name or "deepseek-r1" in name:
            return {"reasoning_mode": "always", "reasoning": True, "official_docs": DEEPSEEK_DOC,
                    "checked_on": CHECKED_ON}
        return {**unknown, "official_docs": DEEPSEEK_DOC}

    if vendor == "qwen" and "glm-5.2" in name:
        # Bailian's GLM-5.2 is a distinct third-party model profile: Responses
        # supports reasoning.effort (high/max), while Chat Completions search
        # is not supported for this model.
        return {"reasoning_mode": "toggle", "reasoning": True,
                "thinking_parameter": "reasoning.effort", "thinking_levels": ["high", "max"],
                "thinking_default": "high", "api_mode": "responses",
                "official_docs": "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses",
                "checked_on": CHECKED_ON}
    if vendor == "qwen" and "kimi-k3" in name:
        # Kimi-K3 on Bailian uses Responses; only the max effort is confirmed
        # for this model family. It does not accept thinking_budget.
        return {"reasoning_mode": "toggle", "reasoning": True,
                "thinking_parameter": "reasoning.effort", "thinking_levels": ["max"],
                "thinking_default": "max", "api_mode": "responses",
                "official_docs": "https://help.aliyun.com/zh/model-studio/kimi-api",
                "checked_on": CHECKED_ON}
    if vendor == "qwen" and any(tag in name for tag in ("qwen3.8", "qwen3.7", "qwen3.6", "qwen3.5")):
        if "omni" in name:
            return {**unknown, "official_docs": BAILIAN_DOC}
        return {"reasoning_mode": "toggle", "reasoning": True,
                "thinking_parameter": "enable_thinking",
                "thinking_budget_parameter": "thinking_budget",
                "thinking_budget_min": 128, "thinking_budget_max": 32768,
                "official_docs": BAILIAN_DOC, "checked_on": CHECKED_ON}
    if vendor == "qwen" and any(tag in name for tag in ("-thinking", "qwq", "qwen3-next")):
        return {"reasoning_mode": "always", "reasoning": True, "official_docs": BAILIAN_DOC,
                "checked_on": CHECKED_ON}

    if vendor == "siliconflow":
        if "glm-5.2" in name or "deepseek-v4" in name:
            return {"reasoning_mode": "toggle", "reasoning": True,
                    "thinking_parameter": "enable_thinking", "reasoning_parameter": "reasoning_effort",
                    "thinking_levels": ["none", "low", "high", "max"],
                    "thinking_budget_parameter": "thinking_budget",
                    "thinking_budget_min": 128, "thinking_budget_max": 32768,
                    "official_docs": SILICONFLOW_DOC, "checked_on": CHECKED_ON}
        if any(tag in name for tag in ("deepseek-r1", "qwq", "reasoner", "thinking")):
            return {"reasoning_mode": "always", "reasoning": True,
                    "thinking_budget_parameter": "thinking_budget",
                    "thinking_budget_min": 128, "thinking_budget_max": 32768,
                    "official_docs": SILICONFLOW_DOC, "checked_on": CHECKED_ON}

    if vendor == "glm":
        if "glm-5.3" in name:
            return {"reasoning_mode": "always", "reasoning": True,
                    "thinking_parameter": "thinking.type", "reasoning_parameter": "reasoning_effort",
                    "thinking_levels": ["low", "high", "max"], "thinking_default": "max",
                    "official_docs": GLM_DOC, "checked_on": CHECKED_ON}
        if "glm-5.2" in name:
            return {"reasoning_mode": "toggle", "reasoning": True,
                    "thinking_parameter": "thinking.type", "reasoning_parameter": "reasoning_effort",
                    "thinking_levels": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
                    "thinking_default": "max", "official_docs": GLM_DOC, "checked_on": CHECKED_ON}
        if any(tag in name for tag in ("glm-5.1", "glm-5", "glm-4.7", "glm-4.5")):
            return {"reasoning_mode": "toggle", "reasoning": True,
                    "thinking_parameter": "thinking.type", "official_docs": GLM_DOC,
                    "checked_on": CHECKED_ON}

    if vendor == "kimi":
        if "kimi-k3" in name:
            return {"reasoning_mode": "always", "reasoning": True,
                    "thinking_parameter": "reasoning_effort", "thinking_levels": ["max"],
                    "thinking_default": "max",
                    "official_docs": "https://help.aliyun.com/zh/model-studio/kimi-api-by-moonshot-ai",
                    "checked_on": CHECKED_ON}
        if "kimi-k2.7-code" in name:
            return {"reasoning_mode": "always", "reasoning": True, "official_docs": KIMI_DOC,
                    "checked_on": CHECKED_ON}
        if "kimi-k2.6" in name:
            return {"reasoning_mode": "toggle", "reasoning": True,
                    "thinking_parameter": "thinking.type", "official_docs": KIMI_DOC,
                    "checked_on": CHECKED_ON}

    if vendor == "volcengine":
        # Ark's Chat Completions reference names concrete model versions for
        # effort control; generic Doubao IDs remain unconfirmed.
        if any(tag in name for tag in ("doubao-seed-2-0-lite-260428", "doubao-seed-2-1-pro-260628",
                                       "deepseek-v4-pro", "deepseek-v4-flash")):
            return {"reasoning_mode": "toggle", "reasoning": True,
                    "thinking_parameter": "thinking.type",
                    "reasoning_parameter": "reasoning.effort" if "doubao-seed-2-1-pro-260628" in name else "reasoning_effort",
                    "thinking_levels": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
                    "thinking_default": "high",
                    "api_mode": "responses" if "doubao-seed-2-1-pro-260628" in name else "auto",
                    "official_docs": ARK_DOC, "checked_on": CHECKED_ON}

    # OpenRouter and custom endpoints can route arbitrary model versions. Only
    # use levels supplied by a provider's model catalog and saved per-model.
    if vendor == "openrouter":
        return {**unknown, "options_source": "provider_model_metadata", "official_docs": OPENROUTER_DOC,
                "checked_on": CHECKED_ON}
    return unknown


def resolve_model_options(vendor_id: str, model: str,
                          saved: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh stale curated mappings while preserving explicit local overrides."""
    current = dict(saved or {})
    documented = model_options(vendor_id, model)
    if documented.get("reasoning_mode") not in {None, "unknown"}:
        checked_on = str(current.get("checked_on") or "")
        if not current or (checked_on and checked_on < str(documented.get("checked_on") or "")):
            current.update(documented)
    elif not current:
        current.update(documented)
    return current
