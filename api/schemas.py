"""API 请求 / 响应模型（DESIGNv0.4 §18.2 交接字段）。

与 Runtime 的关系：
- 请求侧 ``SessionRequest`` 严格对齐 §18.2 的"前端 → 后端"字段；
- 响应侧 ``RuntimeEventModel`` 就是 ``runtime.core.events.RuntimeEvent``
  的 dict 形状（event_id / session_id / trace_id / sequence / type / payload /
  source / timestamp），前端按 sequence 断线重连、不重复渲染（§18.2）。

这里只做校验与形状声明，不含任何教学或学情判断。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime.core.events import RuntimeEvent

__all__ = [
    "ErrorBody",
    "EventReplayResponse",
    "HealthResponse",
    "RuntimeEventModel",
    "SessionCreateRequest",
    "SessionRequest",
    "SessionResponse",
    "event_to_dict",
    "event_type_name",
]


def event_type_name(event_type: Any) -> str:
    """把事件类型规范成 §5.4 的字符串形式（如 ``model.stream.delta``）。

    Runtime 已在 ``RuntimeEvent`` 构造时把枚举规范化成明文 value，
    这里只做兜底：枚举实例取 value，其余原样返回字符串。
    """
    value = getattr(event_type, "value", None)
    if isinstance(value, str):
        return value
    return str(event_type)


def event_to_dict(event: Any) -> dict[str, Any]:
    """RuntimeEvent → §18.2 dict 形状（type 规范化，其余字段原样透传）。"""
    data = dict(event.to_dict())
    data["type"] = event_type_name(event.type)
    return data


class SessionCreateRequest(BaseModel):
    """创建会话请求。

    session_id 可留空（由 Runtime 生成）；learner_id 用于画像隔离（§18.2）。
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(default="", description="会话标识；留空则由 Runtime 生成")
    learner_id: str = Field(default="", description="学习者标识；用于按学习者隔离数据")


class SessionRequest(BaseModel):
    """一轮对话请求（§18.2 SessionRequest：前端 → 后端）。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(default="", description="会话标识；与路径参数一致时校验通过")
    learner_id: str = Field(default="", description="学习者标识")
    request_id: str = Field(
        default="",
        description="客户端请求标识，用于前端去重与重试对齐（§18.2 幂等）",
    )
    content: str = Field(min_length=1, max_length=8000, description="学生输入文本")


class SessionResponse(BaseModel):
    """会话状态响应。"""

    session_id: str
    learner_id: str
    created_at: str
    updated_at: str
    message_count: int


class RuntimeEventModel(BaseModel):
    """Runtime 事件（§5.4 / §18.2 的统一 dict 形状）。"""

    event_id: str
    session_id: str
    trace_id: str
    sequence: int = Field(description="事件序号，前端据此断线重连且不重复渲染")
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str
    timestamp: str

    @classmethod
    def from_event(cls, event: Any) -> "RuntimeEventModel":
        """由 ``RuntimeEvent`` 构造（只做形状与 type 规范化，不加工 payload）。"""
        return cls.model_validate(event_to_dict(event))


class EventReplayResponse(BaseModel):
    """事件回放响应（GET /sessions/{id}/events，断线重连用）。"""

    session_id: str
    from_sequence: int = Field(description="本次请求的起始序号（不含）")
    latest_sequence: int = Field(description="已返回事件中的最大序号，可直接作为下次 from_sequence")
    events: list[RuntimeEventModel]


class HealthResponse(BaseModel):
    """健康检查响应。

    runtime 字段采用**白名单组装**，绝不透传 Provider 配置与密钥（§12 / §16.4）。
    """

    status: str = "ok"
    service: str = "deepprof-api"
    trace_id_header: str = Field(description="Runtime 透传 trace_id 所用的请求头名")
    runtime: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    """结构化错误体（失败必须结构化，便于前端与教学图处理，§7.3）。"""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)