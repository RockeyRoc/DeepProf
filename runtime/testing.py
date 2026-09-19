"""FakeRuntime：RuntimePort 的确定性实现（DESIGNv0.4 §16.3）。

用途：
- 教育组在没有真实 Runtime / 没有密钥时开发与测试教学图；
- 集成测试精确控制"证据不足""连续答错"等分支场景。

它严格遵守 RuntimePort 契约，因此可以直接替换真实 Runtime，
不需要改教学图的任何一行代码。

`execute` 走的是**真实的通用分发器**（runtime/capabilities.py），只有模型与
Skill 被脚本化。这样图侧测试跑的是与生产同一条分发路径（含证据前置条件、
确定性兜底、学情记忆读写），而不是一个"假装返回结果"的替身；绑定表由测试
显式注入，因为 Runtime 侧不得内置任何教学绑定。

它同时满足 RuntimePort（窄面：execute / emit）与 RuntimeHost（宽面：
invoke_skill / call_tool / generate / read_memory / write_memory）——
与 RuntimeService 一致，否则图侧测试与生产的端口形状会悄悄漂移。
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Iterable

from .capabilities import ActionDispatcher, CapabilityRegistry, default_capabilities
from .core.ports import RuntimeContext, RuntimeHost, RuntimePort


class FakeRuntime:
    """记录调用 + 脚本化返回的假 Runtime。"""

    def __init__(
        self,
        *,
        replies: Iterable[str] | None = None,
        tool_results: dict[str, dict] | None = None,
        skill_results: dict[str, dict] | None = None,
        memories: list[dict] | None = None,
        action_bindings: dict[str, dict] | None = None,
        capability_registry: CapabilityRegistry | None = None,
        default_reply: str = "（FakeRuntime）请先说说你的思路。",
    ) -> None:
        self._replies = list(replies or [])
        self._tool_results = dict(tool_results or {})
        self._skill_results = dict(skill_results or {})
        self._memories = list(memories or [])
        self._default_reply = default_reply
        self.capabilities = capability_registry or default_capabilities()
        self.dispatcher = ActionDispatcher(self.capabilities, action_bindings, port=self)
        self.calls: list[dict[str, Any]] = []  # 供测试断言调用顺序与参数
        self.events: list[dict] = []
        self.written_memories: list[dict] = []

    # ---------- RuntimePort ----------
    async def execute(self, request: dict, ctx: dict) -> dict:
        """按注入的绑定表真实分发；没注入绑定时返回 no_binding（与生产一致）。"""
        self._record("execute", request=request, ctx=ctx)
        return await self.dispatcher.execute(request, ctx)

    async def invoke_skill(self, name: str, input: dict, ctx: dict) -> dict:
        self._record("invoke_skill", name=name, input=input, ctx=ctx)
        if name in self._skill_results:
            return dict(self._skill_results[name])
        return {"status": "ok", "skill": name, "result": {}, "source": "fake_runtime"}

    async def call_tool(self, name: str, arguments: dict, ctx: dict) -> dict:
        self._record("call_tool", name=name, arguments=arguments, ctx=ctx)
        if name in self._tool_results:
            return dict(self._tool_results[name])
        return {"ok": False, "content": "", "data": {}, "error": {
            "code": "insufficient_evidence",
            "message": f"（FakeRuntime）没有 {name} 的结果",
        }}

    async def generate(self, request: dict, ctx: dict) -> AsyncIterator[dict]:
        self._record("generate", request=request, ctx=ctx)
        text = self._replies.pop(0) if self._replies else self._default_reply
        for index in range(0, len(text), 8):
            yield {"type": "delta", "text": text[index : index + 8]}
        yield {
            "type": "done",
            "content": text,
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

    async def read_memory(self, query: dict, ctx: dict) -> list[dict]:
        self._record("read_memory", query=query, ctx=ctx)
        memory_type = str(query.get("memory_type") or "")
        return [
            record
            for record in self._memories
            if not memory_type or record.get("memory_type") == memory_type
        ]

    async def write_memory(self, records: list[dict], ctx: dict) -> None:
        self._record("write_memory", records=records, ctx=ctx)
        self.written_memories.extend(records)

    async def emit(self, event: dict) -> None:
        self._record("emit", event=event)
        self.events.append(event)

    # ---------- 测试辅助 ----------
    def calls_of(self, method: str) -> list[dict]:
        return [call for call in self.calls if call["method"] == method]

    def event_types(self) -> list[str]:
        return [str(event.get("type", "")) for event in self.events]

    def _record(self, method: str, **payload: Any) -> None:
        self.calls.append({"method": method, **payload})


def make_context(
    session_id: str = "sess_test",
    learner_id: str = "learner_test",
    trace_id: str = "trace_test",
) -> RuntimeContext:
    """构造测试用上下文。"""
    return RuntimeContext(
        session_id=session_id, learner_id=learner_id, trace_id=trace_id
    )


__all__ = ["FakeRuntime", "RuntimeHost", "RuntimePort", "RuntimeContext", "make_context"]