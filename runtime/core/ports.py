"""Runtime 端口：窄面（图唯一可依赖）与宽面（能力实现可用）。

契约冻结：``execute`` 只接受并返回 ``dict``；图与 Runtime 之间不共享 Python 类型，
教学词汇（hint/teach/ask/...）只存在于图侧的绑定表数据里。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol


class RuntimePort(Protocol):
    """窄面：教学图唯一可依赖的能力面。"""

    async def execute(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]: ...

    async def emit(self, event: dict[str, Any]) -> None: ...


class RuntimeHost(RuntimePort, Protocol):
    """宽面：Runtime 能力实现可用的完整面。"""

    async def invoke_skill(self, name: str, input: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]: ...

    def generate(
        self, request: dict[str, Any], ctx: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def read_memory(self, query: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def write_memory(self, records: list[dict[str, Any]], ctx: dict[str, Any]) -> None: ...