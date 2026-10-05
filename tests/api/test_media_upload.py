"""带图提问：图片上传接口 + message.send 的图片通道。

图片这条路只有两条性质必须成立，测试就盯这两条：

1. **字节落在沙箱白名单内的数据目录里。** 浏览器给不出真实路径，Runtime 也不该去翻用户的
   磁盘，所以网关接字节落盘、会话里只留一条引用，读盘发生在组模型请求的那一刻。
2. **模型拿到的是真字节，不是一个路径。** 断言一直走到 ``data:`` URL 解出来的字节与上传的
   字节逐位相同——只断言「有个 image_url 字段」等于什么都没测。

另一半是**拒绝路径**：类型不对、体量超限、张数超限、模型明确声明不支持图片。每一条都
同时断言「会话一个字都没动、模型一次都没被叫到」，否则「拒绝得干净」与「先写了一半再报错」
在测试里长得一模一样。

聊天会话与学习会话都收图片（第十五轮起学习会话不再被 409 拦掉），但两条路的**口径**
不同，各自有测试看着：聊天会话把图存在消息上交给模型；学习会话同样交给模型，但图片
既不能进资料库、也不能以字节形态进教学图状态。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import time
import zlib

import pytest
from fastapi.testclient import TestClient

import api.sessions as sessions_module
from api.app import create_app
from runtime.core.message import Message
from runtime.core.session import Session
from runtime.testing import make_service, make_settings

PNG_PREFIX = "data:image/png;base64,"


# --------------------------------------------------------------------------- 夹具


def _png(width: int = 1, height: int = 1) -> bytes:
    """真造一张 PNG：魔数、IHDR、IDAT、IEND 都是真的。

    不用 base64 常量是为了让「读回来的字节和写进去的一模一样」这条断言有意义——
    截断或改写过的东西解出来就不等了。
    """
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def _service(tmp_path, *, script=None, **overrides):
    settings = make_settings(library_dir=str(tmp_path / "library"), **overrides)
    service = make_service(script=script, settings=settings)
    # 图片目录跟着数据根走，白名单也要覆盖到它，否则组请求时 check_path 会把图拦下
    service.sandbox.allowed_dirs.append(tmp_path.resolve())
    return service


def _chat_session(*turns: tuple[str, str], title: str = "看图") -> Session:
    session = Session(title=title, metadata={
        "session_mode": "chat", "provider_profile": "fake", "model": "fake-model"})
    for role, content in turns:
        session.append(Message(role=role, content=content, metadata={"turn_mode": "chat"}))
    return session


def _capture() -> tuple[list[dict], object]:
    """把 Provider 收到的请求原样留下来，再回一句固定答案。"""
    sent: list[dict] = []

    def step(request: dict) -> dict:
        sent.append(request)
        return {"content": "图我看到了"}

    return sent, step


def _upload(client: TestClient, body: bytes, *, filename: str = "shot.png",
            content_type: str = "image/png"):
    return client.post("/media/images", params={"filename": filename}, content=body,
                       headers={"content-type": content_type})


def _send(client: TestClient, session_id: str, command_id: str, **payload):
    return client.post("/commands", json={
        "command_id": command_id, "client_id": "test", "surface": "web", "learner_id": "local",
        "session_id": session_id, "type": "message.send", "payload": payload})


def _media_files(tmp_path):
    return sorted((tmp_path / "media").glob("img_*"))


def _wait_for_turn(service, session_id: str, *, timeout: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = service.history(session_id)
        if any(event["type"] == "agent.turn.completed" for event in events):
            return events
        time.sleep(0.01)
    raise AssertionError("回合未在超时前结束")


def _stored_messages(service, session_id: str) -> list[Message]:
    return list(service.get_session(session_id).messages)


# --------------------------------------------------------------------------- 上传


def test_upload_stores_the_image_and_reads_the_same_bytes_back(tmp_path):
    service = _service(tmp_path)
    body = _png(3, 2)
    with TestClient(create_app(service=service)) as client:
        response = _upload(client, body, filename="截图.png")
        assert response.status_code == 200
        payload = response.json()
        fetched = client.get("/media/images/%s" % payload["media_id"])

    assert payload["media_id"].startswith("img_")
    assert payload["name"] == "截图.png"
    assert payload["mime"] == "image/png"
    assert payload["bytes"] == len(body)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.content == body, "读回来的字节与上传的字节不一致"
    # 落盘位置在数据目录里，且不在资料库里
    assert [path.suffix for path in _media_files(tmp_path)] == [".json", ".png"]
    assert not (tmp_path / "library").exists() or not list((tmp_path / "library").glob("img_*"))


def test_upload_trusts_the_bytes_not_the_declared_type(tmp_path):
    """请求头是客户端说了算的：声明成 image/png 的文本必须被魔数拦下。"""
    with TestClient(create_app(service=_service(tmp_path))) as client:
        response = _upload(client, "这不是图片，只是换了 content-type 的一段文字。".encode("utf-8"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_image_type"
    assert _media_files(tmp_path) == [], "被拒绝的上传不该在磁盘上留下任何东西"


@pytest.mark.parametrize("name,body", [
    ("p.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 24),
    ("p.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 24),
    ("p.jpeg", b"\xff\xd8\xff\xdb" + b"\x00" * 24),
    ("p.gif", b"GIF89a" + b"\x00" * 24),
    ("p.webp", b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 24),
])
def test_upload_accepts_the_four_declared_formats(tmp_path, name, body):
    """四种格式的判据是魔数；WebP 的魔数在第 8–11 字节，最容易被写漏。"""
    with TestClient(create_app(service=_service(tmp_path))) as client:
        response = _upload(client, body, filename=name)

    assert response.status_code == 200, response.text
    assert response.json()["mime"] in {"image/png", "image/jpeg", "image/gif", "image/webp"}


def test_upload_rejects_empty_body(tmp_path):
    with TestClient(create_app(service=_service(tmp_path))) as client:
        response = _upload(client, b"")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "image_empty"
    assert _media_files(tmp_path) == []


def test_upload_requires_a_filename(tmp_path):
    with TestClient(create_app(service=_service(tmp_path))) as client:
        response = client.post("/media/images", content=_png())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "image_name_required"


def test_upload_rejects_oversized_bodies(tmp_path):
    service = _service(tmp_path, media_image_max_bytes=64)
    body = _png() + b"\x00" * 256
    with TestClient(create_app(service=service)) as client:
        response = _upload(client, body)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "image_too_large"
    assert _media_files(tmp_path) == []


def test_upload_rejects_oversized_chunked_bodies(tmp_path):
    """分块上传没有 content-length，第二道闸门看的是读进来的真实长度。"""
    service = _service(tmp_path, media_image_max_bytes=64)
    body = _png() + b"\x00" * 256
    with TestClient(create_app(service=service)) as client:
        response = client.post("/media/images", params={"filename": "big.png"},
                               content=iter([body[:32], body[32:]]))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "image_too_large"
    assert _media_files(tmp_path) == []


def test_upload_strips_path_components_from_the_filename(tmp_path):
    """文件名只用来显示与回放，两种分隔符都要剥——POSIX 上也得拦住 Windows 风格的名字。"""
    body = _png()
    with TestClient(create_app(service=_service(tmp_path))) as client:
        for supplied, expected in (("../../evil.png", "evil.png"),
                                   ("..\\..\\evil.png", "evil.png"),
                                   ("/etc/passwd.png", "passwd.png")):
            response = _upload(client, body, filename=supplied)
            assert response.status_code == 200, response.text
            assert response.json()["name"] == expected

    assert _media_files(tmp_path) and len(_media_files(tmp_path)) == 6
    assert not (tmp_path.parent / "evil.png").exists()
    assert not (tmp_path / "evil.png").exists()


def test_upload_audits_without_leaking_the_filename_or_the_bytes(tmp_path):
    service = _service(tmp_path)
    body = _png()
    with TestClient(create_app(service=service)) as client:
        payload = _upload(client, body, filename="患者的化验单.png").json()

    events = [event for event in service.history("") if event["type"] == "media.image.stored"]
    assert events, "没有留下 media.image.stored 审计事件"
    recorded = events[-1]["payload"]
    assert set(recorded) == {"media_id", "mime", "bytes", "sha256"}
    assert recorded["media_id"] == payload["media_id"]
    assert recorded["bytes"] == len(body)
    # 文件名的隐私口径与 document.converted 一致：只留摘要与体量
    assert "化验单" not in json.dumps(recorded, ensure_ascii=False)
    assert base64.b64encode(body).decode("ascii") not in json.dumps(recorded)


def test_media_directory_is_inside_the_sandbox_allowlist(tmp_path):
    """图片目录必须在白名单里，否则组模型请求那一步会被 check_path 拒掉。"""
    service = _service(tmp_path)
    assert service.settings.resolved_media_dir == tmp_path / "media"
    assert service.sandbox.is_path_allowed(service.settings.resolved_media_dir) is True


# --------------------------------------------------------------------------- 带图提问


def test_message_send_hands_the_real_bytes_to_the_model(tmp_path):
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    body = _png(2, 2)
    session = _chat_session()
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        media_id = _upload(client, body, filename="实验装置.png").json()["media_id"]
        assert _send(client, session.session_id, "img-send", content="这是什么图",
                     images=[{"media_id": media_id}]).status_code == 200
        _wait_for_turn(service, session.session_id)

    request = sent[-1]
    messages = request["messages"]
    user = messages[-1]
    assert user["role"] == "user"
    parts = user["content"]
    assert isinstance(parts, list), "带图的那条消息必须是 content parts，不能是字符串"
    assert parts[0] == {"type": "text", "text": "这是什么图"}
    assert parts[1]["type"] == "image_url"
    url = parts[1]["image_url"]["url"]
    assert url.startswith(PNG_PREFIX)
    assert base64.b64decode(url[len(PNG_PREFIX):]) == body, "模型看到的字节与上传的不是同一份"
    # 其余消息照旧是普通字符串，没有被一起包成 parts
    assert all(isinstance(item["content"], str) for item in messages[:-1])
    # 模型侧看不到本机路径，也看不到文件名
    assert str(tmp_path) not in json.dumps(request)
    assert "实验装置.png" not in url


def test_the_session_keeps_only_a_reference_not_the_bytes(tmp_path):
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    body = _png()
    session = _chat_session()
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        media_id = _upload(client, body).json()["media_id"]
        _send(client, session.session_id, "img-ref", content="看图", images=[{"media_id": media_id}])
        _wait_for_turn(service, session.session_id)
        stored = _stored_messages(service, session.session_id)

    assert [message.role for message in stored] == ["user", "assistant"]
    assert stored[0].content == "看图", "正文该留在 content 里，不能因为带图就搬走"
    assert stored[0].metadata["images"] == [{
        "media_id": media_id, "name": "shot.png", "mime": "image/png",
        "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}]
    # 会话里只留定位信息：没有本机路径，也没有字节
    serialized = json.dumps(stored[0].metadata["images"], ensure_ascii=False)
    assert "path" not in serialized and str(tmp_path) not in serialized
    assert base64.b64encode(body).decode("ascii") not in serialized
    assert base64.b64encode(body).decode("ascii") not in json.dumps(
        [message.to_dict() for message in stored], ensure_ascii=False)


def test_a_message_without_images_keeps_plain_text_content(tmp_path):
    """反向控制：加图片通道不能把没有图的消息也一起改形状。"""
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    session = _chat_session()
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        assert _send(client, session.session_id, "plain", content="纯文字提问").status_code == 200
        _wait_for_turn(service, session.session_id)

    assert sent[-1]["messages"][-1]["content"] == "纯文字提问"


def test_duplicate_media_ids_are_collapsed_to_one_image(tmp_path):
    """同一张图带两遍没有意义，而且会让模型对同样的字节看两次。"""
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    session = _chat_session()
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        media_id = _upload(client, _png()).json()["media_id"]
        _send(client, session.session_id, "dup", content="两张一样的",
              images=[{"media_id": media_id}, media_id])
        _wait_for_turn(service, session.session_id)

    parts = sent[-1]["messages"][-1]["content"]
    assert [part["type"] for part in parts] == ["text", "image_url"]


def test_study_session_accepts_images_and_keeps_them_out_of_the_library(tmp_path, monkeypatch):
    """学习会话也收图片，而且只收引用：字节不进图状态，图片不进资料库。

    这条原来钉的是 409（「学习会话要保留完整证据链，不能带图片」）。第十五轮把那条闸门
    去掉了——讲解 / 纠错 / 追问这几轮本来就在调模型，一律拦住只会让一张能用的图变成
    一句说不清为什么的红字。但放开的是**通道**，不是口径，所以这里同时钉三件事：
    命令被接受、交给教学图的是一条**不含字节**的引用、资料库一张图都没进。
    """
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    handled: list[dict] = []

    async def fake_teaching_turn(port, state):
        handled.append(dict(state))
        result = {"response_text": "图我看过了，说说你的思路。", "action": "ask"}
        return result

    monkeypatch.setattr(sessions_module, "run_teaching_turn", fake_teaching_turn)
    study = Session(title="教学", metadata={"session_mode": "study"})
    service.save_session(study)
    with TestClient(create_app(service=service)) as client:
        media_id = _upload(client, _png()).json()["media_id"]
        response = _send(client, study.session_id, "study-img", content="看图",
                         images=[{"media_id": media_id}])
        assert response.status_code == 200
        _wait_for_turn(service, study.session_id)

    assert handled, "学习会话的回合根本没进教学图"
    refs = handled[0]["images"]
    assert [ref["media_id"] for ref in refs] == [media_id]
    # 图状态里只有定位信息：没有字节、没有 base64、没有本机路径（§6.4 不放大文本）
    assert set(refs[0]) == {"media_id", "name", "mime", "bytes", "sha256"}
    stored = _stored_messages(service, study.session_id)
    assert [message.role for message in stored] == ["user", "assistant"]
    assert stored[-1].metadata["turn_mode"] == "study"
    assert [item["media_id"] for item in stored[0].metadata["images"]] == [media_id]
    # 图片只走会话与图状态，资料库一条都没进——学习会话的图片不是「资料」
    assert service.library.list_resources() == []


def test_send_rejects_unknown_media_id_before_touching_the_session(tmp_path):
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    session = _chat_session()
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        for index, supplied in enumerate(("img_" + "0" * 32, "../../etc/passwd", "not-an-image")):
            response = _send(client, session.session_id, "ghost-%d" % index,
                             content="看图", images=[{"media_id": supplied}])
            assert response.status_code == 422, supplied
            assert response.json()["error"]["code"] == "image_not_found"

    assert _stored_messages(service, session.session_id) == []
    assert sent == []


def test_send_rejects_malformed_image_payloads(tmp_path):
    service = _service(tmp_path)
    session = _chat_session()
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        not_a_list = _send(client, session.session_id, "img-shape", content="看图",
                           images="img_abc")
        no_media_id = _send(client, session.session_id, "img-noid", content="看图",
                            images=[{"name": "shot.png"}])

    assert not_a_list.status_code == 422
    assert not_a_list.json()["error"]["code"] == "invalid_image_payload"
    assert no_media_id.status_code == 422
    assert no_media_id.json()["error"]["code"] == "invalid_image_payload"
    assert _stored_messages(service, session.session_id) == []


def test_send_rejects_more_images_than_the_limit(tmp_path):
    service = _service(tmp_path, media_image_max_per_message=1)
    session = _chat_session()
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        first = _upload(client, _png()).json()["media_id"]
        second = _upload(client, _png(2, 2)).json()["media_id"]
        response = _send(client, session.session_id, "img-many", content="两张",
                         images=[{"media_id": first}, {"media_id": second}])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "too_many_images"
    assert _stored_messages(service, session.session_id) == []


@pytest.mark.parametrize("declared,blocked", [
    ({}, False),
    ({"vision": True}, False),
    ({"vision": False}, True),
])
def test_only_an_explicit_vision_false_blocks_the_send(tmp_path, declared, blocked):
    """三态门控：``false`` 拒绝，``true`` 与**未知**都放行。

    把「本机没探测过」当成「模型不支持」会拦掉一批本来能用的多模态模型，
    所以这里必须把未知态一起钉住，而不是只测拒绝。
    """
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    service.router.profile("fake").model_capabilities["fake-model"] = dict(declared)
    session = _chat_session()
    service.save_session(session)
    with TestClient(create_app(service=service)) as client:
        media_id = _upload(client, _png()).json()["media_id"]
        response = _send(client, session.session_id, "img-vision", content="看图",
                         images=[{"media_id": media_id}])
        if blocked:
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "model_without_vision"
            assert sent == []
        else:
            assert response.status_code == 200
            _wait_for_turn(service, session.session_id)
            assert sent and sent[-1]["messages"][-1]["content"][1]["type"] == "image_url"

    stored = _stored_messages(service, session.session_id)
    if blocked:
        assert stored == []
    else:
        assert [message.role for message in stored] == ["user", "assistant"]


def test_study_session_is_blocked_only_when_the_model_declares_no_vision(tmp_path, monkeypatch):
    """学习会话的图片同样受三态门控：``false`` 拦下。

    study 这条路原先走不到这里——图片在更前面就被 409 拒了。闸门去掉之后这一层才真正
    管到学习会话，所以必须另有测试看着它，而且拦下时要真的没进教学图。
    """
    service = _service(tmp_path, script=[_capture()[1]])
    service.router.profile("fake").model_capabilities["fake-model"] = {"vision": False}
    handled: list[dict] = []

    async def fake_teaching_turn(port, state):
        handled.append(dict(state))
        return {"response_text": "看到了", "action": "teach"}

    monkeypatch.setattr(sessions_module, "run_teaching_turn", fake_teaching_turn)
    study = Session(title="教学", metadata={"session_mode": "study",
                                            "provider_profile": "fake", "model": "fake-model"})
    service.save_session(study)
    with TestClient(create_app(service=service)) as client:
        media_id = _upload(client, _png()).json()["media_id"]
        response = _send(client, study.session_id, "study-vision", content="看图",
                         images=[{"media_id": media_id}])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "model_without_vision"
    assert handled == [], "被拒绝的回合不该进教学图"
    stored = service.get_session(study.session_id)
    assert stored.messages == []
    assert not stored.metadata.get("active_turn")


def test_the_runtime_inlines_this_turns_images_into_the_last_user_message(tmp_path):
    """带图的请求只在 RuntimeService.generate 这一个出口被改写，字节与上传的逐位相同。

    讲解与纠错（``generate_grounded``）、追问（socratic skill）各自组请求，但都从这里
    出去，所以「把图交给模型」只需要在这一处成立一次。这一条同时钉住注入的 image_reader：
    读盘在网关，Runtime 只认字节，这也是它不 import api 的原因。
    """
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    body = _png(3, 2)
    with TestClient(create_app(service=service)) as client:
        media_id = _upload(client, body).json()["media_id"]

    request = {"role": "tutor.default", "provider_profile": "fake", "model": "fake-model",
               "messages": [{"role": "system", "content": "你是伴学老师"},
                            {"role": "user", "content": "这张图里画的是什么？"}],
               "temperature": 0.0, "tools": []}
    ctx = {"session_id": "s-img", "trace_id": "trc-img", "images": [{"media_id": media_id}]}

    async def drive():
        return [frame async for frame in service.generate(request, ctx)]

    asyncio.run(drive())

    messages = sent[-1]["messages"]
    parts = messages[-1]["content"]
    assert [part["type"] for part in parts] == ["text", "image_url"]
    assert parts[0]["text"] == "这张图里画的是什么？"
    assert parts[1]["image_url"]["url"] == PNG_PREFIX + base64.b64encode(body).decode("ascii")
    assert messages[0]["content"] == "你是伴学老师", "没挂图的那几条不能被顺手改了形状"


def test_a_runtime_request_without_image_refs_keeps_plain_string_content(tmp_path):
    """反向控制：图片通道不能把没有图的请求也一起改形状（学习侧同一条纪律）。"""
    sent, step = _capture()
    service = _service(tmp_path, script=[step])
    request = {"role": "tutor.default", "provider_profile": "fake", "model": "fake-model",
               "messages": [{"role": "user", "content": "纯文字提问"}],
               "temperature": 0.0, "tools": []}

    async def drive():
        return [frame async for frame in service.generate(request, {"trace_id": "trc-plain"})]

    asyncio.run(drive())
    assert sent[-1]["messages"][-1]["content"] == "纯文字提问"
