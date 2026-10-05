"""把设备上的图片交给多模态模型看的 HTTP 视图。

**为什么必须由网关落盘。** 模型要「看」一张图，字节最终必须以 ``data:`` URL 的形式随请求
交出去；而浏览器出于安全原因只给页面 ``File`` 对象（名字 + 字节），从不给真实路径，Runtime
也不该自己去翻用户的磁盘。所以图片走与 `api/documents.py` 同一条路：**页面把字节 POST
进来，网关校验后落到数据根，会话里只留一条媒体引用**，真正读盘发生在组装模型请求的那一刻。

**为什么不用 multipart。** 同 `api/documents.py`：FastAPI 的 ``File``/``Form`` 需要
``python-multipart``，而它不在 ``requirements.txt`` 里，已装好的用户升上来就会缺依赖。
这里改用**原始请求体**，文件名走查询参数。

三条纪律：

1. **落盘位置在沙箱白名单内**（``settings.resolved_media_dir``，默认 ``~/.deepprof/media``）。
2. **类型只认字节，不认声明。** 请求头里的 ``content-type`` 是客户端说了算的，判「这是不是
   一张允许的图片」必须看魔数；扩展名只用于落盘与回放，绝不参与判断。
3. **审计事件不带原始文件名。** 事件流只记 media_id / 类型 / 字节数 / 摘要，文件名只留在
   本机的旁挂元数据里——与 `document.converted` 的口径一致（见 events.json 的 ``_privacy``）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from api.event_types import MEDIA_IMAGE_STORED_EVENT
from runtime.core.events import RuntimeEvent, new_id

router = APIRouter(prefix="/media", tags=["media"])

# 魔数 → (扩展名, MIME)。只认这四种：既有确定的魔数，也是各家视觉接口都收的格式。
MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)
# new_id("img") = "img_" + uuid4().hex。回读时按这个形状严进，不给路径穿越留余地。
MEDIA_ID_PATTERN = re.compile(r"^img_[0-9a-f]{32}$")
MAX_NAME_CHARS = 80


def _service(request: Request) -> Any:
    return request.app.state.service


def sniff_image(body: bytes) -> tuple[str, str]:
    """按字节判类型，返回 ``(扩展名, MIME)``；不是允许的图片就返回两个空串。"""
    for magic, ext, mime in MAGIC:
        if body.startswith(magic):
            return ext, mime
    # WebP 的魔数是 RIFF????WEBP：第 0–3 字节 RIFF，第 8–11 字节 WEBP
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp", "image/webp"
    return "", ""


def _entry_path(settings: Any, media_id: str) -> Path:
    return Path(settings.resolved_media_dir) / (media_id + ".json")


def _blob_path(settings: Any, media_id: str, ext: str) -> Path:
    return Path(settings.resolved_media_dir) / (media_id + "." + ext)


def load_image(settings: Any, media_id: str) -> dict[str, Any] | None:
    """读出已落盘图片的旁挂元数据（含真实路径）。

    ``media_id`` 形状不对、元数据缺失、字节文件被删掉——一律返回 None，由调用方决定
    是 404 还是 422。这里不抛异常，是为了让两个调用方各说各的话。
    """
    ref = str(media_id or "").strip()
    if not MEDIA_ID_PATTERN.match(ref):
        return None
    try:
        entry = json.loads(_entry_path(settings, ref).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(entry, dict) or not entry.get("ext"):
        return None
    path = _blob_path(settings, ref, str(entry["ext"]))
    if not path.is_file():
        return None
    return {
        "media_id": ref,
        "name": str(entry.get("name") or "图片"),
        "mime": str(entry.get("mime") or "application/octet-stream"),
        "bytes": int(entry.get("bytes") or 0),
        "sha256": str(entry.get("sha256") or ""),
        "path": path,
    }


def image_ref(settings: Any, media_id: str) -> dict[str, Any]:
    """会话里保存的那条引用：只有定位信息，**不含字节、不含 base64**。"""
    entry = load_image(settings, media_id)
    if entry is None:
        raise HTTPException(status_code=422, detail={
            "code": "image_not_found", "message": "图片不存在或已被清理，请重新添加。"})
    return {key: entry[key] for key in ("media_id", "name", "mime", "bytes", "sha256")}


def image_data_url(service: Any, ref: dict[str, Any]) -> str:
    """把一条引用读成 ``data:`` URL，供模型请求内联。

    **读盘在这里，不在 Runtime。** 网关持有沙箱，Runtime 只认字节。文件读不到就直接抛，
    不做静默降级——悄悄少一张图，比多报一次错更难查。
    """
    media_id = str((ref or {}).get("media_id") or "")
    entry = load_image(service.settings, media_id)
    if entry is None:
        # 消息会走到失败卡片上，所以写用户看得懂的话，而不是错误码
        raise RuntimeError("图片文件已不在本机数据目录里（%s），请重新添加后再发送。" % (media_id or "未知"))
    path = entry["path"]
    service.sandbox.check_path(str(path))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError("图片读不出来了（%s），请重新添加后再发送。" % media_id) from exc
    encoded = base64.b64encode(raw).decode("ascii")
    return "data:" + str(entry["mime"]) + ";base64," + encoded


def _too_large(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail={
        "code": "image_too_large",
        "message": "图片超过 %d 字节上限。" % limit,
    })


@router.post("/images")
async def upload_image(request: Request, filename: str = Query(default="")) -> dict[str, Any]:
    service = _service(request)

    # 文件名只用来显示与回放——路径成分一律剥掉（两种分隔符都剥，
    # 免得在 POSIX 上把 Windows 风格的 ..\\..\\x.png 整个当成文件名）
    raw_name = Path(str(filename or "").replace("\\", "/")).name.strip()[:MAX_NAME_CHARS]
    if not raw_name:
        raise HTTPException(status_code=422, detail={
            "code": "image_name_required", "message": "上传的图片没有文件名。"})

    limit = int(service.settings.media_image_max_bytes)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail={
            "code": "image_empty", "message": "上传的图片是空的。"})
    if len(body) > limit:
        raise _too_large(limit)

    ext, mime = sniff_image(body)
    if not ext:
        raise HTTPException(status_code=422, detail={
            "code": "unsupported_image_type",
            "message": "只支持 PNG、JPEG、WebP、GIF 四种图片。"})

    media_id = new_id("img")
    target_dir = Path(service.settings.resolved_media_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body).hexdigest()
    payload = {"media_id": media_id, "name": raw_name, "mime": mime,
               "bytes": len(body), "sha256": digest}
    try:
        _blob_path(service.settings, media_id, ext).write_bytes(body)
        _entry_path(service.settings, media_id).write_text(
            json.dumps({**payload, "ext": ext}, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail={
            "code": "image_store_failed", "message": "图片写入本机数据目录失败。"}) from exc

    await service.emit(RuntimeEvent(type=MEDIA_IMAGE_STORED_EVENT, payload={
        "media_id": media_id, "mime": mime, "bytes": len(body), "sha256": digest,
    }, trace_id=new_id("trc"), source="gateway", client_id="web", surface="web").to_dict())
    return payload


@router.get("/images/{media_id}")
async def get_image(media_id: str, request: Request) -> FileResponse:
    service = _service(request)
    entry = load_image(service.settings, media_id)
    if entry is None:
        raise HTTPException(status_code=404, detail={
            "code": "image_not_found", "message": "图片不存在。"})
    service.sandbox.check_path(str(entry["path"]))
    return FileResponse(str(entry["path"]), media_type=str(entry["mime"]))


__all__ = ["router", "image_data_url", "image_ref", "load_image", "sniff_image"]
