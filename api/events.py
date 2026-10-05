"""事件流（SSE）：按 sequence 补发，重连不重复渲染。

帧构造与传输分离：``sse_frames`` 是纯生成器，便于在不起 HTTP 服务的情况下测试。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from runtime.service import RuntimeService

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 15.0
HEARTBEAT_FRAME = ": ping\n\n"
MAX_SSE_QUEUE = 256
_LOGGER = logging.getLogger(__name__)


def encode_sse(event: dict[str, Any]) -> str:
    frame = f"id: {int(event.get('sequence') or 0)}\n"
    frame += f"event: {event.get('type', 'message')}\n"
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
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_SSE_QUEUE)
    overflow = asyncio.Event()
    queue_peak = 0
    overflow_count = 0

    def listener(payload: dict[str, Any]) -> None:
        nonlocal queue_peak, overflow_count
        if payload.get("session_id") == session_id:
            try:
                queue.put_nowait(payload)
                queue_peak = max(queue_peak, queue.qsize())
                metrics = getattr(service, "performance_metrics", None)
                if isinstance(metrics, dict):
                    metrics["sse_queue_peak"] = max(int(metrics.get("sse_queue_peak") or 0), queue_peak)
            except asyncio.QueueFull:
                # The browser reconnects with Last-Event-ID; persisted history replays
                # everything after the last frame it actually received.
                if not overflow.is_set():
                    overflow_count += 1
                    metrics = getattr(service, "performance_metrics", None)
                    if isinstance(metrics, dict):
                        metrics["sse_overflows"] = int(metrics.get("sse_overflows") or 0) + 1
                    overflow.set()

    service.subscribe(listener)
    last_sequence = from_sequence
    try:
        for event in service.history(session_id, from_sequence):
            last_sequence = max(last_sequence, int(event.get("sequence", 0)))
            yield encode_sse(event)
        while True:
            get_task = asyncio.create_task(queue.get())
            overflow_task = asyncio.create_task(overflow.wait())
            done, pending = await asyncio.wait({get_task, overflow_task}, timeout=heartbeat_seconds,
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if overflow_task in done and overflow.is_set():
                return
            if get_task not in done:
                yield HEARTBEAT_FRAME
                continue
            event = get_task.result()
            sequence = int(event.get("sequence", 0))
            if sequence <= last_sequence:
                continue
            last_sequence = sequence
            yield encode_sse(event)
    finally:
        service.unsubscribe(listener)
        if overflow_count:
            _LOGGER.info("gateway_sse_queue_peak=%d overflowed=true", queue_peak)


@router.get("/sessions/{session_id}/events")
async def stream_events(
    session_id: str, request: Request, from_sequence: int = Query(default=0, ge=0)
) -> StreamingResponse:
    service: RuntimeService = request.app.state.service
    service.get_session(session_id)
    last_event_id = request.headers.get("last-event-id")
    cursor = from_sequence
    if last_event_id is not None:
        if not last_event_id or not last_event_id.isascii() or not last_event_id.isdigit():
            raise HTTPException(status_code=422, detail={"code": "invalid_event_cursor", "message": "Last-Event-ID must be a non-negative sequence."})
        cursor = int(last_event_id)
    return StreamingResponse(
        sse_frames(service, session_id, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
