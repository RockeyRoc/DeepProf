"""Provider 抽象（DESIGNv0.4 §5.1 / §8）。

职责：屏蔽不同模型供应商与流式协议的差异。
边界（§4.4）：Provider 不包含任何教学逻辑；更换模型不得改变图的业务语义。

最小接口：generate / stream / capabilities。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..core.message import Message, ToolCall


@dataclass
class ProviderCapabilities:
    """供应商能力声明，供 Runtime 做降级与切换决策（§7.3）。"""

    name: str
    supports_tools: bool = False
    supports_streaming: bool = False
    models: list[str] = field(default_factory=list)


@dataclass
class ModelRequest:
    """一次模型请求。"""

    messages: list[Message]
    tools: list[dict] = field(default_factory=list)  # JSON Schema 形式的工具描述
    model: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelChunk:
    """流式增量。

    文本增量走 delta；工具调用在流结束时一次性给出（tool_calls），
    避免不同供应商增量拼装协议差异外泄给上层。
    """

    delta: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResponse:
    """一次完整模型返回。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # stop | tool_calls | length | error
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict | None = field(default=None, repr=False)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class Provider(ABC):
    """模型 Provider 统一接口。"""

    name: str = "provider"

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """声明能力，Runtime 据此决定是否传入 tools、是否使用流式。"""

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """非流式生成（图节点做单次判断时使用）。"""

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """流式生成（会话输出使用）。实现为异步生成器。"""