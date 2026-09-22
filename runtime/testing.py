"""测试与离线运行辅助：内存装配、脚本化的 Fake Provider 与记录型 Host。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from config.settings import Settings
from runtime.capabilities import ActionDispatcher
from runtime.core.events import InMemoryEventStore
from runtime.core.session import InMemorySessionStore
from runtime.memory.service import MemoryService
from runtime.memory.sqlite_memory import SqliteMemoryStore
from runtime.providers.fake import FakeProvider
from runtime.providers.profiles import ProviderProfile
from runtime.providers.registry import ProviderRegistry
from runtime.providers.secrets import InMemorySecretStore
from runtime.service import RuntimeService

FAKE_PROFILE_ID = "fake"


def make_settings(**overrides: Any) -> Settings:
    settings = Settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def make_fake_provider(
    *, script: list[Any] | None = None, profile_id: str = FAKE_PROFILE_ID
) -> FakeProvider:
    return FakeProvider(profile_id, script=script)


def make_service(
    *,
    bindings: dict[str, dict[str, Any]] | None = None,
    script: list[Any] | None = None,
    provider: Any | None = None,
    settings: Settings | None = None,
    skills: Any | None = None,
    tools: Any | None = None,
    memory: MemoryService | None = None,
) -> RuntimeService:
    """构造全内存的 RuntimeService，供单元测试使用。"""
    resolved = settings or make_settings()
    fake = provider or make_fake_provider(script=script)
    registry = ProviderRegistry(InMemorySecretStore(), settings=resolved)
    registry.add(
        ProviderProfile(
            profile_id=FAKE_PROFILE_ID,
            display_name="Fake",
            protocol="native",
            default_model="fake-model",
            capabilities={"stream": True},
        ),
        fake,
    )
    registry.set_role("tutor.default", FAKE_PROFILE_ID, "fake-model")

    service = RuntimeService(
        router=registry,
        settings=resolved,
        event_store=InMemoryEventStore(),
        session_store=InMemorySessionStore(),
        skills=skills,
        tools=tools,
        memory=memory or MemoryService(SqliteMemoryStore(path=":memory:")),
        bindings=bindings or {},
    )
    return service


class RecordingHost:
    """记录所有能力调用的 Host，用于断言分发器“调了什么、怎么调的”。"""

    def __init__(
        self,
        *,
        evidence: list[dict[str, Any]] | None = None,
        skill_result: dict[str, Any] | None = None,
        tool_result: dict[str, Any] | None = None,
        model_text: str = "generated",
        memory_records: list[dict[str, Any]] | None = None,
        fail_skills: set[str] | None = None,
    ) -> None:
        self.evidence = evidence if evidence is not None else []
        self.skill_result = skill_result or {}
        self.tool_result = tool_result or {}
        self.model_text = model_text
        self.memory_records = memory_records or []
        self.fail_skills = fail_skills or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.emitted: list[dict[str, Any]] = []

    async def execute(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("execute", request))
        return {}

    async def emit(self, event: dict[str, Any]) -> None:
        self.emitted.append(event)

    async def invoke_skill(self, name: str, input: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("invoke_skill", {"name": name, "input": input}))
        if name in self.fail_skills:
            raise RuntimeError(f"skill {name} failed")
        return dict(self.skill_result)

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("call_tool", {"name": name, "arguments": arguments}))
        if name == "retrieve_evidence":
            return {"evidence": [dict(item) for item in self.evidence]}
        return dict(self.tool_result)

    async def generate(self, request: dict[str, Any], ctx: dict[str, Any]):
        self.calls.append(("generate", request))
        yield {"type": "delta", "text": self.model_text}
        yield {"type": "finish", "finish_reason": "stop"}

    async def read_memory(self, query: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(("read_memory", query))
        return [dict(item) for item in self.memory_records]

    async def write_memory(self, records: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
        self.calls.append(("write_memory", {"records": records}))
        return None

    def called(self, kind: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == kind]


class FakeRuntime:
    """脚本化的 Runtime 测试替身：**真实分发器** + 记录型 Host + 真实绑定表。

    与 ``RecordingHost`` 的区别：它同时实现窄面（``execute`` / ``emit``），
    因此可以直接当 ``RuntimePort`` 交给教学图；分发器仍是生产实现，
    所以断言验证的是“绑定表 + 分发器”的真实行为，而不是替身的臆想。

    脚本内容（全部可选，未声明的按“没有”语义返回）：

    - ``replies``：依次返回的模型文本（最后一轮之后重复最后一条）；
    - ``skill_results``：按 Skill 名返回的固定结果；
    - ``tool_results``：按 Tool 名返回的固定结果，未声明时回 ``evidence``；
    - ``memories``：``read_memory`` 的召回结果；
    - ``evidence``：未声明 tool_results 时的检索命中。
    """

    def __init__(
        self,
        *,
        replies: list[str] | None = None,
        skill_results: dict[str, dict[str, Any]] | None = None,
        tool_results: dict[str, dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        action_bindings: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.replies = [str(item) for item in (replies or ["（模型回复）"])]
        self.skill_results = {name: dict(value) for name, value in (skill_results or {}).items()}
        self.tool_results = {name: dict(value) for name, value in (tool_results or {}).items()}
        self.memory_records = [dict(item) for item in (memories or [])]
        self.evidence = [dict(item) for item in (evidence or [])]
        self.events: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.written_memories: list[dict[str, Any]] = []
        self._reply_index = 0
        self.dispatcher = ActionDispatcher(self, action_bindings or {})

    # ---- 窄面（教学图唯一可依赖的面） ----

    async def execute(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return await self.dispatcher.execute(request, ctx)

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    # ---- 宽面（能力实现可用） ----

    async def invoke_skill(
        self, name: str, input: dict[str, Any], ctx: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("invoke_skill", {"name": name, "input": dict(input)}))
        if name in self.skill_results:
            return dict(self.skill_results[name])
        return {"status": "not_implemented", "skill": name}

    async def call_tool(
        self, name: str, arguments: dict[str, Any], ctx: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("call_tool", {"name": name, "arguments": dict(arguments)}))
        if name in self.tool_results:
            return dict(self.tool_results[name])
        return {"evidence": [dict(item) for item in self.evidence]}

    async def generate(self, request: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(("generate", {"request": dict(request)}))
        index = min(self._reply_index, len(self.replies) - 1)
        self._reply_index += 1
        yield {"type": "delta", "text": self.replies[index]}
        yield {"type": "finish", "finish_reason": "stop"}

    async def read_memory(self, query: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(("read_memory", dict(query)))
        return [dict(item) for item in self.memory_records]

    async def write_memory(self, records: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
        self.calls.append(("write_memory", {"records": [dict(item) for item in records]}))
        self.written_memories.extend(dict(item) for item in records)
        return None

    # ---- 断言辅助 ----

    def calls_of(self, kind: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == kind]

    def event_types(self) -> list[str]:
        return [str(event.get("type") or "") for event in self.events]