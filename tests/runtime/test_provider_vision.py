"""多模态能力探测 + Responses 形状转换。

这一层只做两件事，测试也只看这两件：

1. **``declared_vision`` 只复述 Provider 自己说的话。** ollama 的 ``/api/show.capabilities``
   与 OpenRouter 的 ``architecture.input_modalities`` 是仅有的两处可信来源；**读不到就是
   ``None``**。把未知当否，会在界面上悄悄拦掉一批本来能用的多模态模型，而用户看不到任何理由。
2. **``responses_input_item`` 只换形状，不换数据。** Chat Completions 的 ``text`` /
   ``image_url`` 到这里要变成 Responses 的 ``input_text`` / ``input_image``，且
   ``image_url`` 必须从对象收敛成字符串；除此之外——普通字符串消息、认不出的 part——
   一律原样带走，不静默丢掉任何一段内容。

``describe_model`` 是这两件事的调用方，所以顺带钉住它没有被改坏：它在读图能力的同一次
请求里解析推理模式，两者共用响应体，谁都不该把谁挤掉。
"""

from __future__ import annotations

import httpx
import pytest

# 先落地 runtime.core：core 与 providers 两个包互相 import，谁先被拉起谁负责把对方带出来。
# 直接 import runtime.providers.openai_compatible 会撞上 partially initialized 的 base。
import runtime.core.errors  # noqa: F401

from runtime.model_options import CHECKED_ON
from runtime.providers.openai_compatible import (
    OpenAICompatibleProvider,
    declared_vision,
    responses_input_item,
)
from runtime.providers.profiles import ProviderProfile
from runtime.providers.secrets import InMemorySecretStore


# --------------------------------------------------------------------------- 夹具


def make_provider(handler, *, vendor_id: str = "openrouter",
                  base_url: str = "https://example.invalid/v1", **kwargs):
    """把 Provider 接到一个假 HTTP 层上，并把每次请求记下来。"""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    profile = ProviderProfile(
        profile_id="p1", vendor_id=vendor_id, base_url=base_url,
        api_key_ref="provider:p1", default_model="some-model", **kwargs)
    provider = OpenAICompatibleProvider(
        profile, InMemorySecretStore({"provider:p1": "sk-test"}),
        transport=httpx.MockTransport(respond))
    return provider, seen


def json_handler(payload, *, status: int = 200):
    return lambda request: httpx.Response(status, json=payload)


# --------------------------------------------------------------------------- declared_vision


@pytest.mark.parametrize("payload,expected", [
    ({"capabilities": ["completion", "tools", "vision"]}, True),
    ({"capabilities": ["vision"]}, True),
    ({"capabilities": [" Vision "]}, True),          # 大小写与空白不该改变结论
    ({"capabilities": ["completion", "tools"]}, False),
    ({"capabilities": []}, False),
    ({"capabilities": "vision"}, None),              # 形状不对就是读不到，不是否
    ({"details": {"families": ["clip"]}}, None),
    ({}, None),
    ({"capabilities": None}, None),
])
def test_ollama_capabilities_are_read_verbatim(payload, expected):
    assert declared_vision(payload) is expected


@pytest.mark.parametrize("payload,expected", [
    ({"architecture": {"input_modalities": ["text", "image"]}}, True),
    ({"architecture": {"input_modalities": ["IMAGE"]}}, True),
    ({"architecture": {"input_modalities": ["text"]}}, False),
    ({"architecture": {"input_modalities": []}}, False),
    ({"architecture": {}}, None),
    ({"architecture": {"input_modalities": "image"}}, None),
    ({"architecture": None}, None),
    ({"id": "some/model"}, None),
])
def test_openrouter_modalities_are_read_verbatim(payload, expected):
    assert declared_vision(payload) is expected


def test_the_two_sources_are_never_merged_into_a_guess():
    """两个键同时出现时以 capabilities 为准；两处都读不到就还是 None。

    合并成一个「任一命中即可」的判断，等于把两个厂商的口径揉成一个第三方的口径——
    那样一旦某个上游多返回一个无关字段，结论就会凭空空翻。
    """
    assert declared_vision({
        "capabilities": ["completion"],
        "architecture": {"input_modalities": ["text", "image"]},
    }) is False
    assert declared_vision({
        "capabilities": ["vision"],
        "architecture": {"input_modalities": ["text"]},
    }) is True


