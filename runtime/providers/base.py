"""Provider 统一接口与能力探测。

流式帧协议（``stream`` 产出）：
``delta`` / ``tool_call`` / ``usage`` / ``finish`` / ``error``。
流式工具调用必须按 index 增量装配后再以 ``tool_call`` 发出完整调用。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

from runtime.core.errors import (
    KIND_CAPABILITY_MISSING,
    KIND_UNKNOWN,
    ProviderError,
    classify_external_failure,
)
from runtime.providers.capabilities import CAP_STREAM, normalize_capabilities

PROBE_PROMPT = "Reply with the single word: pong"
PROBE_MAX_TOKENS = 512

STATUS_OK = "ok"
STATUS_INCONCLUSIVE = "inconclusive"
STATUS_FAILED = "failed"


def delta_frame(text: str) -> dict[str, Any]:
    return {"type": "delta", "text": text}


def tool_call_frame(call: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_call", "tool_call": call}


def usage_frame(usage: dict[str, Any]) -> dict[str, Any]:
    return {"type": "usage", "usage": dict(usage)}


def finish_frame(reason: str) -> dict[str, Any]:
    return {"type": "finish", "finish_reason": reason}


def error_frame(error: dict[str, Any]) -> dict[str, Any]:
    return {"type": "error", "error": dict(error)}


class Provider(Protocol):
    """屏蔽不同模型供应商与流式协议差异。"""

    profile_id: str
    protocol: str

    async def stream(self, request: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...

    async def generate(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]: ...

    async def list_models(self) -> list[str]: ...

    def capabilities(self) -> dict[str, bool]: ...

    async def healthcheck(self, model: str | None = None) -> dict[str, Any]: ...


async def probe_provider(
    provider: Provider,
    *,
    model: str | None = None,
    max_tokens: int = PROBE_MAX_TOKENS,
) -> dict[str, Any]:
    """最小探测请求；不发送任何用户对话内容。

    空正文 + ``finish=length`` 视为**不确定**（推理模型可能把预算耗在推理 token 上），
    不能判定为失败，否则会产生假阴性。
    """
    request = {
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "model": model,
        "max_tokens": max_tokens,
        "tools": [],
        "probe": True,
    }
    try:
        result = await provider.generate(request, {"probe": True})
    except ProviderError as exc:
        return {
            "status": STATUS_FAILED,
            "kind": exc.kind,
            "message": exc.message,
            "details": dict(exc.details),
        }
    except Exception as exc:  # 非结构化异常也归一
        classification = classify_external_failure(error=exc)
        return {
            "status": STATUS_FAILED,
            "kind": classification.kind,
            "message": str(exc),
            "details": classification.to_dict(),
        }

    content = str(result.get("content", "")).strip()
    finish_reason = str(result.get("finish_reason", ""))
    capabilities = normalize_capabilities(result.get("capabilities") or provider.capabilities())
    capabilities[CAP_STREAM] = bool(capabilities.get(CAP_STREAM, False))

    if content:
        return {
            "status": STATUS_OK,
            "kind": "ok",
            "message": "",
            "content": content,
            "model": result.get("model", model),
            "capabilities": capabilities,
        }
    if finish_reason == "length":
        return {
            "status": STATUS_INCONCLUSIVE,
            "kind": KIND_UNKNOWN,
            "message": "探测输出被 max_tokens 截断（推理 token 可能占满预算），无法判定",
            "capabilities": capabilities,
        }
    return {
        "status": STATUS_INCONCLUSIVE,
        "kind": KIND_CAPABILITY_MISSING,
        "message": "探测返回空正文且未截断",
        "capabilities": capabilities,
    }