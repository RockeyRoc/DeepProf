"""Secret 适配：只保存引用，明文密钥不进入事件、日志与仓库。"""

from __future__ import annotations

import os
import re
from typing import Protocol

ENV_PREFIX = "DEEPPROF_SECRET_"
_REDACTED = "***"


def env_var_for(api_key_ref: str) -> str:
    """把 ``api_key_ref`` 映射到环境变量名（本地开发用）。"""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", api_key_ref).strip("_").upper()
    return f"{ENV_PREFIX}{cleaned}"


def redact(value: str | None) -> str:
    return _REDACTED if value else ""


class SecretStore(Protocol):
    def get(self, ref: str) -> str | None: ...

    def set(self, ref: str, value: str) -> None: ...

    def delete(self, ref: str) -> None: ...


class InMemorySecretStore:
    """测试与开发用的内存凭据库。"""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._values: dict[str, str] = dict(initial or {})

    def get(self, ref: str) -> str | None:
        return self._values.get(ref)

    def set(self, ref: str, value: str) -> None:
        self._values[ref] = value

    def delete(self, ref: str) -> None:
        self._values.pop(ref, None)


class EnvSecretStore:
    """从环境变量解析密钥；未配置视为缺失凭据（不抛异常）。"""

    def get(self, ref: str) -> str | None:
        if not ref:
            return None
        return os.environ.get(env_var_for(ref))

    def set(self, ref: str, value: str) -> None:
        os.environ[env_var_for(ref)] = value

    def delete(self, ref: str) -> None:
        os.environ.pop(env_var_for(ref), None)


class ChainedSecretStore:
    """按顺序查询多个凭据库，返回首个命中的值。"""

    def __init__(self, *stores: SecretStore) -> None:
        self._stores = stores

    def get(self, ref: str) -> str | None:
        for store in self._stores:
            value = store.get(ref)
            if value:
                return value
        return None

    def set(self, ref: str, value: str) -> None:
        if not self._stores:
            raise RuntimeError("no secret store configured")
        self._stores[0].set(ref, value)

    def delete(self, ref: str) -> None:
        for store in self._stores:
            store.delete(ref)