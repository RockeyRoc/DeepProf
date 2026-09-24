"""会话接入与命令分发。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from graph.education.builder import run_teaching_turn
from graph.education.contracts import POLICY_VERSION
from graph.education.policies import EVIDENCE_GAP_TEXT, GENERATE_TEMPERATURE
from evaluation.question_bank import QuestionBankError, load_bank
from models.learner.bkt import DEFAULT_PARAMETERS, parameters_from_snapshot

from api.event_types import (CONVERSATION_TURN_COMPLETED_EVENT, TEACHING_DECISION_EVENT,
                             TEACHING_TURN_COMPLETED_EVENT)
from api.schemas import ClientCommand, CommandAccepted, SessionMessageView, SessionSummary
from runtime.core.events import EventType, RuntimeEvent, new_id
from runtime.core.message import Message
from runtime.core.session import Session
from runtime.service import RuntimeService

router = APIRouter(tags=["sessions"])
COURSE_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "courses" / "data_structures_c" / "manifest.json"
CHAT_SYSTEM_PROMPT = "你是一个友好、准确的常规对话助手。直接回应用户的问题；不假定用户正在学习数据结构，也不声称引用了教材。"
CHAT_ROUTING_VERSION = "chat-router-rules-v1"
SMALLTALK_PATTERNS = (
    "你好", "您好", "早上好", "晚上好", "谢谢", "感谢", "再见", "你是谁",
    "讲个笑话", "讲个故事", "今天天气", "你能做什么", "随便聊聊",
)
A_SYSTEM_PROMPT = (
    "你是苏格拉底式数据结构助教。只围绕当前问题提出一个简短、递进的问题，"
    "帮助学习者自己推理；不要直接给出最终答案或编造教材结论。"
    "下面提供的教材片段是唯一可引用依据，若片段不能支持回答，应明确说不确定。"
)


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
        SessionMessageView(index=offset + index, role=message.role, content=message.content, name=message.name, metadata=dict(message.metadata))
        for index, message in enumerate(messages)
    ]


@router.get("/sessions/{session_id}/learner")
async def session_learner_estimates(session_id: str, request: Request, concept_id: str = "") -> dict[str, Any]:
    service = _service(request)
    session = service.get_session(session_id)
    experiment = dict(session.metadata.get("experiment") or {})
    group = str(experiment.get("group") or "B").upper()
    if group != "C":
        return {"status": "group_disabled", "experiment_group": group, "estimates": []}
    quiz_service = getattr(request.app.state, "quiz_service", None)
    if quiz_service is not None:
        await quiz_service.flush_events()
    course_id = str(experiment.get("course_id") or "")
    store = getattr(request.app.state, "learner_store", None)
    parameters = parameters_from_snapshot(experiment.get("bkt"))
    estimates = ([store.get_estimate(session.learner_id, course_id, concept_id, parameters)]
                 if concept_id else store.list_estimates(session.learner_id, course_id, parameters)) if store else []
    return {"status": "ok" if estimates else "insufficient_data", "experiment_group": group,
            "course_id": course_id, "model_version": parameters.model_version,
            "config_hash": parameters.config_hash, "parameter_status": parameters.source, "estimates": estimates}


@router.post("/commands", response_model=CommandAccepted)
async def post_command(command: ClientCommand, request: Request) -> CommandAccepted:
    service = _service(request)
    if command.type in {"message.send", "session.resume", "session.fork", "session.compact", "turn.cancel"} and not command.session_id:
        raise HTTPException(status_code=400, detail="session_id is required for this command")
    receipts = request.app.state.command_store
    prior = receipts.get(command.command_id)
    if prior:
        return _receipt_response(command.command_id, prior)
    trace_id = new_id("trc")
    if not receipts.reserve(command.command_id, trace_id):
        return _receipt_response(command.command_id, receipts.get(command.command_id) or {})

    session_id = command.session_id
    result_data: dict[str, Any] = {}
    status = "accepted"
    try:
        if command.type == "session.new":
            requested_group = command.payload.get("group")
            requested_mode = command.payload.get("session_mode")
            mode = str(requested_mode or ("study" if requested_group else "study")).lower()
            if mode not in {"chat", "study"}:
                raise HTTPException(status_code=422, detail={"code": "invalid_session_mode", "message": "session_mode must be chat or study"})
            if requested_group and mode != "study":
                raise HTTPException(status_code=422, detail={"code": "chat_experiment_group_conflict", "message": "实验组会话必须使用 study 模式"})
            group = str(requested_group or "B").upper()
            if mode == "study" and group not in {"A", "B", "C"}:
                raise HTTPException(status_code=422, detail={"code": "invalid_experiment_group", "message": "group must be A, B or C"})
            manifest = json.loads(COURSE_MANIFEST.read_text(encoding="utf-8"))
            course_id = str(command.payload.get("course_id") or manifest["course_id"]) if mode == "study" else ""
            if mode == "study" and course_id != manifest["course_id"]:
                raise HTTPException(status_code=404, detail={"code": "course_not_found", "message": "课程不存在"})
            profile_id, model = _provider_snapshot(service)
            session = service.new_session(learner_id=command.learner_id, title=str(command.payload.get("title", "")))
            try:
                bank_version = str(load_bank().get("version") or "")
            except QuestionBankError:
                bank_version = ""
            session.metadata["session_mode"] = mode
            if mode == "study":
                session.metadata["experiment"] = {
                "group": group,
                "course_id": course_id,
                "provider_profile": profile_id,
                "model": model,
                "policy_version": POLICY_VERSION,
                "model_snapshot": "frozen",
                "retrieval": {"chunk_size": service.settings.library_chunk_size, "chunk_overlap": service.settings.library_chunk_overlap, "top_k": 5},
                "sampling": {"temperature": GENERATE_TEMPERATURE},
                "question_bank_version": bank_version,
                    "bkt": DEFAULT_PARAMETERS.to_dict(),
                    "bkt_config_hash": DEFAULT_PARAMETERS.config_hash,
                    "experiment_run": bool(command.payload.get("experiment_run", False)),
                }
            else:
                session.metadata["provider_profile"] = profile_id
                session.metadata["model"] = model
                session.metadata["chat_routing_version"] = CHAT_ROUTING_VERSION
            service.save_session(session)
            session_id = session.session_id
            await service.emit(RuntimeEvent(
                type=EventType.SESSION_STARTED.value,
                payload={"title": session.title, "learner_id": session.learner_id, "session_mode": mode,
                         "experiment_group": group if mode == "study" else None, "course_id": course_id},
                session_id=session.session_id, trace_id=trace_id, client_id=command.client_id, surface=command.surface,
            ).to_dict())
            result_data = {"session_mode": mode, "experiment_group": group if mode == "study" else None,
                           "course_id": course_id}
        elif command.type == "quiz.generate":
            if not session_id:
                raise HTTPException(status_code=400, detail="session_id is required for this command")
            session = service.get_session(session_id)
            if str(session.metadata.get("session_mode") or "study") != "study":
                raise HTTPException(status_code=409, detail={"code": "study_session_required", "message": "请创建教学会话后再使用测验"})
            try:
                quiz_service = getattr(request.app.state, "quiz_service", None)
                if quiz_service is None:
                    raise QuestionBankError("quiz_service_unavailable", "测验能力未装配。")
                estimate = None
                experiment = dict(session.metadata.get("experiment") or {})
                concept_id = str(command.payload.get("concept_id") or "")
                difficulty = command.payload.get("difficulty")
                if str(experiment.get("group") or "").upper() == "C" and concept_id:
                    estimate = request.app.state.learner_store.get_estimate(
                        session.learner_id, str(experiment.get("course_id") or ""), concept_id,
                        parameters_from_snapshot(experiment.get("bkt")))
                    difficulty = _quiz_difficulty(concept_id, estimate, difficulty)
                result_data = await quiz_service.issue(
                    session_id, trace_id=trace_id, client_id=command.client_id, surface=command.surface,
                    concept_id=concept_id, difficulty=int(difficulty) if difficulty is not None else None)
                result_data.pop("status", None)
            except QuestionBankError as exc:
                result_data = {"code": exc.code, "message": str(exc)}
                status = "blocked"
        elif command.type == "quiz.answer":
            if not session_id:
                raise HTTPException(status_code=400, detail="session_id is required for this command")
            if str(service.get_session(session_id).metadata.get("session_mode") or "study") != "study":
                raise HTTPException(status_code=409, detail={"code": "study_session_required", "message": "请创建教学会话后再提交作答"})
            try:
                quiz_service = getattr(request.app.state, "quiz_service", None)
                if quiz_service is None:
                    raise QuestionBankError("quiz_service_unavailable", "测验能力未装配。")
                result_data = await quiz_service.answer(
                    session_id, attempt_id=command.command_id, trace_id=trace_id,
                    learner_id=command.learner_id if "learner_id" in command.model_fields_set else "",
                    item_id=str(command.payload.get("item_id") or ""),
                    answer=str(command.payload.get("answer") or ""),
                    client_id=command.client_id, surface=command.surface)
            except QuestionBankError as exc:
                raise HTTPException(status_code=409 if exc.code != "invalid_answer" else 422,
                                    detail={"code": exc.code, "message": str(exc)}) from exc
        elif command.type == "library.import":
            library = getattr(service, "library", None)
            if library is None:
                raise HTTPException(status_code=503, detail="resource library is unavailable")
            path = str(command.payload.get("path") or "")
            if not path:
                raise HTTPException(status_code=400, detail="payload.path is required")
            metadata = dict(command.payload.get("metadata") or {})
            metadata["owner_id"] = command.learner_id
            result = library.import_path(path, source_type=str(command.payload.get("source_type") or "import"), metadata=metadata,
                                        actor_id=command.learner_id, as_new_version=bool(command.payload.get("as_new_version", False)),
                                        activate=bool(command.payload.get("activate", False)))
            for item in result.events:
                await service.emit(RuntimeEvent(type=str(item["type"]), payload=dict(item.get("payload") or {}),
                    session_id=command.session_id or "", trace_id=trace_id, source="library", client_id=command.client_id,
                    surface=command.surface).to_dict())
            result_data = result.to_dict()
            result_data["warnings"] = result.warnings
        elif command.type == "document.convert":
            path = str(command.payload.get("path") or "").strip()
            if not path:
                raise HTTPException(status_code=422, detail={"code": "document_path_required", "message": "请提供本地文件路径。"})
            try:
                converted = await service.invoke_skill("markitdown", {
                    "path": path, "first_page": command.payload.get("first_page"),
                    "last_page": command.payload.get("last_page"),
                    "force_ocr": bool(command.payload.get("force_ocr", False)),
                }, {"trace_id": trace_id, "learner_id": command.learner_id,
                    "client_id": command.client_id, "surface": command.surface})
            except Exception as exc:
                raise HTTPException(status_code=422, detail={"code": "document_conversion_failed", "message": str(exc)[:240]}) from exc
            result_data = {key: converted[key] for key in (
                "source_sha256", "page_count", "ocr_used", "review_required", "markdown_path",
                "metadata_path", "format", "markitdown_version") if key in converted}
            result_data["status"] = str(converted.get("status") or "error")
            if converted.get("status") != "ok":
                result_data["error_code"] = str(converted.get("code") or "conversion_failed")
                status = "blocked"
            await service.emit(RuntimeEvent(type="document.converted", payload={
                "status": result_data["status"], "source_sha256": str(converted.get("source_sha256") or ""),
                "page_count": int(converted.get("page_count") or 0), "ocr_used": bool(converted.get("ocr_used")),
                "review_required": bool(converted.get("review_required")),
                "markdown_path": str(converted.get("markdown_path") or ""),
                "metadata_path": str(converted.get("metadata_path") or ""),
                "format": str(converted.get("format") or ""), "error_code": result_data.get("error_code"),
            }, trace_id=trace_id, source="gateway", client_id=command.client_id, surface=command.surface).to_dict())
        elif command.type == "turn.cancel":
            task = request.app.state.turn_tasks.get(str(command.session_id))
            if task is None or task.done():
                status = "not_running"
                result_data = {"code": "no_active_turn"}
            else:
                task.cancel()
                status = "cancellation_requested"
                result_data = {"code": "cancellation_requested"}
        elif command.type in {"session.resume", "session.fork", "session.compact", "message.send"}:
            session = service.get_session(str(command.session_id))
            if command.type == "session.resume":
                pending = session.metadata.get("active_turn")
                if isinstance(pending, dict) and str(pending.get("trace_id") or "") and session.session_id not in request.app.state.active_turns:
                    interrupted_trace = str(pending["trace_id"])
                    session.metadata["active_turn"] = None
                    session.metadata["last_turn"] = {
                        "trace_id": interrupted_trace,
                        "status": "interrupted",
                        "command_id": str(pending.get("command_id") or ""),
                    }
                    service.save_session(session)
                    await service.emit(RuntimeEvent(type=EventType.AGENT_TURN_COMPLETED.value,
                        payload={"status": "interrupted", "reason_code": "gateway_restarted"},
                        session_id=session.session_id, trace_id=interrupted_trace,
                        client_id=command.client_id, surface=command.surface).to_dict())
                await service.emit(RuntimeEvent(type=EventType.SESSION_RESUMED.value,
                    payload={"from_sequence": service.last_sequence(session.session_id)}, session_id=session.session_id,
                    trace_id=trace_id, client_id=command.client_id, surface=command.surface).to_dict())
            elif command.type == "session.fork":
                forked = session.fork(title=command.payload.get("title"))
                service.save_session(forked)
                session_id = forked.session_id
            elif command.type == "session.compact":
                before, after = session.compact(keep=int(command.payload.get("keep", 60)))
                service.save_session(session)
                await service.emit(RuntimeEvent(type=EventType.SESSION_COMPACTED.value, payload={"before": before, "after": after},
                    session_id=session.session_id, trace_id=trace_id, client_id=command.client_id, surface=command.surface).to_dict())
                result_data = {"before": before, "after": after}
            else:
                text = str(command.payload.get("content") or command.payload.get("text") or "")
                if not text:
                    raise HTTPException(status_code=400, detail="payload.content is required")
                experiment = dict(session.metadata.get("experiment") or {})
                session_mode = str(session.metadata.get("session_mode") or "study")
                requested_action = str(command.payload.get("requested_action") or "auto").lower()
                if experiment.get("experiment_run") and requested_action == "chat":
                    raise HTTPException(status_code=409, detail={"code": "experiment_chat_disabled", "message": "实验会话固定使用教学模式"})
                if session_mode == "chat" and requested_action in {"study", "hint", "quiz", "answer"}:
                    raise HTTPException(status_code=409, detail={"code": "study_session_required", "message": "请创建教学会话后再使用教学功能"})
                async with request.app.state.turn_lock:
                    if session.session_id in request.app.state.active_turns:
                        raise HTTPException(status_code=409, detail={"code": "turn_already_active", "message": "此会话已有活动回合"})
                    request.app.state.active_turns.add(session.session_id)
                    session.metadata["active_turn"] = {
                        "command_id": command.command_id,
                        "trace_id": trace_id,
                        "status": "running",
                    }
                    service.save_session(session)
                    ctx = {"session_id": session.session_id, "trace_id": trace_id, "learner_id": session.learner_id,
                           "client_id": command.client_id, "surface": command.surface,
                           "command_id": command.command_id,
                           "provider_profile": str(experiment.get("provider_profile") or session.metadata.get("provider_profile") or ""),
                           "model": str(experiment.get("model") or session.metadata.get("model") or ""),
                           "freeze_model": bool(experiment.get("model_snapshot") == "frozen" or session.metadata.get("model")),
                           "generation_config": dict(experiment.get("sampling") or {"temperature": GENERATE_TEMPERATURE}),
                           "requested_action": requested_action,
                           "session_mode": session_mode,
                           "m3_evidence_options": dict(getattr(request.app.state, "m3_evidence_options", {}).get(session.session_id) or {})}
                    task = asyncio.create_task(_run_turn(service, session, text, ctx, request.app.state))
                    request.app.state.turn_tasks[session.session_id] = task
        else:
            raise HTTPException(status_code=501, detail=f"command type not implemented: {command.type}")

        receipts.complete(command.command_id, status=status, session_id=session_id, result=result_data)
        return CommandAccepted(command_id=command.command_id, session_id=session_id, status=status, trace_id=trace_id, result=result_data)
    except Exception as exc:
        receipts.complete(command.command_id, status="failed", session_id=session_id,
                          result={"code": "command_failed", "message": str(exc)})
        raise


def _receipt_response(command_id: str, receipt: dict[str, Any]) -> CommandAccepted:
    return CommandAccepted(command_id=command_id, session_id=receipt.get("session_id"),
                           status=str(receipt.get("status") or "accepted"), trace_id=receipt.get("trace_id"),
                           result=dict(receipt.get("result") or {}))


def _provider_snapshot(service: RuntimeService) -> tuple[str, str]:
    try:
        _, profile_id, model = service.router.resolve("tutor.default")
        return str(profile_id), str(model)
    except (ValueError, RuntimeError):
        return "", ""


def _concept_for_text(text: str, course_id: str) -> tuple[str, str]:
    try:
        manifest = json.loads(COURSE_MANIFEST.read_text(encoding="utf-8"))
        if course_id == manifest["course_id"]:
            for item in manifest["concepts"]:
                if str(item["concept_id"]).lower() in text.lower() or str(item["name"]) in text:
                    return str(item["concept_id"]), str(item["name"])
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return "", "当前问题"


def _concept_name(concept_id: str, course_id: str) -> str:
    try:
        manifest = json.loads(COURSE_MANIFEST.read_text(encoding="utf-8"))
        if course_id == manifest["course_id"]:
            return next((str(item["name"]) for item in manifest["concepts"]
                         if str(item["concept_id"]) == concept_id), "")
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return ""


def _route_turn(text: str, session: Session, ctx: dict[str, Any]) -> tuple[str, str]:
    experiment = dict(session.metadata.get("experiment") or {})
    requested = str(ctx.get("requested_action") or "auto").lower()
    if experiment.get("experiment_run"):
        return "study", "experiment_locked"
    if str(session.metadata.get("session_mode") or "study") == "chat":
        return "chat", "chat_session_default"
    compact = "".join(text.lower().split()).strip("，。！？,.!?、")
    is_smalltalk = len(compact) <= 28 and any(compact == "".join(pattern.lower().split()) for pattern in SMALLTALK_PATTERNS)
    if session.metadata.get("active_quiz"):
        solve_words = ("答案", "怎么做", "如何解", "怎么解", "结果", "证明", "代码", "为什么", "选什么")
        if not is_smalltalk and (requested != "chat" or any(word in compact for word in solve_words)):
            return "study", "active_quiz_question"
    if requested == "chat":
        return "chat", "explicit_chat"
    if requested in {"study", "ask", "hint", "quiz", "answer"}:
        return "study", "explicit_study_action"
    if is_smalltalk:
        return "chat", "smalltalk_rule"
    return "study", "default_study"


def _quiz_difficulty(concept_id: str, estimate: dict[str, Any] | None, requested: int | None) -> int:
    if requested is not None:
        return max(1, min(3, int(requested)))
    base = 1
    try:
        manifest = json.loads(COURSE_MANIFEST.read_text(encoding="utf-8"))
        item = next((row for row in manifest["concepts"] if str(row.get("concept_id")) == concept_id), None)
        base = max(1, min(3, int((item or {}).get("difficulty") or 1)))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass
    estimate = dict(estimate or {})
    mastery = estimate.get("mastery") if estimate.get("status") == "available" else None
    if isinstance(mastery, (int, float)):
        if float(mastery) < 0.30:
            return max(1, base - 1)
        if float(mastery) >= 0.85:
            return min(3, base + 1)
    return base


def _locator(item: dict[str, Any]) -> dict[str, Any]:
    allowed = ("document_id", "chunk_id", "page", "printed_page", "chapter", "section", "source")
    return {key: item[key] for key in allowed if item.get(key) is not None}


async def _run_turn(service: RuntimeService, session: Session, text: str, ctx: dict[str, Any], app_state: Any) -> None:
    """选择聊天或冻结的教学路径，并将本轮状态与终态事件持久化。"""
    experiment = dict(session.metadata.get("experiment") or {})
    group = str(experiment.get("group") or "B").upper()
    course_id = str(experiment.get("course_id") or "ds.c_language.v1")
    profile_id = str(experiment.get("provider_profile") or session.metadata.get("provider_profile") or "")
    model = str(experiment.get("model") or session.metadata.get("model") or "")
    ctx = {**ctx, "provider_profile": profile_id, "model": model,
           "freeze_model": bool(experiment.get("model_snapshot") == "frozen" or model)}
    turn_mode, routing_reason = _route_turn(text, session, ctx)
    ctx = {**ctx, "turn_mode": turn_mode, "routing_reason": routing_reason,
           "routing_version": CHAT_ROUTING_VERSION}
    terminal_status = "failed"
    action = ""
    citations: list[dict[str, Any]] = []
    try:
        session.append(Message(role="user", content=text, metadata={"turn_mode": turn_mode,
            "routing_reason": routing_reason, "routing_version": CHAT_ROUTING_VERSION}))
        if turn_mode == "study":
            session.metadata["experiment"] = {**experiment, "group": group, "course_id": course_id}
        service.save_session(session)
        started_payload = {"session_mode": str(session.metadata.get("session_mode") or "study"),
                           "turn_mode": turn_mode, "routing_reason": routing_reason,
                           "routing_version": CHAT_ROUTING_VERSION}
        if turn_mode == "study":
            started_payload["group"] = group
        await service.emit(RuntimeEvent(type=EventType.AGENT_TURN_STARTED.value, payload=started_payload,
            session_id=session.session_id, trace_id=ctx["trace_id"], client_id=ctx.get("client_id"), surface=ctx.get("surface")).to_dict())
        if turn_mode == "chat":
            answer = await _run_chat_turn(service, session, ctx)
            if not answer.strip():
                raise RuntimeError("empty_model_response")
            session.append(Message(role="assistant", content=answer, metadata={"trace_id": ctx["trace_id"],
                "turn_mode": "chat", "routing_reason": routing_reason, "routing_version": CHAT_ROUTING_VERSION,
                "provider_profile": profile_id, "model": model}))
            await service.emit(RuntimeEvent(type=CONVERSATION_TURN_COMPLETED_EVENT, payload={
                "session_mode": str(session.metadata.get("session_mode") or "chat"), "turn_mode": "chat",
                "routing_reason": routing_reason, "routing_version": CHAT_ROUTING_VERSION},
                session_id=session.session_id, trace_id=ctx["trace_id"], source="gateway",
                client_id=ctx.get("client_id"), surface=ctx.get("surface")).to_dict())
            await service.emit(RuntimeEvent(type=EventType.AGENT_TURN_COMPLETED.value, payload={"status": "ok"},
                session_id=session.session_id, trace_id=ctx["trace_id"], client_id=ctx.get("client_id"),
                surface=ctx.get("surface")).to_dict())
            action = "chat"
            terminal_status = "ok"
            return
        active_quiz = session.metadata.get("active_quiz")
        teaching_state = dict(session.metadata.get("teaching_state") or {})
        active_concept = str(active_quiz.get("concept_id") or "") if isinstance(active_quiz, dict) else ""
        concept_id, concept = _concept_for_text(text, course_id)
        if active_concept:
            concept_id = active_concept
            concept = _concept_name(active_concept, course_id) or concept
        if group == "A":
            answer, citations, action = await _run_group_a(service, session, text, ctx, course_id, profile_id, model)
        else:
            # Keep error and hint history within this session and detected concept,
            # even before a quiz has created active_quiz. Never borrow state from
            # another session or a different concept.
            prior = teaching_state
            if prior.get("concept_id") != concept_id:
                prior = {"concept_id": concept_id, "hint_level": 0, "turn_count": 0,
                         "attempt_count": 0, "wrong_streak": 0,
                         "last_answer_correct": None, "misconceptions": []}
            learner_estimate: dict[str, Any] = {}
            if group == "C" and concept_id:
                learner_store = getattr(service, "learner_store", None)
                if learner_store is not None:
                    learner_estimate = learner_store.get_estimate(
                        session.learner_id, course_id, concept_id,
                        parameters_from_snapshot(experiment.get("bkt")))
            test_difficulty = _quiz_difficulty(concept_id, learner_estimate, None) if concept_id else None
            result = await run_teaching_turn(service, {
                "session_id": session.session_id, "learner_id": session.learner_id, "trace_id": ctx["trace_id"],
                "user_input": text, "current_concept": concept, "learning_goal": concept,
                "current_concept_id": concept_id, "experiment_group": group,
                "learner_estimate": learner_estimate, "test_difficulty": test_difficulty,
                "item_id": str(active_quiz.get("item_id") or "") if isinstance(active_quiz, dict) else "",
                "course_id": course_id, "provider_profile": profile_id, "model": model,
                "freeze_model": True, "generation_config": dict(ctx.get("generation_config") or {}),
                "attempt_count": int(prior.get("attempt_count") or 0), "wrong_streak": int(prior.get("wrong_streak") or 0), "hint_level": int(prior.get("hint_level") or 0),
                "turn_count": int(prior.get("turn_count") or 0), "max_turns": 6,
                "last_answer_correct": prior.get("last_answer_correct"), "misconceptions": list(prior.get("misconceptions") or []),
                "requested_action": str(ctx.get("requested_action") or ""),
                "evidence_constraint": bool((ctx.get("m3_evidence_options") or {}).get("evidence_constraint", True)),
                "m3_evidence_options": dict(ctx.get("m3_evidence_options") or {}),
            })
            answer = str(result.get("response_text") or "")
            raw_refs = result.get("citations") or result.get("retrieved_evidence_refs") or []
            citations = [_locator(item) for item in raw_refs if isinstance(item, dict)]
            action = str(result.get("action") or "")
            if action == "hint" and isinstance(active_quiz, dict) and answer.strip():
                active_quiz["hint_count"] = int(active_quiz.get("hint_count") or 0) + 1
                session.metadata["active_quiz"] = active_quiz
            session.metadata["teaching_state"] = {"concept_id": concept_id, "hint_level": int(result.get("hint_level") or 0),
                "turn_count": int(result.get("turn_count") or 0), "attempt_count": int(prior.get("attempt_count") or 0),
                "wrong_streak": int(prior.get("wrong_streak") or 0), "last_answer_correct": prior.get("last_answer_correct"),
                "misconceptions": list(prior.get("misconceptions") or [])}
        if not answer.strip():
            raise RuntimeError("empty_model_response")
        session.append(Message(role="assistant", content=answer, metadata={"trace_id": ctx["trace_id"],
            "turn_mode": "study", "experiment_group": group, "course_id": course_id, "action": action, "evidence_refs": citations,
            "provider_profile": profile_id, "model": model}))
        await service.emit(RuntimeEvent(type=TEACHING_TURN_COMPLETED_EVENT, payload={"group": group, "action": action,
            "turn_mode": "study", "routing_reason": routing_reason, "routing_version": CHAT_ROUTING_VERSION,
            "policy_version": POLICY_VERSION, "evidence_refs": citations}, session_id=session.session_id,
            trace_id=ctx["trace_id"], source="gateway", client_id=ctx.get("client_id"), surface=ctx.get("surface")).to_dict())
        await service.emit(RuntimeEvent(type=EventType.AGENT_TURN_COMPLETED.value, payload={"status": "ok"},
            session_id=session.session_id, trace_id=ctx["trace_id"], client_id=ctx.get("client_id"), surface=ctx.get("surface")).to_dict())
        terminal_status = "ok"
    except asyncio.CancelledError:
        terminal_status = "cancelled"
        await service.emit(RuntimeEvent(type=EventType.AGENT_TURN_COMPLETED.value, payload={"status": "cancelled"},
            session_id=session.session_id, trace_id=ctx["trace_id"], client_id=ctx.get("client_id"), surface=ctx.get("surface")).to_dict())
        raise
    except Exception as exc:
        failure = {
            "code": "runtime_error",
            "message": "真实模型未配置" if "provider" in str(exc).lower() else str(exc),
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
        await service.emit(RuntimeEvent(type=EventType.AGENT_TURN_COMPLETED.value, payload={"status": "failed"},
            session_id=session.session_id, trace_id=ctx["trace_id"], client_id=ctx.get("client_id"), surface=ctx.get("surface")).to_dict())
    finally:
        pending = session.metadata.get("active_turn")
        if isinstance(pending, dict) and pending.get("trace_id") == ctx.get("trace_id"):
            session.metadata["active_turn"] = None
        session.metadata["last_turn"] = {
            "command_id": str(ctx.get("command_id") or ""),
            "trace_id": str(ctx.get("trace_id") or ""),
            "status": terminal_status,
            "action": action,
            "evidence_refs": citations,
        }
        service.save_session(session)
        app_state.active_turns.discard(session.session_id)
        app_state.turn_tasks.pop(session.session_id, None)


async def _run_chat_turn(service: RuntimeService, session: Session, ctx: dict[str, Any]) -> str:
    messages = [Message(role="system", content=CHAT_SYSTEM_PROMPT)]
    prior = [message for message in session.messages[:-1]
             if message.role in {"user", "assistant"}
             and str(message.metadata.get("turn_mode") or "study") == "chat"]
    messages.extend(prior[-58:])
    messages.append(session.messages[-1])
    request = {"role": "tutor.default", "provider_profile": ctx.get("provider_profile") or None,
               "model": ctx.get("model") or None, "messages": [message.to_dict() for message in messages],
               "temperature": float((ctx.get("generation_config") or {}).get("temperature", GENERATE_TEMPERATURE)),
               "tools": []}
    parts: list[str] = []
    async for frame in service.generate(request, {**ctx, "suppress_user_stream": False}):
        if frame.get("type") == "delta":
            parts.append(str(frame.get("text") or ""))
        elif frame.get("type") == "error":
            raise RuntimeError(str((frame.get("error") or {}).get("message") or "provider_failed"))
    return "".join(parts)


async def _run_group_a(service: RuntimeService, session: Session, text: str, ctx: dict[str, Any],
                       course_id: str, profile_id: str, model: str) -> tuple[str, list[dict[str, Any]], str]:
    """Baseline Socratic prompt: same course retriever and model snapshot, no graph nodes."""
    options = dict(ctx.get("m3_evidence_options") or {})
    retrieval_enabled = bool(options.get("retrieval_enabled", True))
    evidence_constraint = bool(options.get("evidence_constraint", True))
    hits = service.library.search(text, top_k=5, course_id=course_id, owner_id=session.learner_id) if retrieval_enabled and service.library else {}
    raw = list(hits.get("evidence") or [])
    citations = [_locator(item) for item in raw if isinstance(item, dict) and item.get("document_id") and item.get("chunk_id") and item.get("page") is not None]
    if not citations and evidence_constraint:
        await service.emit(RuntimeEvent(type=TEACHING_DECISION_EVENT, payload={"group": "A", "action": "evidence_gap",
            "policy_version": POLICY_VERSION, "reason_codes": ["insufficient_evidence"], "evidence_refs": []},
            session_id=session.session_id, trace_id=ctx["trace_id"], source="gateway", client_id=ctx.get("client_id"),
            surface=ctx.get("surface")).to_dict())
        return EVIDENCE_GAP_TEXT, [], "evidence_gap"
    evidence = "\n\n".join(f"[{i + 1}] {item.get('chapter') or item.get('section') or ''}，PDF第{item.get('page')}页，书内第{item.get('printed_page') or '未识别'}页\n{str(item.get('text') or '')[:600]}" for i, item in enumerate(raw[:5]))
    _, concept = _concept_for_text(text, course_id)
    prompt = f"当前问题：{text}\n\n教材片段（仅供依据）：\n{evidence}\n\n请按苏格拉底方式提出一个问题，不要直接揭示答案。"
    await service.emit(RuntimeEvent(type=TEACHING_DECISION_EVENT, payload={"group": "A", "action": "ask",
        "policy_version": POLICY_VERSION, "reason_codes": ["socratic_baseline"], "evidence_refs": citations},
        session_id=session.session_id, trace_id=ctx["trace_id"], source="gateway", client_id=ctx.get("client_id"),
        surface=ctx.get("surface")).to_dict())
    parts: list[str] = []
    system_prompt = A_SYSTEM_PROMPT if evidence_constraint else (
        "你是苏格拉底式数据结构助教。针对当前问题提供有帮助的解释或追问；不要编造出处。"
    )
    request = {"role": "tutor.default", "provider_profile": profile_id, "model": model,
               "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
               "temperature": float((ctx.get("generation_config") or {}).get("temperature", GENERATE_TEMPERATURE)), "tools": []}
    async for frame in service.generate(request, {**ctx, "suppress_user_stream": True}):
        if frame.get("type") == "delta":
            parts.append(str(frame.get("text") or ""))
        elif frame.get("type") == "error":
            raise RuntimeError(str((frame.get("error") or {}).get("message") or "provider_failed"))
    from skills.socratic import validate_socratic_question

    question = validate_socratic_question("".join(parts))
    if not question:
        question = f"关于「{concept}」，你认为判断这个问题时首先需要明确哪个条件？"
        action = "ask_fallback"
    else:
        action = "ask"
    return question, citations, action


def _summary(service: RuntimeService, session: Session) -> SessionSummary:
    experiment = dict(session.metadata.get("experiment") or {})
    return SessionSummary(
        session_id=session.session_id,
        learner_id=session.learner_id,
        title=session.title,
        parent_id=session.parent_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_sequence=service.last_sequence(session.session_id),
        message_count=len(session.messages),
        session_mode=str(session.metadata.get("session_mode") or "study"),
        experiment_group=str(experiment.get("group") or ""),
        course_id=str(experiment.get("course_id") or ""),
        provider_profile=str(experiment.get("provider_profile") or ""),
        model=str(experiment.get("model") or ""),
    )
