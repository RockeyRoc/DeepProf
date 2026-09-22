"""Gateway 请求/响应模型。密钥是只写字段，永不回显。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "packages" / "contracts"
_COMMANDS = json.loads((_CONTRACTS_DIR / "client_command.json").read_text(encoding="utf-8"))
COMMAND_TYPES: frozenset[str] = frozenset(_COMMANDS["command_types"])

Surface = Literal["desktop", "cli", "pet"]


class ClientCommand(BaseModel):
    """三端统一命令契约。"""

    command_id: str
    client_id: str = "unknown"
    surface: Surface = "desktop"
    session_id: str | None = None
    learner_id: str = "local"
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in COMMAND_TYPES:
            raise ValueError(f"unknown command type: {value!r}; known={sorted(COMMAND_TYPES)}")
        return value


class CommandAccepted(BaseModel):
    command_id: str
    session_id: str | None
    status: str = "accepted"
    result: dict[str, Any] | None = None


class ProviderProfileIn(BaseModel):
    """Provider Settings 提交体；``api_key`` 只写不读。"""

    profile_id: str
    display_name: str = ""
    protocol: Literal["openai_compatible", "native", "local"] = "openai_compatible"
    base_url: str = ""
    api_key: str | None = None
    api_key_ref: str = ""
    default_model: str = ""
    models: list[str] = Field(default_factory=list)
    api_mode: Literal["chat_completions", "responses", "auto"] = "auto"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = 0
    max_retries: int = 0
    capabilities: dict[str, bool] = Field(default_factory=dict)
    enabled: bool = True


class ProviderProfileOut(BaseModel):
    """对外视图：只有 ``api_key_ref``，没有任何明文密钥。"""

    profile_id: str
    display_name: str
    protocol: str
    base_url: str
    api_key_ref: str
    default_model: str
    models: list[str]
    api_mode: str
    timeout_ms: int
    max_retries: int
    capabilities: dict[str, bool]
    enabled: bool
    has_secret: bool = False


class ProbeRequest(BaseModel):
    model: str | None = None


class SessionSummary(BaseModel):
    session_id: str
    learner_id: str
    title: str
    parent_id: str | None = None
    created_at: str
    updated_at: str
    last_sequence: int
    message_count: int


class SessionMessageView(BaseModel):
    index: int
    role: str
    content: str
    name: str | None = None


class ProviderSelection(BaseModel):
    role: str = "tutor.default"
    profile_id: str
    model: str = ""
