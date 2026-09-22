"""Pi 开发者适配器：JSONL 帧、请求关联与事件投影（不依赖真实 Pi 进程）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from integrations.pi.event_adapter import adapt_event, is_developer_only_event
from integrations.pi.rpc_client import (
    PiRpcClient,
    PiRpcError,
    decode_frame,
    encode_frame,
    session_isolation_guard,
)


# ---- 帧编解码 ----

def test_encode_frame_is_single_lf_terminated_line():
    frame = encode_frame({"id": "1", "method": "ping"})
    assert frame.endswith(b"\n")
    assert frame.count(b"\n") == 1
    assert json.loads(frame.decode()) == {"id": "1", "method": "ping"}


def test_decode_frame_tolerates_crlf_and_blank_lines():
    assert decode_frame(b'{"a":1}\r\n') == {"a": 1}
    assert decode_frame("\n") is None
    assert decode_frame("   ") is None


def test_decode_frame_rejects_malformed_json():
    with pytest.raises(PiRpcError):
        decode_frame("{not json")


def test_decode_frame_rejects_non_object():
    with pytest.raises(PiRpcError):
        decode_frame("[1,2,3]")


def test_encode_decode_round_trip_with_unicode():
    payload = {"text": "苏格拉底式追问"}
    assert decode_frame(encode_frame(payload)) == payload


# ---- 请求关联 ----

class FakeWriter:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def write(self, data: bytes) -> None:
        self.frames.append(json.loads(data.decode()))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


def make_client(responses: list[dict]) -> tuple[PiRpcClient, FakeWriter]:
    stream = asyncio.StreamReader()
    for item in responses:
        stream.feed_data(encode_frame(item))
    stream.feed_eof()
    writer = FakeWriter()
    return PiRpcClient(stream, writer, request_timeout=2.0), writer


async def test_request_correlates_response_by_id():
    client, writer = make_client([{"id": "pi-1", "result": {"ok": True}}])
    result = await client.request("session.new", {"title": "t"})
    assert result["result"] == {"ok": True}
    assert writer.frames[0]["method"] == "session.new"
    await client.close()


async def test_request_raises_on_error_frame():
    client, _ = make_client([{"id": "pi-1", "error": {"message": "boom"}}])
    with pytest.raises(PiRpcError):
        await client.request("session.new")
    await client.close()


async def test_request_times_out_without_response():
    stream = asyncio.StreamReader()  # 永不产出
    client = PiRpcClient(stream, FakeWriter(), request_timeout=0.05)
    with pytest.raises(PiRpcError, match="超时"):
        await client.request("session.new")
    await client.close()


async def test_unmatched_frames_become_notifications():
    client, _ = make_client([{"event": "message.delta", "payload": {"delta": "hi"}}])
    client.start()
    frame = await asyncio.wait_for(client.notifications().__anext__(), timeout=2.0)
    assert frame["event"] == "message.delta"
    await client.close()


async def test_pending_requests_fail_when_process_exits():
    client, _ = make_client([])  # 立即 EOF
    with pytest.raises(PiRpcError, match="已关闭连接"):
        await client.request("session.new")
    await client.close()


# ---- 事件投影 ----

def test_adapt_event_maps_delta():
    event = adapt_event(
        {"event": "message.delta", "payload": {"delta": "你好"}}, session_id="s1", trace_id="t1"
    )
    assert event["type"] == "model.stream.delta"
    assert event["payload"]["text"] == "你好"
    assert event["source"] == "pi"


def test_adapt_event_returns_none_for_unknown():
    assert adapt_event({"event": "unknown.thing"}) is None


def test_adapted_event_is_marked_developer_only():
    event = adapt_event({"event": "tool.started", "payload": {"tool": "x"}})
    assert is_developer_only_event(event) is True
    assert is_developer_only_event({"source": "runtime"}) is False


def test_developer_mode_cannot_write_learner_memory():
    with pytest.raises(PiRpcError):
        session_isolation_guard(writes_memory=True)
    session_isolation_guard(writes_memory=False)