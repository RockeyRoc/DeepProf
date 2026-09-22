"""插件信任名单：MVP 只加载受信、审核后的 Python 插件（D-4）。"""

from __future__ import annotations

import json
from pathlib import Path


class TrustList:
    def __init__(self, trusted: set[str] | None = None) -> None:
        self._trusted: set[str] = set(trusted or ())

    @classmethod
    def load(cls, path: str | Path) -> "TrustList":
        target = Path(path)
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        items = data.get("trusted", data) if isinstance(data, dict) else data
        return cls({str(item) for item in items})

    def is_trusted(self, plugin_id: str) -> bool:
        return plugin_id in self._trusted

    def trust(self, plugin_id: str) -> None:
        self._trusted.add(plugin_id)

    def revoke(self, plugin_id: str) -> None:
        self._trusted.discard(plugin_id)

    def as_list(self) -> list[str]:
        return sorted(self._trusted)