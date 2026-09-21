"""Sandbox 策略（DESIGNv0.4 §5.3 / D-5 / §13.3）。

演示版采用"目录白名单 + 审批"：
- 默认拒绝越界路径；
- 网络与进程默认关闭；
- 危险动作交由 ApprovalGate 显式审批。

Sandbox 是风险降低措施，不是绝对安全保证（§13.3），
启用任意代码执行前必须升级为进程/容器隔离。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config import PROJECT_ROOT, project_path
from config import settings as default_settings

from ..core.errors import PermissionDenied

# 权限标识：Tool 与 Plugin manifest 都使用这一套命名
PERM_FS_READ = "fs.read"
PERM_FS_WRITE = "fs.write"
PERM_FS_DELETE = "fs.delete"
PERM_NETWORK = "net"
PERM_PROCESS = "process"
PERM_SECRET = "secret"
PERM_LEARNER_DATA = "data.learner"

ALL_PERMISSIONS = frozenset(
    {
        PERM_FS_READ,
        PERM_FS_WRITE,
        PERM_FS_DELETE,
        PERM_NETWORK,
        PERM_PROCESS,
        PERM_SECRET,
        PERM_LEARNER_DATA,
    }
)


@dataclass
class SandboxPolicy:
    """目录白名单与权限开关。"""

    allowed_roots: list[Path] = field(default_factory=list)
    allow_network: bool = False
    allow_process: bool = False
    allow_secret: bool = False

    @classmethod
    def from_settings(cls, allowed_roots: str = "", **overrides) -> "SandboxPolicy":
        """按配置构造：逗号分隔的目录列表。

        相对路径相对项目根；``~/`` 开头相对用户主目录（先 expanduser 再判定，
        绝对路径原样放行）。
        """
        raw = allowed_roots or default_settings.sandbox_allowed_roots
        roots = [
            project_path(Path(item.strip()).expanduser()).resolve()
            for item in raw.split(",")
            if item.strip()
        ]
        return cls(
            allowed_roots=roots,
            allow_network=overrides.get("allow_network", default_settings.sandbox_allow_network),
            allow_process=overrides.get("allow_process", default_settings.sandbox_allow_process),
            allow_secret=overrides.get("allow_secret", False),
        )

    # ---------- 路径 ----------
    def is_allowed(self, path: str | Path) -> bool:
        """路径是否位于白名单目录内。"""
        candidate = project_path(path).resolve()
        if candidate == PROJECT_ROOT.resolve():
            return False
        for root in self.allowed_roots:
            if root == candidate or root in candidate.parents:
                return True
        return False

    def resolve(self, path: str | Path, *, write: bool = False) -> Path:
        """校验并返回可用的绝对路径，越界即拒绝（默认拒绝原则）。"""
        candidate = project_path(path).resolve()
        if not self.is_allowed(candidate):
            raise PermissionDenied(
                "路径不在 Sandbox 白名单内", path=str(candidate), write=write
            )
        return candidate

    # ---------- 权限 ----------
    def authorize(self, permissions: frozenset[str] | set[str]) -> None:
        """按权限集合校验；不满足即拒绝。"""
        for permission in permissions:
            if permission == PERM_NETWORK and not self.allow_network:
                raise PermissionDenied("Sandbox 未开放网络权限", permission=permission)
            if permission == PERM_PROCESS and not self.allow_process:
                raise PermissionDenied("Sandbox 未开放进程权限", permission=permission)
            if permission == PERM_SECRET and not self.allow_secret:
                raise PermissionDenied("Sandbox 未开放密钥读取权限", permission=permission)
            if permission not in ALL_PERMISSIONS:
                raise PermissionDenied("未知权限标识", permission=permission)

    def describe(self) -> dict:
        """供事件与前端展示当前隔离级别。"""
        return {
            "allowed_roots": [str(root) for root in self.allowed_roots],
            "allow_network": self.allow_network,
            "allow_process": self.allow_process,
            "allow_secret": self.allow_secret,
        }


class ApprovalGate:
    """危险工具审批闸门。

    MVP 为进程内记录，审批结果会写入 tool.approved / tool.requested 事件，
    后续可替换为 UI 审批流而不改变 Tool 执行路径。
    """

    def __init__(self, auto_approve: set[str] | None = None) -> None:
        self._approved: set[str] = set(auto_approve or set())

    def approve(self, tool_name: str) -> None:
        self._approved.add(tool_name)

    def revoke(self, tool_name: str) -> None:
        self._approved.discard(tool_name)

    def is_approved(self, tool_name: str) -> bool:
        return tool_name in self._approved

    def clear(self) -> None:
        self._approved.clear()