"""跨 Provider Profile 切换不改教学策略（MVP-1 验收：§16.1、§19.5、§4.4）。

要证明的命题：**换 Profile 只换模型，不动教学策略**。
做法是让同一段学情分别走两套 Base URL 不同的 OpenAI-compatible Profile，
然后逐项比对策略产物是否逐字一致：

- 教学动作、引用、决策事件的 capability/status 完全相同；
- 送给模型的**提示词逐字相同**（策略与提示词在绑定表里，与厂商无关）；
- 只有模型、provider_profile 与回复正文不同。

零网络：两套 Profile 都由 ``httpx.MockTransport`` 提供假 SSE 流；
两套 Profile 的 Base URL 刻意不同（``api-a.example`` / ``api-b.example``），
以证明“路由按逻辑角色解析”，而不是哪家模型恰好被写死。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from config.settings import Settings
from graph.education.bindings import ACTION_BINDINGS
from graph.education.builder import run_teaching_turn
from graph.education.nodes import EVENT_DECISION
from runtime.core.events import EventType, InMemoryEventStore
from runtime.core.session import InMemorySessionStore
from runtime.memory.service import MemoryService
from runtime.memory.sqlite_memory import SqliteMemoryStore
from runtime.providers.profiles import ProviderProfile
from runtime.providers.registry import ProviderRegistry
from runtime.providers.secrets import InMemorySecretStore
from runtime.service import RuntimeService
from runtime.tools.base import FunctionTool
from skills import register_default_skills

EVIDENCE = {
    "document_id": "doc1",
    "chunk_id": "c1",
    "page": 12,
    "text": "梯度下降沿负梯度方向迭代更新参数。",
    "source": "教材A",
}

BASE_STATE = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "current_concept": "梯度下降",
    "learning_goal": "理解梯度下降的迭代条件",
    "user_input": "请讲讲什么是梯度下降",
}

PROFILE_A = ("profile-a", "https://api-a.example/v1", "model-a")
PROFILE_B = ("profile-b", "https://api-b.example/v1", "model-b")


def sse_body(text: str) -> str:
    """一段最小 SSE 流：增量 → finish → usage（choices 为空的收尾帧）→ [DONE]。"""
    chunks = [
        {"choices": [{"delta": {"content": text}}]},
        {"choices": [{"finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}},
    ]
    lines = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


def make_transport(recorded: list[dict[str, Any]], label: str) -> httpx.MockTransport:
    """按请求 host 返回不同正文，并记录请求体（用于比对提示词）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        recorded.append({"host": request.url.host, "body": body})
        return httpx.Response(
            200,
            text=sse_body(f"来自 {request.url.host} 的讲解"),
            headers={"content-type": "text/event-stream"},
        )

    del label
    return httpx.MockTransport(handler)


