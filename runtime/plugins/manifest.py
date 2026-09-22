"""Plugin manifest：唯一 id、版本、能力声明与权限分域。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PERMISSION_KEYS = ("files", "network", "process", "secrets", "data")


@dataclass(slots=True)
class PluginManifest:
    id: str
    version: str = "0.0.0"
    description: str = ""
    entry: str = ""
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    root: str = ""

    def permission(self, name: str) -> Any:
        return self.permissions.get(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "description": self.description,
            "entry": self.entry,
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "permissions": dict(self.permissions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, root: str = "") -> "PluginManifest":
        unknown = [key for key in data.get("permissions", {}) if key not in PERMISSION_KEYS]
        if unknown:
            raise ValueError(f"plugin {data.get('id')!r} 声明了未知权限域: {unknown}")
        return cls(
            id=str(data.get("id") or ""),
            version=str(data.get("version", "0.0.0")),
            description=str(data.get("description", "")),
            entry=str(data.get("entry", "")),
            capabilities=list(data.get("capabilities") or []),
            dependencies=list(data.get("dependencies") or []),
            permissions=dict(data.get("permissions") or {}),
            root=root,
        )

    @classmethod
    def load(cls, path: str | Path) -> "PluginManifest":
        target = Path(path)
        data = json.loads(target.read_text(encoding="utf-8"))
        return cls.from_dict(data, root=str(target.parent))