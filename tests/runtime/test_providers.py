"""Provider 契约测试（DESIGNv0.4 §12 最低测试范围）。

不联网、不需要密钥：通过注入 mock client 验证 OpenAI 兼容 Provider
把各家 SDK 差异收敛成统一契约的行为。重点覆盖两处最容易出错的协议适配：

1. 流式 tool_calls 按 index 分片下发时的拼装（真实调用很难碰到——
   终端 REPL 未注册工具，模型不会返回 tool_calls）；
2. 失败包装：SDK 异常必须变成结构化 ProviderError，而不是裸异常（§7.3）。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from config import settings
from runtime.core.errors import ProviderError
from runtime.core.message import Message
from runtime.providers.base import ModelRequest
from runtime.providers.factory import get_provider
from runtime.providers.fake import FakeProvider
from runtime.providers.openai_compat import OpenAICompatProvider

# ---------- mock SDK 对象 ----------
class _AsyncChunks:
    """模拟 openai SDK 的流式返回对象（可异步迭代）。

    列表中的 Exception 元素会在迭代到该位置时抛出，用于验证"读流中断"。
    """

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    async def _iter(self):
        for item in self._items:
            if isinstance(item, Exception):
                raise item
            yield item

    def __aiter__(self):
        return self._iter()


class _StubCompletions:
    """记录请求参数的 mock completions 接口。"""

    def __init__(
        self,
        *,
        response: Any = None,
        chunks: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._chunks = list(chunks or [])
        self._error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:  # 模拟建连失败（await create 阶段）
            raise self._error
        if kwargs.get("stream"):
            return _AsyncChunks(self._chunks)
        return self._response


def _client(completions: _StubCompletions) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _provider(completions: _StubCompletions, **overrides) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        api_key="sk-test-not-a-real-key",
        base_url="https://example.invalid/v1",
        model="deepseek-flash",
        name="stub",
        async_client=_client(completions),
        **overrides,
    )


# ---------- mock 报文构造 ----------
def _chunk(content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)]
    )


def _call_fragment(index: int, *, id=None, name=None, arguments=None):
    """流式 tool_calls 的单个分片（OpenAI 兼容协议按 index 增量下发）。"""
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


def _call(id: str, name: str, arguments: str):
    return SimpleNamespace(id=id, function=SimpleNamespace(name=name, arguments=arguments))


def _response(content="", tool_calls=None, finish_reason="stop", usage=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        model="deepseek-flash",
        usage=usage,
    )


# ---------- 能力声明 ----------
def test_capabilities_declare_tools_and_streaming():
    provider = _provider(_StubCompletions())

    capabilities = provider.capabilities()

    assert capabilities.name == "stub"
    assert capabilities.supports_tools is True
    assert capabilities.supports_streaming is True
    assert capabilities.models == ["deepseek-flash"]


# ---------- generate：非流式 ----------
async def test_generate_parses_content_tool_calls_and_usage():
    completions = _StubCompletions(
        response=_response(
            content="先看定义",
            tool_calls=[_call("call_1", "retrieve", '{"query": "递归", "k": 3}')],
            finish_reason="tool_calls",
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )
    )
    provider = _provider(completions)

    result = await provider.generate(ModelRequest(messages=[Message.user("什么是递归")]))

    assert result.content == "先看定义"
    assert result.finish_reason == "tool_calls"
    assert result.wants_tools is True
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "retrieve"
    # arguments 的 JSON 字符串要被解成 dict，上层 Tool 才能直接校验参数
    assert result.tool_calls[0].arguments == {"query": "递归", "k": 3}
    assert result.usage == {"prompt_tokens": 11, "completion_tokens": 7}
    assert completions.calls[0]["stream"] is False


async def test_generate_wraps_sdk_failure_as_provider_error():
    provider = _provider(_StubCompletions(error=RuntimeError("boom")))

    with pytest.raises(ProviderError) as excinfo:
        await provider.generate(ModelRequest(messages=[Message.user("在吗")]))

    assert excinfo.value.code == "provider_failed"
    assert "boom" in str(excinfo.value)


async def test_missing_api_key_fails_with_clear_error():
    provider = OpenAICompatProvider(
        api_key="", base_url="https://example.invalid/v1", model="deepseek-flash", name="deepseek"
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.generate(ModelRequest(messages=[Message.user("在吗")]))

    assert excinfo.value.code == "provider_failed"
    assert ".env" in str(excinfo.value)  # 报错要指向修复方式，不回显密钥


# ---------- stream：增量与拼装 ----------
async def test_stream_emits_deltas_and_finish_reason():
    completions = _StubCompletions(
        chunks=[
            _chunk(content="递归"),
            _chunk(content="是自调用"),
            _chunk(finish_reason="stop"),
        ]
    )
    provider = _provider(completions)

    chunks = [chunk async for chunk in provider.stream(ModelRequest(messages=[Message.user("q")]))]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["递归", "是自调用"]
    # 只有收尾分片带 finish_reason 与 tool_calls，中间分片保持空
    assert chunks[0].finish_reason == ""
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].tool_calls == []
    assert completions.calls[0]["stream"] is True


async def test_stream_assembles_tool_call_fragments_by_index():
    """按 index 分片下发的 tool_calls 必须拼回完整调用，且按 index 排序。"""
    completions = _StubCompletions(
        chunks=[
            _chunk(tool_calls=[_call_fragment(0, id="call_a", name="retriev", arguments='{"query"')]),
            _chunk(tool_calls=[_call_fragment(0, name="e", arguments=': "递归"}')]),
            _chunk(
                tool_calls=[
                    _call_fragment(1, id="call_b", name="save_mistake", arguments='{"item_id": 7}')
                ]
            ),
            _chunk(finish_reason="tool_calls"),
        ]
    )
    provider = _provider(completions)

    chunks = [chunk async for chunk in provider.stream(ModelRequest(messages=[Message.user("q")]))]

    calls = chunks[-1].tool_calls
    assert [call.id for call in calls] == ["call_a", "call_b"]
    # 名字与参数都被跨分片拼接：retriev + e，{"query" + : "递归"}
    assert [call.name for call in calls] == ["retrieve", "save_mistake"]
    assert calls[0].arguments == {"query": "递归"}
    assert calls[1].arguments == {"item_id": 7}
    assert chunks[-1].finish_reason == "tool_calls"


async def test_malformed_tool_arguments_are_passed_through():
    """非法 JSON 不在 Provider 层吞掉，原样交给 Tool 校验层报错。"""
    completions = _StubCompletions(
        chunks=[
            _chunk(tool_calls=[_call_fragment(0, id="call_x", name="retrieve", arguments="{not json")]),
            _chunk(finish_reason="tool_calls"),
        ]
    )
    provider = _provider(completions)

    chunks = [chunk async for chunk in provider.stream(ModelRequest(messages=[Message.user("q")]))]

    assert chunks[-1].tool_calls[0].arguments == {"_raw": "{not json"}


async def test_stream_wraps_read_failure_as_provider_error():
    """读流中途失败要变成结构化错误；已产出的增量不回收，由调用方决定。"""
    completions = _StubCompletions(
        chunks=[_chunk(content="先说一半"), RuntimeError("connection reset")]
    )
    provider = _provider(completions)

    received: list[str] = []
    with pytest.raises(ProviderError) as excinfo:
        async for chunk in provider.stream(ModelRequest(messages=[Message.user("q")])):
            received.append(chunk.delta)

    assert excinfo.value.code == "provider_failed"
    assert "connection reset" in str(excinfo.value)
    assert received == ["先说一半"]


# ---------- 请求参数下发 ----------
async def test_request_kwargs_carry_model_budget_and_tools():
    completions = _StubCompletions(response=_response(content="ok"))
    provider = _provider(completions, temperature=0.2, max_tokens=256)
    tools = [{"type": "function", "function": {"name": "retrieve"}}]

    await provider.generate(
        ModelRequest(
            messages=[Message.user("q")],
            tools=tools,
            model="deepseek-chat",
            temperature=0.9,
        )
    )

    kwargs = completions.calls[0]
    assert kwargs["model"] == "deepseek-chat"  # request.model 覆盖构造参数
    assert kwargs["temperature"] == 0.9  # request 优先于 provider 默认
    assert kwargs["max_tokens"] == 256  # 未指定则用 provider 默认
    assert kwargs["messages"][0]["role"] == "user"
    assert kwargs["tools"] == tools
    assert kwargs["tool_choice"] == "auto"


async def test_request_kwargs_fall_back_to_config_defaults():
    completions = _StubCompletions(response=_response(content="ok"))
    provider = _provider(completions)

    await provider.generate(ModelRequest(messages=[Message.user("q")]))

    kwargs = completions.calls[0]
    assert kwargs["model"] == "deepseek-flash"
    assert kwargs["max_tokens"] == settings.llm_max_tokens
    # 没有工具时不下发 tools / tool_choice，避免模型被诱导发起工具调用
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


# ---------- 工厂 ----------
def test_factory_builds_every_supported_provider():
    for name in ("deepseek", "openai", "qwen"):
        provider = get_provider(name)
        assert provider.name == name
        assert provider.capabilities().supports_streaming is True
    assert isinstance(get_provider("fake"), FakeProvider)


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError) as excinfo:
        get_provider("bogus")

    assert "bogus" in str(excinfo.value)


async def test_factory_injects_client_override_without_touching_network():
    """overrides 必须透传到具体 Provider，测试才能注入 mock client。"""
    completions = _StubCompletions(response=_response(content="来自 stub"))
    provider = get_provider("deepseek", async_client=_client(completions))

    result = await provider.generate(ModelRequest(messages=[Message.user("q")]))

    assert result.content == "来自 stub"
    assert provider.capabilities().models == [settings.deepseek_model]