async def search_textbook(arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    del ctx
    return {"evidence": [dict(EVIDENCE)], "status": "ok", "query": arguments.get("query", "")}


def make_service(profile: tuple[str, str, str], recorded: list[dict[str, Any]]) -> RuntimeService:
    profile_id, base_url, model = profile
    secrets = InMemorySecretStore()
    registry = ProviderRegistry(secrets, settings=Settings(), transport=make_transport(recorded, profile_id))
    registry.add(
        ProviderProfile(
            profile_id=profile_id,
            display_name=profile_id,
            base_url=base_url,
            api_key_ref=f"provider:{profile_id}",
            default_model=model,
            capabilities={"stream": True},
        )
    )
    registry.set_secret(f"provider:{profile_id}", "sk-test-only")

    service = RuntimeService(
        router=registry,
        settings=Settings(),
        event_store=InMemoryEventStore(),
        session_store=InMemorySessionStore(),
        memory=MemoryService(SqliteMemoryStore(path=":memory:")),
        bindings=ACTION_BINDINGS,
    )
    register_default_skills(service.skills)
    service.tools.register(
        FunctionTool(name="search_textbook", handler=search_textbook, description="测试用检索")
    )
    # 教学代码只认逻辑角色（§19.5），组合根负责把角色映射到 Profile
    registry.set_role("tutor.default", profile_id, model)
    return service


def decisions(service: RuntimeService) -> list[dict[str, Any]]:
    return [
        event["payload"]
        for event in service.history(BASE_STATE["session_id"])
        if event.get("type") == EVENT_DECISION
    ]


def model_events(service: RuntimeService) -> list[dict[str, Any]]:
    return [
        event
        for event in service.history(BASE_STATE["session_id"])
        if event.get("type") == EventType.MODEL_REQUESTED.value
    ]


def test_profile_switch_changes_model_but_not_teaching_strategy():
    recorded_a: list[dict[str, Any]] = []
    recorded_b: list[dict[str, Any]] = []
    result_a = asyncio.run(run_teaching_turn(make_service(PROFILE_A, recorded_a), BASE_STATE))
    result_b = asyncio.run(run_teaching_turn(make_service(PROFILE_B, recorded_b), BASE_STATE))

    # 1) 策略产物逐项一致：动作、引用、情感标签、状态字段
    assert result_a["action"] == result_b["action"] == "teach"
    assert result_a["citations"] == result_b["citations"], "引用定位不应随模型厂商变化"
    assert result_a["emotion"] == result_b["emotion"]
    assert result_a["hint_level"] == result_b["hint_level"]
    assert result_a["evidence_sufficient"] is result_b["evidence_sufficient"] is True

    # 2) 提示词逐字一致：策略与提示词在绑定表里，与厂商无关
    prompt_a = recorded_a[0]["body"]["messages"]
    prompt_b = recorded_b[0]["body"]["messages"]
    assert prompt_a == prompt_b, "换 Provider 不得改变提示词"

    # 3) 只有模型与正文不同
    assert recorded_a[0]["host"] == "api-a.example"
    assert recorded_b[0]["host"] == "api-b.example"
    assert recorded_a[0]["body"]["model"] == "model-a"
    assert recorded_b[0]["body"]["model"] == "model-b"
    assert "api-a.example" in result_a["response_text"]
    assert "api-b.example" in result_b["response_text"]


def test_profile_switch_does_not_touch_decisions_or_events_contract():
    recorded_a: list[dict[str, Any]] = []
    recorded_b: list[dict[str, Any]] = []
    service_a = make_service(PROFILE_A, recorded_a)
    service_b = make_service(PROFILE_B, recorded_b)
    asyncio.run(run_teaching_turn(service_a, BASE_STATE))
    asyncio.run(run_teaching_turn(service_b, BASE_STATE))

    # 决策事件：除 provider 相关信息外，策略判据字段逐项一致
    decisions_a = [{k: v for k, v in item.items() if k != "reason"} for item in decisions(service_a)]
    decisions_b = [{k: v for k, v in item.items() if k != "reason"} for item in decisions(service_b)]
    assert decisions_a == decisions_b, "决策事件的策略判据不应随 Profile 变化"

    # 模型事件：绑定了哪个 Profile / 哪个模型，必须记在轨迹里（可审计、可复现实验）
    requested_a = model_events(service_a)[0]["payload"]
    requested_b = model_events(service_b)[0]["payload"]
    assert (requested_a["provider_profile"], requested_a["model"]) == ("profile-a", "model-a")
    assert (requested_b["provider_profile"], requested_b["model"]) == ("profile-b", "model-b")
    assert requested_a["role"] == requested_b["role"] == "tutor.default"


def test_missing_credential_reports_structured_error_not_silent_switch():
    """凭据缺失必须显式失败：禁止静默换到另一家模型（§5.6、§13.4）。"""
    recorded: list[dict[str, Any]] = []
    service = make_service(PROFILE_A, recorded)
    service.router.set_secret("provider:profile-a", "")  # 清掉凭据

    result = asyncio.run(run_teaching_turn(service, BASE_STATE))

    # 绑定表声明了兜底文案：学生看到确定性失败表述，而不是另一家模型的输出
    assert recorded == [], "凭据缺失时不得发出任何模型请求"
    assert "生成失败" in result["response_text"]
    teach_decision = next(item for item in decisions(service) if item["node"] == "teach")
    assert teach_decision["capability_status"] == "success"
    assert teach_decision["source_attached"] is False