"""会话接入路由（DESIGNv0.4 §8 会话接入与流式输出；§7.2 主会话链路；§18.2）。

四条链路：
1. ``POST /sessions``                     创建/恢复会话；
2. ``POST /sessions/{id}/messages``       一轮原始对话（Agent 循环），SSE 推送 Runtime 事件；
3. ``POST /sessions/{id}/teaching-turn``  一轮教学策略图（UI/API → Graph → RuntimePort，§9.1）；
4. ``GET  /sessions/{id}/events``         按 sequence 回放事件（断线重连）。

关键约定：
- 流式一律用 SSE（``text/event-stream``），帧的 ``data`` 就是
  ``RuntimeEvent`` 的 dict 形状（§5.4 / §18.2），``id`` 为 sequence，
  ``event`` 为便于前端 switch 的粗粒度名字（delta/tool/done/error/event）；
- ``trace_id`` 透传：请求头 ``settings.trace_id_header`` 存在时作为本轮 trace_id，
  否则由 Runtime 生成（§18.2 trace 可追溯）；
- 会话不存在返回结构化 404，而不是 500；
- 前端可按 ``sequence`` 重连补事件，不重复渲染（§18.2）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from config import settings
from graph.education.builder import run_teaching_turn
from graph.education.state import DEFAULT_STATE, new_state
from runtime.core.events import EventType, RuntimeEvent
from runtime.core.message import Message
from runtime.core.session import Session
from runtime.service import RuntimeService

from ..deps import get_runtime_service
from ..schemas import (
    EventReplayResponse,
    RuntimeEventModel,
    SessionCreateRequest,
    SessionRequest,
    SessionResponse,
    event_to_dict,
    event_type_name,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])

#: 交前端的三件套 + 依据（§16.3）；由 API 层合成，不入事件库
_RESULT_EVENT_TYPE = "pedagogy.result"

#: Runtime 事件类型 → SSE 事件名（前端只需 switch 这几个名字，不必认识全部事件类型）；
#: 教学图的收尾帧同样用 ``done``，前端处理两种链路的结束方式保持一致
_SSE_EVENT_NAMES = {
    EventType.MODEL_STREAM_DELTA.value: "delta",
    EventType.AGENT_TURN_COMPLETED.value: "done",
    EventType.AGENT_FAILED.value: "error",
    EventType.MODEL_FAILED.value: "error",
    EventType.TOOL_COMPLETED.value: "tool",
    EventType.TOOL_FAILED.value: "tool",
    _RESULT_EVENT_TYPE: "done",
}
_DEFAULT_SSE_EVENT = "event"

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # 关闭反向代理缓冲，避免流式被攒到最后
}

#: 终结事件：收到即表示本轮结束
_TERMINAL_TYPES = frozenset(
    {EventType.AGENT_TURN_COMPLETED.value, EventType.AGENT_FAILED.value}
)


# ================= 会话创建 =================
@router.post(
    "",
    response_model=SessionResponse,
    status_code=201,
    summary="创建或恢复会话",
    responses={409: {"description": "learner_id 与该会话已绑定学习者不一致"}},
)
def create_session(
    payload: SessionCreateRequest,
    service: RuntimeService = Depends(get_runtime_service),
) -> SessionResponse:
    """创建会话；``session_id`` 已存在时按恢复处理（幂等，§18.2）。

    恢复不会改写已绑定的 ``learner_id``：一个学习者的数据不能因会话切换被覆盖，
    也不允许换一个 learner_id 接管别人的 session（§17.3 用户级隔离）。
    """
    existing = service.session_store.load(payload.session_id) if payload.session_id else None
    if existing is not None:
        if payload.learner_id and existing.learner_id and payload.learner_id != existing.learner_id:
            raise _conflict(
                "learner_mismatch",
                "该会话已绑定其它学习者，拒绝接管",
                session_id=existing.session_id,
            )
        session = service.open_session(existing.session_id, learner_id=existing.learner_id)
    else:
        session = service.open_session(payload.session_id, learner_id=payload.learner_id)
    return _to_session_response(session)


# ================= 一轮对话（SSE） =================
@router.post(
    "/{session_id}/messages",
    summary="发送一轮消息（SSE 流式输出）",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Runtime 事件流"},
        404: {"description": "会话不存在（session_not_found）"},
    },
)
async def post_message(
    session_id: str,
    payload: SessionRequest,
    request: Request,
    service: RuntimeService = Depends(get_runtime_service),
) -> StreamingResponse:
    """执行一轮对话，并把 ``run_turn`` 产生的 Runtime 事件以 SSE 推送。

    每一帧：
        id: {sequence}
        event: delta | tool | done | error | event
        data: {"event_id":..., "session_id":..., "trace_id":..., "sequence":...,
               "type":..., "payload":..., "source":..., "timestamp":...}
    """
    session = _require_session_for_turn(service, session_id, payload)
    trace_id = (request.headers.get(settings.trace_id_header) or "").strip()

    return StreamingResponse(
        _event_stream(service, session, payload.content, trace_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ================= 一轮教学策略图（SSE） =================
@router.post(
    "/{session_id}/teaching-turn",
    summary="按教学策略图执行一轮（SSE 流式输出）",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "教学图与 Runtime 事件流"},
        404: {"description": "会话不存在（session_not_found）"},
    },
)
async def post_teaching_turn(
    session_id: str,
    payload: SessionRequest,
    request: Request,
    service: RuntimeService = Depends(get_runtime_service),
) -> StreamingResponse:
    """用 Pedagogical Graph 跑一轮教学（§9.1：UI / API → Graph → RuntimePort）。

    与 ``/messages`` 的区别：这条链路先经过教学策略图（Assess → Teach/Ask/Hint/
    Correct → Test → UpdateProfile），由策略决定"讲/问/提示/纠错/测验"，
    模型与工具都由图通过 RuntimePort 调用。

    最后一帧 ``event: done`` 的 payload 是交前端的三件套（§16.3）：
    ``response_text`` / ``action`` / ``emotion``（附 ``citations``）。
    该帧由 API 层合成、不入事件库（事件库里只留决策与依据，不复制正文，§13.2）。
    """
    session = _require_session_for_turn(service, session_id, payload)
    trace_id = (request.headers.get(settings.trace_id_header) or "").strip()

    return StreamingResponse(
        _teaching_event_stream(service, session, payload.content, trace_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ================= 事件回放 =================
@router.get(
    "/{session_id}/events",
    response_model=EventReplayResponse,
    summary="按 sequence 回放会话事件（断线重连）",
    responses={404: {"description": "会话不存在（session_not_found）"}},
)
def get_events(
    session_id: str,
    from_sequence: int = Query(0, ge=0, description="只返回 sequence 大于该值的事件"),
    service: RuntimeService = Depends(get_runtime_service),
) -> EventReplayResponse:
    """回放事件：前端用上次渲染到的 sequence 作为 ``from_sequence`` 即可补齐且不重复渲染。"""
    if service.session_store.load(session_id) is None:
        raise _not_found(session_id)
    events = [
        RuntimeEventModel.from_event(event)
        for event in service.replay(session_id, from_sequence)
    ]
    latest_sequence = max(
        (event.sequence for event in events), default=from_sequence
    )
    return EventReplayResponse(
        session_id=session_id,
        from_sequence=from_sequence,
        latest_sequence=latest_sequence,
        events=events,
    )


# ================= 内部实现 =================
async def _event_stream(
    service: RuntimeService,
    session: Session,
    content: str,
    trace_id: str,
) -> AsyncIterator[str]:
    """驱动 ``run_turn`` 并把事件总线上的事件转成 SSE 帧。

    实现要点：先订阅事件流，再并发执行本轮对话；
    事件由 Runtime 自己写入（含真实 sequence），API 只做转发，
    因此前端拿到的事件与落盘轨迹完全一致，可断线重连对账。
    """
    subscription = service.bus.open_stream(session.session_id)
    failure: dict | None = None

    async def _drive() -> None:
        # AgentChunk 本身不被直接转发：它承载的文本/工具结果已由 Runtime 写成事件
        async for _chunk in service.run_turn(session, content, trace_id=trace_id):
            pass

    task = asyncio.create_task(_drive())

    def _on_done(finished: asyncio.Task) -> None:
        """本轮结束（正常或异常）即结束订阅，避免流悬挂。"""
        nonlocal failure
        if not finished.cancelled():
            exc = finished.exception()
            if exc is not None:  # 兜底：非 Runtime 结构化的意外异常也要让前端看到
                failure = {
                    "code": getattr(exc, "code", "runtime_error"),
                    "message": str(exc),
                    "details": getattr(exc, "details", {}),
                }
        service.bus.close_stream(subscription)

    task.add_done_callback(_on_done)
    last_sequence = 0
    try:
        async for event in subscription:
            last_sequence = event.sequence
            yield _frame(event)
            if event_type_name(event.type) in _TERMINAL_TYPES:
                break
        if failure is not None:
            # source 标明该帧由 API 层兜底生成，不在事件库中（避免前端误以为已落盘）；
            # sequence 接在真实事件之后，保证 SSE 的 id 行始终单调递增（§18.2 重连对账）
            yield _frame(
                RuntimeEvent(
                    type=str(EventType.AGENT_FAILED),
                    payload={"error": failure},
                    session_id=session.session_id,
                    trace_id=trace_id,
                    sequence=last_sequence + 1,
                    source="deepprof.api",
                )
            )
    finally:
        service.bus.close_stream(subscription)
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def _frame(event: RuntimeEvent) -> str:
    """把 RuntimeEvent 转成一条 SSE 帧（data 即 §18.2 的事件 dict 形状）。"""
    name = _SSE_EVENT_NAMES.get(event_type_name(event.type), _DEFAULT_SSE_EVENT)
    data = json.dumps(event_to_dict(event), ensure_ascii=False)
    return f"id: {event.sequence}\nevent: {name}\ndata: {data}\n\n"


# ---------- 教学策略图链路 ----------
async def _teaching_event_stream(
    service: RuntimeService,
    session: Session,
    content: str,
    trace_id: str,
) -> AsyncIterator[str]:
    """驱动一轮教学策略图，并把图产生的事件转成 SSE 帧。

    图的事件没有"本轮结束"标记（它表达的是节点进出与决策），
    因此这里以"图执行完成"为界：先并发订阅实时事件，图结束后再按 sequence
    补齐事件库中尚未推送的部分，保证既不丢事件也不重复（sequence 严格递增）。
    """
    state = _next_pedagogy_state(session, content, trace_id)
    subscription = service.bus.open_stream(session.session_id)
    result: dict = {}
    failure: dict | None = None

    async def _drive() -> None:
        nonlocal result
        result = await run_teaching_turn(service, state)

    task = asyncio.create_task(_drive())
    task.add_done_callback(lambda finished: service.bus.close_stream(subscription))
    last_sequence = 0
    try:
        async for event in _events_until(subscription, task):
            last_sequence = event.sequence
            yield _frame(event)
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            failure = {
                "code": getattr(exc, "code", "graph_error"),
                "message": str(exc),
                "details": getattr(exc, "details", {}),
            }
        # 收尾：把订阅关闭前已落盘但未推送的事件补上
        for event in service.replay(session.session_id, last_sequence):
            last_sequence = event.sequence
            yield _frame(event)
        if failure is not None:
            # source 标明该帧由 API 层兜底生成、不在事件库中；
            # sequence 接在真实事件之后，保证 SSE 的 id 行单调递增（§18.2 重连对账）
            yield _frame(
                RuntimeEvent(
                    type=EventType.AGENT_FAILED.value,
                    payload={"error": failure},
                    session_id=session.session_id,
                    trace_id=trace_id,
                    sequence=last_sequence + 1,
                    source="deepprof.api",
                )
            )
            return
        yield _frame(
            _result_frame(service, session, result, trace_id, sequence=last_sequence + 1)
        )
    finally:
        service.bus.close_stream(subscription)
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def _events_until(subscription, task: asyncio.Task) -> AsyncIterator[RuntimeEvent]:
    """在 ``task`` 结束前持续产出订阅到的事件；task 结束即停止等待。"""
    iterator = subscription.__aiter__()
    while True:
        pending = asyncio.ensure_future(iterator.__anext__())
        done, _ = await asyncio.wait({pending, task}, return_when=asyncio.FIRST_COMPLETED)
        if pending not in done:  # 图先结束：取消等待，剩余事件走 replay 补齐
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
            return
        try:
            yield pending.result()
        except StopAsyncIteration:
            return


def _next_pedagogy_state(session: Session, content: str, trace_id: str) -> dict:
    """构造本轮图状态：沿用上一轮的教学状态，使 hint_level / turn_count 跨轮延续。

    图状态按 §6.4 只含 JSON 友好字段，因此可以直接放在会话元数据里随会话落盘；
    重启后 open_session 读回会话即可继续（§5.1 恢复）。
    """
    previous = dict(session.metadata.get("pedagogy") or {})
    merged = {
        **previous,
        "session_id": session.session_id,
        "learner_id": session.learner_id,
        "trace_id": trace_id,
        "user_input": content,
        "student_stopped": False,
    }
    return new_state(**merged)


def _result_frame(
    service: RuntimeService,
    session: Session,
    result: dict,
    trace_id: str,
    *,
    sequence: int,
) -> RuntimeEvent:
    """收尾帧：交前端的三件套（§16.3），并把本轮教学状态与会话消息落盘。

    ``sequence`` 由调用方接在最后一条真实事件之后：该帧不落事件库，
    但仍要满足"SSE 的 id 行单调递增"这一前端重连假设（§18.2）。
    """
    response_text = str(result.get("response_text") or "")
    session.metadata["pedagogy"] = {
        key: value for key, value in result.items() if key in DEFAULT_STATE
    }
    session.append(Message.user(str(result.get("user_input") or "")))
    session.append(Message.assistant(response_text))
    service.save_session(session)

    frame = RuntimeEvent(
        type=_RESULT_EVENT_TYPE,
        payload={
            "session_id": session.session_id,
            "trace_id": trace_id,
            "action": str(result.get("action") or ""),
            "response_text": response_text,
            "emotion": str(result.get("emotion") or ""),
            "citations": list(result.get("citations") or []),
            "next_action": str(result.get("next_action") or ""),
            "turn_count": int(result.get("turn_count") or 0),
            "hint_level": int(result.get("hint_level") or 0),
            "misconceptions": list(result.get("misconceptions") or []),
            "evidence_sufficient": bool(result.get("evidence_sufficient")),
            "strategy_note": str(result.get("strategy_note") or ""),
            "source": "deepprof.api",
        },
        session_id=session.session_id,
        trace_id=trace_id,
        sequence=sequence,
        source="deepprof.api",
    )
    return frame


# ---------- 会话校验 ----------
def _require_session_for_turn(
    service: RuntimeService, session_id: str, payload: SessionRequest
) -> Session:
    """加载会话并校验一轮请求（路径/会话/学习者三者必须一致）。"""
    session = service.session_store.load(session_id)
    if session is None:
        raise _not_found(session_id)
    if payload.session_id and payload.session_id != session_id:
        raise _bad_request(
            "session_id_mismatch",
            "请求体 session_id 与路径不一致",
            path_session_id=session_id,
        )
    if payload.learner_id and session.learner_id and payload.learner_id != session.learner_id:
        raise _conflict("learner_mismatch", "该会话已绑定其它学习者", session_id=session_id)

    # request_id 目前没有 Runtime 侧的通道，先记录在会话元数据里保证可追溯（§18.2）
    if payload.request_id:
        session.metadata["last_request_id"] = payload.request_id
    return session


def _to_session_response(session: Session) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        learner_id=session.learner_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.messages),
    )


# ---------- 结构化错误（§7.3：失败必须结构化，不用 500 掩盖） ----------
def _not_found(session_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "session_not_found",
            "message": f"会话不存在: {session_id}",
            "details": {"session_id": session_id},
        },
    )


def _bad_request(code: str, message: str, **details) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message, "details": details})


def _conflict(code: str, message: str, **details) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message, "details": details})