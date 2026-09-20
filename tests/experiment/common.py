"""实验公共设施：配置读取、密钥脱敏、时间戳。

安全约定（DESIGN §12 / §13）：
- 密钥只从 config.settings（.env）读取，绝不硬编码；
- 任何日志、CSV、报告里只出现 mask() 后的密钥；
- 实验不打印学生真实个人信息，learner_id 用实验编号。
"""
from __future__ import annotations

import time

from config import settings


def has_real_key() -> bool:
    """配置是否已填真实密钥（非空且非占位符）。"""
    key = settings.deepseek_api_key
    return bool(key) and "{{" not in key and key.strip() != ""


def mask(key: str) -> str:
    """脱敏显示：sk-d19***f845（只留首尾，不泄露完整密钥）。"""
    if not key:
        return "<未配置>"
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}***{key[-4:]}"


def provider_info() -> dict[str, str]:
    """当前 provider 概况（不含明文密钥）。"""
    return {
        "provider": settings.llm_provider,
        "model": settings.deepseek_model,
        "base_url": settings.deepseek_base_url,
        "api_key_masked": mask(settings.deepseek_api_key),
    }


def now_ts() -> str:
    """本地 ISO 时间戳，用于记录行的 occurred_at 列。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def classify_error(exc: BaseException) -> str:
    """把 openai SDK 异常归入稳定中文类别（吸收自孙一新实验的 common.py）。

    用途：冒烟实验里断言"失败以可辨识的类别出现"，而不是只看抛没抛异常。
    注意：Provider 会把 SDK 异常包装成 ProviderError，分类前先取 __cause__。
    """
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return "无效密钥(401)"
    if isinstance(exc, openai.PermissionDeniedError):
        return "无权限(403)"
    if isinstance(exc, openai.RateLimitError):
        return "限流(429)"
    if isinstance(exc, openai.APITimeoutError):
        return "超时"
    if isinstance(exc, openai.APIConnectionError):
        return "网络连接失败"
    if isinstance(exc, openai.APIStatusError):
        # DeepSeek 额度不足返回 HTTP 402，无专属异常类，按状态码归类
        code = getattr(exc, "status_code", 0)
        msg = str(exc).lower()
        if code == 402 or "insufficient" in msg or "balance" in msg:
            return "额度不足(402)"
        return f"接口错误({code})"
    if isinstance(exc, openai.BadRequestError):
        return "参数错误(400)"
    return f"未知异常({type(exc).__name__})"
