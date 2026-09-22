"""OpenAI-compatible Adapter。

不假定所有“OpenAI 格式”实现都完整支持 tools / JSON / vision / reasoning；
能力由 Profile 声明 + 启动探测决定，缺失时显式返回 ``provider_capability_missing``。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from runtime.core.errors import (
    KIND_MISSING_CREDENTIAL,
    ProviderError,
    classify_external_failure,
)
from runtime.providers.capabilities import CAP_JSON, CAP_STREAM, CAP_TOOLS, CAP_VISION, normalize_capabilities
from runtime.providers.profiles import (
    API_MODE_CHAT,
    PROTOCOL_OPENAI_COMPATIBLE,
    ProviderProfile,
)
from runtime.providers.base import error_frame, finish_frame, tool_call_frame, usage_frame
from runtime.providers.secrets import SecretStore

CHAT_ENDPOINT = "/chat/completions"
MODELS_ENDPOINT = "/models"


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
        self._default_max_tokens = default_max_tokens
        self._timeout_seconds = timeout_seconds

    # ---- 接口 ----

    def capabilities(self) -> dict[str, bool]:
        return normalize_capabilities(self.profile.capabilities)

    async def list_models(self) -> list[str]:
        url = self._url(MODELS_ENDPOINT)
        client, owns = self._make_client()
        try:
            response = await client.get(url, headers=self._headers(require_key=False))
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

    async def generate(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        body = self._body(request, stream=False)
        client, owns = self._make_client()
        try:
            response = await client.post(self._url(CHAT_ENDPOINT), json=body, headers=self._headers())
            self._ensure_success(response)
            data = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise self._wrap(exc) from exc
        finally:
            if owns:
                await client.aclose()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {
            "content": message.get("content") or "",
            "tool_calls": self._assemble_final_tool_calls(message.get("tool_calls") or []),
            "finish_reason": choice.get("finish_reason") or "",
            "usage": data.get("usage") or {},
            "model": data.get("model") or request.get("model") or self.profile.default_model,
            "capabilities": self.capabilities(),
        }

    async def stream(
        self, request: dict[str, Any], ctx: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        body = self._body(request, stream=True)
        client, owns = self._make_client()
        try:
            async with client.stream(
                "POST", self._url(CHAT_ENDPOINT), json=body, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    raw = await response.aread()
                    error = self._error_from(response.status_code, raw)
                    yield error_frame(error.to_dict())
                    return

                pending: dict[int, dict[str, Any]] = {}
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

                    # usage 出现在 choices 为空的收尾帧，必须单独捕获
                    if chunk.get("usage"):
                        yield usage_frame(chunk["usage"])

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield {"type": "delta", "text": text}
                    for call in delta.get("tool_calls") or []:
                        self._merge_tool_call(pending, call)
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])

                for index in sorted(pending):
                    yield tool_call_frame(_finalize_tool_call(pending[index]))
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

    # ---- 内部 ----

    def _make_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        timeout = (self.profile.timeout_ms / 1000.0) if self.profile.timeout_ms else self._timeout_seconds
        client = httpx.AsyncClient(timeout=timeout, transport=self._transport)
        return client, True

    def _url(self, path: str) -> str:
        return f"{self.profile.base_url.rstrip('/')}{path}"

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
        body: dict[str, Any] = {
            "model": request.get("model") or self.profile.default_model,
            "messages": [
                {k: v for k, v in (m if isinstance(m, dict) else m.to_dict()).items() if k != "metadata"}
                for m in messages
            ],
            "max_tokens": int(request.get("max_tokens") or self._default_max_tokens),
            "stream": stream,
        }
        tools = request.get("tools") or []
        if tools:
            body["tools"] = tools
        if request.get("temperature") is not None:
            body["temperature"] = request["temperature"]
        if stream:
            body["stream_options"] = {"include_usage": True}
        if self.profile.api_mode == API_MODE_CHAT:
            body["api_mode"] = API_MODE_CHAT
        return body

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
        return ProviderError(
            f"provider {self.profile_id} 调用失败：{exc}",
            kind=classification.kind,
            details={"profile_id": self.profile_id, **classification.to_dict()},
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