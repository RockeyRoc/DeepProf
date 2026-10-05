"""上传文件并转换成 Markdown 的 HTTP 接口。

这个接口只为一件事存在：**浏览器不会把用户选中文件的真实路径交给页面**，而
`document.convert` 收的偏偏是服务端路径。所以测试盯住四件事：

1. 字节确实落在**沙箱白名单内**（否则 convert_document 的 check_path 会拒），
2. 体量上限与空文件被拦下，
3. 文件名里的路径成分被剥掉，
4. 临时源文件用完就删——转换结果留在数据目录，临时文件不许长住。

转换本身走既有的 markitdown 链路，这里不重复测它的解析质量。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import create_app
from runtime.testing import make_service, make_settings
from skills import register_default_skills


def _service(tmp_path, **overrides):
    settings = make_settings(library_dir=str(tmp_path / "library"), **overrides)
    service = make_service(settings=settings)
    # 注入 service 时 create_app 会跳过 build_service_with_bindings，默认技能不会自己注册；
    # 这也是仓库里其它 API 测试的做法
    register_default_skills(service.skills)
    # 上传目录跟着数据根走，白名单也要覆盖到它
    service.sandbox.allowed_dirs.append(tmp_path.resolve())
    return service


def _post(client: TestClient, body: bytes, *, filename: str = "note.txt", **params):
    query = {"filename": filename}
    query.update({key: str(value) for key, value in params.items()})
    return client.post("/documents/convert", params=query, content=body)


def _uploads(tmp_path):
    return sorted((tmp_path / "tmp" / "uploads").glob("*"))


def test_upload_converts_and_leaves_no_temp_source(tmp_path):
    service = _service(tmp_path)
    with TestClient(create_app(service=service)) as client:
        response = _post(client, "栈是后进先出。\n".encode("utf-8"), filename="栈.txt")
        assert response.status_code == 200
        body = response.json()

    assert body["filename"] == "栈.txt"
    assert body["bytes"] > 0
    assert body["status"] == "ok", body
    # 结果落在数据目录里，临时源文件用完就删
    assert body.get("markdown_path"), body
    assert _uploads(tmp_path) == [], "临时源文件没有被清掉"


def test_upload_audits_without_leaking_the_file_body(tmp_path):
    service = _service(tmp_path)
    secret = "绝密正文-abc123"
    with TestClient(create_app(service=service)) as client:
        assert _post(client, f"{secret}\n".encode("utf-8"), filename="a.txt").status_code == 200

    events = [event for event in service.history("") if event["type"] == "document.converted"]
    assert events, "没有留下 document.converted 审计事件"
    assert events[-1]["payload"]["status"] == "ok"
    # 审计只记来源摘要与结果位置，正文一个字都不进去
    assert secret not in str(events[-1]["payload"])


def test_upload_rejects_empty_body(tmp_path):
    with TestClient(create_app(service=_service(tmp_path))) as client:
        response = _post(client, b"", filename="empty.txt")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_empty"
    assert _uploads(tmp_path) == []


def test_upload_requires_a_filename(tmp_path):
    with TestClient(create_app(service=_service(tmp_path))) as client:
        response = client.post("/documents/convert", content=b"hello")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_name_required"


def test_upload_rejects_oversized_bodies(tmp_path):
    service = _service(tmp_path, library_max_import_bytes=64)
    with TestClient(create_app(service=service)) as client:
        response = _post(client, b"x" * 256, filename="big.txt")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "document_too_large"
    assert _uploads(tmp_path) == []


def test_upload_strips_path_components_from_the_filename(tmp_path):
    service = _service(tmp_path)
    with TestClient(create_app(service=service)) as client:
        # 两种分隔符都要剥掉：不能因为跑在 POSIX 上就放过 Windows 风格的名字
        for supplied in ("../../evil.txt", "..\\..\\evil.txt", "/etc/passwd.txt"):
            response = _post(client, b"hi\n", filename=supplied)
            assert response.status_code == 200
            assert response.json()["filename"] == "evil.txt" if "evil" in supplied else True

    assert _uploads(tmp_path) == []
    assert not (tmp_path.parent / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_upload_directory_is_inside_the_sandbox_allowlist(tmp_path):
    """上传目录必须落在白名单里，否则转换那一步会被 check_path 拒掉。"""
    service = _service(tmp_path)
    target = service.settings.resolved_library_dir.parent / "tmp" / "uploads"
    assert service.sandbox.is_path_allowed(target) is True
