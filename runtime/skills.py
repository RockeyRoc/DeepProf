"""Skill 注册表：面向模型的可复用能力说明与流程。

Skill 负责“怎么完成一类任务”，不拥有底层权限；由绑定表按名引用，
图节点不直接调用它（§4.4）。

两类 Skill：

- 纯函数式（默认）：只需要 ``input`` 与 ``ctx``，适合模板化、规则化任务；
- 需要模型/工具/记忆的（``uses_host = True``）：额外拿到 ``RuntimeHost``
  宽面（§6.4、§7.1）。宽面**只交给能力实现**，图节点拿不到。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from runtime.core.errors import RuntimeFailure


@runtime_checkable
class Skill(Protocol):
    name: str
    description: str

    async def invoke(self, input: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]: ...


class FunctionSkill:
    """用一个可调用对象实现的 Skill（不需要宽面）。"""

    uses_host = False

    def __init__(self, name: str, handler, *, description: str = "") -> None:
        self.name = name
        self.description = description
        self._handler = handler

    async def invoke(self, input: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        result = self._handler(input, ctx)
        if hasattr(result, "__await__"):
            result = await result
        return dict(result or {})


def uses_host(skill: Any) -> bool:
    """该 Skill 是否声明需要宽面（``RuntimeHost``）。

    由 Skill 自己声明（``uses_host = True``），而不是靠参数个数猜——
    猜错时要么调用失败，要么把宽面悄悄递给不该拿到的实现。
    """
    return bool(getattr(skill, "uses_host", False))


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[str(skill.name)] = skill

    def unregister(self, name: str) -> None:
        self._skills.pop(name, None)

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise RuntimeFailure(
                f"skill_not_found: {name}",
                details={"kind": "skill_not_found", "skill": name, "registered": sorted(self._skills)},
            )
        return self._skills[name]

    def names(self) -> list[str]:
        return sorted(self._skills)

    async def invoke(
        self,
        name: str,
        input: dict[str, Any],
        ctx: dict[str, Any],
        host: Any | None = None,
    ) -> dict[str, Any]:
        skill = self.get(name)
        if uses_host(skill):
            if host is None:
                raise RuntimeFailure(
                    f"host_required: {name}",
                    details={"kind": "capability_missing", "skill": name},
                )
            return await skill.invoke(input, ctx, host)  # type: ignore[call-arg]
        return await skill.invoke(input, ctx)


async def collect_stream(host: Any, request: dict[str, Any], ctx: dict[str, Any]) -> str:
    """消费宽面的流式生成帧并拼成完整文本；失败或空内容返回空串。

    ``delta`` 是增量，``content`` 带完整内容，``error`` 表示 Provider 失败。
    调用方据空串判断“未取得内容”，不解析字符串（§7.3）。
    """
    parts: list[str] = []
    final = ""
    async for frame in host.generate(request, ctx):
        kind = str(frame.get("type") or "")
        if kind == "delta":
            parts.append(str(frame.get("text") or ""))
        elif kind == "content":
            final = str(frame.get("content") or "")
        elif kind == "error":
            return ""
    return final or "".join(parts)