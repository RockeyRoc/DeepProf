"""OpenAI 兼容协议 Provider。

DeepSeek、OpenAI、Qwen（DashScope 兼容模式）都提供 OpenAI 兼容接口，
因此共用一个实现，差异只在 base_url / api_key / model，由工厂注入。

安全（§12）：密钥只从 settings（即 .env）读取，不写日志、不进仓库。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from config import settings

from ..core.errors import ProviderError
from ..core.message import Message, ToolCall
from .base import ModelChunk, ModelRequest, ModelResponse, Provider, ProviderCapabilities


class OpenAICompatProvider(Provider):
    """通用 OpenAI 兼容 Provider。

    生产：不传 async_client，按配置构造真实 client。
    测试：注入 mock client，不触达网络。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        name: str = "openai_compat",
        async_client: Any | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = async_client

    # ---------- 能力 ----------
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            supports_tools=True,
            supports_streaming=True,
            models=[self._model] if self._model else [],
        )

    # ---------- 生成 ----------
    async def generate(self, request: ModelRequest) -> ModelResponse:
        client = self._ensure_client()
        try:
            resp = await client.chat.completions.create(**self._build_kwargs(request, stream=False))
        except Exception as exc:  # 统一包装成结构化错误，供 §7.3 的降级逻辑使用
            raise ProviderError(f"{self.name} 调用失败: {exc}", provider=self.name) from exc

        choice = resp.choices[0]
        message = choice.message
        return ModelResponse(
            content=message.content or "",
            tool_calls=_parse_tool_calls(message),
            finish_reason=choice.finish_reason or "stop",
            model=getattr(resp, "model", self._model),
            usage=_parse_usage(resp),
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        client = self._ensure_client()
        try:
            stream = await client.chat.completions.create(
                **self._build_kwargs(request, stream=True)
            )
        except Exception as exc:
            raise ProviderError(f"{self.name} 流式调用失败: {exc}", provider=self.name) from exc

        buffer: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if getattr(delta, "tool_calls", None):
                    _accumulate_tool_calls(buffer, delta.tool_calls)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                text = getattr(delta, "content", None)
                if text:
                    yield ModelChunk(delta=text)
        except Exception as exc:
            raise ProviderError(f"{self.name} 流式读取失败: {exc}", provider=self.name) from exc

        yield ModelChunk(
            tool_calls=_finalize_tool_calls(buffer),
            finish_reason=finish_reason or "stop",
            usage={},
        )

    # ---------- 内部 ----------
    def _ensure_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise ProviderError(
                    f"{self.name} 缺少 API Key，请在 .env 配置后重试", provider=self.name
                )
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def _build_kwargs(self, request: ModelRequest, *, stream: bool) -> dict:
        kwargs: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": [m.to_provider_dict() for m in request.messages],
            "temperature": (
                request.temperature
                if request.temperature is not None
                else (self._temperature if self._temperature is not None else settings.llm_temperature)
            ),
            "max_tokens": (
                request.max_tokens
                if request.max_tokens is not None
                else (self._max_tokens if self._max_tokens is not None else settings.llm_max_tokens)
            ),
            "stream": stream,
        }
        if request.tools:
            kwargs["tools"] = request.tools
            kwargs["tool_choice"] = "auto"
        return kwargs


def _parse_tool_calls(message: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in getattr(message, "tool_calls", None) or []:
        function = getattr(raw, "function", None)
        calls.append(
            ToolCall(
                id=getattr(raw, "id", "") or "",
                name=getattr(function, "name", "") or "",
                arguments=_loads(getattr(function, "arguments", "") or ""),
            )
        )
    return calls


def _accumulate_tool_calls(buffer: dict[int, dict[str, Any]], deltas: list[Any]) -> None:
    """按 index 拼接流式工具调用分片（OpenAI 兼容协议的增量形式）。"""
    for raw in deltas:
        index = getattr(raw, "index", 0) or 0
        slot = buffer.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if getattr(raw, "id", None):
            slot["id"] = raw.id
        function = getattr(raw, "function", None)
        if function is not None:
            if getattr(function, "name", None):
                slot["name"] += function.name
            if getattr(function, "arguments", None):
                slot["arguments"] += function.arguments


def _finalize_tool_calls(buffer: dict[int, dict[str, Any]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(buffer):
        slot = buffer[index]
        if not slot["name"]:
            continue
        calls.append(
            ToolCall(
                id=slot["id"],
                name=slot["name"],
                arguments=_loads(slot["arguments"]),
            )
        )
    return calls


def _loads(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}  # 非法 JSON 交给 Tool 校验层报错，不在这里吞掉
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _parse_usage(resp: Any) -> dict:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    return {"prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0)}