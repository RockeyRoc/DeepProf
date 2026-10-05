"""OpenAI-compatible Adapter。

不假定所有“OpenAI 格式”实现都完整支持 tools / JSON / vision / reasoning；
能力由 Profile 声明 + 启动探测决定，缺失时显式返回 ``provider_capability_missing``。
"""

from __future__ import annotations

import json
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from runtime.core.errors import (
    KIND_MISSING_CREDENTIAL,
    ProviderError,
    classify_external_failure,
)
from runtime.providers.capabilities import CAP_JSON, CAP_STREAM, CAP_TOOLS, CAP_VISION, normalize_capabilities
from runtime.providers.profiles import (
    PROTOCOL_OPENAI_COMPATIBLE,
    ProviderProfile,
)
from runtime.providers.base import error_frame, finish_frame, tool_call_frame, usage_frame
from runtime.providers.secrets import SecretStore
from runtime.model_options import CHECKED_ON, resolve_model_options
from runtime.web_search import extract_native_sources

CHAT_ENDPOINT = "/chat/completions"
RESPONSES_ENDPOINT = "/responses"
MODELS_ENDPOINT = "/models"


def responses_input_item(item: Any) -> Any:
    """把 chat 形状的 content parts 翻成 Responses 形状。

    Chat Completions 写 ``{"type": "text"}`` 与 ``{"type": "image_url", "image_url": {"url": …}}``；
    Responses 写 ``input_text`` 与 ``input_image``，而且 ``image_url`` 是**字符串**不是对象。
    没有 parts 的消息原样返回；认不出的 part 也原样带着走，不静默丢掉。
    """
    if not isinstance(item, dict) or not isinstance(item.get("content"), list):
        return item
    parts: list[dict[str, Any]] = []
    for part in item["content"]:
        if not isinstance(part, dict):
            continue
        kind = str(part.get("type") or "")
        if kind == "text":
            parts.append({"type": "input_text", "text": str(part.get("text") or "")})
        elif kind == "image_url":
            url = part.get("image_url")
            parts.append({"type": "input_image",
                          "image_url": str((url or {}).get("url") if isinstance(url, dict) else (url or ""))})
        else:
            parts.append(dict(part))
    return {**item, "content": parts} if parts else item


def declared_vision(payload: dict[str, Any]) -> bool | None:
    """从 Provider 自己的模型元数据里读「这个模型吃不吃图片」。

    两处口径都读：ollama ``/api/show`` 的 ``capabilities`` 数组里有 ``"vision"``，
    OpenRouter ``/models`` 的 ``architecture.input_modalities`` 里有 ``"image"``。

    **读不到就返回 None，绝不猜成 False。** 把「不知道」当成「不支持」，会让一批本来
    能用的多模态模型在界面上被拦住——用户看不到任何理由，只会觉得功能是坏的。
    """
    capabilities = payload.get("capabilities")
    if isinstance(capabilities, list):
        return CAP_VISION in {str(item).strip().casefold() for item in capabilities}
    architecture = payload.get("architecture")
    if isinstance(architecture, dict):
        modalities = architecture.get("input_modalities")
        if isinstance(modalities, list):
            return "image" in {str(item).strip().casefold() for item in modalities}
    return None


