"""RuntimeService：同时实现窄面（RuntimePort）与宽面（RuntimeHost）。

对教学图只暴露 ``execute`` / ``emit`` 的类型承诺；对能力实现才移交宽面。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Callable

import httpx

from config.settings import Settings
from runtime.capabilities import ActionDispatcher
from runtime.core.errors import KIND_MODEL_TRUNCATED, ProviderError, RuntimeFailure, classify_external_failure
from runtime.core.events import (
    EventType,
    InMemoryEventStore,
    RuntimeEvent,
    new_id,
)
from runtime.core.session import Session
from runtime.providers.registry import ProviderRegistry
from runtime.providers.capabilities import CAP_STREAM, missing_capabilities
from runtime.providers.profiles import PROTOCOL_LOCAL, PROTOCOL_OPENAI_COMPATIBLE
from runtime.sandbox.policy import SandboxPolicy
from runtime.skills import SkillRegistry
from runtime.tools.registry import ToolRegistry

Listener = Callable[[dict[str, Any]], None]
_LOGGER = logging.getLogger(__name__)


class RuntimeService:
    """自研 Runtime 的执行底座。"""

    def __init__(
        self,
        *,
        router: ProviderRegistry,
        settings: Settings | None = None,
        event_store: Any | None = None,
        session_store: Any | None = None,
        skills: SkillRegistry | None = None,
        tools: ToolRegistry | None = None,
        bindings: dict[str, dict[str, Any]] | None = None,
        sandbox: SandboxPolicy | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.router = router
        self.events = event_store or InMemoryEventStore()
        self.sessions = session_store
        self.skills = skills or SkillRegistry()
        self.tools = tools or ToolRegistry()
        self.sandbox = sandbox or SandboxPolicy.from_settings(self.settings)
        self.dispatcher = ActionDispatcher(self, bindings)
        # Product-level services may attach here at the composition root.  The
        # Runtime itself never imports the resource-library package.
        self.library: Any | None = None
        # 读图字节的能力同样由组合根注入（见 api/app.py）。Runtime 只认字节，
        # 不 import api，也不知道图片落在哪台机器的哪个目录里。
        self.image_reader: Callable[[dict[str, Any]], str] | None = None
        self._listeners: list[Listener] = []
        self._emit_lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None
        self.performance_metrics: dict[str, float | int] = {
            "event_write_count": 0, "event_write_ms_total": 0.0, "event_write_ms_max": 0.0,
            "first_model_event_ms_last": 0.0, "sse_queue_peak": 0, "sse_overflows": 0,
            "cancel_latency_ms_last": 0.0,
        }
        self._trace_started_at: dict[str, float] = {}
        self._first_model_event_traces: set[str] = set()
        self._cancel_requested_at: dict[str, float] = {}

    def mark_cancel_requested(self, session_id: str) -> None:
        self._cancel_requested_at[session_id] = time.perf_counter()

    def record_turn_finished(self, session_id: str) -> None:
        started = self._cancel_requested_at.pop(session_id, None)
        if started is None:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.performance_metrics["cancel_latency_ms_last"] = elapsed_ms
        _LOGGER.info("gateway_cancel_latency_ms=%.1f", elapsed_ms)

    def http_client(self) -> httpx.AsyncClient:
        """A process-lifetime outbound HTTP pool for search/extraction services."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0))
        return self._http_client

    async def aclose(self) -> None:
        close = getattr(self.router, "aclose", None)
        if callable(close):
            await close()
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ---- 窄面 ----

    async def execute(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return await self.dispatcher.execute(request, ctx)

    async def emit(self, event: dict[str, Any]) -> None:
        runtime_event = event if isinstance(event, RuntimeEvent) else RuntimeEvent.from_dict(event)
        # SQLite-backed event stores are synchronous. Keep their disk work out of
        # the event loop while serializing append+publish so sequence order is stable.
        async with self._emit_lock:
            write_started = time.perf_counter()
            stored = await asyncio.to_thread(self.events.append, runtime_event)
            elapsed_ms = (time.perf_counter() - write_started) * 1000.0
            self.performance_metrics["event_write_count"] = int(self.performance_metrics["event_write_count"]) + 1
            self.performance_metrics["event_write_ms_total"] = float(self.performance_metrics["event_write_ms_total"]) + elapsed_ms
            self.performance_metrics["event_write_ms_max"] = max(float(self.performance_metrics["event_write_ms_max"]), elapsed_ms)
            if elapsed_ms >= 25.0:
                _LOGGER.info("gateway_event_write_ms=%.1f event_type=%s", elapsed_ms, stored.type)
            trace_id = str(stored.trace_id or "")
            if stored.type == EventType.AGENT_TURN_STARTED.value and trace_id:
                self._trace_started_at[trace_id] = time.perf_counter()
            elif stored.type in {EventType.MODEL_STREAM_DELTA.value, EventType.MODEL_STREAM_REASONING_DELTA.value} and trace_id:
                started = self._trace_started_at.get(trace_id)
                if started is not None and trace_id not in self._first_model_event_traces:
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    self.performance_metrics["first_model_event_ms_last"] = latency_ms
                    self._first_model_event_traces.add(trace_id)
                    _LOGGER.info("gateway_first_model_event_ms=%.1f", latency_ms)
            elif stored.type == EventType.AGENT_TURN_COMPLETED.value and trace_id:
                self._trace_started_at.pop(trace_id, None)
                self._first_model_event_traces.discard(trace_id)
            payload = stored.to_dict()
            for listener in list(self._listeners):
                try:
                    listener(payload)
                except Exception:  # 监听器故障不影响主链路
                    continue

    # ---- 宽面 ----

    async def invoke_skill(self, name: str, input: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """调用 Skill；声明 ``uses_host`` 的实现额外拿到本服务的宽面（§6.4）。"""
        return await self.skills.invoke(name, input, ctx, host=self)

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return await self.tools.call(name, arguments, ctx)

    async def generate(self, request: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """能力层的模型调用入口：解析逻辑角色 → 流式产出统一帧，并写入模型事件。"""
        role = str(request.get("role") or "tutor.default")
        pinned_profile = str(request.get("provider_profile") or ctx.get("provider_profile") or "")
        pinned_model = str(request.get("model") or ctx.get("model") or "")
        experiment = bool(ctx.get("experiment_run") or ctx.get("fixed_model_experiment"))
        if pinned_profile:
            try:
                profile = self.router.profile(pinned_profile)
                resolver = getattr(self.router, "resolve_chain_for_profile", None)
                candidates = (resolver(pinned_profile, requested_model=pinned_model or None)
                              if callable(resolver) and not experiment else [])
                if not candidates:
                    if not profile.enabled:
                        raise ValueError(f"provider profile disabled: {pinned_profile!r}")
                    candidates = [(self.router.get(pinned_profile), pinned_profile,
                                   pinned_model or profile.default_model)]
            except ValueError as exc:
                raise RuntimeError(f"provider profile unavailable: {pinned_profile}") from exc
        elif ctx.get("freeze_model"):
            raise RuntimeError("真实模型未配置；当前会话的模型配置已冻结")
        else:
            try:
                candidates = self.router.resolve_chain(role, requested_model=request.get("model"))
            except ValueError as exc:
                raise RuntimeError("真实模型未配置") from exc
            if not candidates:
                raise RuntimeError("no provider candidate available")
        allow_fallback = not (experiment or not ctx.get("allow_provider_fallback", True))
        primary_id = candidates[0][1]
        payload = dict(request)
        generation_config = ctx.get("generation_config")
        if isinstance(generation_config, dict):
            if isinstance(generation_config.get("temperature"), (int, float)):
                payload["temperature"] = float(generation_config["temperature"])
            if isinstance(generation_config.get("max_output_tokens"), int):
                payload["max_tokens"] = max(1, int(generation_config["max_output_tokens"]))
        suppress_user_stream = bool(ctx.get("suppress_user_stream"))
        # 本轮的图片引用在这一处补进请求：讲解 / 纠错 / 追问各自组的 messages 都从这里出去，
        # 「带图」这件事因此只有一个主人（读盘走注入的 image_reader，见文件末尾的说明）。
        payload["messages"] = _inline_images(self, payload.get("messages"), ctx)
        last_error: dict[str, Any] | None = None
        for index, (provider, profile_id, model) in enumerate(candidates):
            capabilities = provider.capabilities()
            # A missing declaration is different from an explicit ``stream: false``.
            # The OpenAI-compatible and local adapters implement streaming, so for
            # ordinary turns we can try the request and let the upstream response
            # establish whether this particular endpoint/model accepts it.
            try:
                profile = self.router.profile(profile_id)
                declared_stream = profile.capabilities.get(CAP_STREAM)
                protocol = profile.protocol
            except (AttributeError, ValueError):
                declared_stream = None
                protocol = str(getattr(provider, "protocol", ""))
            if declared_stream is True:
                missing: list[str] = []
            elif declared_stream is False:
                missing = [CAP_STREAM]
            elif experiment:
                # Experiment evidence requires an explicit, frozen declaration.
                missing = [CAP_STREAM]
            elif protocol in {PROTOCOL_OPENAI_COMPATIBLE, PROTOCOL_LOCAL}:
                missing = []
            else:
                missing = missing_capabilities(capabilities, (CAP_STREAM,))
            if ctx.get("require_explicit_thinking_mode"):
                try:
                    profile = self.router.profile(profile_id)
                    model_caps = dict(profile.model_capabilities.get(model) or {})
                except (AttributeError, ValueError):
                    model_caps = {}
                mode = str(model_caps.get("reasoning_mode") or "")
                parameter = str(model_caps.get("thinking_parameter") or "")
                if (ctx.get("thinking_enabled") is not False or mode != "toggle"
                        or parameter != "thinking.type"):
                    error = {"code": "provider_capability_missing",
                        "message": "固定模型实验要求明确声明可关闭的思考模式",
                        "details": {"profile_id": profile_id, "missing_capabilities": [
                            "thinking.type=disabled"], "reasoning_mode": mode or "unknown",
                            "kind": "capability_missing"}}
                    await self._emit_ctx(EventType.MODEL_FAILED, {"error": error}, ctx)
                    yield {"type": "error", "error": error}
                    return
            if missing:
                last_error = {"code": "provider_capability_missing", "message": "所选模型未声明流式输出能力，无法用于当前请求",
                              "details": {"profile_id": profile_id, "missing_capabilities": missing}}
                if allow_fallback and index + 1 < len(candidates):
                    continue
                await self._emit_ctx(EventType.MODEL_FAILED, {"error": last_error}, ctx)
                yield {"type": "error", "error": last_error}
                return

            attempt_payload = {**payload, "model": model}
            request_event = {"role": role, "provider_profile": profile_id, "model": model}
            if index:
                request_event.update({"degraded_from": primary_id, "fallback_index": index})
            await self._emit_ctx(EventType.MODEL_REQUESTED, request_event, ctx)
            usage: dict[str, Any] = {}
            finish_reason = ""
            has_text = False
            visible_output = False
            attempt_error: dict[str, Any] | None = None
            try:
                async for frame in provider.stream(attempt_payload, ctx):
                    kind = frame.get("type")
                    if kind == "delta":
                        delta_text = str(frame.get("text") or "")
                        has_text = has_text or bool(delta_text.strip())
                        visible_output = visible_output or bool(delta_text.strip())
                        if not suppress_user_stream:
                            await self._emit_ctx(EventType.MODEL_STREAM_DELTA, {"text": delta_text}, ctx)
                    elif kind == "reasoning_delta":
                        reasoning_text = str(frame.get("text") or "")
                        visible_output = visible_output or bool(reasoning_text)
                        if reasoning_text and not suppress_user_stream:
                            await self._emit_ctx(EventType.MODEL_STREAM_REASONING_DELTA, {"text": reasoning_text}, ctx)
                    elif kind == "usage":
                        usage = dict(frame.get("usage") or {})
                    elif kind == "finish":
                        finish_reason = str(frame.get("finish_reason") or "")
                    elif kind == "error":
                        attempt_error = dict(frame.get("error") or {})
                        break
                    elif kind == "tool_call":
                        visible_output = True
                    yield frame
            except Exception as exc:
                if isinstance(exc, ProviderError):
                    attempt_error = exc.to_dict()
                else:
                    classified = classify_external_failure(error=exc)
                    attempt_error = {"code": classified.kind, "message": str(exc),
                                     "details": classified.to_dict()}

            if attempt_error is not None:
                last_error = attempt_error
                await self._emit_ctx(EventType.MODEL_FAILED, {"error": attempt_error}, ctx)
                if allow_fallback and not visible_output and index + 1 < len(candidates):
                    continue
                yield {"type": "error", "error": attempt_error}
                return
            if not has_text:
                kind = KIND_MODEL_TRUNCATED if finish_reason == "length" else "empty_model_response"
                message = "模型输出达到 token 上限但正文为空" if kind == KIND_MODEL_TRUNCATED else "模型未返回可用正文"
                error = {"code": kind, "message": message,
                         "details": {"kind": kind, "finish_reason": finish_reason, "usage": usage}}
                last_error = error
                await self._emit_ctx(EventType.MODEL_FAILED, {"error": error}, ctx)
                if allow_fallback and not visible_output and index + 1 < len(candidates):
                    continue
                yield {"type": "error", "error": error}
                return
            await self._emit_ctx(EventType.MODEL_COMPLETED,
                {"usage": usage, "model": model, "finish_reason": finish_reason}, ctx)
            return

        error = last_error or {"code": "provider_unavailable", "message": "没有可用模型", "details": {}}
        yield {"type": "error", "error": error}

    # ---- 会话 ----

    def new_session(self, *, learner_id: str = "local", title: str = "") -> Session:
        session = Session(learner_id=learner_id, title=title)
        if self.sessions is not None:
            self.sessions.save(session)
        return session

    def get_session(self, session_id: str) -> Session:
        session = self.sessions.load(session_id) if self.sessions is not None else None
        if session is None:
            raise RuntimeFailure(
                f"session_not_found: {session_id}",
                details={"kind": "session_not_found", "session_id": session_id},
            )
        return session

    def save_session(self, session: Session) -> None:
        if self.sessions is not None:
            self.sessions.save(session)

    def history(self, session_id: str, from_sequence: int = 0) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events.replay(session_id, from_sequence)]

    def last_sequence(self, session_id: str) -> int:
        return self.events.last_sequence(session_id)

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ---- 装配状态 ----

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "bindings": self.dispatcher.binding_summary(),
            "skills": self.skills.names(),
            "tools": self.tools.names(),
            "providers": self.router.status(),
            "roles": {role: list(binding) for role, binding in self.router.roles.items()},
        }

    async def _emit_ctx(self, event_type: EventType, payload: dict[str, Any], ctx: dict[str, Any]) -> None:
        await self.emit(
            RuntimeEvent(
                type=event_type.value,
                payload=payload,
                session_id=str(ctx.get("session_id") or ""),
                trace_id=str(ctx.get("trace_id") or new_id("trc")),
                client_id=ctx.get("client_id"),
                surface=ctx.get("surface"),
            ).to_dict()
        )


def _inline_images(service: RuntimeService, messages: Any, ctx: dict[str, Any]) -> Any:
    """把本轮带的图片内联进模型请求的最后一条 user 消息。

    **为什么放在这一处。** 教学侧的讲解与纠错（``generate_grounded``）和追问
    （socratic skill）各自组请求，但它们**都**经过 ``RuntimeService.generate``。
    放这里等于让「带图」只有一个主人：能力层照常拼提示词，图片由这里补成
    content parts（OpenAI 兼容接口定义的形状，与聊天侧的
    ``api/sessions._model_message`` 完全一致——两条路交出去的请求长得一模一样）。

    **字节从哪来。** 会话与图状态里都只存引用，读盘由组合根注入的 ``image_reader``
    完成（见 api/app.py），所以 runtime 既不 import api，也不认识数据目录在哪（§4.4）。

    没有引用时原样返回，**一个字段都不动**——不带图的消息不能被顺手改了形状。
    带了引用却读不出字节、或压根没有可挂的 user 消息时显式报错：悄悄少一张图，
    比多报一次错更难查（与 api/media.image_data_url 同一条纪律）。
    """
    refs = [item for item in (ctx.get("images") or []) if isinstance(item, dict)]
    if not refs or not isinstance(messages, list) or not messages:
        return messages
    reader = getattr(service, "image_reader", None)
    if not callable(reader):
        raise RuntimeError("图片读取未装配：本轮图片交不出去，请检查网关对 image_reader 的注入。")
    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if not isinstance(item, dict) or str(item.get("role") or "") != "user":
            continue
        text = item.get("content")
        parts: list[dict[str, Any]] = []
        if isinstance(text, str) and text.strip():
            parts.append({"type": "text", "text": text})
        elif isinstance(text, list):
            parts.extend(text)
        parts.extend({"type": "image_url", "image_url": {"url": reader(ref)}} for ref in refs)
        return [*messages[:index], {**item, "content": parts}, *messages[index + 1:]]
    raise RuntimeError("本轮的模型请求里没有 user 消息，图片无处可挂。")
