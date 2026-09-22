"""事件流（SSE）：按 sequence 补发，重连不重复渲染。

帧构造与传输分离：``sse_frames`` 是纯生成器，便于在不起 HTTP 服务的情况下测试。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from runtime.service import RuntimeService

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 15.0
HEARTBEAT_FRAME = ": ping\n\n"


def encode_sse(event: dict[str, Any]) -> str:
    frame = f"event: {event.get('type', 'message')}\n"
    frame += f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
    return frame


async def sse_frames(
    service: RuntimeService,
    session_id: str,
    from_sequence: int = 0,
    *,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """先补发历史事件，再推送实时事件；空窗期发心跳。

    ``sequence`` 已消费过的事件不再下发，保证多 Surface 重连不重复渲染。
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def listener(payload: dict[str, Any]) -> None:
        if payload.get("session_id") == session_id:
            queue.put_nowait(payload)

    service.subscribe(listener)
    last_sequence = from_sequence
    try:
        for event in service.history(session_id, from_sequence):
            last_sequence = max(last_sequence, int(event.get("sequence", 0)))
            yield encode_sse(event)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield HEARTBEAT_FRAME
                continue
            sequence = int(event.get("sequence", 0))
            if sequence <= last_sequence:
                continue
            last_sequence = sequence
            yield encode_sse(event)
    finally:
        service.unsubscribe(listener)


@router.get("/sessions/{session_id}/events")
async def stream_events(
    session_id: str, request: Request, from_sequence: int = 0
) -> StreamingResponse:
    service: RuntimeService = request.app.state.service
    service.get_session(session_id)
    return StreamingResponse(
        sse_frames(service, session_id, from_sequence),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )