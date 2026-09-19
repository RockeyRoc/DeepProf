"""Plugin Runtime 与服务层装配的集成测试（§5.2 / D-4）。

单元测试（tests/runtime/test_plugins.py）直接驱动 PluginManager；
这里验证 RuntimeService 这条真实路径：安装 → 启动 → 能力可用 → 停止 → 能力撤销。
"""
from __future__ import annotations

import pytest

from runtime.core.events import EventType
from runtime.providers.fake import FakeProvider
from runtime.sandbox.policy import SandboxPolicy
from runtime.service import RuntimeService


@pytest.fixture
def service(db, project_root) -> RuntimeService:
    return RuntimeService(
        provider=FakeProvider(), db=db, sandbox=SandboxPolicy.from_settings()
    )


async def test_install_start_stop_example_plugin(service, project_root):
    """示例插件的完整生命周期：注册的工具真的能用，停用后真的消失。"""
    state = service.install_plugin(project_root / "plugins" / "example_echo")
    assert state.status == "installed"
    assert "plugin_echo" not in service.tools.names()  # install 不注册能力

    await service.start_plugins()
    assert "plugin_echo" in service.tools.names()

    result = await service.call_tool(
        "plugin_echo",
        {"text": "插件你好"},
        {"session_id": "s1", "learner_id": "learner_a", "trace_id": "trace_p"},
    )
    assert result["ok"] and result["content"] == "echo: 插件你好"

    await service.stop_plugins()
    assert "plugin_echo" not in service.tools.names()
    # 插件注册的工具调用同样进入审计轨迹（tool.requested → tool.completed）
    types = [event.type for event in service.events_by_trace("trace_p")]
    assert EventType.TOOL_REQUESTED.value in types
    assert EventType.TOOL_COMPLETED.value in types


async def test_describe_lists_installed_plugin(service, project_root):
    """自述快照要能回答"装了哪些插件、什么状态"，供门户与排障使用。"""
    service.install_plugin(project_root / "plugins" / "example_echo")

    plugins = service.describe()["plugins"]

    assert [plugin["id"] for plugin in plugins] == ["example_echo"]
    assert plugins[0]["status"] == "installed"
    # describe 里的 trusted 必须报告**外部裁定**结果而非插件自述，
    # 否则门户会把"插件自称受信"显示成"已信任"（两者含义完全不同）。
    assert plugins[0]["trusted"] is True
    assert plugins[0]["trust_reason"]
    assert plugins[0]["declared_trusted"] is True  # 自述值单独呈现，仅供对照


async def test_describe_reports_untrusted_when_trust_check_skipped(db, project_root):
    """关掉 trusted_only 直接安装时没有裁定记录，trusted 必须报 False 而不是自述值。"""
    service = RuntimeService(
        provider=FakeProvider(), db=db, sandbox=SandboxPolicy.from_settings()
    )
    service.plugins.trusted_only = False
    service.install_plugin(project_root / "plugins" / "example_echo")

    plugin = service.describe()["plugins"][0]

    assert plugin["declared_trusted"] is True  # 插件确实自称受信
    assert plugin["trusted"] is False, "未经外部裁定不得报告为已信任"
    assert plugin["trust_reason"] == ""