"""Pi 事件 → DeepProf 事件投影。

开发者模式的事件只用于观测嵌入实验，不进入 Learner Session 事实源。
"""

from __future__ import annotations

from typing import Any

from runtime.core.events import SOURCE_RUNTIME

# Pi 事件名 → DeepProf 事件类型（未映射的按原样透传到 payload）
PI_EVENT_MAP = {
    "message.delta": "model.stream.delta",
    "message.completed": "model.completed",
    "tool.started": "tool.started",
    "tool.completed": "tool.completed",
    "tool.failed": "tool.failed",
    "session.started": "session.started",
    "session.ended": "session.ended",
}

SOURCE_PI = "pi"


def adapt_event(frame: dict[str, Any], *, session_id: str = "", trace_id: str = "") -> dict[str, Any] | None:
    """把一帧 Pi 通知投影为 DeepProf 事件 dict；无法识别时返回 None。"""
    name = str(frame.get("event") or frame.get("type") or "")
    mapped = PI_EVENT_MAP.get(name)
    if mapped is None:
        return None
    payload = dict(frame.get("payload") or frame.get("data") or {})
    if mapped == "model.stream.delta" and "text" not in payload and "delta" in payload:
        payload["text"] = payload.pop("delta")
    return {
        "type": mapped,
        "payload": payload,
        "session_id": session_id,
        "trace_id": trace_id,
        "source": SOURCE_PI,
    }


def is_developer_only_event(event: dict[str, Any]) -> bool:
    """开发者模式事件带 ``source=pi``，不应混入学生链路。"""
    return event.get("source") == SOURCE_PI


__all__ = ["PI_EVENT_MAP", "SOURCE_PI", "SOURCE_RUNTIME", "adapt_event", "is_developer_only_event"]