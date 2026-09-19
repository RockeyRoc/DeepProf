"""Plugin manifest（DESIGNv0.4 §5.2 / D-4）。

插件必须声明：唯一 id、版本、能力、依赖与权限（文件/网络/进程/密钥/数据）。
MVP 只加载受信、审核后的 Python 插件。

**注意 `trusted` 字段的性质**：它来自插件包自己的 plugin.json，因此只是插件的
**自我声明**，不构成授权——真正的"是否受信"由运维维护的外部清单裁定
（见 trust.PluginTrustStore）。安装时不要把该字段当依据，否则任何插件写一个
`"trusted": true` 就能自证清白。本字段保留用于展示与排障。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import PluginError
from ..sandbox.policy import ALL_PERMISSIONS

# 插件可提供的能力（§5.2：Tool、Skill、Provider、Storage 与 Sandbox 都可由插件提供）
CAPABILITIES = frozenset({"tool", "skill", "provider", "storage", "sandbox", "ui"})

MANIFEST_FILENAME = "plugin.json"


@dataclass
class PluginManifest:
    """插件清单。"""

    id: str
    version: str
    entry: str = ""  # "plugin.py:plugin"（相对插件目录的文件 + 属性名）
    name: str = ""
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    trusted: bool = False  # 仅插件自述，不构成授权；裁定见 trust.PluginTrustStore
    author: str = ""
    owner: str = ""  # 团队责任人，便于交接（§16）

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.id

    def validate(self) -> None:
        """manifest 自检：缺一不可，未知能力/权限直接拒绝。"""
        if not self.id:
            raise PluginError("插件缺少 id")
        if not self.version:
            raise PluginError("插件缺少 version", plugin=self.id)
        if not self.entry or ":" not in self.entry:
            raise PluginError(
                "插件 entry 必须是 '文件名:属性名' 形式", plugin=self.id, entry=self.entry
            )
        unknown_caps = [cap for cap in self.capabilities if cap not in CAPABILITIES]
        if unknown_caps:
            raise PluginError("插件声明了未知能力", plugin=self.id, capabilities=unknown_caps)
        unknown_perms = [p for p in self.permissions if p not in ALL_PERMISSIONS]
        if unknown_perms:
            raise PluginError("插件声明了未知权限", plugin=self.id, permissions=unknown_perms)
        if self.id in self.dependencies:
            raise PluginError("插件不能依赖自己", plugin=self.id)

    @property
    def entry_file(self) -> str:
        return self.entry.split(":", 1)[0]

    @property
    def entry_object(self) -> str:
        return self.entry.split(":", 1)[1]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "entry": self.entry,
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "permissions": list(self.permissions),
            "trusted": self.trusted,
            "author": self.author,
            "owner": self.owner,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PluginManifest":
        return cls(
            id=str(data.get("id", "")),
            version=str(data.get("version", "")),
            entry=str(data.get("entry", "")),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            capabilities=list(data.get("capabilities") or []),
            dependencies=list(data.get("dependencies") or []),
            permissions=list(data.get("permissions") or []),
            trusted=bool(data.get("trusted", False)),
            author=str(data.get("author", "")),
            owner=str(data.get("owner", "")),
        )

    @classmethod
    def from_dir(cls, plugin_dir: Path) -> "PluginManifest":
        """从插件目录读取 plugin.json。"""
        manifest_path = plugin_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            raise PluginError(f"插件目录缺少 {MANIFEST_FILENAME}", plugin_dir=str(plugin_dir))
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginError(f"{MANIFEST_FILENAME} 不是合法 JSON", plugin_dir=str(plugin_dir)) from exc
        manifest = cls.from_dict(data)
        manifest.validate()
        return manifest


def discover(directory: Path) -> list[tuple[Path, PluginManifest]]:
    """扫描插件目录，返回 (目录, manifest) 列表；非法插件跳过并记录原因。

    设计取舍：发现阶段不因单个插件损坏而整体失败，
    但 install 阶段会再次校验并抛错。
    """
    if not directory.exists():
        return []
    found: list[tuple[Path, PluginManifest]] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or not (child / MANIFEST_FILENAME).exists():
            continue
        try:
            found.append((child, PluginManifest.from_dir(child)))
        except PluginError:
            continue
    return found