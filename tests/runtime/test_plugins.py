"""Plugin Runtime 测试（§5.2 / D-4）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.core.errors import PermissionDenied, PluginError
from runtime.core.events import EventBus, EventType
from runtime.plugins.lifecycle import STATUS_STARTED, STATUS_STOPPED, PluginManager
from runtime.plugins.manifest import PluginManifest
from runtime.plugins.registry import ServiceRegistry
from runtime.plugins.trust import PluginTrustStore, plugin_digest
from runtime.sandbox.policy import PERM_NETWORK, ApprovalGate, SandboxPolicy
from runtime.storage.base import InMemoryEventStore
from runtime.tools.registry import ToolRegistry


@pytest.fixture
def bus():
    return EventBus(store=InMemoryEventStore())


def _manager(bus, *, sandbox=None, trust_store=None) -> PluginManager:
    services = ServiceRegistry()
    services.provide("tools", ToolRegistry(bus=bus))
    return PluginManager(
        services,
        bus=bus,
        sandbox=sandbox or SandboxPolicy(),
        trusted_only=True,
        trust_store=trust_store,
    )


def _trust_store(root: Path, *plugin_ids: str) -> PluginTrustStore:
    """构造外部信任清单，并把指定插件登记为已审批（模拟运维审批动作）。"""
    store = PluginTrustStore(root / "trusted.json")
    for plugin_id in plugin_ids:
        store.approve(root / plugin_id, approved_by="test")
    return store


def _event_types(bus) -> list[str]:
    return [event.type for event in bus.replay("")]


def _write_plugin(root: Path, plugin_id: str, *, manifest_extra: dict | None = None,
                  body: str = "") -> Path:
    """在临时目录里生成一个插件包，用于验证加载与生命周期。"""
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "version": "0.1.0",
        "entry": "plugin.py:plugin",
        "capabilities": ["tool"],
        "permissions": [],
        "trusted": True,
    }
    manifest.update(manifest_extra or {})
    (plugin_dir / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text(
        body
        or (
            "from runtime.plugins import PluginBase\n\n"
            "class P(PluginBase):\n"
            "    def setup(self, ctx):\n"
            "        self.ctx = ctx\n"
            "plugin = P()\n"
        ),
        encoding="utf-8",
    )
    return plugin_dir


# ---------- manifest 校验 ----------
def test_manifest_requires_entry_and_known_capabilities():
    with pytest.raises(PluginError):
        PluginManifest(id="p1", version="1.0", entry="plugin.py").validate()  # 缺属性名
    with pytest.raises(PluginError):
        PluginManifest(id="p1", version="1.0", entry="plugin.py:p", capabilities=["magic"]).validate()


def test_manifest_rejects_unknown_permission_and_self_dependency():
    """权限必须使用统一标识，插件不能依赖自己（否则拓扑排序会出错）。"""
    with pytest.raises(PluginError):
        PluginManifest(
            id="p1", version="1.0", entry="plugin.py:p", permissions=["root"]
        ).validate()
    with pytest.raises(PluginError):
        PluginManifest(
            id="p1", version="1.0", entry="plugin.py:p", dependencies=["p1"]
        ).validate()


def test_manifest_read_from_dir(tmp_path):
    plugin_dir = _write_plugin(tmp_path, "demo")
    manifest = PluginManifest.from_dir(plugin_dir)
    assert manifest.id == "demo"
    assert manifest.entry_file == "plugin.py"
    assert manifest.entry_object == "plugin"


# ---------- 安装与生命周期 ----------
async def test_install_start_stop_uninstall_example_plugin(bus, project_root):
    """端到端走一遍示例插件：注册工具 → 可用 → 停止后撤销。

    信任清单用仓库里随附的 plugins/trusted.json——顺带验证它与示例插件内容同步，
    不会因插件被改动而使清单过期。
    """
    manager = _manager(
        bus, trust_store=PluginTrustStore(project_root / "plugins" / "trusted.json")
    )
    state = manager.install_from_dir(project_root / "plugins" / "example_echo")
    assert state.status == "installed"

    await manager.start(state.manifest.id)
    tools = manager.services.get("tools")
    assert "plugin_echo" in tools.names()
    assert (await tools.execute("plugin_echo", {"text": "hi"})).ok

    await manager.stop(state.manifest.id)
    assert "plugin_echo" not in tools.names()

    await manager.uninstall(state.manifest.id)
    assert manager.get("example_echo") is None
    types = _event_types(bus)
    for expected in (
        EventType.PLUGIN_APPROVED.value,
        EventType.PLUGIN_INSTALLED.value,
        EventType.PLUGIN_STARTED.value,
        EventType.PLUGIN_STOPPED.value,
        EventType.PLUGIN_UNINSTALLED.value,
    ):
        assert expected in types


# ---------- 信任裁定（§5.2 / §13.3：外部裁定，不看插件自述） ----------
async def test_self_declared_trusted_plugin_is_rejected(bus, tmp_path):
    """插件自报 trusted=true 也必须被拒：信任只能来自包外的运维清单。

    这是本模块的核心安全属性——若安装时采信 manifest.trusted，
    任何插件写一个 "trusted": true 就能自证清白，trusted_only 形同虚设。
    """
    plugin_dir = _write_plugin(tmp_path, "self_claimed", manifest_extra={"trusted": True})
    manager = _manager(bus)  # 没有任何外部审批记录

    with pytest.raises(PluginError, match="信任裁定"):
        manager.install_from_dir(plugin_dir)
    assert manager.get("self_claimed") is None
    assert EventType.PLUGIN_APPROVED.value not in _event_types(bus), "被拒的插件不应留下审批事件"


async def test_unapproved_plugin_is_rejected_even_when_list_has_others(bus, tmp_path):
    """清单里没有该 id 就拒绝，与它是否自报受信无关。"""
    _write_plugin(tmp_path, "approved_one")
    _write_plugin(tmp_path, "stranger", manifest_extra={"trusted": True})
    manager = _manager(bus, trust_store=_trust_store(tmp_path, "approved_one"))

    with pytest.raises(PluginError, match="不在信任清单中"):
        manager.install_from_dir(tmp_path / "stranger")


async def test_content_change_invalidates_approval(bus, tmp_path):
    """审批绑定内容摘要：插件被改动后原审批失效，必须重新审批。"""
    plugin_dir = _write_plugin(tmp_path, "tampered")
    manager = _manager(bus, trust_store=_trust_store(tmp_path, "tampered"))
    approved_digest = plugin_digest(plugin_dir)

    # 审批之后插件内容被改动（例如被植入额外逻辑）
    (plugin_dir / "plugin.py").write_text(
        "from runtime.plugins import PluginBase\n\n"
        "class P(PluginBase):\n"
        "    pass\n"
        "plugin = P()\n",
        encoding="utf-8",
    )
    assert plugin_digest(plugin_dir) != approved_digest

    with pytest.raises(PluginError, match="摘要不匹配"):
        manager.install_from_dir(plugin_dir)


async def test_version_change_invalidates_approval(bus, tmp_path):
    """版本升级同样需要重新审批，避免"旧审批套新版本"。"""
    plugin_dir = _write_plugin(tmp_path, "bumped")
    store = _trust_store(tmp_path, "bumped")
    manager = _manager(bus, trust_store=store)

    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": "bumped",
                "version": "0.2.0",
                "entry": "plugin.py:plugin",
                "capabilities": ["tool"],
                "permissions": [],
                "trusted": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(PluginError, match="版本与审批记录不一致"):
        manager.install_from_dir(plugin_dir)


async def test_approved_plugin_emits_plugin_approved_event(bus, tmp_path):
    """裁定通过要落 plugin.approved 事件，并带上审批人与摘要，便于审计。"""
    plugin_dir = _write_plugin(tmp_path, "good")
    manager = _manager(bus, trust_store=_trust_store(tmp_path, "good"))

    manager.install_from_dir(plugin_dir)

    approvals = [
        event for event in bus.replay("") if event.type == EventType.PLUGIN_APPROVED.value
    ]
    assert len(approvals) == 1
    payload = approvals[0].payload
    assert payload["plugin"] == "good"
    assert payload["trusted"] is True
    assert payload["record"]["approved_by"] == "test"
    assert payload["record"]["sha256"]


async def test_revoked_plugin_is_rejected(bus, tmp_path):
    """撤销审批后立即失效（清单是唯一权威）。"""
    _write_plugin(tmp_path, "revoked")
    store = _trust_store(tmp_path, "revoked")
    assert store.revoke("revoked") is True
    manager = _manager(bus, trust_store=store)

    with pytest.raises(PluginError, match="信任裁定"):
        manager.install_from_dir(tmp_path / "revoked")


async def test_plugin_permission_out_of_sandbox_is_rejected(tmp_path, bus):
    """插件声明的权限超出 Sandbox 授权范围时，安装阶段就失败。

    信任裁定在前、沙箱校验在后：即使已审批，越界权限仍被拒绝（§13.3）。
    """
    plugin_dir = _write_plugin(tmp_path, "netter", manifest_extra={"permissions": [PERM_NETWORK]})
    manager = _manager(
        bus,
        sandbox=SandboxPolicy(allow_network=False),
        trust_store=_trust_store(tmp_path, "netter"),
    )

    with pytest.raises(PermissionDenied):
        manager.install_from_dir(plugin_dir)


async def test_dependencies_must_be_installed_first(tmp_path, bus):
    """依赖未安装时必须拒绝，避免半可用状态。"""
    _write_plugin(tmp_path, "child", manifest_extra={"dependencies": ["parent"]})
    manager = _manager(bus, trust_store=_trust_store(tmp_path, "child"))

    with pytest.raises(PluginError, match="依赖"):
        manager.install_from_dir(tmp_path / "child")


async def test_start_all_follows_dependency_order(tmp_path, bus):
    """依赖方后启动；单个插件失败不影响其它插件（失败隔离）。"""
    _write_plugin(tmp_path, "parent")
    _write_plugin(
        tmp_path,
        "child",
        manifest_extra={"dependencies": ["parent"]},
        body=(
            "from runtime.plugins import PluginBase\n\n"
            "class P(PluginBase):\n"
            "    async def start(self):\n"
            "        raise RuntimeError('boom')\n"
            "plugin = P()\n"
        ),
    )
    manager = _manager(bus, trust_store=_trust_store(tmp_path, "parent", "child"))
    manager.install_all(tmp_path)

    started = await manager.start_all()

    assert [state.manifest.id for state in started] == ["parent"]
    assert manager.get("child").status == "failed"
    assert EventType.PLUGIN_FAILED.value in _event_types(bus)


async def test_install_failure_is_recorded_and_isolated(tmp_path, bus):
    """setup 抛错时记录 plugin.failed，且不影响同目录其它插件（失败隔离）。"""
    _write_plugin(tmp_path, "healthy")
    _write_plugin(
        tmp_path,
        "broken",
        body=(
            "from runtime.plugins import PluginBase\n\n"
            "class P(PluginBase):\n"
            "    def setup(self, ctx):\n"
            "        raise ValueError('setup 失败')\n"
            "plugin = P()\n"
        ),
    )
    manager = _manager(bus, trust_store=_trust_store(tmp_path, "healthy", "broken"))

    manager.install_all(tmp_path)

    assert manager.get("broken").status == "failed"
    assert manager.get("healthy") is not None
    assert EventType.PLUGIN_FAILED.value in _event_types(bus)


def test_install_all_skips_invalid_manifests(tmp_path, bus):
    """损坏的插件目录不影响其它插件的安装。"""
    _write_plugin(tmp_path, "good")
    (tmp_path / "no_manifest").mkdir()
    bad = tmp_path / "bad_json"
    bad.mkdir()
    (bad / "plugin.json").write_text("{ not json", encoding="utf-8")

    manager = _manager(bus, trust_store=_trust_store(tmp_path, "good"))
    manager.install_all(tmp_path)

    assert manager.get("good") is not None
    assert manager.get("bad_json") is None


# ---------- Service Registry ----------
def test_service_registry_prevents_plugin_from_hijacking_core_service():
    """插件不能覆盖核心服务，避免权限越界（§5.2）。"""
    services = ServiceRegistry()
    services.provide("tools", object())

    with pytest.raises(PluginError):
        services.provide("tools", object(), owner="evil_plugin")


def test_service_registry_provide_get_revoke():
    services = ServiceRegistry()
    sentinel = object()
    services.provide("memory", sentinel, owner="p1")
    assert services.get("memory") is sentinel
    assert services.has("memory")

    services.revoke("memory", owner="p1")
    assert not services.has("memory")
    with pytest.raises(PluginError):
        services.get("memory")