"""Tool 抽象（DESIGNv0.4 §5.3）。

Tools = 可执行且有结构化参数的原子操作，必须经过
schema 校验 → 权限检查 → 执行 → 结果规范化 → 审计事件。

参数校验直接用 pydantic：调用方拿到的总是合法模型，
避免手写校验器与 JSON Schema 两套逻辑漂移。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from config import settings

from ..core.errors import ToolValidationError
from ..sandbox.policy import SandboxPolicy


@dataclass
class ToolContext:
    """工具执行上下文（不含全局 Session 引用，§4.4）。"""

    session_id: str = ""
    learner_id: str = ""
    trace_id: str = ""
    sandbox: SandboxPolicy = field(default_factory=SandboxPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """工具执行结果（结构化，便于图节点判断与前端展示）。"""

    ok: bool = True
    content: str = ""  # 文本化结果，作为 tool 消息回填给模型
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    @classmethod
    def success(cls, content: str = "", **data: Any) -> "ToolResult":
        return cls(ok=True, content=content, data=data)

    @classmethod
    def failure(cls, code: str, message: str, **details: Any) -> "ToolResult":
        return cls(
            ok=False,
            content=f"[工具错误] {message}",
            error={"code": code, "message": message, "details": details},
        )

    def to_dict(self) -> dict:
        return {"ok": self.ok, "content": self.content, "data": self.data, "error": self.error}


class Tool(ABC):
    """Tool 基类。"""

    name: str = ""
    description: str = ""
    input_model: type[BaseModel] = BaseModel
    permissions: frozenset[str] = frozenset()  # 默认最小权限
    requires_approval: bool = False
    #: 单次执行的超时上限（秒）；None = 用 settings.tool_timeout_seconds。
    #: 有网络/磁盘 IO 的工具应显式声明自己的合理上限，而不是依赖全局值。
    timeout_seconds: float | None = None

    def effective_timeout(self) -> float:
        """解析实际超时：工具自声明优先，否则落到全局默认（§7.3 防挂起）。

        超时是**执行层护栏**，与 llm_timeout_seconds 同一类：挂起的调用会被
        中止并转成结构化失败，而不是把 Agent 循环无限拖住。
        """
        if self.timeout_seconds is not None:
            return float(self.timeout_seconds)
        return float(settings.tool_timeout_seconds)

    def schema(self) -> dict:
        """OpenAI 兼容的 function 描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def validate(self, arguments: dict) -> BaseModel:
        """校验参数；失败抛 ToolValidationError。"""
        try:
            return self.input_model.model_validate(arguments or {})
        except ValidationError as exc:
            raise ToolValidationError(
                f"工具 {self.name} 参数校验失败", errors=exc.errors(include_url=False)
            ) from exc

    @abstractmethod
    async def execute(self, arguments: BaseModel, ctx: ToolContext) -> ToolResult:
        """执行工具。实现里不要捕获权限错误，交给 registry 统一处理。"""


class FunctionTool(Tool):
    """用函数快速定义一个 Tool（项目级工具与插件常用）。"""

    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[BaseModel],
        handler,
        *,
        permissions: frozenset[str] | set[str] = frozenset(),
        requires_approval: bool = False,
        timeout_seconds: float | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self.permissions = frozenset(permissions)
        self.requires_approval = requires_approval
        self.timeout_seconds = timeout_seconds
        self._handler = handler

    async def execute(self, arguments: BaseModel, ctx: ToolContext) -> ToolResult:
        result = await self._handler(arguments, ctx)
        return normalize_result(result)


def normalize_result(result: Any) -> ToolResult:
    """把任意返回规范化为 ToolResult，保证事件与消息结构一致。"""
    if isinstance(result, ToolResult):
        return result
    if isinstance(result, dict):
        return ToolResult(ok=True, content=str(result.get("content", "")), data=result)
    return ToolResult(ok=True, content=str(result), data={})