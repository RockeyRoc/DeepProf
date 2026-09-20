"""API 健康检查测试（DESIGNv0.4 §16.1 验收：客户端不持有模型密钥；§12 不硬编码密钥）。

覆盖：
- GET /health 返回 200 与运行时快照；
- 快照中不出现任何密钥字段名与密钥值，也不透传本机 sqlite 路径；
- ``app.dependency_overrides`` 能替换 RuntimeService（测试注入路径可用）。

测试用 FakeProvider + ``SqliteDatabase(":memory:")``，不触网。
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import BaseModel

from api.app import create_app
from api.deps import (
    get_default_service,
    get_runtime_service,
    reset_default_service,
)
from config import settings
from runtime.tools.base import FunctionTool, ToolContext, ToolResult
from tests.conftest import make_client, make_service

#: 一旦作为字段名出现就意味着可能泄露密钥的形态
SECRET_KEY_SUFFIXES = ("_key", "_token", "_secret", "_password", "_credential")


def _is_secret_field(key: str) -> bool:
    """凭据字段：以敏感后缀结尾；``allow_*`` 是权限开关，不算凭据。"""
    lowered = key.lower()
    return not lowered.startswith("allow_") and lowered.endswith(SECRET_KEY_SUFFIXES)


def _all_keys(value) -> list[str]:
    """递归收集 JSON 结构里的全部字段名。"""
    if isinstance(value, dict):
        keys = [str(key) for key in value]
        for item in value.values():
            keys.extend(_all_keys(item))
        return keys
    if isinstance(value, list):
        collected: list[str] = []
        for item in value:
            collected.extend(_all_keys(item))
        return collected
    return []


async def _noop(arguments: BaseModel, ctx: ToolContext) -> ToolResult:
    """标记工具的空实现。"""
    return ToolResult.success("marker")


def test_health_ok_and_no_secrets():
    """健康检查必须 200，且响应里既无密钥字段名也无密钥值。"""
    response = make_client(make_service()).get("/health")

    assert response.status_code == 200
    payload = response.json()
    for key in _all_keys(payload):
        assert not _is_secret_field(key), f"响应疑似泄露字段: {key}"
    for attr in ("deepseek_api_key", "openai_api_key", "qwen_api_key"):
        value = getattr(settings, attr)
        if value:  # 只检查非空密钥，避免空串恒真
            assert value not in response.text, f"响应泄露了 {attr} 的值"

    assert payload["status"] == "ok"
    assert payload["service"] == "deepprof-api"
    assert payload["trace_id_header"] == settings.trace_id_header
    assert payload["runtime"]["provider"] == "fake"
    assert "search_textbook" in payload["runtime"]["tools"]  # 项目级工具已装配
    # 教育能力也必须装配：漏装时教学图会拿到 skill_not_found 并静默"证据不足"（§6.3）
    assert {"rag", "socratic", "quiz", "diagnosis", "paper_reader"} <= set(
        payload["runtime"]["skills"]
    )
    # 教学绑定表同样必须装配：漏装时图侧 execute 返回 no_binding，学生只收到空回复（§7.3）
    assert payload["runtime"]["action_bindings"], "未装配 action → capability 绑定表"
    assert "sqlite" not in payload["runtime"]  # 本机路径属内部细节，不透传


def test_default_service_is_configured_too():
    """进程内默认单例也必须走同一套装配。

    单例与 create_app 构造的实例是两条路径；若只有后者装配，
    单例就是一个零绑定的裸内核——跑到教学链路才失败，且现场很难看出原因。
    """
    reset_default_service()
    try:
        service = get_default_service()

        assert service.action_bindings, "默认单例漏了绑定表"
        assert service.skills.names(), "默认单例漏了 Skill"
        assert service.tools.names(), "默认单例漏了工具"
    finally:
        reset_default_service()


def test_dependency_override_replaces_runtime():
    """dependency_overrides 注入的 Runtime 才是实际被调用的那一个。"""
    app_service = make_service()
    override_service = make_service()
    override_service.register_tool(
        FunctionTool(
            name="marker_tool",
            description="测试用标记工具",
            input_model=BaseModel,
            handler=_noop,
        )
    )

    app = create_app(runtime=app_service)
    app.dependency_overrides[get_runtime_service] = lambda: override_service
    tools = TestClient(app).get("/health").json()["runtime"]["tools"]

    assert "marker_tool" in tools
    assert "marker_tool" not in app_service.tools.names()