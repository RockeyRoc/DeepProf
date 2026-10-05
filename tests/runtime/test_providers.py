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
from runtime.testing import make_service

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


async def test_qwen_native_search_uses_bailian_chat_completions_fields():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "已搜索。"}, "finish_reason": "stop"}],
            "search_info": {"search_results": [{"title": "官方来源", "url": "https://example.com/source",
                                                    "content": "搜索摘要"}]},
        })

    provider = make_provider(handler, vendor_id="qwen")
    result = await provider.generate({"model": "qwen-plus", "messages": [{"role": "user", "content": "最新信息"}],
        "native_web_search": {"body": {"enable_search": True, "search_options": {"forced_search": True}}}}, {})
    assert seen["enable_search"] is True
    assert seen["search_options"] == {"forced_search": True}
    assert result["web_search_sources"] == [{"title": "官方来源", "url": "https://example.com/source",
        "snippet": "搜索摘要", "published_at": "", "provider": "qwen"}]


async def test_glm_native_search_uses_only_zhipu_websearchprime_mcp():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "已调用智谱原生搜索。"}, "finish_reason": "stop"}],
        })

    provider = make_provider(handler, vendor_id="glm")
    await provider.generate({"model": "glm-4.6", "messages": [{"role": "user", "content": "最新信息"}],
        "tools": [{"type": "mcp", "mcp": {"server_label": "mcp code",
            "transport_type": "streamable-http", "allowed_tools": ["webSearchPrime"]}}],
        "native_web_search": {"body": {}, "tool_choice": "auto"}}, {})
    assert seen["tools"][0]["type"] == "mcp"
    assert seen["tools"][0]["mcp"]["allowed_tools"] == ["webSearchPrime"]
    assert seen["tool_choice"] == "auto"
    assert "enable_search" not in seen


async def test_bailian_responses_search_parses_reasoning_answer_usage_and_sources():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "resp_1", "status": "completed", "model": "glm-5.2",
            "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "先核对来源。"}]},
                {"type": "web_search_call", "action": {"sources": [{
                    "title": "百炼官方说明", "url": "https://help.aliyun.com/zh/model-studio/web-search",
                    "snippet": "Responses API search source",
                }]}},
                {"type": "message", "content": [{"type": "output_text", "text": "已按官方来源回答。"}]},
            ], "usage": {"input_tokens": 23, "output_tokens": 12},
        })

    provider = make_provider(handler, vendor_id="qwen")
    result = await provider.generate({
        "model": "glm-5.2", "messages": [{"role": "user", "content": "今天的更新是什么？"}],
        "thinking_enabled": True, "thinking_level": "max", "max_tokens": 4096,
        "native_web_search": {"api_mode": "responses", "tools": [{"type": "web_search"}], "body": {}},
    }, {})

    assert seen["path"] == "/v1/responses"
    assert seen["body"]["input"] == [{"role": "user", "content": "今天的更新是什么？"}]
    assert seen["body"]["tools"] == [{"type": "web_search"}]
    assert seen["body"]["reasoning"] == {"effort": "max"}
    assert "messages" not in seen["body"] and "max_tokens" not in seen["body"]
    assert result["content"] == "已按官方来源回答。"
    assert result["reasoning_content"] == "先核对来源。"
    assert result["usage"]["input_tokens"] == 23
    assert result["web_search_sources"][0]["url"].endswith("web-search")


