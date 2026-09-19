"""Agent loop（DESIGNv0.4 §5.1）。

职责：驱动一次或多次"模型 → 工具"循环，维护运行状态，并把每一步写成事件。

最小接口：run(context) -> AsyncEventStream
这里的异步事件流由 AgentChunk 组成：文本增量、工具结果、完成或失败，
便于 API 转发、REPL 打印与测试断言时序。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator

from config import settings

from ..providers.base import ModelRequest, ModelResponse, Provider
from ..sandbox.policy import SandboxPolicy
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry
from .errors import AgentLoopLimit, ModelTruncated, ProviderError
from .events import EventBus, EventType, RuntimeEvent
from .message import Message
from .ports import RuntimeContext
from .session import Session


@dataclass
class AgentChunk:
    """Agent 流的一块输出。"""

    kind: str  # delta | tool | done | error
    text: str = ""
    message: Message | None = None
    tool_name: str = ""
    tool_ok: bool = True
    error: dict | None = None
    event: RuntimeEvent | None = None
    metadata: dict = field(default_factory=dict)


class Agent:
    """模型—工具循环驱动器。"""

    def __init__(
        self,
        provider: Provider,
        tools: ToolRegistry,
        bus: EventBus,
        *,
        sandbox: SandboxPolicy | None = None,
        max_turns: int | None = None,
        source: str = "deepprof.runtime.agent",
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._bus = bus
        self._sandbox = sandbox or SandboxPolicy()
        self._max_turns = max_turns or settings.agent_max_turns
        self._source = source

    # ---------- 主循环 ----------
    async def run(
        self,
        session: Session,
        ctx: RuntimeContext,
        *,
        user_input: str | None = None,
        system_prompt: str = "",
        use_tools: bool = True,
        model: str = "",
    ) -> AsyncIterator[AgentChunk]:
        """驱动一轮（可能含多次模型—工具往返）对话。"""
        if system_prompt and not any(m.role == "system" for m in session.messages):
            session.append(Message.system(system_prompt))
        if user_input is not None:
            session.append(Message.user(user_input))

        self._emit(
            EventType.AGENT_STARTED,
            {"learner_id": ctx.learner_id, "messages": len(session.messages)},
            ctx,
        )

        schemas = self._tools.schemas() if use_tools else []
        try:
            async for chunk in self._loop(session, ctx, schemas, model):
                yield chunk
        except asyncio.CancelledError:
            # 取消也必须可观测（§16.1）：用户中断/超时后轨迹里要留下记录
            self._emit(EventType.AGENT_CANCELLED, {"reason": "cancelled"}, ctx)
            raise

    async def _loop(
        self, session: Session, ctx: RuntimeContext, schemas: list[dict], model: str
    ) -> AsyncIterator[AgentChunk]:
        """模型—工具循环主体（与取消处理分离，便于阅读）。"""
        for turn in range(1, self._max_turns + 1):
            self._emit(EventType.AGENT_TURN_STARTED, {"turn": turn}, ctx)
            self._emit(
                EventType.MODEL_REQUESTED,
                {
                    "provider": self._provider.name,
                    "model": model or "",
                    "tools": [schema["function"]["name"] for schema in schemas],
                },
                ctx,
            )

            content_parts: list[str] = []
            try:
                response = None
                async for chunk in self._provider.stream(
                    ModelRequest(messages=list(session.messages), tools=schemas, model=model)
                ):
                    if chunk.delta:
                        content_parts.append(chunk.delta)
                        self._emit(EventType.MODEL_STREAM_DELTA, {"text": chunk.delta}, ctx)
                        yield AgentChunk(kind="delta", text=chunk.delta)
                    response = _merge(response, chunk, self._provider.name)
            except ProviderError as exc:
                self._emit(EventType.MODEL_FAILED, {"error": exc.to_dict()}, ctx)
                event = self._emit(EventType.AGENT_FAILED, {"error": exc.to_dict()}, ctx)
                yield AgentChunk(kind="error", error=exc.to_dict(), event=event)
                return

            final = _finalize(response, content_parts, self._provider.name)
            self._emit(
                EventType.MODEL_COMPLETED,
                {
                    "model": final.model,
                    "finish_reason": final.finish_reason,
                    "usage": final.usage,
                    "content_length": len(final.content),
                },
                ctx,
            )

            # 截断必须可观测（§16.1）：推理模型可能把 token 预算耗在 reasoning 上，
            # 于是 finish_reason=length 且正文为空；若按成功收尾，学生只看到空回复，
            # 轨迹里也看不出失败。此处明确失败，且不把空答案写进会话上下文。
            if final.finish_reason == "length" and not final.content.strip() and not final.tool_calls:
                truncated = ModelTruncated(
                    "模型输出被截断且没有正文（finish_reason=length）；"
                    "请提高 LLM_MAX_TOKENS 或改用非推理模型。",
                    finish_reason=final.finish_reason,
                    provider=self._provider.name,
                )
                event = self._emit(
                    EventType.AGENT_FAILED, {"error": truncated.to_dict()}, ctx
                )
                yield AgentChunk(
                    kind="error", error=truncated.to_dict(), event=event
                )
                return

            session.append(Message.assistant(final.content, final.tool_calls))

            if not final.tool_calls:
                event = self._emit(
                    EventType.AGENT_TURN_COMPLETED,
                    {"turn": turn, "finish_reason": final.finish_reason},
                    ctx,
                )
                yield AgentChunk(kind="done", message=session.messages[-1], event=event)
                return

            # 依次执行模型请求的工具，并把结果作为 tool 消息回填
            for call in final.tool_calls:
                result = await self._tools.execute(
                    call.name, call.arguments, self._tool_context(ctx)
                )
                session.append(Message.tool(result.content, call.id, call.name))
                yield AgentChunk(
                    kind="tool",
                    text=result.content,
                    message=session.messages[-1],
                    tool_name=call.name,
                    tool_ok=result.ok,
                    error=result.error,
                    metadata={"tool_call_id": call.id},
                )

        # 循环超限：明确失败，避免无限调用（§7.3）
        limit = AgentLoopLimit(
            f"模型—工具循环超过 {self._max_turns} 轮", max_turns=self._max_turns
        )
        event = self._emit(EventType.AGENT_FAILED, {"error": limit.to_dict()}, ctx)
        yield AgentChunk(kind="error", error=limit.to_dict(), event=event)

    # ---------- 内部 ----------
    def _tool_context(self, ctx: RuntimeContext) -> ToolContext:
        return ToolContext(
            session_id=ctx.session_id,
            learner_id=ctx.learner_id,
            trace_id=ctx.trace_id,
            sandbox=self._sandbox,
            metadata=ctx.metadata,
        )

    def _emit(self, event_type: EventType, payload: dict, ctx: RuntimeContext) -> RuntimeEvent:
        return self._bus.emit(
            event_type,
            payload,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            source=self._source,
        )


def _merge(current: ModelResponse | None, chunk, provider_name: str) -> ModelResponse:
    """把流式分片合并成 ModelResponse（工具调用以最后一块为准）。"""
    base = current or ModelResponse(model=provider_name)
    if chunk.tool_calls:
        base.tool_calls = list(chunk.tool_calls)
    if chunk.finish_reason:
        base.finish_reason = chunk.finish_reason
    if chunk.usage:
        base.usage = dict(chunk.usage)
    return base


def _finalize(
    response: ModelResponse | None, content_parts: list[str], provider_name: str
) -> ModelResponse:
    final = response or ModelResponse(model=provider_name)
    final.content = "".join(content_parts)
    return final