# --------------------------------------------------------------------------- responses_input_item


def test_text_and_image_parts_are_translated_to_the_responses_shape():
    item = {"role": "user", "content": [
        {"type": "text", "text": "这是什么图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}

    assert responses_input_item(item) == {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "这是什么图"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        ],
    }


def test_the_image_url_is_unwrapped_into_a_string():
    """Responses 要的是字符串；留着对象会被上游按「无效图片」整条拒掉。"""
    translated = responses_input_item(
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://a/b.png"}}]})
    url = translated["content"][0]["image_url"]
    assert isinstance(url, str) and url == "https://a/b.png"


@pytest.mark.parametrize("part,expected", [
    ({"type": "image_url", "image_url": "https://a/b.png"}, "https://a/b.png"),
    ({"type": "image_url", "image_url": {"url": "https://a/b.png", "detail": "high"}}, "https://a/b.png"),
    ({"type": "image_url"}, ""),
    ({"type": "image_url", "image_url": None}, ""),
])
def test_every_image_url_shape_lands_on_a_string(part, expected):
    translated = responses_input_item({"role": "user", "content": [part]})
    assert translated["content"][0]["image_url"] == expected


def test_unknown_parts_ride_along_untouched():
    """认不出的 part 原样带走：丢掉它等于悄悄删了用户消息的一部分。"""
    audio = {"type": "input_audio", "input_audio": {"data": "AAA", "format": "wav"}}
    assert responses_input_item({"role": "user", "content": [audio]})["content"] == [audio]
    assert responses_input_item(
        {"role": "user", "content": [None, "文字", {"type": "text", "text": "hi"}]}
    )["content"] == [{"type": "input_text", "text": "hi"}]


@pytest.mark.parametrize("item", [
    {"role": "user", "content": "纯文字提问"},        # 没有图的消息仍是字符串
    {"role": "assistant", "content": [{"type": "text", "text": "答"}]},
])
def test_items_are_never_mutated_in_place(item):
    before = repr(item)
    translated = responses_input_item(item)
    assert repr(item) == before, "翻译不能改到调用方手上那份"
    assert translated is not item or translated == item


@pytest.mark.parametrize("item", [
    {"role": "user", "content": "纯文字提问"},
    {"role": "assistant", "content": []},
    {"role": "system"},
    "裸字符串不是消息",
    {"role": "user", "content": {"type": "text", "text": "对象形状的 content"}},
])
def test_untouched_shapes_come_back_identical(item):
    """反向控制：没有 parts 可翻的时候必须原样返回。"""
    assert responses_input_item(item) is item


def test_plain_messages_stay_plain_next_to_translated_ones():
    """一条消息带图，不该把整轮对话都改成 parts。"""
    items = [
        {"role": "system", "content": "你是助教"},
        {"role": "user", "content": "第一问"},
        {"role": "user", "content": [{"type": "text", "text": "第二问"},
                                     {"type": "image_url", "image_url": {"url": "data:x"}}]},
    ]
    translated = [responses_input_item(item) for item in items]

    assert isinstance(translated[0]["content"], str)
    assert isinstance(translated[1]["content"], str)
    assert [part["type"] for part in translated[2]["content"]] == ["input_text", "input_image"]


# --------------------------------------------------------------------------- describe_model


@pytest.mark.asyncio
async def test_openrouter_catalog_reports_vision_and_reasoning_together():
    """同一份 /models 响应既给推理模式也给模态：两个都要活下来。"""
    provider, seen = make_provider(json_handler({"data": [
        {"id": "other/model"},
        {"id": "some-model",
         "architecture": {"input_modalities": ["text", "image"]},
         "reasoning": {"supported_efforts": ["high", "low"], "default_effort": "low"}},
    ]}))

    described = await provider.describe_model("some-model")

    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://example.invalid/v1/models"
    assert described["vision"] is True
    assert described["reasoning_mode"] == "toggle"
    assert described["reasoning"] is True
    assert described["thinking_levels"] == ["high", "low"]
    assert described["thinking_default"] == "low"
    assert described["thinking_parameter"] == "reasoning.enabled"
    assert described["reasoning_parameter"] == "reasoning.effort"
    assert described["options_source"] == "provider_model_metadata"
    assert described["checked_on"] == CHECKED_ON


@pytest.mark.asyncio
async def test_openrouter_model_missing_from_the_catalog_stays_unknown():
    """目录里没有这个模型：读不到就是 None，不能兜底成「不支持图片」。"""
    provider, _ = make_provider(json_handler({"data": [{"id": "other/model"}]}))

    described = await provider.describe_model("some-model")

    assert described["vision"] is None
    assert described["reasoning_mode"] == "unknown"


@pytest.mark.asyncio
async def test_openrouter_reasoning_is_mandatory_when_the_catalog_says_so():
    provider, _ = make_provider(json_handler({"data": [
        {"id": "some-model", "reasoning": {"mandatory": True, "supported_efforts": ["max"]}}]}))

    described = await provider.describe_model("some-model")

    assert described["reasoning_mode"] == "always"
    assert described["thinking_parameter"] == ""      # 强制推理没有开关可拨
    assert described["reasoning_parameter"] == "reasoning.effort"


@pytest.mark.asyncio
async def test_ollama_show_answers_vision_and_thinking_from_one_call():
    """ollama 把「吃不吃图」和「怎么想」都放在 /api/show 里，一次请求出两个结论。"""
    provider, seen = make_provider(
        json_handler({"capabilities": ["completion", "thinking", "vision"],
                      "thinking": {"values": ["low", "high"], "default": "low"}}),
        vendor_id="ollama", base_url="http://localhost:11434/v1")

    described = await provider.describe_model("qwen3:8b")

    assert str(seen[0].url) == "http://localhost:11434/api/show", "本地接口不带 /v1"
    assert seen[0].method == "POST"
    assert described["vision"] is True
    assert described["reasoning_mode"] == "toggle"
    assert described["thinking_levels"] == ["low", "high"]
    assert described["thinking_parameter"] == "ollama.think"
    assert described["options_source"] == "ollama_api_show"
    assert described["checked_on"] == CHECKED_ON


@pytest.mark.asyncio
async def test_ollama_text_only_model_is_reported_as_text_only():
    provider, _ = make_provider(
        json_handler({"capabilities": ["completion", "tools"]}),
        vendor_id="ollama", base_url="http://localhost:11434/v1")

    described = await provider.describe_model("qwen3:8b")

    assert described["vision"] is False
    assert described["reasoning_mode"] == "unknown"


@pytest.mark.asyncio
async def test_ollama_without_a_capability_list_discloses_unknown():
    """老版本 ollama 不返回 capabilities：推理照样解析，图片能力如实说不知道。"""
    provider, _ = make_provider(
        json_handler({"thinking": {"values": [True]}}),
        vendor_id="ollama", base_url="http://localhost:11434/v1")

    described = await provider.describe_model("qwen3:8b")

    assert described["vision"] is None
    assert described["reasoning_mode"] == "always"


@pytest.mark.asyncio
async def test_ollama_disabling_thinking_does_not_disturb_the_vision_reading():
    provider, _ = make_provider(
        json_handler({"capabilities": ["vision"], "thinking": {"values": [False]}}),
        vendor_id="ollama", base_url="http://localhost:11434/v1")

    described = await provider.describe_model("qwen3:8b")

    assert described["reasoning_mode"] == "unsupported"
    assert described["reasoning"] is False
    assert described["vision"] is True


@pytest.mark.asyncio
async def test_other_vendors_are_not_probed_at_all():
    """没有官方目录的厂商不做探测：宁可空着，也不发一次猜出来的请求。"""
    provider, seen = make_provider(json_handler({"data": []}), vendor_id="deepseek")

    assert await provider.describe_model("deepseek-v4") == {}
    assert seen == []


@pytest.mark.asyncio
async def test_the_responses_body_is_translated_end_to_end():
    """整条链路：带图请求经 _responses_body 之后，input 里是 Responses 形状的 parts。"""
    provider, _ = make_provider(json_handler({"data": []}), vendor_id="qwen",
                                api_mode="responses")

    body = provider._responses_body({"model": "some-model", "messages": [
        {"role": "system", "content": "你是助教"},
        {"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
    ]}, stream=False)

    assert "messages" not in body
    assert body["input"][0]["content"] == "你是助教"
    assert body["input"][1]["content"] == [
        {"type": "input_text", "text": "看图"},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]
    assert body["max_output_tokens"] and "max_tokens" not in body
