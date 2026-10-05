from __future__ import annotations

import pytest
import httpx

from runtime.web_search import (OLLAMA_SEARCH_SECRET_REF, WebSearchError, extract_native_sources,
                                native_search_capability, native_search_request, ollama_web_search)
from runtime.model_options import model_options


def test_documented_provider_native_search_support_is_model_specific():
    qwen = native_search_capability("qwen", "qwen-plus")
    assert qwen["supported"] is True
    assert qwen["always_search"] is True
    assert native_search_capability("qwen", "qwen3.8-omni-flash")["supported"] is False

    glm = native_search_capability("glm", "glm-4.6")
    assert glm["supported"] is True
    assert glm["kind"] == "zhipu_web_search_prime"

    bailian_glm = native_search_capability("qwen", "glm-5.2")
    assert bailian_glm["supported"] is True
    assert bailian_glm["api_mode"] == "responses"
    assert bailian_glm["kind"] == "bailian_responses_web_search"
    assert native_search_capability("qwen", "kimi-k3")["api_mode"] == "responses"
    ark = native_search_capability("volcengine", "doubao-seed-2-1-pro-260628")
    assert ark["supported"] is True
    assert ark["api_mode"] == "responses"

    ollama = native_search_capability("ollama", "llama3.2")
    assert ollama["supported"] is True
    assert ollama["requires_api_key"] is True
    assert OLLAMA_SEARCH_SECRET_REF == "web-search:ollama"

    for vendor, model in (("deepseek", "deepseek-chat"), ("kimi", "kimi-k2"),
                          ("openrouter", "openai/gpt-5"),
                          ("custom", "some-model")):
        assert native_search_capability(vendor, model)["supported"] is False

    assert native_search_capability("qwen", "glm-5.2", api_mode="chat_completions")["supported"] is False


def test_qwen_modes_map_to_provider_search_fields():
    capability = native_search_capability("qwen", "qwen-plus")
    assert native_search_request(capability, "auto")["body"] == {"enable_search": True}
    assert native_search_request(capability, "always")["body"] == {
        "enable_search": True, "search_options": {"forced_search": True},
    }


def test_responses_native_search_uses_documented_builtin_tool_and_auto_only():
    capability = native_search_capability("qwen", "glm-5.2")
    request = native_search_request(capability, "auto")
    assert request == {"api_mode": "responses", "body": {}, "tools": [{"type": "web_search"}],
                       "tool_choice": None}
    with pytest.raises(WebSearchError, match="强制执行"):
        native_search_request(capability, "always")


def test_thinking_controls_are_model_specific_and_cite_the_vendor_contract():
    glm = model_options("qwen", "glm-5.2")
    assert glm["api_mode"] == "responses"
    assert glm["thinking_parameter"] == "reasoning.effort"
    assert glm["thinking_levels"] == ["high", "max"]
    assert glm["official_docs"].endswith("qwen-api-via-openai-responses")

    kimi = model_options("qwen", "kimi-k3")
    assert kimi["thinking_levels"] == ["max"]
    assert "thinking_budget_parameter" not in kimi

    ark = model_options("volcengine", "doubao-seed-2-1-pro-260628")
    assert ark["api_mode"] == "responses"
    assert ark["reasoning_parameter"] == "reasoning.effort"


def test_glm_uses_only_provider_owned_search_tool_and_cannot_force():
    capability = native_search_capability("glm", "glm-4.6")
    request = native_search_request(capability, "auto")
    assert request["tools"] == [{"type": "mcp", "mcp": {
        "server_label": "mcp code", "transport_type": "streamable-http",
        "allowed_tools": ["webSearchPrime"],
    }}]
    assert request["tool_choice"] == "auto"
    with pytest.raises(WebSearchError, match="强制执行"):
        native_search_request(capability, "always")
    with pytest.raises(WebSearchError, match="原生联网"):
        native_search_request(native_search_capability("deepseek", "deepseek-chat"), "auto")


def test_only_provider_returned_http_sources_are_normalized():
    sources = extract_native_sources({"search_info": {"search_results": [
        {"title": "Official", "url": "https://example.com/", "content": "Summary"},
        {"title": "Unsafe", "url": "javascript:alert(1)", "content": "ignored"},
    ]}}, vendor="qwen")
    assert sources == [{"title": "Official", "url": "https://example.com/", "snippet": "Summary",
                        "published_at": "", "provider": "qwen"}]

    responses_sources = extract_native_sources({"response": {"output": [
        {"type": "web_search_call", "action": {"sources": [
            {"title": "Responses source", "url": "https://example.com/responses", "snippet": "Snippet"},
        ]}},
    ]}}, vendor="volcengine")
    assert responses_sources[0]["title"] == "Responses source"
    assert responses_sources[0]["provider"] == "volcengine"


async def test_ollama_web_search_uses_its_official_api_and_returns_real_sources():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"results": [{
            "title": "Ollama docs", "url": "https://docs.ollama.com/", "content": "Official result",
        }]})

    results = await ollama_web_search("Ollama search API", "ollama-test-key", transport=httpx.MockTransport(handler))
    assert seen["url"] == "https://ollama.com/api/web_search"
    assert seen["authorization"] == "Bearer ollama-test-key"
    assert seen["body"] == '{"query":"Ollama search API","max_results":5}'
    assert results[0]["url"] == "https://docs.ollama.com/"
    assert results[0]["snippet"] == "Official result"


async def test_ollama_web_search_maps_auth_failure_without_retry():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(WebSearchError) as captured:
        await ollama_web_search("query", "bad-key", transport=httpx.MockTransport(handler))
    assert captured.value.code == "search_authentication_failed"
    assert captured.value.retryable is False