async def test_ark_responses_search_stream_emits_text_thinking_and_sources():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        events = [
            {"type": "response.reasoning_summary_text.delta", "delta": "核查实时信息。"},
            {"type": "response.output_text.delta", "delta": "方舟搜索已执行。"},
            {"type": "response.output_item.done", "item": {"type": "web_search_call", "action": {
                "sources": [{"title": "方舟说明", "url": "https://docs.volcengine.com/docs/ark/online-content-plugin-guide"}],
            }}},
            {"type": "response.completed", "response": {"status": "completed", "usage": {"output_tokens": 19}}},
            "[DONE]",
        ]
        body = "".join(("data: " + (item if isinstance(item, str) else json.dumps(item)) + "\n\n") for item in events)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = make_provider(handler, vendor_id="volcengine")
    frames = [frame async for frame in provider.stream({
        "model": "doubao-seed-2-1-pro-260628", "messages": [{"role": "user", "content": "查询新闻"}],
        "thinking_enabled": True, "thinking_level": "high", "max_tokens": 4096,
        "native_web_search": {"api_mode": "responses", "tools": [{"type": "web_search"}], "body": {}},
    }, {})]
    assert seen["path"] == "/v1/responses"
    assert seen["body"]["tools"] == [{"type": "web_search"}]
    assert seen["body"]["thinking"] == {"type": "enabled"}
    assert seen["body"]["reasoning"] == {"effort": "high"}
    assert [frame["type"] for frame in frames] == ["reasoning_delta", "delta", "usage", "web_search_sources", "finish"]
    assert frames[0]["text"] == "核查实时信息。"
    assert frames[1]["text"] == "方舟搜索已执行。"
    assert frames[3]["sources"][0]["provider"] == "volcengine"


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


async def test_thinking_parameters_are_provider_and_model_capability_specific():
    deepseek = make_provider(
        lambda _: httpx.Response(200, json={}), vendor_id="deepseek",
        model_capabilities={"model-a": {"reasoning_mode": "toggle", "thinking_parameter": "thinking.type"}},
    )
    body = deepseek._body({"model": "model-a", "messages": [], "thinking_enabled": True}, stream=False)
    assert body["thinking"] == {"type": "enabled"}
    disabled = deepseek._body({"model": "model-a", "messages": [], "thinking_enabled": False}, stream=False)
    assert disabled["thinking"] == {"type": "disabled"}

    qwen = make_provider(
        lambda _: httpx.Response(200, json={}), vendor_id="qwen",
        model_capabilities={"qwen3": {"reasoning_mode": "toggle", "thinking_parameter": "enable_thinking"}},
    )
    body = qwen._body({"model": "qwen3", "messages": [], "thinking_enabled": True}, stream=False)
    assert body["enable_thinking"] is True
    assert "thinking" not in body

    unknown = make_provider(lambda _: httpx.Response(200, json={}), vendor_id="glm")
    body = unknown._body({"messages": [], "thinking_enabled": True}, stream=False)
    assert "thinking" not in body and "enable_thinking" not in body and "reasoning" not in body


