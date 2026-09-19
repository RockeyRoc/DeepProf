"""插件信任裁定：由外部清单决定，不看插件自述（DESIGNv0.4 §5.2 / §13.3 / D-4）。

**为什么需要这个模块**

`plugin.json` 里的 `trusted` 字段由插件包自己提供。若安装时信任该字段，
就等于让被信任方自证清白——任何插件只要写一个 `"trusted": true` 就能通过
`PLUGIN_TRUSTED_ONLY=true`，该开关形同虚设。

因此把信任记录放在**插件包之外**、由运维维护的清单文件里（`plugin_trust_store`），
并用内容摘要把"信任"绑定到具体制品：

- 清单里没有该 `id` → 拒绝；
- 版本不一致 → 拒绝；
- 内容摘要不一致（插件被改动过）→ 拒绝，且需重新审批。

插件目录里的 `trusted` 字段从此只作**声明与展示**用途，不参与裁定。

与工具侧的对称性：Tool 有 `requires_approval` + `ApprovalGate` + `tool.approved`；
插件侧对应 `PluginTrustStore` + `plugin.approved`（见 lifecycle.install）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from config import ensure_parent

from ..core.message import utc_now
from .manifest import PluginManifest

#: 计算内容摘要时跳过的目录与后缀（编译产物与运行期产物不属制品内容）
DIGEST_SKIP_DIRS = frozenset({"__pycache__"})
DIGEST_SKIP_SUFFIXES = (".pyc", ".pyo")

#: 清单文件格式版本，便于后续演进时识别
TRUST_FILE_VERSION = 1


def plugin_digest(plugin_dir: Path) -> str:
    """对插件目录内容取确定性摘要（相对路径 + 文件内容）。

    摘要把审批绑定到具体制品：插件内容一旦改动，原审批自动失效。
    顺序固定（按相对路径排序），因此同一份内容在任何机器上得到同一摘要。
    """
    digest = hashlib.sha256()
    for path in _iter_artifacts(plugin_dir):
        digest.update(path.relative_to(plugin_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _iter_artifacts(root: Path) -> Iterator[Path]:
    """按相对路径排序产出参与摘要的文件。"""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in DIGEST_SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if path.suffix in DIGEST_SKIP_SUFFIXES:
            continue
        yield path


@dataclass
class TrustRecord:
    """一条外部审批记录：谁、什么时候、批准了哪个版本与哪份内容。"""

    id: str
    version: str = ""
    sha256: str = ""
    approved_by: str = ""
    approved_at: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "sha256": self.sha256,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrustRecord":
        return cls(
            id=str(data.get("id", "")),
            version=str(data.get("version", "")),
            sha256=str(data.get("sha256", "")),
            approved_by=str(data.get("approved_by", "")),
            approved_at=str(data.get("approved_at", "")),
            note=str(data.get("note", "")),
        )


@dataclass
class TrustDecision:
    """裁定结果。``reason`` 面向排障者，必须说明"为什么不被信任"。"""

    trusted: bool
    reason: str
    record: TrustRecord | None = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trusted": self.trusted,
            "reason": self.reason,
            "record": self.record.to_dict() if self.record else None,
            **self.details,
        }


class PluginTrustStore:
    """运维维护的插件信任清单（外部裁定，不与插件包同源）。

    ``path`` 为空时清单为空，此时``trusted_only`` 语义为"什么都不能装"——
    这是有意的 fail-closed：没配清单就不装插件，而不是默认放行。

    清单文件形状::

        {
          "version": 1,
          "plugins": [
            {"id": "example_echo", "version": "0.1.0", "sha256": "...",
             "approved_by": "刘俊鹏", "approved_at": "...", "note": ""}
          ]
        }
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._records: dict[str, TrustRecord] = {}
        if self.path is not None and self.path.exists():
            self._load()

    # ---------- 裁定 ----------
    def is_trusted(self, manifest: PluginManifest, plugin_dir: Path) -> TrustDecision:
        """按外部清单裁定该插件是否可信；任何不确定都返回不可信。"""
        if not self._records:
            return TrustDecision(
                False,
                "未配置插件信任清单（plugin_trust_store），按 fail-closed 拒绝安装",
            )

        record = self._records.get(manifest.id)
        if record is None:
            return TrustDecision(
                False,
                f"插件 {manifest.id} 不在信任清单中"
                "（manifest 里的 trusted 字段由插件自述，不作为裁定依据）",
            )

        if record.version and record.version != manifest.version:
            return TrustDecision(
                False,
                f"版本与审批记录不一致：清单为 {record.version}，插件为 {manifest.version}",
                record,
            )

        if record.sha256:
            actual = plugin_digest(plugin_dir)
            if actual != record.sha256:
                return TrustDecision(
                    False,
                    "插件内容与审批记录不一致（摘要不匹配）：制品已被改动，需重新审批",
                    record,
                    {"expected_sha256": record.sha256, "actual_sha256": actual},
                )

        return TrustDecision(
            True,
            f"已通过外部信任裁定（审批人：{record.approved_by or '未记录'}）",
            record,
        )

    # ---------- 清单维护（运维动作） ----------
    def approve(
        self, plugin_dir: Path, *, approved_by: str = "", note: str = ""
    ) -> TrustRecord:
        """审批一个插件目录：记录 id、版本与内容摘要，并持久化。

        插件内容之后的任何改动都会使摘要失配，从而需要重新审批。
        """
        manifest = PluginManifest.from_dir(plugin_dir)
        record = TrustRecord(
            id=manifest.id,
            version=manifest.version,
            sha256=plugin_digest(plugin_dir),
            approved_by=approved_by,
            approved_at=utc_now(),
            note=note,
        )
        self._records[record.id] = record
        self._persist()
        return record

    def revoke(self, plugin_id: str) -> bool:
        """撤销审批；返回是否确有记录被移除。"""
        removed = self._records.pop(plugin_id, None) is not None
        if removed:
            self._persist()
        return removed

    def records(self) -> list[dict]:
        """供门户/排障查看的已审批清单。"""
        return [record.to_dict() for record in self._records.values()]

    # ---------- 持久化 ----------
    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for item in data.get("plugins") or []:
            if not isinstance(item, dict):
                continue
            record = TrustRecord.from_dict(item)
            if record.id:
                self._records[record.id] = record

    def _persist(self) -> None:
        if self.path is None:
            return
        ensure_parent(self.path)
        payload = {
            "version": TRUST_FILE_VERSION,
            "plugins": [record.to_dict() for record in self._records.values()],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


__all__ = [
    "DIGEST_SKIP_DIRS",
    "DIGEST_SKIP_SUFFIXES",
    "TRUST_FILE_VERSION",
    "PluginTrustStore",
    "TrustDecision",
    "TrustRecord",
    "plugin_digest",
]