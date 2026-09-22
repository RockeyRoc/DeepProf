"""插件生命周期：install → start → stop → uninstall。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from runtime.plugins.manifest import PluginManifest


class PluginState(str, Enum):
    INSTALLED = "installed"
    STARTED = "started"
    STOPPED = "stopped"
    FAILED = "failed"
    UNINSTALLED = "uninstalled"


@dataclass(slots=True)
class PluginRecord:
    manifest: PluginManifest
    instance: Any = None
    state: str = PluginState.INSTALLED.value
    error: str = ""
    provided: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.manifest.id,
            "version": self.manifest.version,
            "state": self.state,
            "error": self.error,
            "capabilities": list(self.manifest.capabilities),
            "permissions": dict(self.manifest.permissions),
        }