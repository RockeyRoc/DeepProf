"""Plugin 生命周期（DESIGNv0.4 §5.2 / D-4 / §7.3）。

生命周期：install → start → stop → uninstall。
- install：外部信任裁定 → 校验 manifest（依赖、权限）→ 加载模块 → setup
- start / stop：插件自行启停其能力（注册/撤销服务、工具、Skill）
- uninstall：停止并移除记录，撤销它提供的服务

信任（§5.2 / §13.3 / D-4）：install 阶段的"是否受信"由 PluginTrustStore
这个**外部清单**裁定，不看插件自述的 manifest.trusted（自述等于自证清白）。
裁定通过会写 plugin.approved 事件，与工具侧的 tool.approved 对称。

失败隔离：单个插件崩溃只记录 plugin.failed 事件并回退到核心能力，
不影响 Runtime 其它部分（§7.3）。
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import settings

from ..core.errors import PluginError
from ..core.events import EventBus, EventType
from ..sandbox.policy import SandboxPolicy
from .manifest import PluginManifest, discover
from .registry import ServiceRegistry
from .trust import PluginTrustStore

STATUS_INSTALLED = "installed"
STATUS_STARTED = "started"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"


@dataclass
class PluginContext:
    """交给插件的上下文（只暴露受控服务，不暴露 Runtime 内部对象）。"""

    plugin_id: str
    services: ServiceRegistry
    bus: EventBus | None = None
    sandbox: SandboxPolicy | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        if self.bus is None:
            return
        self.bus.emit(
            event_type,
            {"plugin": self.plugin_id, **(payload or {})},
            source="deepprof.runtime.plugins",
        )


class PluginBase:
    """插件基类：MVP 只有受信 Python 插件。"""

    manifest: PluginManifest

    def setup(self, ctx: PluginContext) -> None:
        """install 阶段调用：获取服务、准备资源。默认不做任何事。"""

    async def start(self) -> None:
        """start 阶段调用：注册工具/Skill 或启动后台任务。"""

    async def stop(self) -> None:
        """stop 阶段调用：撤销注册、释放资源。"""

    @property
    def id(self) -> str:
        return self.manifest.id


@dataclass
class PluginState:
    """插件运行时状态记录。"""

    manifest: PluginManifest
    path: Path
    status: str = STATUS_INSTALLED
    instance: PluginBase | None = None
    error: dict | None = None
    #: 外部信任裁定结果（未跑裁定则为 None，例如 trusted_only=False 时）
    trust: dict | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.manifest.id,
            "version": self.manifest.version,
            "name": self.manifest.name,
            "capabilities": list(self.manifest.capabilities),
            "dependencies": list(self.manifest.dependencies),
            "permissions": list(self.manifest.permissions),
            # trusted 报告的是**外部裁定**结果，不是插件自述值：
            # 若这里回传 manifest.trusted，门户会把"插件自称"显示成"已信任"。
            # 未裁定一律报告 False（安全字段默认取保守值）。
            "trusted": bool(self.trust and self.trust.get("trusted")),
            "declared_trusted": self.manifest.trusted,
            "trust_reason": (self.trust or {}).get("reason", ""),
            "status": self.status,
            "error": self.error,
        }


class PluginManager:
    """插件安装、启停与治理。"""

    def __init__(
        self,
        services: ServiceRegistry,
        *,
        bus: EventBus | None = None,
        sandbox: SandboxPolicy | None = None,
        trusted_only: bool | None = None,
        trust_store: PluginTrustStore | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.services = services
        self.bus = bus
        self.sandbox = sandbox
        self.trusted_only = settings.plugin_trusted_only if trusted_only is None else trusted_only
        # 未提供清单时为空清单 → fail-closed（什么插件都装不上），而不是默认放行
        self.trust_store = trust_store if trust_store is not None else PluginTrustStore()
        self.config = dict(config or {})
        self._plugins: dict[str, PluginState] = {}

    # ---------- install ----------
    def install(self, manifest: PluginManifest, plugin_dir: Path) -> PluginState:
        """安装插件：信任裁定 → 校验 → 加载 → setup。"""
        manifest.validate()
        if manifest.id in self._plugins:
            raise PluginError("插件已安装", plugin=manifest.id, version=manifest.version)

        state = PluginState(manifest=manifest, path=plugin_dir)

        if self.trusted_only:
            decision = self.trust_store.is_trusted(manifest, plugin_dir)
            if not decision.trusted:
                raise PluginError(
                    f"插件未通过信任裁定：{decision.reason}",
                    plugin=manifest.id,
                    trust=decision.to_dict(),
                )
            state.trust = decision.to_dict()
            self._emit(EventType.PLUGIN_APPROVED, state, decision.to_dict())

        missing = [dep for dep in manifest.dependencies if dep not in self._plugins]
        if missing:
            raise PluginError("插件依赖未安装", plugin=manifest.id, missing=missing)
        if self.sandbox is not None:
            # 权限声明必须在 Sandbox 允许范围内，否则安装即失败
            self.sandbox.authorize(frozenset(manifest.permissions))

        entry_path = plugin_dir / manifest.entry_file
        if not entry_path.exists():
            raise PluginError("插件入口文件不存在", plugin=manifest.id, entry=str(entry_path))

        try:
            instance = _load_instance(entry_path, manifest)
            ctx = PluginContext(
                plugin_id=manifest.id,
                services=self.services,
                bus=self.bus,
                sandbox=self.sandbox,
                config=self.config,
            )
            instance.setup(ctx)
        except Exception as exc:
            state.status = STATUS_FAILED
            state.error = _error_of(exc)
            self._plugins[manifest.id] = state
            self._emit(EventType.PLUGIN_FAILED, state, {"stage": "install"})
            raise PluginError(
                f"插件安装失败: {exc}", plugin=manifest.id, error=state.error
            ) from exc

        state.instance = instance
        self._plugins[manifest.id] = state
        self._emit(EventType.PLUGIN_INSTALLED, state)
        return state

    def install_from_dir(self, plugin_dir: Path) -> PluginState:
        """按目录安装（读取 plugin.json）。"""
        manifest = PluginManifest.from_dir(plugin_dir)
        return self.install(manifest, plugin_dir)

    def install_all(self, directory: Path) -> list[PluginState]:
        """安装目录下全部插件；单个失败不影响其它（记录 plugin.failed）。

        目录扫描顺序不保证依赖排在前面，因此按轮次推进：每轮只尝试依赖已就绪的
        插件，直到没有新插件被安装为止（依赖缺失或成环时自然停下）。
        """
        pending = discover(directory)
        states: list[PluginState] = []
        while pending:
            deferred: list[tuple[Path, PluginManifest]] = []
            for plugin_dir, manifest in pending:
                if any(dep not in self._plugins for dep in manifest.dependencies):
                    deferred.append((plugin_dir, manifest))
                    continue
                try:
                    states.append(self.install(manifest, plugin_dir))
                except PluginError:
                    continue
            if len(deferred) == len(pending):
                break
            pending = deferred
        return states

    # ---------- start / stop ----------
    async def start(self, plugin_id: str) -> PluginState:
        state = self._require(plugin_id)
        try:
            await state.instance.start()
        except Exception as exc:
            state.status = STATUS_FAILED
            state.error = _error_of(exc)
            self._emit(EventType.PLUGIN_FAILED, state, {"stage": "start"})
            raise PluginError(f"插件启动失败: {exc}", plugin=plugin_id) from exc
        state.status = STATUS_STARTED
        state.error = None
        self._emit(EventType.PLUGIN_STARTED, state)
        return state

    async def start_all(self) -> list[PluginState]:
        """按依赖顺序启动全部已安装插件。"""
        started: list[PluginState] = []
        for state in self._sorted_by_dependency():
            if state.status == STATUS_STARTED:
                continue
            try:
                started.append(await self.start(state.manifest.id))
            except PluginError:
                continue
        return started

    async def stop(self, plugin_id: str) -> PluginState:
        state = self._require(plugin_id)
        try:
            await state.instance.stop()
        except Exception as exc:
            state.status = STATUS_FAILED
            state.error = _error_of(exc)
            self._emit(EventType.PLUGIN_FAILED, state, {"stage": "stop"})
            raise PluginError(f"插件停止失败: {exc}", plugin=plugin_id) from exc
        state.status = STATUS_STOPPED
        self._emit(EventType.PLUGIN_STOPPED, state)
        return state

    async def stop_all(self) -> None:
        for state in reversed(self._sorted_by_dependency()):
            if state.status == STATUS_STARTED:
                try:
                    await self.stop(state.manifest.id)
                except PluginError:
                    continue

    async def uninstall(self, plugin_id: str) -> None:
        state = self._require(plugin_id)
        if state.status == STATUS_STARTED:
            await self.stop(plugin_id)
        for name in self.services.names():
            if self.services.describe().get(name) == plugin_id:
                self.services.revoke(name, owner=plugin_id)
        self._plugins.pop(plugin_id, None)
        self._emit(EventType.PLUGIN_UNINSTALLED, state)

    # ---------- 查询 ----------
    def get(self, plugin_id: str) -> PluginState | None:
        return self._plugins.get(plugin_id)

    def list(self) -> list[dict]:
        return [state.to_dict() for state in self._plugins.values()]

    def _require(self, plugin_id: str) -> PluginState:
        state = self._plugins.get(plugin_id)
        if state is None:
            raise PluginError(f"插件未安装: {plugin_id}", plugin=plugin_id)
        return state

    def _sorted_by_dependency(self) -> list[PluginState]:
        """按依赖拓扑排序（依赖少的先启动）；D-4 范围内插件数量小，简单排序足够。"""
        ordered: list[PluginState] = []
        remaining = dict(self._plugins)
        while remaining:
            ready = [
                state
                for state in remaining.values()
                if all(dep in {s.manifest.id for s in ordered} for dep in state.manifest.dependencies)
            ]
            if not ready:  # 存在环依赖，交由 manifest 校验层暴露
                ready = list(remaining.values())
            for state in ready:
                ordered.append(state)
                remaining.pop(state.manifest.id, None)
        return ordered

    def _emit(self, event_type: EventType, state: PluginState, extra: dict | None = None) -> None:
        if self.bus is None:
            return
        self.bus.emit(
            event_type,
            {"plugin": state.manifest.id, "version": state.manifest.version,
             "status": state.status, "error": state.error, **(extra or {})},
            source="deepprof.runtime.plugins",
        )


def _load_instance(entry_path: Path, manifest: PluginManifest) -> PluginBase:
    """从文件加载插件对象；支持暴露实例或类。"""
    module_name = f"deepprof_plugin_{manifest.id}"
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec is None or spec.loader is None:
        raise PluginError("无法加载插件模块", plugin=manifest.id, entry=str(entry_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = getattr(module, manifest.entry_object, None)
    if target is None:
        raise PluginError(
            f"插件入口属性不存在: {manifest.entry_object}", plugin=manifest.id
        )
    instance = target() if isinstance(target, type) else target
    if not isinstance(instance, PluginBase):
        raise PluginError("插件入口必须继承 PluginBase", plugin=manifest.id)
    instance.manifest = manifest
    return instance


def _error_of(exc: Exception) -> dict:
    to_dict = getattr(exc, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {"code": "plugin_error", "message": f"{type(exc).__name__}: {exc}", "details": {}}