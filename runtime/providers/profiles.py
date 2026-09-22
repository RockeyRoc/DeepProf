"""Provider Profile：配置对象，不写入教学图，不持有明文密钥。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

PROTOCOL_OPENAI_COMPATIBLE = "openai_compatible"
PROTOCOL_NATIVE = "native"
PROTOCOL_LOCAL = "local"

API_MODE_CHAT = "chat_completions"
API_MODE_RESPONSES = "responses"
API_MODE_AUTO = "auto"


class Protocol(str, Enum):
    OPENAI_COMPATIBLE = PROTOCOL_OPENAI_COMPATIBLE
    NATIVE = PROTOCOL_NATIVE
    LOCAL = PROTOCOL_LOCAL


@dataclass(slots=True)
class ProviderProfile:
    """一套 OpenAI-compatible / 本地模型接入配置。"""

    profile_id: str
    display_name: str = ""
    protocol: str = PROTOCOL_OPENAI_COMPATIBLE
    base_url: str = ""
    api_key_ref: str = ""
    default_model: str = ""
    models: list[str] = field(default_factory=list)
    api_mode: str = API_MODE_AUTO
    extra_headers: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 0
    max_retries: int = 0
    capabilities: dict[str, bool] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self, *, include_secret_ref: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "models": list(self.models),
            "api_mode": self.api_mode,
            "extra_headers": dict(self.extra_headers),
            "timeout_ms": self.timeout_ms,
            "max_retries": self.max_retries,
            "capabilities": dict(self.capabilities),
            "enabled": self.enabled,
        }
        if include_secret_ref:
            data["api_key_ref"] = self.api_key_ref
        return data

    def export(self) -> dict[str, Any]:
        """导出（默认剔除 secret 引用，只保留可迁移字段）。"""
        data = self.to_dict(include_secret_ref=False)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderProfile":
        return cls(
            profile_id=str(data["profile_id"]),
            display_name=str(data.get("display_name", "")),
            protocol=str(data.get("protocol", PROTOCOL_OPENAI_COMPATIBLE)),
            base_url=str(data.get("base_url", "")),
            api_key_ref=str(data.get("api_key_ref", "")),
            default_model=str(data.get("default_model", "")),
            models=list(data.get("models") or []),
            api_mode=str(data.get("api_mode", API_MODE_AUTO)),
            extra_headers=dict(data.get("extra_headers") or {}),
            timeout_ms=int(data.get("timeout_ms") or 0),
            max_retries=int(data.get("max_retries") or 0),
            capabilities=dict(data.get("capabilities") or {}),
            enabled=bool(data.get("enabled", True)),
        )


# 角色 -> (profile_id, model) 的逻辑路由；由组合根注入，教学代码不写厂商名。
RoleBinding = tuple[str, str]
RoleMap = dict[str, RoleBinding]