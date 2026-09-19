"""FakeProvider：可运行的确定性模型（DESIGNv0.4 §16.1）。

用途：
- 全员在没有密钥/没有网络时也能跑通 Runtime 与教学图；
- 单元测试通过 script 精确控制"模型先请求工具、再给答案"的时序。

行为约定（确定性，不是"假装智能"）：
- 若 script 中有待消费的返回，按序返回；
- 否则回显最后一条用户消息（截断），并标注来源为 FakeProvider。
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Iterable

from ..core.message import Message, Role, ToolCall
from .base import ModelChunk, ModelRequest, ModelResponse, Provider, ProviderCapabilities


class FakeProvider(Provider):
    """确定性 Provider。"""

    name = "fake"

    def __init__(
        self,
        script: Iterable[ModelResponse | dict] | None = None,
        *,
        echo_prefix: str = "（FakeProvider）我收到了：",
        chunk_size: int = 4,
        supports_tools: bool = True,
    ) -> None:
        self._script: list[ModelResponse] = [
            _to_response(item) for item in (script or [])
        ]
        self._echo_prefix = echo_prefix
        self._chunk_size = max(1, chunk_size)
        self._supports_tools = supports_tools
        self.calls: list[ModelRequest] = []  # 供测试断言"模型确实被调用"

    # ---------- 脚本控制 ----------
    def push(self, response: ModelResponse | dict) -> "FakeProvider":
        """追加一个预设返回。"""
        self._script.append(_to_response(response))
        return self

    @property
    def remaining(self) -> int:
        return len(self._script)

    # ---------- Provider 接口 ----------
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            supports_tools=self._supports_tools,
            supports_streaming=True,
            models=["fake-deterministic"],
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if self._script:
            return self._script.pop(0)
        return self._default_response(request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        response = await self.generate(request)
        for index in range(0, len(response.content), self._chunk_size):
            yield ModelChunk(delta=response.content[index : index + self._chunk_size])
        yield ModelChunk(
            tool_calls=response.tool_calls,
            finish_reason=response.finish_reason,
            usage=response.usage,
        )

    # ---------- 内部 ----------
    def _default_response(self, request: ModelRequest) -> ModelResponse:
        last_user = _last_user_text(request.messages)
        content = f"{self._echo_prefix}{last_user[:80]}" if last_user else "（FakeProvider）暂无输入。"
        return ModelResponse(
            content=content,
            finish_reason="stop",
            model="fake-deterministic",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )


def _last_user_text(messages: list[Message]) -> str:
    for message in reversed(messages):
        if message.role == Role.USER:
            return message.content
    return ""


def _to_response(item: ModelResponse | dict) -> ModelResponse:
    """把 dict 形式转成 ModelResponse，便于在测试/示例里写得简短。"""
    if isinstance(item, ModelResponse):
        return item
    tool_calls = [
        call if isinstance(call, ToolCall) else ToolCall.from_dict(call)
        for call in item.get("tool_calls", [])
    ]
    return ModelResponse(
        content=item.get("content", ""),
        tool_calls=tool_calls,
        finish_reason=item.get("finish_reason") or ("tool_calls" if tool_calls else "stop"),
        model=item.get("model", "fake-deterministic"),
        usage=item.get("usage") or {},
        raw=item.get("raw"),
    )


def tool_call(name: str, arguments: dict | None = None, call_id: str = "") -> dict:
    """构造工具调用的简写，供 FakeProvider 脚本使用。"""
    data = {"name": name, "arguments": arguments or {}}
    if call_id:
        data["id"] = call_id
    return data


def dump_arguments(arguments: dict) -> str:
    """与 OpenAI 兼容协议一致的 arguments 序列化（测试断言用）。"""
    return json.dumps(arguments, ensure_ascii=False)