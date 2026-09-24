"""沙箱策略：目录白名单 + 网络域名白名单。

沙箱是风险降低措施，不是绝对安全保证（§13.3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config import paths
from config.settings import Settings

DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = ()


@dataclass(slots=True)
class SandboxPolicy:
    allowed_dirs: list[Path] = field(default_factory=list)
    allowed_files: list[Path] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    allow_process: bool = False
    allow_network: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> "SandboxPolicy":
        # 用户数据根必须始终可写（session DB / memory 就在那里），
        # 即便 DEEPPROF_HOME 被覆盖到别处，白名单也要跟着走。
        dirs = [paths.expand(item) for item in settings.sandbox_allowlist]
        home = paths.expand(paths.deepprof_home())
        if home not in dirs:
            dirs.append(home)
        exact_files = [settings.resolved_data_structures_pdf_path] if settings.resolved_data_structures_pdf_path else []
        return cls(allowed_dirs=dirs, allowed_files=exact_files, allowed_domains=list(DEFAULT_ALLOWED_DOMAINS))

    def is_path_allowed(self, target: str | Path) -> bool:
        candidate = paths.expand(target)
        if candidate in self.allowed_files:
            return True
        for directory in self.allowed_dirs:
            try:
                candidate.relative_to(directory)
                return True
            except ValueError:
                continue
        return False

    def check_path(self, target: str | Path) -> None:
        if not self.is_path_allowed(target):
            from runtime.core.errors import RuntimeFailure

            raise RuntimeFailure(
                f"sandbox_denied: 路径越界 {target}",
                details={"kind": "sandbox_denied", "path": str(target)},
            )

    def is_domain_allowed(self, domain: str) -> bool:
        if not self.allow_network:
            return False
        if not self.allowed_domains:
            # 未配置域名白名单时不做域限制（由资源库自己的 allowlist 决定）
            return True
        host = domain.lower().strip()
        return any(host == item or host.endswith(f".{item}") for item in self.allowed_domains)
