"""统一消息模型（DESIGNv0.4 §5.1）。

所有角色（user / assistant / system / tool）共用一种结构，
Provider 层负责把它翻译成各家 SDK 的格式。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Role(str, Enum):
    """消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


def new_id(prefix: str) -> str:
    """生成带前缀的短标识（如 msg_3f9c1a2b）。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    """统一使用 ISO8601 UTC 时间字符串，便于跨模块比较与落盘。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolCall:
    """模型请求调用某个工具。

    与 ToolCall 一一对应的还有 ToolResult，两者通过 id 关联。
    """

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id("call")

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict) -> "ToolCall":
        return cls(
            name=data["name"],
            arguments=data.get("arguments") or {},
            id=data.get("id", ""),
        )


@dataclass
class Message:
    """统一消息。

    Attributes:
        role: system | user | assistant | tool
        content: 文本内容（工具消息为工具结果的文本化表示）
        message_id / created_at: 轨迹与幂等所需标识
        tool_calls: assistant 消息中模型请求的工具调用
        tool_call_id: tool 消息对应的调用 id
        name: 工具名（role=tool 时）
        metadata: 其它可序列化附加信息（教学动作、情感标签等）
    """

    role: Role | str
    content: str = ""
    message_id: str = ""
    created_at: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 允许外部直接传字符串角色，内部统一成枚举
        if not isinstance(self.role, Role):
            self.role = Role(str(self.role))
        if not self.message_id:
            self.message_id = new_id("msg")
        if not self.created_at:
            self.created_at = utc_now()

    @property
    def text(self) -> str:
        """content 的别名，读起来更自然。"""
        return self.content

    def to_provider_dict(self) -> dict:
        """转成 OpenAI 兼容的消息字典（DeepSeek/Qwen 同构）。"""
        data: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        # OpenAI 兼容协议要求 arguments 为 JSON 字符串
                        "arguments": _json_dumps(call.arguments),
                    },
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data

    def to_dict(self) -> dict:
        """完整序列化，用于 Session 持久化与事件负载。"""
        return {
            "role": self.role.value,
            "content": self.content,
            "message_id": self.message_id,
            "created_at": self.created_at,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            message_id=data.get("message_id", ""),
            created_at=data.get("created_at", ""),
            tool_calls=[ToolCall.from_dict(c) for c in data.get("tool_calls", [])],
            tool_call_id=data.get("tool_call_id", ""),
            name=data.get("name", ""),
            metadata=dict(data.get("metadata") or {}),
        )

    # ---- 便捷构造 ----
    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(Role.SYSTEM, content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(Role.USER, content)

    @classmethod
    def assistant(cls, content: str, tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(Role.ASSISTANT, content, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, content: str, tool_call_id: str, name: str) -> "Message":
        return cls(Role.TOOL, content, tool_call_id=tool_call_id, name=name)


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)