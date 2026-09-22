"""Agent：驱动一次模型—工具循环。

保持极简：Agent 只负责“把消息交给模型、把工具交给模型、把结果交回”，
不含任何教学逻辑（教学策略在 graph 侧，经绑定表落到 capability）。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from runtime.core.errors import KIND_MODEL_TRUNCATED
from runtime.core.events import EventType, RuntimeEvent, new_id
from runtime.core.message import Message, ToolCall
from runtime.core.ports import RuntimeHost
from runtime.providers.registry import ModelRouter
from config.settings import Settings


class Agent:
    """一次或多次模型—工具循环。"""

    def __init__(
        self,
        host: RuntimeHost,
        router: ModelRouter,
        settings: Settings,
        *,
        max_tool_rounds: int = 4,
    ) -> None:
        self._host = host
        self._router = router
        self._settings = settings
        self._max_tool_rounds = max_tool_rounds

    async def run(self, request: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """执行一轮或多轮模型调用，逐步产出流式帧并写入事件轨迹。"""
        session_id = str(ctx.get("session_id") or request.get("session_id") or "")
        trace_id = str(ctx.get("trace_id") or request.get("trace_id") or new_id("trc"))
        messages = [Message.from_dict(m) if isinstance(m, dict) else m for m in request.get("messages", [])]
        role = str(request.get("role") or "tutor.default")

        await self._emit(EventType.AGENT_STARTED, {"request_id": request.get("request_id", "")}, session_id, trace_id, ctx)

        usage: dict[str, Any] = {}
        final: Message | None = None

        for turn in range(self._max_tool_rounds):
            await self._emit(EventType.AGENT_TURN_STARTED, {"turn": turn}, session_id, trace_id, ctx)
            call = {
                "messages": [m.to_dict() if isinstance(m, Message) else m for m in messages],
                "role": role,
                "model": request.get("model"),
                "max_tokens": int(request.get("max_tokens") or self._settings.llm_max_tokens),
                "tools": request.get("tools") or [],
            }
            provider, profile_id, model = self._router.resolve(role, requested_model=request.get("model"))
            await self._emit(
                EventType.MODEL_REQUESTED,
                {"role": role, "provider_profile": profile_id, "model": model},
                session_id,
                trace_id,
                ctx,
            )

            content_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            finish_reason = ""
            failure: dict[str, Any] | None = None

            async for frame in provider.stream(call, ctx):
                kind = frame.get("type")
                if kind == "delta":
                    text = str(frame.get("text", ""))
                    if text:
                        content_parts.append(text)
                        await self._emit(EventType.MODEL_STREAM_DELTA, {"text": text}, session_id, trace_id, ctx)
                        yield {"type": "delta", "text": text}
                elif kind == "tool_call":
                    tool_calls.append(ToolCall.from_dict(frame["tool_call"]))
                elif kind == "usage":
                    usage = dict(frame.get("usage") or {})
                elif kind == "finish":
                    finish_reason = str(frame.get("finish_reason") or "")
                elif kind == "error":
                    failure = dict(frame.get("error") or {})
                    break

            if failure is not None:
                await self._emit(EventType.MODEL_FAILED, {"error": failure}, session_id, trace_id, ctx)
                await self._emit(
                    EventType.AGENT_FAILED,
                    {"error": failure},
                    session_id,
                    trace_id,
                    ctx,
                )
                yield {"type": "error", "error": failure}
                return

            content = "".join(content_parts)

            # 截断：空正文 + 无工具调用 + finish=length => 结构化 model_truncated，
            # 不写入会话上下文，避免污染后续轮次。
            if not content and not tool_calls and finish_reason == "length":
                error = {
                    "code": KIND_MODEL_TRUNCATED,
                    "message": "模型输出被 max_tokens 截断且正文为空",
                    "details": {"kind": KIND_MODEL_TRUNCATED, "model": model, "finish_reason": finish_reason},
                }
                await self._emit(EventType.MODEL_FAILED, {"error": error}, session_id, trace_id, ctx)
                await self._emit(EventType.AGENT_FAILED, {"error": error}, session_id, trace_id, ctx)
                yield {"type": "error", "error": error}
                return

            await self._emit(
                EventType.MODEL_COMPLETED,
                {"usage": usage, "model": model, "finish_reason": finish_reason},
                session_id,
                trace_id,
                ctx,
            )

            assistant = Message(role="assistant", content=content, tool_calls=tool_calls)
            messages.append(assistant)
            final = assistant

            if not tool_calls:
                await self._emit(
                    EventType.AGENT_TURN_COMPLETED, {"turn": turn, "status": "ok"}, session_id, trace_id, ctx
                )
                yield {
                    "type": "result",
                    "message": assistant.to_dict(),
                    "usage": usage,
                    "status": "success",
                    "model": model,
                }
                return

            for call_ in tool_calls:
                await self._emit(
                    EventType.TOOL_REQUESTED,
                    {"tool": call_.name, "arguments": dict(call_.arguments)},
                    session_id,
                    trace_id,
                    ctx,
                )
                await self._emit(EventType.TOOL_STARTED, {"tool": call_.name}, session_id, trace_id, ctx)
                try:
                    result = await self._host.call_tool(call_.name, call_.arguments, ctx)
                except Exception as exc:  # 工具失败结构化，不抛出到外层
                    error = {"code": "tool_failed", "message": str(exc), "details": {"tool": call_.name}}
                    await self._emit(EventType.TOOL_FAILED, {"tool": call_.name, "error": error}, session_id, trace_id, ctx)
                    messages.append(
                        Message(
                            role="tool",
                            content=f"工具失败：{exc}",
                            tool_call_id=call_.id,
                            name=call_.name,
                        )
                    )
                    continue
                await self._emit(
                    EventType.TOOL_COMPLETED,
                    {"tool": call_.name, "result": result if isinstance(result, dict) else {"value": result}},
                    session_id,
                    trace_id,
                    ctx,
                )
                messages.append(
                    Message(
                        role="tool",
                        content=str(result.get("content", "")) if isinstance(result, dict) else str(result),
                        tool_call_id=call_.id,
                        name=call_.name,
                    )
                )
            await self._emit(EventType.AGENT_TURN_COMPLETED, {"turn": turn, "status": "tool"}, session_id, trace_id, ctx)

        yield {
            "type": "result",
            "message": (final or Message(role="assistant")).to_dict(),
            "usage": usage,
            "status": "success",
        }

    async def _emit(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        session_id: str,
        trace_id: str,
        ctx: dict[str, Any],
    ) -> None:
        await self._host.emit(
            RuntimeEvent(
                type=event_type.value,
                payload=payload,
                session_id=session_id,
                trace_id=trace_id,
                client_id=ctx.get("client_id"),
                surface=ctx.get("surface"),
            ).to_dict()
        )