"""Tool 注册表：schema 校验、权限检查与执行。"""

from __future__ import annotations

from typing import Any

from runtime.core.errors import RuntimeFailure
from runtime.tools.base import Tool, tool_not_found, validate_arguments


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[str(tool.name)] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise tool_not_found(name, sorted(self._tools))
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        selected = names or self.names()
        result = []
        for name in selected:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if hasattr(tool, "schema"):
                result.append(tool.schema())
            else:  # pragma: no cover - 自定义 Tool 实现
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": getattr(tool, "parameters", {}),
                        },
                    }
                )
        return result

    async def call(self, name: str, arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        tool = self.get(name)
        missing = validate_arguments(tool, arguments)
        if missing:
            raise RuntimeFailure(
                f"invalid_arguments: {name} 缺少 {missing}",
                details={"kind": "invalid_arguments", "tool": name, "missing_fields": missing},
            )
        if getattr(tool, "requires_approval", False) and name not in set(ctx.get("approved_tools") or []):
            raise RuntimeFailure(
                f"approval_required: {name}",
                details={"kind": "approval_required", "tool": name},
            )
        return await tool.execute(arguments, ctx)