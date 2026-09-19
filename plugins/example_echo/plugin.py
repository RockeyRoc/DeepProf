"""示例插件：演示 Plugin Runtime 的最小闭环（§5.2 / D-4）。

它做三件事：
1. setup 阶段从 ServiceRegistry 取到 ToolRegistry 服务；
2. start 阶段注册一个 echo 工具；
3. stop 阶段撤销注册，保证卸载后不残留能力。

注意：插件只通过 ServiceRegistry 拿服务，不直接访问 Runtime 内部对象。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from runtime.plugins import PluginBase, PluginContext
from runtime.tools.base import FunctionTool, ToolResult

TOOL_NAME = "plugin_echo"


class EchoInput(BaseModel):
    """echo 工具入参（pydantic 即 schema）。"""

    text: str = Field(min_length=1, description="要回显的文本")


async def _echo(arguments: EchoInput, ctx) -> ToolResult:
    """最小处理器：原样回显，便于验证工具链路与审计事件。"""
    return ToolResult.success(f"echo: {arguments.text}", text=arguments.text)


class ExampleEchoPlugin(PluginBase):
    """示例插件实现。"""

    def setup(self, ctx: PluginContext) -> None:
        # 只取服务句柄，不做注册，避免 install 失败后残留能力
        self._ctx = ctx

    async def start(self) -> None:
        tools = self._ctx.services.get("tools")
        tools.register(
            FunctionTool(
                name=TOOL_NAME,
                description="回显给定文本（示例插件能力）",
                input_model=EchoInput,
                handler=_echo,
            )
        )

    async def stop(self) -> None:
        self._ctx.services.get("tools").unregister(TOOL_NAME)


plugin = ExampleEchoPlugin()