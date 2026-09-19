"""RuntimeService：把 Core 服务装配在一起，并实现 RuntimePort。

它承担两件事：
1. 会话与轨迹的接入（API / REPL 的唯一入口）；
2. 作为教学图依赖的 RuntimePort 实现（§7.1）。

装配关系（可在构造时全部替换，便于测试与后续换实现）：
    Provider ─┐
    ToolRegistry ─┼─> Agent 循环
    SkillRegistry ─┘
    EventStore / SessionStore / MemoryStore / ProfileStore
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from config import project_path, settings

from .capabilities import (
    ActionDispatcher,
    Capability,
    CapabilityRegistry,
    default_capabilities,
)
from .core.agent import Agent, AgentChunk
from .core.errors import ModelTruncated
from .core.events import EventBus, EventType, RuntimeEvent
from .core.message import Message, new_id
from .core.ports import RuntimeContext
from .core.session import Session
from .memory.service import MemoryService
from .plugins.lifecycle import PluginManager, PluginState
from .plugins.registry import ServiceRegistry
from .plugins.trust import PluginTrustStore
from .providers.base import ModelChunk, ModelRequest, ModelResponse, Provider
from .providers.factory import get_provider
from .sandbox.policy import ApprovalGate, SandboxPolicy
from .skills import SkillRegistry
from .storage.sqlite_store import (
    SqliteDatabase,
    SqliteEventStore,
    SqliteProfileStore,
    SqliteSessionStore,
)
from .tools.base import Tool
from .tools.registry import ToolRegistry

SYSTEM_PROMPT = (
    "你是 DeepProf，一只陪伴高校学生学习的 AI 智能宠物老师。"
    "请用苏格拉底式提问引导学生思考，不直接给答案；"
    "引用教材时必须给出可定位来源，没有证据就说明证据不足。"
    "用中文回答，语气亲切。"
)


class RuntimeService:
    """DeepProf Runtime 的门面。"""

    def __init__(
        self,
        *,
        provider: Provider | None = None,
        db: SqliteDatabase | None = None,
        event_store=None,
        session_store=None,
        memory_store=None,
        profile_store=None,
        sandbox: SandboxPolicy | None = None,
        approval: ApprovalGate | None = None,
        tool_registry: ToolRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        trust_store: PluginTrustStore | None = None,
        capability_registry: CapabilityRegistry | None = None,
        action_bindings: dict[str, dict] | None = None,
    ) -> None:
        self.db = db
        if event_store is None or session_store is None or profile_store is None:
            self.db = db or SqliteDatabase()
            event_store = event_store or SqliteEventStore(self.db)
            session_store = session_store or SqliteSessionStore(self.db)
            profile_store = profile_store or SqliteProfileStore(self.db)
        if memory_store is None:
            from .memory.sqlite_memory import SqliteMemoryStore

            self.db = self.db or SqliteDatabase()
            memory_store = SqliteMemoryStore(self.db)

        self.provider = provider or get_provider()
        self.sandbox = sandbox or SandboxPolicy.from_settings()
        self.approval = approval or ApprovalGate()
        self.event_store = event_store
        self.session_store = session_store
        self.profile_store = profile_store
        self.bus = EventBus(store=event_store, source=settings.runtime_source)
        self.memory = MemoryService(memory_store, self.bus)
        self.tools = tool_registry or ToolRegistry(
            sandbox=self.sandbox, approval=self.approval, bus=self.bus
        )
        self.skills = skill_registry or SkillRegistry()
        self.agent = Agent(self.provider, self.tools, self.bus, sandbox=self.sandbox)

        # 声明式分发（§4.4）：能力是通用原语、绑定是纯数据。
        # 绑定表**不在 Runtime 里写死任何默认值**：教学动作 → capability 的映射
        # 属于策略侧，由组合根（api/app.py）注入，否则只是把耦合从结构搬到了词汇。
        self.capabilities = capability_registry or default_capabilities()
        self.dispatcher = ActionDispatcher(self.capabilities, action_bindings, port=self)

        # 插件只能通过 ServiceRegistry 取服务（§5.2），因此这里显式登记能力面
        self.services = ServiceRegistry()
        self._register_services()
        self.plugins = PluginManager(
            self.services,
            bus=self.bus,
            sandbox=self.sandbox,
            trust_store=trust_store or self._build_trust_store(),
        )

    @staticmethod
    def _build_trust_store() -> PluginTrustStore:
        """按配置构建插件信任清单（外部裁定，见 plugins/trust.py）。"""
        return PluginTrustStore(project_path(settings.plugin_trust_store))

    def _register_services(self) -> None:
        """登记插件可用的服务；不暴露数据库连接等私有对象。"""
        self.services.provide("provider", self.provider)
        self.services.provide("tools", self.tools)
        self.services.provide("skills", self.skills)
        self.services.provide("memory", self.memory)
        self.services.provide("events", self.bus)
        self.services.provide("sandbox", self.sandbox)
        self.services.provide("session_store", self.session_store)
        self.services.provide("event_store", self.event_store)
        self.services.provide("profile_store", self.profile_store)

    # ================= 会话生命周期 =================
    def create_session(
        self,
        learner_id: str = "",
        session_id: str = "",
        metadata: dict | None = None,
    ) -> Session:
        """创建并持久化新会话，写入 session.started 事件。"""
        session = Session(
            session_id=session_id,
            learner_id=learner_id,
            metadata=dict(metadata or {}),
        )
        self.session_store.save(session)
        self.bus.emit(
            EventType.SESSION_STARTED,
            {"learner_id": learner_id},
            session_id=session.session_id,
            source=settings.runtime_source,
        )
        return session

    def open_session(self, session_id: str, learner_id: str = "") -> Session:
        """恢复会话；不存在则创建（重启可恢复）。"""
        session = self.session_store.load(session_id)
        if session is None:
            return self.create_session(learner_id=learner_id, session_id=session_id)
        self.bus.emit(
            EventType.SESSION_RESUMED,
            {"learner_id": session.learner_id, "messages": len(session.messages)},
            session_id=session.session_id,
            source=settings.runtime_source,
        )
        return session

    def save_session(self, session: Session) -> None:
        self.session_store.save(session)

    def close_session(self, session: Session) -> None:
        self.session_store.save(session)
        self.bus.emit(
            EventType.SESSION_ENDED,
            {"messages": len(session.messages)},
            session_id=session.session_id,
            source=settings.runtime_source,
        )

    def compact_session(self, session: Session, keep_last: int | None = None) -> Session:
        """压缩上下文并落盘（§5.1 compact）。"""
        session.compact(keep_last or settings.session_compact_keep)
        self.session_store.save(session)
        self.bus.emit(
            EventType.SESSION_COMPACTED,
            {"keep_last": keep_last or settings.session_compact_keep},
            session_id=session.session_id,
            source=settings.runtime_source,
        )
        return session

    def replay(self, session_id: str, from_sequence: int = 0) -> Iterator[RuntimeEvent]:
        """按 sequence 回放会话事件（断线重连、复盘、审计）。"""
        return self.bus.replay(session_id, from_sequence)

    def events_by_trace(self, trace_id: str) -> list[RuntimeEvent]:
        return self.event_store.list_by_trace(trace_id)

    # ================= Runtime 接入 =================
    async def run_turn(
        self,
        session: Session,
        user_input: str | None = None,
        *,
        trace_id: str = "",
        system_prompt: str = SYSTEM_PROMPT,
        use_tools: bool = True,
        model: str = "",
    ) -> AsyncIterator[AgentChunk]:
        """执行一轮完整对话（含工具循环），并把会话落盘。"""
        ctx = RuntimeContext(
            session_id=session.session_id,
            learner_id=session.learner_id,
            trace_id=trace_id or new_id("trace"),
            source=settings.runtime_source,
            metadata={"sandbox": self.sandbox},
        )
        try:
            async for chunk in self.agent.run(
                session,
                ctx,
                user_input=user_input,
                system_prompt=system_prompt,
                use_tools=use_tools,
                model=model,
            ):
                yield chunk
        finally:
            self.session_store.save(session)

    # ================= 声明式分发（WHAT → HOW，§4.4） =================
    @property
    def action_bindings(self) -> dict[str, dict]:
        """当前的绑定表（供 /health 展示与装配检查）。"""
        return self.dispatcher.bindings

    def set_action_bindings(self, bindings: dict[str, dict]) -> None:
        """注入 action → capability 的绑定表（数据来自策略侧，Runtime 只查表）。"""
        self.dispatcher.set_bindings(bindings)

    def register_capability(self, capability: Capability) -> Any:
        return self.capabilities.register(capability)

    async def execute(self, request: dict, ctx: dict) -> dict:
        """把一条声明式决策交给通用分发器执行（实现见 runtime/capabilities.py）。"""
        return await self.dispatcher.execute(request, ctx)

    # ================= RuntimePort 实现 =================
    async def invoke_skill(self, name: str, input: dict, ctx: dict) -> dict:
        context = RuntimeContext.from_dict(ctx)
        return await self.skills.invoke(name, input, context, self)

    async def call_tool(self, name: str, arguments: dict, ctx: dict) -> dict:
        context = RuntimeContext.from_dict(ctx)
        from .tools.base import ToolContext

        result = await self.tools.execute(
            name,
            arguments,
            ToolContext(
                session_id=context.session_id,
                learner_id=context.learner_id,
                trace_id=context.trace_id,
                sandbox=self.sandbox,
                metadata=context.metadata,
            ),
        )
        return result.to_dict()

    async def generate(self, request: dict, ctx: dict) -> AsyncIterator[dict]:
        """流式生成：逐条产出 {"type": "delta"|"done"|"error"}。"""
        context = RuntimeContext.from_dict(ctx)
        model_request = _to_model_request(request)
        self.bus.emit(
            EventType.MODEL_REQUESTED,
            {"provider": self.provider.name, "model": model_request.model},
            session_id=context.session_id,
            trace_id=context.trace_id,
            source=settings.runtime_source,
        )
        parts: list[str] = []
        final: ModelResponse | None = None
        try:
            async for chunk in self.provider.stream(model_request):
                if chunk.delta:
                    parts.append(chunk.delta)
                    self.bus.emit(
                        EventType.MODEL_STREAM_DELTA,
                        {"text": chunk.delta},
                        session_id=context.session_id,
                        trace_id=context.trace_id,
                        source=settings.runtime_source,
                    )
                    yield {"type": "delta", "text": chunk.delta}
                final = _merge_response(final, chunk, self.provider.name)
        except Exception as exc:
            error = getattr(exc, "to_dict", lambda: {"message": str(exc)})()
            self.bus.emit(
                EventType.MODEL_FAILED,
                {"error": error},
                session_id=context.session_id,
                trace_id=context.trace_id,
                source=settings.runtime_source,
            )
            yield {"type": "error", "error": error}
            return

        response = _finalize_response(final, parts, self.provider.name)
        self.bus.emit(
            EventType.MODEL_COMPLETED,
            {"model": response.model, "finish_reason": response.finish_reason, "usage": response.usage},
            session_id=context.session_id,
            trace_id=context.trace_id,
            source=settings.runtime_source,
        )
        # 与 Agent 同一判定：截断且无正文不算成功（§16.1）。
        # 能力层（runtime/capabilities.py 的 collect_stream）消费本端口，
        # 只有 error 才能让它按失败处理；
        # 若这里照常返回 done，讲解能力会把空串当成正常讲解返回给学生。
        if (
            response.finish_reason == "length"
            and not response.content.strip()
            and not response.tool_calls
        ):
            truncated = ModelTruncated(
                "模型输出被截断且没有正文（finish_reason=length）；"
                "请提高 LLM_MAX_TOKENS 或改用非推理模型。",
                finish_reason=response.finish_reason,
                provider=self.provider.name,
            )
            yield {"type": "error", "error": truncated.to_dict()}
            return
        yield {
            "type": "done",
            "content": response.content,
            "tool_calls": [call.to_dict() for call in response.tool_calls],
            "finish_reason": response.finish_reason,
            "usage": response.usage,
        }

    async def read_memory(self, query: dict, ctx: dict) -> list[dict]:
        return await self.memory.read(query, ctx)

    async def write_memory(self, records: list[dict], ctx: dict) -> None:
        await self.memory.write(records, ctx)

    async def emit(self, event: dict) -> None:
        """写入教学决策事件（图节点进出、路由决策等）。"""
        payload = dict(event.get("payload") or {})
        self.bus.publish(
            RuntimeEvent(
                type=str(event.get("type") or "runtime.event"),
                payload=payload,
                event_id=str(event.get("event_id") or ""),
                session_id=str(event.get("session_id") or ""),
                trace_id=str(event.get("trace_id") or ""),
                source=str(event.get("source") or "deepprof.graph"),
                timestamp=str(event.get("timestamp") or ""),
            )
        )

    # ================= 注册与自述 =================
    def register_tool(self, tool: Tool) -> Tool:
        return self.tools.register(tool)

    def register_skill(self, skill) -> Any:
        return self.skills.register(skill)

    def tool_schemas(self) -> list[dict]:
        return self.tools.schemas()

    # ================= 插件（D-4：仅受信 Python 插件） =================
    def install_plugin(self, plugin_dir) -> PluginState:
        """安装一个插件目录（含 plugin.json）。"""
        return self.plugins.install_from_dir(Path(plugin_dir))

    async def start_plugins(self) -> list[PluginState]:
        """按依赖顺序启动已安装插件；单插件失败不阻断其它插件。"""
        return await self.plugins.start_all()

    async def stop_plugins(self) -> None:
        await self.plugins.stop_all()

    def describe(self) -> dict:
        """供 /health 与门户展示的运行时快照（不含任何密钥）。"""
        return {
            "provider": self.provider.name,
            "provider_capabilities": vars(self.provider.capabilities()),
            "tools": self.tools.names(),
            "skills": self.skills.names(),
            "capabilities": self.capabilities.names(),
            "action_bindings": sorted(self.action_bindings),
            "plugins": self.plugins.list(),
            "services": self.services.describe(),
            "sandbox": self.sandbox.describe(),
            "sqlite": getattr(self.db, "path", ""),
        }

    def new_context(self, session: Session, trace_id: str = "") -> RuntimeContext:
        return RuntimeContext(
            session_id=session.session_id,
            learner_id=session.learner_id,
            trace_id=trace_id or new_id("trace"),
            source=settings.runtime_source,
            metadata={"sandbox": self.sandbox},
        )


# ================= 辅助 =================
def _to_model_request(request: dict) -> ModelRequest:
    """把端口层的 dict 请求转成 ModelRequest（兼容 Message 与 dict 两种消息）。"""
    messages: list[Message] = []
    for item in request.get("messages", []):
        messages.append(item if isinstance(item, Message) else Message.from_dict(item))
    return ModelRequest(
        messages=messages,
        tools=list(request.get("tools") or []),
        model=str(request.get("model") or ""),
        temperature=request.get("temperature"),
        max_tokens=request.get("max_tokens"),
        stream=bool(request.get("stream", True)),
        metadata=dict(request.get("metadata") or {}),
    )


def _merge_response(
    current: ModelResponse | None, chunk: ModelChunk, provider_name: str
) -> ModelResponse:
    base = current or ModelResponse(model=provider_name)
    if chunk.tool_calls:
        base.tool_calls = list(chunk.tool_calls)
    if chunk.finish_reason:
        base.finish_reason = chunk.finish_reason
    if chunk.usage:
        base.usage = dict(chunk.usage)
    return base


def _finalize_response(
    response: ModelResponse | None, parts: list[str], provider_name: str
) -> ModelResponse:
    final = response or ModelResponse(model=provider_name)
    final.content = "".join(parts)
    return final


def build_runtime_service(**overrides) -> RuntimeService:
    """按配置构造 Runtime（Provider / SQLite / 沙箱），**不装配项目级能力**。

    有意只做这么少：tools / skills 与教学绑定表都在 `runtime/` 的上层，
    由组合根（`api/deps.configure_runtime`）注入——本模块若反过来 import 它们，
    就破坏了 §9.1 的依赖方向。

    因此直接用本函数构造出的 Runtime 是"零工具、零 Skill、零绑定"的裸内核：
    它适合验证 Agent/Session/Event/Provider 这些 Runtime 自身的东西，
    不适合跑教学链路。要两者都有，请让组合根装配（脚本可调 `configure_runtime`）。
    """
    return RuntimeService(**overrides)