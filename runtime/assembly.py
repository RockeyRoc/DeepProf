"""组合根辅助：把 Provider Hub、存储、记忆与绑定表装配成 RuntimeService。

真正的组合根是 ``api/app.py``；本模块只提供可复用的装配函数，
避免把装配逻辑散落到 Runtime 内部。
"""

from __future__ import annotations

from typing import Any

from config import paths
from config.settings import Settings
from runtime.providers.factory import build_registry, load_profiles, load_role_map
from runtime.providers.profiles import ProviderProfile
from runtime.providers.secrets import ChainedSecretStore, EnvSecretStore, SecretStore
from runtime.service import RuntimeService
from runtime.storage.sqlite_store import SqliteEventStore, SqliteSessionStore


def build_runtime_service(
    *,
    settings: Settings | None = None,
    bindings: dict[str, dict[str, Any]] | None = None,
    profiles: list[ProviderProfile] | None = None,
    secret_store: SecretStore | None = None,
    role_map: dict[str, tuple[str, str]] | None = None,
    transport: Any = None,
    sqlite_path: str | None = None,
    with_mock: bool = False,
) -> RuntimeService:
    """按用户数据根装配一套可运行的 Runtime。

    教育类 Skill 与检索 Tool 由后续模块（MVP-2 / MVP-4）通过
    ``service.skills.register`` / ``service.tools.register`` 注入；
    MVP-1 不预置任何教学能力，避免 Runtime 反向依赖教学词汇。
    """
    resolved = settings or Settings.from_env()
    paths.ensure_layout()

    secrets: SecretStore = secret_store or ChainedSecretStore(EnvSecretStore())
    registry = build_registry(
        profiles if profiles is not None else load_profiles(),
        secret_store=secrets,
        settings=resolved,
        transport=transport,
        role_map=role_map if role_map is not None else load_role_map(),
        with_mock=with_mock,
    )

    db = sqlite_path or str(resolved.resolved_sqlite_path)
    service = RuntimeService(
        router=registry,
        settings=resolved,
        event_store=SqliteEventStore.open(db),
        session_store=SqliteSessionStore.open(db),
        bindings=bindings or {},
    )
    return service