class OpenAICompatibleProvider:
    """以可配置 Base URL / Model / Secret Ref 接入兼容接口。"""

    protocol = PROTOCOL_OPENAI_COMPATIBLE

    def __init__(
        self,
        profile: ProviderProfile,
        secret_store: SecretStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        default_max_tokens: int = 4096,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.profile = profile
        self.profile_id = profile.profile_id
        self._secrets = secret_store
        self._transport = transport
        self._client = client
        self._owns_client = client is None
        self._default_max_tokens = default_max_tokens
        self._timeout_seconds = timeout_seconds

    # ---- 接口 ----

    def capabilities(self) -> dict[str, bool]:
        return normalize_capabilities(self.profile.capabilities)

    async def list_models(self) -> list[str]:
        url = self._url(MODELS_ENDPOINT)
        client, owns = self._make_client()
        try:
            response = await client.get(url, headers=self._headers(require_key=False), timeout=self._timeout_seconds)
            if response.status_code in {404, 405, 501}:
                raise ProviderError(
                    f"provider {self.profile_id} does not expose a model catalog",
                    kind="model_list_unsupported",
                    status_code=response.status_code,
                    details={"profile_id": self.profile_id},
                )
            self._ensure_success(response)
            data = response.json()
            models = [str(item.get("id", "")) for item in data.get("data", []) if item.get("id")]
            return models or list(self.profile.models)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._wrap(exc) from exc
        finally:
            if owns:
                await client.aclose()

    async def describe_model(self, model: str) -> dict[str, Any]:
        """Read per-model reasoning metadata from the provider's own catalog."""
        if self.profile.vendor_id == "openrouter":
            client, owns = self._make_client(timeout_seconds=15.0)
            try:
                response = await client.get(self._url(MODELS_ENDPOINT), headers=self._headers(), timeout=15.0)
                self._ensure_success(response)
                rows = response.json().get("data", [])
                row = next((item for item in rows if str(item.get("id") or "") == model), None)
                base = {"options_source": "provider_model_metadata",
                        "vision": declared_vision(row if isinstance(row, dict) else {}),
                        "official_docs": "https://openrouter.ai/docs/guides/best-practices/reasoning-tokens",
                        "checked_on": CHECKED_ON}
                reasoning = row.get("reasoning") if isinstance(row, dict) else None
                if not isinstance(reasoning, dict):
                    return {**base, "reasoning_mode": "unknown"}
                efforts = reasoning.get("supported_efforts")
                efforts = [str(value) for value in efforts if isinstance(value, str)] if isinstance(efforts, list) else []
                mandatory = bool(reasoning.get("mandatory"))
                return {**base, "reasoning_mode": "always" if mandatory else "toggle", "reasoning": True,
                        "thinking_parameter": "" if mandatory else "reasoning.enabled",
                        "reasoning_parameter": "reasoning.effort" if efforts else "",
                        "thinking_levels": efforts,
                        "thinking_default": str(reasoning.get("default_effort") or "")}
            finally:
                if owns:
                    await client.aclose()
        if self.profile.vendor_id != "ollama":
            return {}
        root = self.profile.base_url.rstrip("/")
        if root.lower().endswith("/v1"):
            root = root[:-3]
        client, owns = self._make_client(timeout_seconds=10.0)
        try:
            response = await client.post(f"{root}/api/show", json={"model": model}, headers=self._headers(require_key=False), timeout=10.0)
            self._ensure_success(response)
            payload = response.json()
            # 是哪种推理模式、以及吃不吃图片，都在这一份 /api/show 的响应里
            base = {"options_source": "ollama_api_show", "vision": declared_vision(payload),
                    "official_docs": "https://docs.ollama.com/capabilities/thinking",
                    "checked_on": CHECKED_ON}
            metadata = payload.get("thinking")
            if not isinstance(metadata, dict) or not isinstance(metadata.get("values"), list):
                return {**base, "reasoning_mode": "unknown"}
            values = metadata["values"]
            if values == [False] or not values:
                return {**base, "reasoning_mode": "unsupported", "reasoning": False, "thinking_levels": []}
            if all(isinstance(value, str) for value in values):
                return {**base, "reasoning_mode": "toggle", "reasoning": True,
                        "thinking_levels": values,
                        "thinking_default": metadata.get("default"),
                        "thinking_enabled_default": bool(metadata.get("default")),
                        "thinking_parameter": "ollama.think"}
            if all(type(value) is bool for value in values):
                return {**base, "reasoning_mode": "always" if values == [True] else "toggle",
                        "reasoning": True,
                        "thinking_levels": [],
                        "thinking_default": metadata.get("default"),
                        "thinking_enabled_default": bool(metadata.get("default")),
                        "thinking_parameter": "ollama.think"}
            return {**base, "reasoning_mode": "unknown"}
        finally:
            if owns:
                await client.aclose()

    async def generate(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        request = self._with_context_thinking(request, ctx)
        use_responses = self._uses_responses_api(request)
        body = self._responses_body(request, stream=False) if use_responses else self._body(request, stream=False)
        self._archive_research_request(body, ctx)
        timeout = max(self._timeout_seconds, 1800.0) if request.get("thinking_enabled") else self._timeout_seconds
        client, owns = self._make_client(timeout_seconds=timeout)
        try:
            response = await client.post(self._url(RESPONSES_ENDPOINT if use_responses else CHAT_ENDPOINT),
                                         json=body, headers=self._headers(), timeout=timeout)
            self._ensure_success(response)
            data = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise self._wrap(exc) from exc
        finally:
            if owns:
                await client.aclose()

        if use_responses:
            return self._responses_result(data, request)

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {
            "content": message.get("content") or "",
            "reasoning_content": (message.get("reasoning_content") or message.get("reasoning") or message.get("thinking") or "")
            if self._reasoning_allowed(request) else "",
            "tool_calls": self._assemble_final_tool_calls(message.get("tool_calls") or []),
            "finish_reason": choice.get("finish_reason") or "",
            "usage": data.get("usage") or {},
            "model": data.get("model") or request.get("model") or self.profile.default_model,
            "capabilities": self.capabilities(),
            "web_search_sources": extract_native_sources(data, vendor=self.profile.vendor_id)
            if request.get("native_web_search") else [],
        }

    async def stream(
        self, request: dict[str, Any], ctx: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        request = self._with_context_thinking(request, ctx)
        if self._uses_responses_api(request):
            if ctx.get("require_explicit_thinking_mode") or os.environ.get("DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED") == "true":
                raise ProviderError("research_wire_configuration_mismatch: chat thinking.type=disabled required",
                                    kind="capability_missing")
            async for frame in self._stream_responses(request):
                yield frame
            return
        body = self._body(request, stream=True)
        self._archive_research_request(body, ctx)
        timeout = max(self._timeout_seconds, 1800.0) if request.get("thinking_enabled") else self._timeout_seconds
        client, owns = self._make_client(timeout_seconds=timeout)
        try:
            async with client.stream(
                "POST", self._url(CHAT_ENDPOINT), json=body, headers=self._headers(), timeout=timeout
            ) as response:
                if response.status_code >= 400:
                    raw = await response.aread()
                    error = self._error_from(response.status_code, raw)
                    yield error_frame(error.to_dict())
                    return

                pending: dict[int, dict[str, Any]] = {}
                search_sources: dict[str, dict[str, Any]] = {}
                finish_reason = ""
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    if request.get("native_web_search"):
                        for source in extract_native_sources(chunk, vendor=self.profile.vendor_id):
                            search_sources[str(source["url"])] = source

                    # usage 出现在 choices 为空的收尾帧，必须单独捕获
                    if chunk.get("usage"):
                        yield usage_frame(chunk["usage"])

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking")
                    if isinstance(reasoning, str) and reasoning and self._reasoning_allowed(request):
                        yield {"type": "reasoning_delta", "text": reasoning}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield {"type": "delta", "text": text}
                    for call in delta.get("tool_calls") or []:
                        self._merge_tool_call(pending, call)
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])

                for index in sorted(pending):
                    yield tool_call_frame(_finalize_tool_call(pending[index]))
                if search_sources:
                    yield {"type": "web_search_sources", "sources": list(search_sources.values())[:10]}
                yield finish_frame(finish_reason)
        except ProviderError as exc:
            yield error_frame(exc.to_dict())
        except Exception as exc:
            yield error_frame(self._wrap(exc).to_dict())
        finally:
            if owns:
                await client.aclose()

    async def healthcheck(self, model: str | None = None) -> dict[str, Any]:
        from runtime.providers.base import probe_provider

        return await probe_provider(self, model=model or self.profile.default_model)

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- 内部 ----

    def _make_client(self, *, timeout_seconds: float | None = None) -> tuple[httpx.AsyncClient, bool]:
        if self._client is None:
            timeout = (self.profile.timeout_ms / 1000.0) if self.profile.timeout_ms else self._timeout_seconds
            self._client = httpx.AsyncClient(timeout=timeout, transport=self._transport)
            self._owns_client = True
        # Call sites apply timeout_seconds per request; the pool remains reusable.
        return self._client, False

    def _url(self, path: str) -> str:
        return f"{self.profile.base_url.rstrip('/')}{path}"

    def _uses_responses_api(self, request: dict[str, Any]) -> bool:
        if self.profile.api_mode == "responses":
            return True
        if self.profile.api_mode == "chat_completions":
            return False
        model = str(request.get("model") or self.profile.default_model)
        capabilities = self._model_capabilities(model)
        native = request.get("native_web_search") or {}
        return capabilities.get("api_mode") == "responses" or (
            isinstance(native, dict) and native.get("api_mode") == "responses")

    def _model_capabilities(self, model: str) -> dict[str, Any]:
        return resolve_model_options(self.profile.vendor_id, model,
                                     self.profile.model_capabilities.get(model))

    def _responses_body(self, request: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        """Translate our chat-shaped request to the compatible Responses wire format."""
        body = self._body(request, stream=stream)
        body["input"] = [responses_input_item(item) for item in body.pop("messages", [])]
        body["max_output_tokens"] = body.pop("max_tokens", self._default_max_tokens)
        body.pop("stream_options", None)
        # Some providers accept sampling options only on Chat Completions. Keep
        # Responses payloads on the common documented surface.
        body.pop("temperature", None)
        native = request.get("native_web_search") or {}
        if isinstance(native, dict):
            if native.get("tools") and not body.get("tools"):
                body["tools"] = list(native.get("tools") or [])
            if native.get("tool_choice"):
                body["tool_choice"] = native["tool_choice"]
            native_body = native.get("body") or {}
            if native_body.get("enable_search"):
                body.pop("enable_search", None)
                body.pop("search_options", None)
                tools = list(body.get("tools") or [])
                if not any(isinstance(item, dict) and item.get("type") == "web_search" for item in tools):
                    tools.append({"type": "web_search"})
                body["tools"] = tools
        return body

    def _responses_result(self, data: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        answer: list[str] = []
        reasoning: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                        answer.append(str(part.get("text") or ""))
            elif item.get("type") == "reasoning":
                for part in item.get("summary") or []:
                    if isinstance(part, dict):
                        reasoning.append(str(part.get("text") or ""))
        content = str(data.get("output_text") or "") or "".join(answer)
        caps = self._model_capabilities(str(request.get("model") or self.profile.default_model))
        mode = str(caps.get("reasoning_mode") or "unknown")
        reasoning_allowed = mode == "always" or (mode == "toggle" and
            (bool(request.get("thinking_enabled")) if request.get("thinking_mode") != "default"
             else bool(caps.get("thinking_enabled_default"))))
        return {"content": content, "reasoning_content": "".join(reasoning) if reasoning_allowed else "",
                "tool_calls": [], "finish_reason": str(data.get("status") or "completed"),
                "usage": data.get("usage") or {}, "model": data.get("model") or request.get("model") or self.profile.default_model,
                "capabilities": self.capabilities(),
                "web_search_sources": extract_native_sources(data, vendor=self.profile.vendor_id)
                if request.get("native_web_search") else []}

    async def _stream_responses(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        body = self._responses_body(request, stream=True)
        timeout = max(self._timeout_seconds, 1800.0) if request.get("thinking_enabled") else self._timeout_seconds
        client, owns = self._make_client(timeout_seconds=timeout)
        seen_sources: dict[str, dict[str, Any]] = {}
        answer_parts: list[str] = []
        finish_reason = "completed"
        try:
            async with client.stream("POST", self._url(RESPONSES_ENDPOINT), json=body,
                                     headers=self._headers(), timeout=timeout) as response:
                if response.status_code >= 400:
                    raw = await response.aread()
                    yield error_frame(self._error_from(response.status_code, raw).to_dict())
                    return
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    event_type = str(event.get("type") or "")
                    if event_type in {"error", "response.failed"}:
                        failure = event.get("error") or (event.get("response") or {}).get("error") or {}
                        code = str(failure.get("code") or "provider_response_failed") if isinstance(failure, dict) else "provider_response_failed"
                        message = str(failure.get("message") or "Responses API 生成失败。") if isinstance(failure, dict) else "Responses API 生成失败。"
                        yield error_frame({"code": code, "message": message,
                                           "details": {"kind": code, "profile_id": self.profile_id}})
                        return
                    if event_type in {"response.output_text.delta", "response.refusal.delta"}:
                        delta = str(event.get("delta") or "")
                        if delta:
                            answer_parts.append(delta)
                            yield {"type": "delta", "text": delta}
                    elif event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                        delta = str(event.get("delta") or "")
                        if delta and self._reasoning_allowed(request):
                            yield {"type": "reasoning_delta", "text": delta}
                    if request.get("native_web_search"):
                        for source in extract_native_sources(event, vendor=self.profile.vendor_id):
                            seen_sources[str(source["url"])] = source
                    wrapped = event.get("response") if isinstance(event.get("response"), dict) else {}
                    usage = wrapped.get("usage") or event.get("usage")
                    if event_type == "response.completed" and isinstance(usage, dict):
                        yield usage_frame(usage)
                    if event_type in {"response.completed", "response.incomplete"}:
                        finish_reason = str(wrapped.get("status") or event_type.removeprefix("response."))
                        # Some compatible APIs send the final text only in the
                        # completed event. Avoid duplicating deltas if present.
                        if not answer_parts:
                            final = self._responses_result(wrapped, request)
                            if final.get("content"):
                                answer_parts.append(str(final["content"]))
                                yield {"type": "delta", "text": str(final["content"])}
                        if request.get("native_web_search"):
                            for source in extract_native_sources(wrapped, vendor=self.profile.vendor_id):
                                seen_sources[str(source["url"])] = source
        except ProviderError as exc:
            yield error_frame(exc.to_dict())
            return
        except Exception as exc:
            yield error_frame(self._wrap(exc).to_dict())
            return
        finally:
            if owns:
                await client.aclose()
        if seen_sources:
            yield {"type": "web_search_sources", "sources": list(seen_sources.values())[:10]}
        yield finish_frame(finish_reason)

    def _headers(self, *, require_key: bool = True) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.profile.extra_headers}
        key = self._secrets.get(self.profile.api_key_ref) if self.profile.api_key_ref else None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif require_key and self.profile.protocol != "local":
            raise ProviderError(
                f"profile {self.profile_id} 缺少可用凭据",
                kind=KIND_MISSING_CREDENTIAL,
                details={"profile_id": self.profile_id, "api_key_ref": self.profile.api_key_ref},
            )
        return headers

    def _body(self, request: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        messages = request.get("messages") or []
        model = str(request.get("model") or self.profile.default_model)
        model_caps = self._model_capabilities(model)
        body: dict[str, Any] = {
            "model": model,
            "messages": [self._message_for_model(m, model, model_caps) for m in messages],
            "max_tokens": int(request.get("max_tokens") or self._default_max_tokens),
            "stream": stream,
        }
        tools = request.get("tools") or []
        if tools:
            body["tools"] = tools
        native_search = request.get("native_web_search") or {}
        if isinstance(native_search, dict):
            native_body = native_search.get("body") or {}
            if isinstance(native_body, dict):
                body.update(native_body)
            if native_search.get("tool_choice"):
                body["tool_choice"] = native_search["tool_choice"]
        if request.get("temperature") is not None:
            body["temperature"] = request["temperature"]
        thinking_enabled = request.get("thinking_enabled")
        thinking_level = str(request.get("thinking_level") or "")
        thinking_mode = str(request.get("thinking_mode") or "")
        if thinking_mode == "default":
            thinking_enabled, thinking_level = None, ""
        mode = str(model_caps.get("reasoning_mode") or "unknown")
        if mode == "always":
            thinking_enabled = True
        elif mode != "toggle":
            thinking_enabled = None
        if thinking_level and thinking_level != "default":
            allowed_levels = model_caps.get("thinking_levels") or []
            if thinking_level not in allowed_levels:
                raise ProviderError("所选模型不支持该思考等级", kind="invalid_reasoning_effort",
                                    details={"model": model, "thinking_level": thinking_level,
                                             "allowed": allowed_levels})
        budget = request.get("thinking_budget")
        budget_parameter = str(model_caps.get("thinking_budget_parameter") or "")
        if thinking_mode != "default" and budget is not None and budget_parameter:
            minimum = int(model_caps.get("thinking_budget_min") or 1)
            maximum = int(model_caps.get("thinking_budget_max") or 32768)
            if type(budget) is not int or not minimum <= budget <= maximum:
                raise ProviderError("思考预算超出当前模型允许范围", kind="invalid_thinking_budget",
                                    details={"minimum": minimum, "maximum": maximum})
            body[budget_parameter] = budget
        parameter = str(model_caps.get("thinking_parameter") or "")
        enabled = bool(thinking_enabled) if thinking_enabled is not None else None
        if parameter == "thinking.type" and enabled is not None:
            body["thinking"] = {"type": "enabled" if enabled else "disabled"}
        elif parameter == "enable_thinking" and enabled is not None:
            body["enable_thinking"] = enabled
        elif parameter == "reasoning.enabled" and enabled is not None:
            body["reasoning"] = {"enabled": enabled}
        elif parameter == "reasoning.effort" and enabled is not None:
            effort = (thinking_level if enabled and thinking_level and thinking_level != "default"
                      else str(model_caps.get("thinking_default") or "") if enabled else "none")
            if effort:
                if self.profile.api_mode == "chat_completions":
                    body["reasoning_effort"] = effort
                else:
                    reasoning_config = body.setdefault("reasoning", {})
                    reasoning_config["effort"] = effort
        elif parameter == "reasoning_effort" and enabled is not None:
            body["reasoning_effort"] = (thinking_level if enabled and thinking_level and thinking_level != "default"
                                         else str(model_caps.get("thinking_default") or "") if enabled else "none")
            if enabled and not thinking_level and not model_caps.get("thinking_default"):
                body.pop("reasoning_effort", None)
        elif parameter == "ollama.think" and enabled is not None:
            body["think"] = (thinking_level if enabled and thinking_level and thinking_level != "default"
                             else enabled)
        reasoning_parameter = str(model_caps.get("reasoning_parameter") or "")
        if reasoning_parameter and enabled is not None:
            if reasoning_parameter == "reasoning_effort":
                body[reasoning_parameter] = (thinking_level if enabled and thinking_level and thinking_level != "default"
                                             else str(model_caps.get("thinking_default") or "") if enabled else "none")
                if enabled and not thinking_level and not model_caps.get("thinking_default"):
                    body.pop(reasoning_parameter, None)
            elif reasoning_parameter == "reasoning.effort":
                effort = (thinking_level if enabled and thinking_level and thinking_level != "default"
                          else str(model_caps.get("thinking_default") or "") if enabled else "none")
                if effort:
                    if self.profile.api_mode == "chat_completions":
                        body["reasoning_effort"] = effort
                    else:
                        reasoning_config = body.setdefault("reasoning", {})
                        reasoning_config["effort"] = effort
        if model_caps.get("max_output_tokens"):
            body["max_tokens"] = min(body["max_tokens"], int(model_caps["max_output_tokens"]))
        if stream and self.profile.protocol != "local":
            body["stream_options"] = {"include_usage": True}
        return body

    @staticmethod
    def _archive_research_request(body: dict[str, Any], ctx: dict[str, Any]) -> None:
        """Opt-in local reproducibility archive; credentials/headers are never included."""
        rejected = bool(ctx.get("require_explicit_thinking_mode")
            or os.environ.get("DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED") == "true") and body.get("thinking") != {"type": "disabled"}
        directory = os.environ.get("DEEPPROF_RESEARCH_REQUEST_ARCHIVE")
        if not directory:
            if rejected:
                raise ProviderError("research_wire_configuration_mismatch: thinking.type=disabled missing",
                                    kind="capability_missing")
            return
        root = Path(directory).resolve()
        if rejected:
            root = root / "rejected-before-http"
        root.mkdir(parents=True, exist_ok=True)
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        messages = json.dumps(body.get("messages", body.get("input", [])), ensure_ascii=False,
                              sort_keys=True, separators=(",", ":"))
        payload = {"schema_version": "deepprof-private-request-snapshot-v1",
                   "trace_id": str(ctx.get("trace_id") or ""),
                   "session_id": str(ctx.get("session_id") or ""),
                   "body_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                   "messages_sha256": hashlib.sha256(messages.encode()).hexdigest(),
                   "body": body, "private_local_only": True,
                   "request_state": "rejected_before_http" if rejected else "prepared_for_http"}
        path = root / (uuid.uuid4().hex + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        if rejected:
            raise ProviderError("research_wire_configuration_mismatch: thinking.type=disabled missing",
                                kind="capability_missing")

    @staticmethod
    def _with_context_thinking(request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        if "thinking_enabled" not in ctx:
            return request
        mode = ctx.get("thinking_mode", request.get("thinking_mode", ""))
        if ctx.get("require_explicit_thinking_mode") and ctx["thinking_enabled"] is False:
            mode = "off"
        return {**request, "thinking_enabled": ctx["thinking_enabled"],
                "thinking_mode": mode}

    def _message_for_model(self, message: Any, model: str, model_caps: dict[str, Any]) -> dict[str, Any]:
        data = dict(message if isinstance(message, dict) else message.to_dict())
        metadata = dict(data.pop("metadata", {}) or {})
        if (
            model_caps.get("preserve_reasoning")
            and data.get("role") == "assistant"
            and metadata.get("provider_profile") == self.profile_id
            and metadata.get("model") == model
            and metadata.get("reasoning_content")
        ):
            data["reasoning_content"] = str(metadata["reasoning_content"])
        return data

    def _reasoning_allowed(self, request: dict[str, Any]) -> bool:
        model = str(request.get("model") or self.profile.default_model)
        mode = str(self._model_capabilities(model).get("reasoning_mode") or "unknown")
        caps = self._model_capabilities(model)
        return mode == "always" or (mode == "toggle" and
            (bool(request.get("thinking_enabled")) if request.get("thinking_mode") != "default"
             else bool(caps.get("thinking_enabled_default"))))

    @staticmethod
    def _merge_tool_call(pending: dict[int, dict[str, Any]], call: dict[str, Any]) -> None:
        """按 index 增量装配：先出现的 id/name 保留，arguments 分片拼接。"""
        index = int(call.get("index", 0))
        slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if call.get("id"):
            slot["id"] = str(call["id"])
        function = call.get("function") or {}
        if function.get("name"):
            slot["name"] = str(function["name"])
        if function.get("arguments"):
            slot["arguments"] += str(function["arguments"])

    @staticmethod
    def _assemble_final_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in calls:
            function = item.get("function") or {}
            result.append(
                {
                    "id": str(item.get("id", "")),
                    "name": str(function.get("name", "")),
                    "arguments": _loads_or_empty(function.get("arguments")),
                }
            )
        return result

    def _ensure_success(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise self._error_from(response.status_code, response.content)

    def _error_from(self, status_code: int, raw: bytes | str | None) -> ProviderError:
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")
        classification = classify_external_failure(status_code=status_code, body=text)
        return ProviderError(
            f"provider {self.profile_id} 返回 HTTP {status_code}",
            kind=classification.kind,
            status_code=status_code,
            details={"profile_id": self.profile_id, "body": _truncate(text), "detail": classification.detail},
        )

    def _wrap(self, exc: BaseException) -> ProviderError:
        classification = classify_external_failure(error=exc)
        cause_types: list[str] = []
        seen: set[int] = set()
        cause: BaseException | None = exc
        while cause is not None and id(cause) not in seen and len(cause_types) < 4:
            seen.add(id(cause))
            cause_types.append(type(cause).__name__)
            cause = cause.__cause__ or cause.__context__
        os_error = next((item for item in _exception_chain(exc) if isinstance(item, OSError)), None)
        return ProviderError(
            f"provider {self.profile_id} 调用失败：{exc}",
            kind=classification.kind,
            details={"profile_id": self.profile_id, **classification.to_dict(),
                     "cause_types": cause_types,
                     **({"os_errno": os_error.errno} if os_error and os_error.errno is not None else {})},
        )


def _finalize_tool_call(slot: dict[str, Any]) -> dict[str, Any]:
    """把按 index 拼好的分片归一为最终调用：arguments 必须是 dict。

    流式与非流式必须产出同一形状，否则 Agent 侧解析会不一致。
    """
    return {
        "id": str(slot.get("id", "")),
        "name": str(slot.get("name", "")),
        "arguments": _loads_or_empty(slot.get("arguments")),
    }


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 4:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _loads_or_empty(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _truncate(text: str, limit: int = 400) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def build_openai_compatible(
    profile: ProviderProfile,
    secret_store: SecretStore,
    **kwargs: Any,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(profile, secret_store, **kwargs)
