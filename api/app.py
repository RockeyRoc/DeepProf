"""Gateway 组合根：装配 Runtime、注入绑定表、暴露环回地址 API。

绑定表（action → capability）由本层从图侧数据读取并注入 Runtime；
Runtime 自身不认识任何教学词汇（§4.4、§16.1 第 4 条）。
教育 Skill、检索 Tool 与绑定表都在这里装配，缺一样都会在 /health 里显式暴露。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import asyncio

from api import courses, events, health, library as library_api, providers, replay, sessions
from config.settings import Settings
from graph.education.bindings import ACTION_BINDINGS
from library.service import ResourceLibrary
from runtime.assembly import build_runtime_service
from runtime.core.errors import RuntimeFailure
from runtime.service import RuntimeService
from runtime.storage.resource_store import SqliteResourceStore
from runtime.storage.command_store import SqliteCommandStore
from runtime.tools.base import FunctionTool
from skills import register_default_skills
from tools.retrieval import build_search_textbook_tool
from evaluation.quiz_service import QuizService
from models.learner.store import SqliteLearnerStore
from library.markitdown_converter import convert_local as convert_markitdown
from starlette.concurrency import run_in_threadpool

__all__ = ["app", "create_app", "build_service_with_bindings"]

# Runtime 结构化错误 → HTTP 状态码
_ERROR_STATUS = {
    "session_not_found": 404,
    "tool_not_found": 404,
    "skill_not_found": 404,
    "sandbox_denied": 403,
    "approval_required": 409,
    "invalid_arguments": 422,
    "invalid_memory_record": 422,
    "resource_not_found": 404,
    "resource_forbidden": 403,
    "file_not_found": 404,
    "size_limit": 413,
    "duplicate": 409,
    "invalid_request": 422,
}


def build_service_with_bindings(
    *,
    settings: Settings | None = None,
    bindings: dict[str, dict[str, Any]] | None = None,
    with_education: bool = True,
) -> RuntimeService:
    """装配可运行的 Runtime：注入教学绑定表，并注册教育与检索能力。

    ``with_education=False`` 用于只验证 Runtime 装配的场合（此时 ``execute``
    会显式返回 ``no_binding``，并由 /health 暴露缺口）。
    """
    service = build_runtime_service(
        settings=settings, bindings=bindings if bindings is not None else ACTION_BINDINGS
    )
    _attach_library(service)
    if with_education:
        register_default_skills(service.skills)
        service.tools.register(build_search_textbook_tool(service.library.search))
        _attach_m2_services(service)
    return service


def create_app(
    *,
    service: RuntimeService | None = None,
    settings: Settings | None = None,
    bindings: dict[str, dict[str, Any]] | None = None,
) -> FastAPI:
    app = FastAPI(title="DeepProf Gateway", version="0.6.2")
    app.state.service = service or build_service_with_bindings(settings=settings, bindings=bindings)
    _attach_library(app.state.service)
    app.state.command_store = SqliteCommandStore.open(str(app.state.service.settings.resolved_sqlite_path))
    _attach_m2_services(app.state.service)
    app.state.learner_store = getattr(app.state.service, "learner_store", None)
    app.state.quiz_service = getattr(app.state.service, "quiz_service", None)
    app.state.active_turns = set()
    app.state.turn_tasks = {}
    app.state.turn_lock = asyncio.Lock()

    @app.exception_handler(RuntimeFailure)
    async def _runtime_failure_handler(request: Request, exc: RuntimeFailure) -> JSONResponse:
        """把 Runtime 的结构化错误映射为合适的 HTTP 状态码，保留 kind 供前端分流。"""
        kind = str(exc.details.get("kind", ""))
        return JSONResponse(status_code=_ERROR_STATUS.get(kind, 400), content={"error": exc.to_dict()})

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Keep gateway transport status and a stable structured error envelope."""
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code") or "http_error")
            message = str(detail.get("message") or detail.get("detail") or code)
            details = detail.get("details") if isinstance(detail.get("details"), dict) else {}
        else:
            code = "http_error"
            message = str(detail or code)
            details = {}
        details = {"http_status": exc.status_code, **details}
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={"error": {"code": code, "message": message, "details": details}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "request validation failed",
                    "details": {"errors": jsonable_encoder(exc.errors())},
                }
            },
        )

    app.include_router(health.router)
    app.include_router(providers.router)
    app.include_router(sessions.router)
    app.include_router(events.router)
    app.include_router(library_api.router)
    app.include_router(courses.router)
    app.include_router(replay.router)
    return app


