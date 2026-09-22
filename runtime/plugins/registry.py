"""插件注册表：受信插件的注册、启停与事件记录。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from runtime.core.errors import RuntimeFailure
from runtime.core.events import EventType
from runtime.plugins.lifecycle import PluginRecord, PluginState
from runtime.plugins.manifest import PluginManifest
from runtime.plugins.trust import TrustList

EmitHook = Callable[[str, dict[str, Any]], Awaitable[None]]


class PluginRegistry:
    def __init__(self, trust: TrustList | None = None, *, emit: EmitHook | None = None) -> None:
        self._trust = trust or TrustList()
        self._records: dict[str, PluginRecord] = {}
        self._emit = emit

    @property
    def trust(self) -> TrustList:
        return self._trust

    def register(self, manifest: PluginManifest, instance: Any = None) -> PluginRecord:
        record = PluginRecord(manifest=manifest, instance=instance)
        self._records[manifest.id] = record
        return record

    def get(self, plugin_id: str) -> PluginRecord:
        if plugin_id not in self._records:
            raise RuntimeFailure(
                f"plugin_not_found: {plugin_id}",
                details={"kind": "plugin_not_found", "plugin_id": plugin_id},
            )
        return self._records[plugin_id]

    def list(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._records.values()]

    async def start(self, plugin_id: str, ctx: dict[str, Any] | None = None) -> PluginRecord:
        record = self.get(plugin_id)
        if not self._trust.is_trusted(plugin_id):
            record.state = PluginState.FAILED.value
            record.error = "插件不在信任名单内，拒绝加载"
            await self._notify(EventType.PLUGIN_FAILED, {"plugin_id": plugin_id, "error": {"code": "untrusted_plugin", "message": record.error}})
            return record
        for dependency in record.manifest.dependencies:
            dependency_record = self._records.get(dependency)
            if dependency_record is None or dependency_record.state != PluginState.STARTED.value:
                record.state = PluginState.FAILED.value
                record.error = f"依赖未启动: {dependency}"
                await self._notify(
                    EventType.PLUGIN_FAILED,
                    {"plugin_id": plugin_id, "error": {"code": "dependency_missing", "message": record.error}},
                )
                return record
        try:
            if record.instance is not None and hasattr(record.instance, "start"):
                result = record.instance.start(dict(ctx or {}))
                if hasattr(result, "__await__"):
                    await result
        except Exception as exc:
            record.state = PluginState.FAILED.value
            record.error = str(exc)
            await self._notify(
                EventType.PLUGIN_FAILED,
                {"plugin_id": plugin_id, "error": {"code": "plugin_start_failed", "message": str(exc)}},
            )
            return record
        record.state = PluginState.STARTED.value
        record.error = ""
        await self._notify(
            EventType.PLUGIN_STARTED, {"plugin_id": plugin_id, "version": record.manifest.version}
        )
        return record

    async def stop(self, plugin_id: str) -> PluginRecord:
        record = self.get(plugin_id)
        if record.instance is not None and hasattr(record.instance, "stop"):
            result = record.instance.stop()
            if hasattr(result, "__await__"):
                await result
        record.state = PluginState.STOPPED.value
        await self._notify(EventType.PLUGIN_STOPPED, {"plugin_id": plugin_id})
        return record

    async def uninstall(self, plugin_id: str) -> None:
        record = self.get(plugin_id)
        if record.state == PluginState.STARTED.value:
            await self.stop(plugin_id)
        record.state = PluginState.UNINSTALLED.value
        self._records.pop(plugin_id, None)

    async def start_all(self, ctx: dict[str, Any] | None = None) -> list[PluginRecord]:
        return [await self.start(plugin_id, ctx) for plugin_id in list(self._records)]

    async def _notify(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self._emit is None:
            return
        await self._emit(event_type.value, payload)