async def test_thinking_model_default_omits_overrides_and_explicit_on_uses_declared_default():
    deepseek = make_provider(lambda _: httpx.Response(200, json={}), vendor_id="deepseek",
        model_capabilities={"deepseek-flash": {"reasoning_mode": "toggle", "thinking_parameter": "reasoning_effort",
            "thinking_default": "high", "thinking_levels": ["low", "high", "max"]}})
    default = deepseek._body({"model": "deepseek-flash", "messages": [], "thinking_mode": "default",
                              "thinking_enabled": None, "thinking_level": "", "thinking_budget": None}, stream=False)
    assert "reasoning_effort" not in default
    enabled = deepseek._body({"model": "deepseek-flash", "messages": [], "thinking_mode": "on",
                              "thinking_enabled": True, "thinking_level": ""}, stream=False)
    assert enabled["reasoning_effort"] == "high"
    disabled = deepseek._body({"model": "deepseek-flash", "messages": [], "thinking_mode": "off",
                               "thinking_enabled": False}, stream=False)
    assert disabled["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_provider_model_catalog_cache_refresh_and_stale_fallback():
    service = make_service()
    provider = service.router.get("fake")
    calls = 0
    fail = False

    async def list_models():
        nonlocal calls
        calls += 1
        if fail:
            raise ProviderError("temporary catalog failure")
        return ["first", "second", "first"]

    provider.list_models = list_models
    first = await service.router.list_model_catalog("fake")
    cached = await service.router.list_model_catalog("fake")
    assert first == {"models": ["first", "second"], "status": "verified"}
    assert cached == {"models": ["first", "second"], "status": "cached"}
    assert calls == 1

    refreshed = await service.router.list_model_catalog("fake", refresh=True)
    assert refreshed["status"] == "verified"
    assert calls == 2
    fail = True
    stale = await service.router.list_model_catalog("fake", refresh=True)
    assert stale["status"] == "stale"
    assert stale["models"] == ["first", "second"]

    fail = False
    profile = service.router.profile("fake")
    service.router.add(profile, provider)
    invalidated = await service.router.list_model_catalog("fake")
    assert invalidated["status"] == "verified"
    assert calls == 4


async def test_study_context_disables_deepseek_thinking_in_the_actual_request():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] == 4096
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content=sse(delta("ok"), {"choices": [{"delta": {}, "finish_reason": "stop"}]}))

    provider = make_provider(handler, vendor_id="deepseek",
        model_capabilities={"deepseek-flash": {"reasoning_mode": "toggle",
            "thinking_parameter": "thinking.type", "max_output_tokens": 4096}})
    frames = [frame async for frame in provider.stream({"model": "deepseek-flash",
        "max_tokens": 4096, "messages": []}, {"thinking_enabled": False})]
    assert frames[-1] == {"type": "finish", "finish_reason": "stop"}


async def test_reasoning_sse_frames_are_separate_from_answer_frames():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "enabled"}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
            {"choices": [{"delta": {"reasoning_content": "推理"}, "finish_reason": None}]},
            delta("正文"),
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ))

    provider = make_provider(
        handler, vendor_id="deepseek",
        model_capabilities={"model-a": {"reasoning_mode": "toggle", "thinking_parameter": "thinking.type"}},
    )
    frames = [frame async for frame in provider.stream({"model": "model-a", "messages": [], "thinking_enabled": True}, {})]
    assert frames[:2] == [
        {"type": "reasoning_delta", "text": "推理"},
        {"type": "delta", "text": "正文"},
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


@pytest.mark.parametrize(
    ("failure", "expected_kind"),
    [
        ("http_503", KIND_UPSTREAM_ERROR),
        ("read_timeout", KIND_TIMEOUT),
    ],
)
async def test_stream_records_injected_upstream_failure_kind(failure: str, expected_kind: str):
    """故障注入验证 Provider 适配器将 HTTP 错误和超时归一为结构化帧。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "http_503":
            return httpx.Response(503, json={"error": {"message": "injected upstream failure"}})
        raise httpx.ReadTimeout("injected timeout", request=request)

    provider = make_provider(handler)
    frames = [frame async for frame in provider.stream({"messages": [{"role": "user", "content": "test"}]}, {})]
    assert frames[-1]["type"] == "error"
    assert frames[-1]["error"]["details"]["kind"] == expected_kind
    assert frames[-1]["error"]["details"]["retryable"] is True


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


def test_glm_and_ollama_requests_use_only_supported_openai_fields():
    glm = make_provider(lambda _: httpx.Response(200, json={}), api_mode="chat_completions")
    glm.profile.base_url = "https://open.bigmodel.cn/api/paas/v4"
    glm_body = glm._body({"messages": [{"role": "user", "content": "hi"}]}, stream=True)
    assert glm._url("/chat/completions") == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert "api_mode" not in glm_body
    assert glm_body["stream_options"] == {"include_usage": True}

    ollama = make_provider(lambda _: httpx.Response(200, json={}), api_key=None, protocol="local")
    ollama_body = ollama._body({"messages": [{"role": "user", "content": "hi"}]}, stream=True)
    assert "api_mode" not in ollama_body
    assert "stream_options" not in ollama_body


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
