"""会话接入与命令分发。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from api.schemas import ClientCommand, CommandAccepted, SessionMessageView, SessionSummary
from runtime.core.agent import Agent
from runtime.core.events import EventType, RuntimeEvent, new_id
from runtime.core.message import Message
from runtime.core.session import Session
from runtime.service import RuntimeService

router = APIRouter(tags=["sessions"])


def _service(request: Request) -> RuntimeService:
    return request.app.state.service


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(request: Request, learner_id: str | None = None) -> list[SessionSummary]:
    service = _service(request)
    if service.sessions is None:
        return []
    summaries = []
    for session_id in service.sessions.list(learner_id):
        session = service.sessions.load(session_id)
        if session is None:
            continue
        summaries.append(_summary(service, session))
    return summaries


@router.get("/sessions/{session_id}", response_model=SessionSummary)
async def get_session(session_id: str, request: Request) -> SessionSummary:
    service = _service(request)
    return _summary(service, service.get_session(session_id))


@router.get("/sessions/{session_id}/messages", response_model=list[SessionMessageView])
async def get_messages(session_id: str, request: Request, limit: int = 200) -> list[SessionMessageView]:
    session = _service(request).get_session(session_id)
    safe_limit = max(1, min(int(limit), 1000))
    messages = session.messages[-safe_limit:]
    offset = len(session.messages) - len(messages)
    return [
        SessionMessageView(index=offset + index, role=message.role, content=message.content, name=message.name)
        for index, message in enumerate(messages)
    ]


@router.post("/commands", response_model=CommandAccepted)
async def post_command(command: ClientCommand, request: Request) -> CommandAccepted:
    service = _service(request)
    ctx = {
        "session_id": command.session_id or "",
        "trace_id": new_id("trc"),
        "learner_id": command.learner_id,
        "client_id": command.client_id,
        "surface": command.surface,
    }

    if command.type == "session.new":
        session = service.new_session(
            learner_id=command.learner_id, title=str(command.payload.get("title", ""))
        )
        await service.emit(
            RuntimeEvent(
                type=EventType.SESSION_STARTED.value,
                payload={"title": session.title, "learner_id": session.learner_id},
                session_id=session.session_id,
                trace_id=ctx["trace_id"],
                client_id=command.client_id,
                surface=command.surface,
            ).to_dict()
        )
        return CommandAccepted(command_id=command.command_id, session_id=session.session_id)

    if command.type in ("library.import", "library.crawl"):
        library = getattr(service, "library", None)
        if library is None:
            raise HTTPException(status_code=503, detail="resource library is unavailable")
        metadata = dict(command.payload.get("metadata") or {})
        # Ownership comes from the authenticated command envelope, never from
        # mutable resource metadata supplied by the client.
        metadata["owner_id"] = command.learner_id
        if command.type == "library.import":
            path = str(command.payload.get("path") or "")
            if not path:
                raise HTTPException(status_code=400, detail="payload.path is required")
            result = library.import_path(
                path,
                source_type=str(command.payload.get("source_type") or "import"),
                metadata=metadata,
                actor_id=command.learner_id,
                as_new_version=bool(command.payload.get("as_new_version", False)),
                activate=bool(command.payload.get("activate", False)),
            )
        else:
            url = str(command.payload.get("url") or "")
            if not url:
                raise HTTPException(status_code=400, detail="payload.url is required")
            result = await library.crawl(
                url,
                metadata=metadata,
                actor_id=command.learner_id,
                as_new_version=bool(command.payload.get("as_new_version", False)),
                activate=bool(command.payload.get("activate", False)),
            )
        for item in result.events:
            await service.emit(
                RuntimeEvent(
                    type=str(item["type"]),
                    payload=dict(item.get("payload") or {}),
                    session_id=command.session_id or "",
                    trace_id=ctx["trace_id"],
                    source="library",
                    client_id=command.client_id,
                    surface=command.surface,
                ).to_dict()
            )
        return CommandAccepted(
            command_id=command.command_id,
            session_id=command.session_id,
            result=result.to_dict(),
        )

    if command.type in ("message.send", "session.resume", "session.fork", "session.compact"):
        if not command.session_id:
            raise HTTPException(status_code=400, detail="session_id is required for this command")
        session = service.get_session(command.session_id)
        ctx["session_id"] = session.session_id

        if command.type == "session.resume":
            await service.emit(
                RuntimeEvent(
                    type=EventType.SESSION_RESUMED.value,
                    payload={"from_sequence": service.last_sequence(session.session_id)},
                    session_id=session.session_id,
                    trace_id=ctx["trace_id"],
                    client_id=command.client_id,
                    surface=command.surface,
                ).to_dict()
            )
            return CommandAccepted(command_id=command.command_id, session_id=session.session_id)

        if command.type == "session.fork":
            forked = session.fork(title=command.payload.get("title"))
            service.save_session(forked)
            return CommandAccepted(command_id=command.command_id, session_id=forked.session_id)

        if command.type == "session.compact":
            before, after = session.compact(keep=int(command.payload.get("keep", 60)))
            service.save_session(session)
            await service.emit(
                RuntimeEvent(
                    type=EventType.SESSION_COMPACTED.value,
                    payload={"before": before, "after": after},
                    session_id=session.session_id,
                    trace_id=ctx["trace_id"],
                    client_id=command.client_id,
                    surface=command.surface,
                ).to_dict()
            )
            return CommandAccepted(command_id=command.command_id, session_id=session.session_id)

        text = str(command.payload.get("content") or command.payload.get("text") or "")
        if not text:
            raise HTTPException(status_code=400, detail="payload.content is required")
        asyncio.create_task(_run_turn(service, session, text, ctx))
        return CommandAccepted(command_id=command.command_id, session_id=session.session_id)

    raise HTTPException(status_code=501, detail=f"command type not implemented in MVP-1: {command.type}")


async def _run_turn(service: RuntimeService, session: Session, text: str, ctx: dict[str, Any]) -> None:
    """执行一轮对话：写入用户消息 → Agent 循环 → 落库助手消息。"""
    try:
        session.append(Message(role="user", content=text))
        agent = Agent(service, service.router, service.settings)
        request = {
            "messages": [message.to_dict() for message in session.context()],
            "role": "tutor.default",
            "tools": [],
        }
        async for frame in agent.run(request, ctx):
            if frame.get("type") == "result":
                session.append(Message.from_dict(frame["message"]))
    except Exception as exc:
        failure = {
            "code": "runtime_error",
            "message": str(exc),
            "details": {"kind": "runtime_error", "type": type(exc).__name__},
        }
        await service.emit(
            RuntimeEvent(
                type=EventType.AGENT_FAILED.value,
                payload={"error": failure},
                session_id=session.session_id,
                trace_id=str(ctx.get("trace_id") or new_id("trc")),
                client_id=ctx.get("client_id"),
                surface=ctx.get("surface"),
            ).to_dict()
        )
    finally:
        service.save_session(session)


def _summary(service: RuntimeService, session: Session) -> SessionSummary:
    return SessionSummary(
        session_id=session.session_id,
        learner_id=session.learner_id,
        title=session.title,
        parent_id=session.parent_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_sequence=service.last_sequence(session.session_id),
        message_count=len(session.messages),
    )
