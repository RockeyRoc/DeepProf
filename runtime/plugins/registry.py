"""Service Registry（DESIGNv0.4 §5.2）。

插件只能通过显式注册的服务获取能力，不得读取 Runtime 私有状态。
这样 Runtime 内部重构不会破坏插件，插件也无法越权访问数据库连接等对象。
"""
from __future__ import annotations

from typing import Any, Iterator

from ..core.errors import PluginError


class ServiceRegistry:
    """受控服务容器。"""

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._owners: dict[str, str] = {}  # 服务名 → 提供者（core 或插件 id）

    def provide(self, name: str, service: Any, *, owner: str = "core") -> None:
        """注册服务；同名服务只能由同一 owner 覆盖，避免插件悄悄替换核心服务。"""
        existing = self._owners.get(name)
        if existing is not None and existing != owner:
            raise PluginError(
                f"服务 {name} 已被 {existing} 提供，{owner} 不能覆盖", service=name
            )
        self._services[name] = service
        self._owners[name] = owner

    def revoke(self, name: str, *, owner: str = "") -> None:
        """撤销服务（插件卸载时调用）。"""
        if owner and self._owners.get(name) != owner:
            return
        self._services.pop(name, None)
        self._owners.pop(name, None)

    def get(self, name: str) -> Any:
        if name not in self._services:
            raise PluginError(f"服务未注册: {name}", service=name)
        return self._services[name]

    def has(self, name: str) -> bool:
        return name in self._services

    def names(self) -> list[str]:
        return sorted(self._services)

    def describe(self) -> dict[str, str]:
        """服务名 → 提供者，供审计与文档展示。"""
        return dict(self._owners)

    def __iter__(self) -> Iterator[str]:
        return iter(self._services)