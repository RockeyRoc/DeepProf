"""Tool 定义与描述。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from runtime.core.errors import RuntimeFailure


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    async def execute(self, arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class FunctionTool:
    """用一个可调用对象实现的 Tool。"""

    name: str
    handler: Any
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False

    async def execute(self, arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        result = self.handler(arguments, ctx)
        if hasattr(result, "__await__"):
            result = await result
        return dict(result or {})

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


def validate_arguments(tool: Tool, arguments: dict[str, Any]) -> list[str]:
    """返回缺失的必填参数名。"""
    schema = getattr(tool, "parameters", {}) or {}
    required = schema.get("required") or []
    return [name for name in required if name not in arguments]


def tool_not_found(name: str, registered: list[str]) -> RuntimeFailure:
    return RuntimeFailure(
        f"tool_not_found: {name}",
        details={"kind": "tool_not_found", "tool": name, "registered": registered},
    )