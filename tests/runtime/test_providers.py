"""Provider 契约测试：用 mock client 验证协议适配，不发任何网络请求。"""

from __future__ import annotations

import json

import httpx
import pytest

from runtime.core.errors import (
    KIND_AUTH_FAILED,
    KIND_MODEL_TRUNCATED,
    KIND_RATE_LIMITED,
    KIND_REGION_OR_PERMISSION_BLOCKED,
    KIND_TIMEOUT,
    KIND_UPSTREAM_ERROR,
    ProviderError,
    classify_external_failure,
)
from runtime.providers.base import (
    PROBE_MAX_TOKENS,
    STATUS_FAILED,
    STATUS_INCONCLUSIVE,
    STATUS_OK,
    probe_provider,
)
from runtime.providers.openai_compatible import OpenAICompatibleProvider
from runtime.providers.profiles import ProviderProfile
from runtime.providers.secrets import InMemorySecretStore

BASE_URL = "https://example.invalid/v1"


def make_provider(handler, *, api_key: str | None = "sk-test", **profile_kwargs):
    transport = httpx.MockTransport(handler)
    profile = ProviderProfile(
        profile_id="p1",
        display_name="Test",
        base_url=BASE_URL,
        api_key_ref="provider:p1",
        default_model="model-a",
        capabilities={"stream": True, "tools": True},
        **profile_kwargs,
    )
    secrets = InMemorySecretStore({"provider:p1": api_key} if api_key else {})
    return OpenAICompatibleProvider(profile, secrets, transport=transport)


def sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return (body + "data: [DONE]\n\n").encode()


def delta(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}, "finish_reason": None}]}


# ---- 非流式 ----

async def test_generate_maps_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "model": "model-a",
                "choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    provider = make_provider(handler)
    result = await provider.generate({"messages": [{"role": "user", "content": "hi"}]}, {})
    assert result["content"] == "你好"
    assert result["finish_reason"] == "stop"
    assert result["usage"]["prompt_tokens"] == 3


async def test_generate_parses_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {"name": "retrieve_evidence", "arguments": '{"q":"x"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    provider = make_provider(handler)
    result = await provider.generate({"messages": []}, {})
    assert result["tool_calls"] == [
        {"id": "call_1", "name": "retrieve_evidence", "arguments": {"q": "x"}}
    ]


# ---- 流式 ----

async def test_stream_emits_delta_usage_and_finish():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                delta("你"),
                delta("好"),
                # usage 帧的 choices 为空，必须单独捕获
                {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ),
        )

    provider = make_provider(handler)
    frames = [frame async for frame in provider.stream({"messages": []}, {})]
    assert [f["text"] for f in frames if f["type"] == "delta"] == ["你", "好"]
    assert [f["usage"] for f in frames if f["type"] == "usage"] == [
        {"prompt_tokens": 5, "completion_tokens": 2}
    ]
    assert frames[-1] == {"type": "finish", "finish_reason": "stop"}


async def test_stream_assembles_tool_calls_by_index():
    """流式工具调用按 index 增量装配：id/name 先到，arguments 分片拼接。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "id": "call_a", "function": {"name": "search", "arguments": '{"q":'}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '"books"}'}},
                                    {"index": 1, "id": "call_b", "function": {"name": "read", "arguments": "{}"}},
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ),
        )

    provider = make_provider(handler)
    frames = [frame async for frame in provider.stream({"messages": []}, {})]
    calls = [f["tool_call"] for f in frames if f["type"] == "tool_call"]
    assert calls == [
        {"id": "call_a", "name": "search", "arguments": {"q": "books"}},
        {"id": "call_b", "name": "read", "arguments": {}},
    ]


async def test_stream_closes_sdk_stream_on_error():
    """流中断必须包成结构化 error 帧，而不是把异常抛到循环清理处。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = make_provider(handler)
    frames = [frame async for frame in provider.stream({"messages": []}, {})]
    assert frames[-1]["type"] == "error"
    assert frames[-1]["error"]["details"]["kind"] == "connection_failed"


