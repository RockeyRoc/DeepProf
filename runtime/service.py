"""RuntimeService：同时实现窄面（RuntimePort）与宽面（RuntimeHost）。

对教学图只暴露 ``execute`` / ``emit`` 的类型承诺；对能力实现才移交宽面。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from config.settings import Settings
from runtime.capabilities import ActionDispatcher
from runtime.core.errors import RuntimeFailure
from runtime.core.events import (
    EventType,
    InMemoryEventStore,
    RuntimeEvent,
    new_id,
)
from runtime.core.session import Session
from runtime.memory.service import MemoryService
from runtime.providers.registry import ProviderRegistry
from runtime.sandbox.policy import SandboxPolicy
from runtime.skills import SkillRegistry
from runtime.tools.registry import ToolRegistry

Listener = Callable[[dict[str, Any]], None]


class RuntimeService:
    """自研 Runtime 的执行底座。"""

    def __init__(
        self,
        *,
        router: ProviderRegistry,
        settings: Settings | None = None,
        event_store: Any | None = None,
        session_store: Any | None = None,
        skills: SkillRegistry | None = None,
        tools: ToolRegistry | None = None,
        memory: MemoryService | None = None,
        bindings: dict[str, dict[str, Any]] | None = None,
        sandbox: SandboxPolicy | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.router = router
        self.events = event_store or InMemoryEventStore()
        self.sessions = session_store
        self.skills = skills or SkillRegistry()
        self.tools = tools or ToolRegistry()
        self.memory = memory or MemoryService()
        self.sandbox = sandbox or SandboxPolicy.from_settings(self.settings)
        self.dispatcher = ActionDispatcher(self, bindings)
        self.plugins: Any | None = None
        # Product-level services may attach here at the composition root.  The
        # Runtime itself never imports the resource-library package.
        self.library: Any | None = None
        self._listeners: list[Listener] = []

    # ---- 窄面 ----

    async def execute(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return await self.dispatcher.execute(request, ctx)

    async def emit(self, event: dict[str, Any]) -> None:
        runtime_event = event if isinstance(event, RuntimeEvent) else RuntimeEvent.from_dict(event)
        stored = self.events.append(runtime_event)
        payload = stored.to_dict()
        for listener in list(self._listeners):
            try:
                listener(payload)
            except Exception:  # 监听器故障不影响主链路
                continue

    # ---- 宽面 ----

    async def invoke_skill(self, name: str, input: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """调用 Skill；声明 ``uses_host`` 的实现额外拿到本服务的宽面（§6.4）。"""
        return await self.skills.invoke(name, input, ctx, host=self)

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return await self.tools.call(name, arguments, ctx)

    async def generate(self, request: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """能力层的模型调用入口：解析逻辑角色 → 流式产出统一帧，并写入模型事件。"""
        role = str(request.get("role") or "tutor.default")
        provider, profile_id, model = self.router.resolve(role, requested_model=request.get("model"))
        payload = dict(request)
        payload["model"] = model

        await self._emit_ctx(
            EventType.MODEL_REQUESTED,
            {"role": role, "provider_profile": profile_id, "model": model},
            ctx,
        )
        usage: dict[str, Any] = {}
        finish_reason = ""
        failed = False
        async for frame in provider.stream(payload, ctx):
            kind = frame.get("type")
            if kind == "delta":
                await self._emit_ctx(EventType.MODEL_STREAM_DELTA, {"text": frame.get("text", "")}, ctx)
            elif kind == "usage":
                usage = dict(frame.get("usage") or {})
            elif kind == "finish":
                finish_reason = str(frame.get("finish_reason") or "")
            elif kind == "error":
                failed = True
                await self._emit_ctx(EventType.MODEL_FAILED, {"error": frame.get("error") or {}}, ctx)
            yield frame
        if not failed:
            await self._emit_ctx(
                EventType.MODEL_COMPLETED,
                {"usage": usage, "model": model, "finish_reason": finish_reason},
                ctx,
            )

    async def read_memory(self, query: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
        records = await self.memory.read(query, ctx)
        await self._emit_ctx(
            EventType.MEMORY_READ,
            {"scope": str(query.get("scope", "")), "count": len(records)},
            ctx,
        )
        return records

    async def write_memory(self, records: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
        await self.memory.write(records, ctx)
        await self._emit_ctx(
            EventType.MEMORY_WRITE,
            {"scope": str(ctx.get("scope", "")), "count": len(records)},
            ctx,
        )
        return None

    # ---- 会话 ----

    def new_session(self, *, learner_id: str = "local", title: str = "") -> Session:
        session = Session(learner_id=learner_id, title=title)
        if self.sessions is not None:
            self.sessions.save(session)
        return session

    def get_session(self, session_id: str) -> Session:
        session = self.sessions.load(session_id) if self.sessions is not None else None
        if session is None:
            raise RuntimeFailure(
                f"session_not_found: {session_id}",
                details={"kind": "session_not_found", "session_id": session_id},
            )
        return session

    def save_session(self, session: Session) -> None:
        if self.sessions is not None:
            self.sessions.save(session)

    def history(self, session_id: str, from_sequence: int = 0) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events.replay(session_id, from_sequence)]

    def last_sequence(self, session_id: str) -> int:
        return self.events.last_sequence(session_id)

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ---- 装配状态 ----

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "bindings": self.dispatcher.binding_summary(),
            "skills": self.skills.names(),
            "tools": self.tools.names(),
            "providers": self.router.status(),
            "roles": {role: list(binding) for role, binding in self.router.roles.items()},
            "plugins": [] if self.plugins is None else self.plugins.list(),
        }

    async def _emit_ctx(self, event_type: EventType, payload: dict[str, Any], ctx: dict[str, Any]) -> None:
        await self.emit(
            RuntimeEvent(
                type=event_type.value,
                payload=payload,
                session_id=str(ctx.get("session_id") or ""),
                trace_id=str(ctx.get("trace_id") or new_id("trc")),
                client_id=ctx.get("client_id"),
                surface=ctx.get("surface"),
            ).to_dict()
        )
