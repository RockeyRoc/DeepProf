"""Local, read-only and redacted event replay."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["replay"])
PAGE = Path(__file__).resolve().parents[1] / "apps" / "replay" / "index.html"

_FIELDS: dict[str, tuple[str, ...]] = {
    "session.started": ("session_mode", "experiment_group", "course_id"),
    "session.resumed": ("from_sequence",),
    "session.compacted": ("before", "after"),
    "agent.turn.started": ("session_mode", "turn_mode", "routing_reason", "routing_version", "group"),
    "agent.turn.completed": ("status",),
    "model.requested": ("role", "provider_profile", "model"),
    "model.completed": ("usage", "model", "finish_reason"),
    "model.failed": (),
    "pedagogy.node.entered": ("node", "turn_count", "hint_level", "attempt_count"),
    "pedagogy.decision": (
        "node", "action", "reason", "policy_version", "reason_codes", "evidence_sufficient",
        "evidence_count", "attempt_count", "wrong_streak", "hint_level", "turn_count",
        "max_turns", "capability", "capability_status", "answer_leaked",
        "bkt_model_version", "bkt_config_hash", "mastery", "learner_evidence_count",
    ),
    "pedagogy.node.exited": ("node", "action", "response_chars", "citation_count"),
    "teaching.decision": ("group", "action", "policy_version", "reason_codes", "mastery", "bkt_model_version", "learner_evidence_count"),
    "teaching.turn.completed": ("session_mode", "turn_mode", "routing_reason", "routing_version", "group", "action", "policy_version", "mastery", "bkt_model_version", "learner_evidence_count"),
    "conversation.turn.completed": ("session_mode", "turn_mode", "routing_reason", "routing_version"),
    "library.imported": ("resource_id", "hash", "duplicate"),
    "library.indexed": ("resource_id", "chunks"),
    "quiz.issued": ("item_id", "concept_ids", "difficulty", "question_type"),
    "quiz.scored": ("item_id", "scored_concept_id", "correct", "grading_source", "confidence"),
    "quiz.review_pending": ("item_id", "scored_concept_id", "correct", "grading_source", "confidence"),
    "pedagogy.attempt": ("attempt_id", "course_id", "item_id", "scored_concept_id", "concept_ids", "question_bank_version",
                         "correct", "hint_count", "grading_source", "confidence", "predicted_correct"),
    "pedagogy.attempt_skipped": ("attempt_id", "course_id", "item_id", "scored_concept_id", "concept_ids",
                                 "question_bank_version", "correct", "eligible", "skip_reason", "hint_count", "grading_source"),
    "document.converted": ("status", "source_sha256", "page_count", "ocr_used", "review_required",
                            "markdown_path", "metadata_path", "format", "error_code"),
}

_SAFE_FAILURE_CODES = {
    "provider_error", "runtime_error", "model_truncated", "empty_model_response",
    "auth_failed", "region_or_permission_blocked", "rate_limited", "timeout",
    "connection_failed", "upstream_error", "not_found", "missing_credential",
    "capability_missing",
}
_SAFE_FAILURE_KINDS = _SAFE_FAILURE_CODES - {"provider_error", "runtime_error"}
_SAFE_FINISH_REASONS = {"stop", "length", "tool_calls", "content_filter", "function_call"}
_SAFE_USAGE_FIELDS = {
    "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
}


def _safe_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item for key, item in value.items()
        if key in _SAFE_USAGE_FIELDS and isinstance(item, (int, float)) and not isinstance(item, bool)
        and item >= 0
    }


@router.get("/replay", response_class=HTMLResponse)
async def replay_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.get("/replay/sessions/{session_id}")
async def replay_session(session_id: str, request: Request) -> dict[str, Any]:
    service = request.app.state.service
    try:
        session = service.get_session(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"code": "session_not_found", "message": "会话不存在"}) from exc
    experiment = dict(session.metadata.get("experiment") or {})
    events = service.history(session_id)
    safe_events = [_safe_event(event) for event in events]
    return {
        "session": {
            "session_id": session.session_id,
            "parent_id": session.parent_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": len(session.messages),
            "session_mode": str(session.metadata.get("session_mode") or "study"),
            "experiment_group": str(experiment.get("group") or "") if experiment else "",
            "course_id": str(experiment.get("course_id") or ""),
            "provider_profile": str(experiment.get("provider_profile") or ""),
            "model": str(experiment.get("model") or ""),
            "policy_version": str(experiment.get("policy_version") or "legacy"),
        },
        "events": safe_events,
        "redaction": {"student_text": True, "textbook_text": True, "secrets": True},
    }


@router.get("/replay/sessions/{session_id}/events")
async def replay_events(session_id: str, request: Request, from_sequence: int = 0) -> list[dict[str, Any]]:
    service = request.app.state.service
    try:
        service.get_session(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"code": "session_not_found", "message": "会话不存在"}) from exc
    return [_safe_event(event) for event in service.history(session_id, max(0, int(from_sequence)))]


def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or "")
    raw = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    payload: dict[str, Any] = {key: raw[key] for key in _FIELDS.get(event_type, ()) if key in raw}
    if event_type == "pedagogy.attempt" and isinstance(raw.get("learner_estimate"), dict):
        estimate = raw["learner_estimate"]
        payload["learner_estimate"] = {key: estimate[key] for key in (
            "status", "model_type", "model_version", "config_hash", "mastery", "evidence_count",
            "uncertainty", "uncertainty_kind", "updated_at") if key in estimate}
    if event_type == "model.stream.delta":
        payload = {"text_chars": len(str(raw.get("text") or ""))}
    if event_type == "agent.failed":
        error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        raw_code = error.get("code")
        raw_kind = details.get("kind")
        code = raw_code if isinstance(raw_code, str) and raw_code in _SAFE_FAILURE_CODES else "runtime_error"
        kind = raw_kind if isinstance(raw_kind, str) and raw_kind in _SAFE_FAILURE_KINDS else "unknown"
        payload = {"error": {"code": code, "kind": kind}}
    if event_type == "model.failed":
        error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        raw_code = error.get("code")
        raw_kind = details.get("kind")
        code = raw_code if isinstance(raw_code, str) and raw_code in _SAFE_FAILURE_CODES else "provider_error"
        kind = raw_kind if isinstance(raw_kind, str) and raw_kind in _SAFE_FAILURE_KINDS else "unknown"
        payload = {"error": {"code": code, "kind": kind}}
        usage = _safe_usage(details.get("usage") or raw.get("usage"))
        if usage:
            payload["usage"] = usage
        finish_reason = details.get("finish_reason") or raw.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            payload["finish_reason"] = finish_reason if finish_reason in _SAFE_FINISH_REASONS else "other"
    # New records use evidence_refs. Historic `citations`/`sources` are accepted
    # on read, then projected to the same locator-only shape.
    refs = raw.get("evidence_refs") or raw.get("citations") or raw.get("sources")
    if isinstance(refs, list) and event_type in {"pedagogy.decision", "teaching.decision", "teaching.turn.completed"}:
        payload["evidence_refs"] = [_safe_ref(item) for item in refs if isinstance(item, dict)]
    return {
        "sequence": int(event.get("sequence") or 0),
        "trace_id": str(event.get("trace_id") or ""),
        "timestamp": str(event.get("timestamp") or ""),
        "type": event_type,
        "source": str(event.get("source") or ""),
        "payload": payload,
    }


_CHAPTER_LABELS = {
    "线性表": "线性表",
    "栈和队列": "栈和队列",
    "树和二叉树": "树和二叉树",
    "图": "图",
    "查找": "查找（二叉排序树）",
    "内部排序": "内部排序",
}


def _safe_ref(ref: dict[str, Any]) -> dict[str, Any]:
    # Chapter labels come from parsed document text, so accept only known course
    # headings. Never replay arbitrary section/source strings that can contain
    # page text or local filesystem paths.
    safe = {key: ref[key] for key in ("document_id", "chunk_id", "page", "printed_page") if ref.get(key) is not None}
    label = " ".join(str(ref.get("chapter") or "").split())
    for candidate, display in _CHAPTER_LABELS.items():
        if label == candidate or re.fullmatch(r"第\s*[一二三四五六七八九十百\d]+\s*章\s*" + re.escape(candidate), label):
            safe["chapter"] = display
            break
    return safe
