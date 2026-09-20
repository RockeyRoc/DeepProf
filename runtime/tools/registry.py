"""Tool 注册、校验、审批与执行（DESIGNv0.4 §5.3 / §5.4）。

设计要点：
- execute() 始终返回结构化 ToolResult，不把业务失败变成异常：
  教学图据此选择重试、替代工具或解释限制（§7.3）。
- 执行有超时上限（§7.3 防挂起）：工具可能做网络/磁盘 IO，挂起会把
  Agent 的"模型—工具"循环无限拖住；超时即中止该调用并转**结构化失败**，
  与 Provider 的 llm_timeout_seconds 是同一类护栏——两者都不许静默等待。
  上限取值：工具 timeout_seconds 优先，否则 settings.tool_timeout_seconds。
- 每次执行都写审计事件：tool.requested / approved / started / completed / failed。
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..core.errors import ToolNotFound
from ..core.events import EventBus, EventType
from ..sandbox.policy import ApprovalGate, SandboxPolicy
from .base import Tool, ToolContext, ToolResult


class ToolRegistry:
    """进程内工具注册表。"""

    def __init__(
        self,
        *,
        sandbox: SandboxPolicy | None = None,
        approval: ApprovalGate | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._sandbox = sandbox or SandboxPolicy()
        self._approval = approval or ApprovalGate()
        self._bus = bus

    # ---------- 注册 ----------
    def register(self, tool: Tool) -> Tool:
        """注册工具；同名覆盖前先报错，避免静默替换。"""
        if not tool.name:
            raise ValueError("Tool 必须有 name")
        if tool.name in self._tools:
            raise ValueError(f"Tool 名称重复: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(f"工具不存在: {name}", tool=name)
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, only: list[str] | None = None) -> list[dict]:
        """返回供模型使用的工具描述列表。"""
        selected = only or self.names()
        return [self._tools[name].schema() for name in selected if name in self._tools]

    # ---------- 执行 ----------
    async def execute(
        self,
        name: str,
        arguments: dict | None = None,
        ctx: ToolContext | None = None,
    ) -> ToolResult:
        """执行工具并返回结构化结果（失败不抛异常）。"""
        context = ctx or ToolContext()
        payload = {
            "tool": name,
            "arguments": arguments or {},
            "session_id": context.session_id,
            "trace_id": context.trace_id,
        }
        self._emit(EventType.TOOL_REQUESTED, payload, context)

        tool = self._tools.get(name)
        if tool is None:
            return self._fail(
                EventType.TOOL_FAILED, context, name, "tool_not_found", f"工具不存在: {name}"
            )

        # 1) 权限：Sandbox 默认拒绝越界
        try:
            self._sandbox.authorize(tool.permissions)
        except Exception as exc:  # PermissionDenied
            code = getattr(exc, "code", "permission_denied")
            return self._fail(EventType.TOOL_FAILED, context, name, code, str(exc))

        # 2) 参数：schema 校验
        try:
            parsed = tool.validate(arguments or {})
        except Exception as exc:  # ToolValidationError
            return self._fail(
                EventType.TOOL_FAILED,
                context,
                name,
                getattr(exc, "code", "tool_invalid_arguments"),
                str(exc),
                details=getattr(exc, "details", {}),
            )

        # 3) 审批：高风险动作需显式授权
        if tool.requires_approval:
            if not self._approval.is_approved(name):
                return self._fail(
                    EventType.TOOL_FAILED,
                    context,
                    name,
                    "tool_approval_required",
                    f"工具 {name} 需要显式审批后才可执行",
                )
            self._emit(EventType.TOOL_APPROVED, {"tool": name}, context)

        # 4) 执行（带超时护栏：单个工具挂起不得拖住整轮对话）
        self._emit(EventType.TOOL_STARTED, {"tool": name}, context)
        timeout = tool.effective_timeout()
        try:
            result = await asyncio.wait_for(tool.execute(parsed, context), timeout=timeout)
        except TimeoutError:  # Python 3.11+ 起 asyncio.TimeoutError 即内置 TimeoutError
            # 结构性失败而非异常：调用方（教学图 / Agent 循环）才能换成替代工具
            # 或向学生说明该能力暂不可用，而不是整轮无响应（§7.3）。
            return self._fail(
                EventType.TOOL_FAILED,
                context,
                name,
                "tool_timeout",
                f"工具 {name} 执行超过 {timeout}s 未返回，已中止并取消该调用；"
                "请改用替代工具或说明该能力暂不可用",
                timeout_seconds=timeout,
            )
        except Exception as exc:  # 工具内部异常统一转结构化失败
            return self._fail(
                EventType.TOOL_FAILED,
                context,
                name,
                "tool_execution_failed",
                f"{type(exc).__name__}: {exc}",
            )

        if not result.ok:
            self._emit(
                EventType.TOOL_FAILED,
                {"tool": name, "error": result.error},
                context,
            )
            return result
        self._emit(
            EventType.TOOL_COMPLETED,
            {"tool": name, "ok": True, "data_keys": sorted(result.data)},
            context,
        )
        return result

    # ---------- 内部 ----------
    def _fail(
        self,
        event_type: EventType,
        context: ToolContext,
        tool_name: str,
        code: str,
        message: str,
        **extra: Any,
    ) -> ToolResult:
        self._emit(
            event_type,
            {"tool": tool_name, "error": {"code": code, "message": message}, **extra},
            context,
        )
        return ToolResult.failure(code, message, tool=tool_name)

    def _emit(self, event_type: EventType, payload: dict, context: ToolContext) -> None:
        if self._bus is None:
            return
        self._bus.emit(
            event_type,
            payload,
            session_id=context.session_id,
            trace_id=context.trace_id,
            source="deepprof.runtime.tools",
        )