async def test_stream_reports_http_error_as_error_frame():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limit"}})

    provider = make_provider(handler)
    frames = [frame async for frame in provider.stream({"messages": []}, {})]
    assert frames[0]["type"] == "error"
    assert frames[0]["error"]["details"]["kind"] == KIND_RATE_LIMITED
    assert frames[0]["error"]["details"]["retryable"] is True


# ---- 凭据 ----

async def test_missing_credential_is_structured_error():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("缺少凭据时不应发出请求")

    provider = make_provider(handler, api_key=None)
    with pytest.raises(ProviderError) as excinfo:
        await provider.generate({"messages": []}, {})
    assert excinfo.value.kind == "missing_credential"


async def test_local_provider_does_not_require_key():
    from runtime.providers.local import LocalProvider

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    profile = ProviderProfile(profile_id="local", protocol="local", base_url="http://127.0.0.1:11434/v1")
    provider = LocalProvider(profile, transport=httpx.MockTransport(handler))
    assert (await provider.generate({"messages": []}, {}))["content"] == "ok"


# ---- 能力探测 ----

async def test_probe_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}]}
        )

    result = await probe_provider(make_provider(handler))
    assert result["status"] == STATUS_OK
    assert result["capabilities"]["stream"] is True


async def test_probe_uses_reasoning_safe_budget():
    """探测预算过小会让推理模型假阴性，因此必须使用 PROBE_MAX_TOKENS。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}]}
        )

    await probe_provider(make_provider(handler))
    assert seen["max_tokens"] == PROBE_MAX_TOKENS >= 512


async def test_probe_length_truncation_is_inconclusive_not_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        )

    result = await probe_provider(make_provider(handler))
    assert result["status"] == STATUS_INCONCLUSIVE


async def test_probe_http_error_is_failure_with_kind():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "region not supported"}})

    result = await probe_provider(make_provider(handler))
    assert result["status"] == STATUS_FAILED
    assert result["kind"] == KIND_REGION_OR_PERMISSION_BLOCKED


async def test_probe_never_sends_conversation_content():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}]})

    await probe_provider(make_provider(handler), model="m")
    assert seen["messages"] == [{"role": "user", "content": "Reply with the single word: pong"}]


# ---- 错误分类 ----

@pytest.mark.parametrize(
    "status,expected",
    [
        (401, KIND_AUTH_FAILED),
        (403, KIND_REGION_OR_PERMISSION_BLOCKED),
        (429, KIND_RATE_LIMITED),
        (408, KIND_TIMEOUT),
        (503, KIND_UPSTREAM_ERROR),
    ],
)
def test_status_code_drives_classification(status: int, expected: str):
    result = classify_external_failure(status_code=status, body="")
    assert result.kind == expected


def test_status_code_wins_over_body_substring():
    """历史缺陷：403 地区限制因元数据含 '401' 被误判为鉴权失败。"""
    body = '{"error": {"code": 401, "message": "region restricted", "metadata": "401"}}'
    result = classify_external_failure(status_code=403, body=body)
    assert result.kind == KIND_REGION_OR_PERMISSION_BLOCKED


def test_connection_error_is_retryable():
    result = classify_external_failure(error=httpx.ConnectError("boom"))
    assert result.kind == "connection_failed"
    assert result.retryable is True


def test_provider_error_carries_kind_in_details():
    error = ProviderError("x", kind=KIND_RATE_LIMITED, status_code=429)
    payload = error.to_dict()
    assert payload["details"]["kind"] == KIND_RATE_LIMITED
    assert payload["details"]["http_status"] == 429
    assert payload["details"]["retryable"] is True


def test_truncation_kind_exists():
    assert KIND_MODEL_TRUNCATED == "model_truncated"