def _attach_library(service: RuntimeService) -> None:
    if service.library is not None:
        return
    settings = service.settings
    store = SqliteResourceStore.open(str(settings.resolved_sqlite_path))
    service.library = ResourceLibrary(
        store,
        library_root=settings.resolved_library_dir,
        chunk_size=settings.library_chunk_size,
        chunk_overlap=settings.library_chunk_overlap,
        max_import_bytes=settings.library_max_import_bytes,
        path_allowed=service.sandbox.is_path_allowed,
    )


def _attach_m2_services(service: RuntimeService) -> None:
    """Install reviewed-quiz and local document-conversion tools at composition time."""
    if not service.skills.names():
        return
    if not hasattr(service, "learner_store"):
        service.learner_store = SqliteLearnerStore.open(str(service.settings.resolved_sqlite_path))
    if not hasattr(service, "quiz_service"):
        service.quiz_service = QuizService(service, service.learner_store)
    if "quiz" in service.skills.names():
        async def issue_quiz(arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
            return await service.quiz_service.issue_from_tool(arguments, ctx)

        service.tools.register(FunctionTool(
            name="issue_quiz", handler=issue_quiz, description="从当前会话冻结版本中签发已审核题目",
            parameters={"type": "object", "properties": {
                "concept_id": {"type": "string"}, "difficulty": {"type": ["integer", "null"]}},
                "required": []},
        ))
    if "markitdown" in service.skills.names():
        async def convert_document(arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
            source = str(arguments.get("path") or "").strip()
            if not source:
                return {"status": "error", "code": "document_path_required"}
            service.sandbox.check_path(source)
            try:
                result = await run_in_threadpool(
                    convert_markitdown, source,
                    first_page=arguments.get("first_page"), last_page=arguments.get("last_page"),
                    force_ocr=bool(arguments.get("force_ocr", False)),
                    max_bytes=service.settings.library_max_import_bytes,
                )
                return result
            except Exception as exc:
                return {"status": "error", "code": str(exc).split(":", 1)[0], "message": str(exc)[:240]}

        service.tools.register(FunctionTool(
            name="convert_document", handler=convert_document,
            description="只转换沙箱允许的本地文档，结果写入 DeepProf 用户数据目录",
            parameters={"type": "object", "properties": {
                "path": {"type": "string"}, "first_page": {"type": ["integer", "null"]},
                "last_page": {"type": ["integer", "null"]}, "force_ocr": {"type": "boolean"}},
                "required": ["path"]},
        ))


def main() -> None:  # pragma: no cover - 手动启动入口
    import uvicorn

    resolved = Settings.from_env()
    uvicorn.run(create_app(settings=resolved), host=resolved.api_host, port=resolved.api_port)


class _LazyASGIApplication:
    """Keep ``uvicorn api.app:app`` without touching user storage at import time."""

    def __init__(self) -> None:
        self._instance: FastAPI | None = None

    def _get(self) -> FastAPI:
        if self._instance is None:
            self._instance = create_app()
        return self._instance

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        await self._get()(scope, receive, send)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)


# Uvicorn's conventional object entry remains available while importing the
# module becomes safe during pytest collection and CLI startup.
app = _LazyASGIApplication()
