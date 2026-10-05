"""上传文件并转换成 Markdown 的 HTTP 视图。

**为什么需要它。** `document.convert` 收的是**服务端路径**，而浏览器出于安全原因从不把
用户选中文件的真实路径交给页面——给的是 `File` 对象，只有名字和字节。所以「设备选文件 /
拖拽上传」必须有一个把字节送进来的入口。

**为什么不用 multipart。** FastAPI 的 `File`/`Form` 需要 `python-multipart`，而它不在
`requirements.txt` 里，已装好的用户升上来就会缺依赖。这里改用**原始请求体**：文件名走查询
参数，字节就是 body。Starlette 的 `Request.body()` 是核心能力，不需要任何新依赖，前端也能
直接把 `File` 当 body 发出去。

两条纪律：

1. **落盘位置必须在沙箱白名单内。** `convert_document` 会 `check_path`，写到系统临时目录
   会被拒；这里用 `paths.temp_dir()`（`~/.deepprof/tmp`），本来就在白名单里。
2. **转换不在本模块实现。** 交给与 `document.convert` 完全相同的那条 markitdown 链路，
   返回字段与 `document.converted` 审计事件都对齐，免得两条路各长各的。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from runtime.core.events import RuntimeEvent, new_id

router = APIRouter(prefix="/documents", tags=["documents"])

RESULT_KEYS = ("source_sha256", "page_count", "ocr_used", "review_required",
               "markdown_path", "metadata_path", "format", "markitdown_version")
MAX_SUFFIX = 12


def _service(request: Request) -> Any:
    return request.app.state.service


def _safe_suffix(name: str) -> str:
    """只取扩展名，且只接受短小的字母数字后缀。文件名本身一律丢掉："""
    suffix = Path(name).suffix
    if not suffix or len(suffix) > MAX_SUFFIX:
        return ""
    return suffix if suffix[1:].isalnum() else ""


def _too_large(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail={
        "code": "document_too_large",
        "message": "文件超过 %d 字节上限。" % limit,
    })


@router.post("/convert")
async def convert_upload(
    request: Request,
    filename: str = Query(default=""),
    force_ocr: bool = Query(default=False),
    first_page: int | None = Query(default=None),
    last_page: int | None = Query(default=None),
    learner_id: str = Query(default="local"),
) -> dict[str, Any]:
    service = _service(request)

    # 文件名只用来判扩展名——路径成分一律剥掉（两种分隔符都剥，
    # 免得在 POSIX 上把 Windows 风格的 ..\..\x.txt 整个当成文件名）
    raw_name = Path(str(filename or "").replace("\\", "/")).name
    if not raw_name:
        raise HTTPException(status_code=422, detail={
            "code": "document_name_required", "message": "上传的文件没有文件名。"})

    limit = int(service.settings.library_max_import_bytes)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail={
            "code": "document_empty", "message": "上传的文件是空的。"})
    if len(body) > limit:
        raise _too_large(limit)

    # 数据根跟着 settings 走，而不是硬编码全局路径：这样配置覆盖（含测试）也生效，
    # 同时它本来就在沙箱白名单内（默认白名单就是数据根 ~/.deepprof）
    target_dir = service.settings.resolved_library_dir.parent / "tmp" / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (new_id("upl") + _safe_suffix(raw_name))

    trace_id = new_id("trc")
    try:
        target.write_bytes(body)
        try:
            converted = await service.invoke_skill("markitdown", {
                "path": str(target),
                "first_page": first_page,
                "last_page": last_page,
                "force_ocr": bool(force_ocr),
            }, {"trace_id": trace_id, "learner_id": learner_id,
                "client_id": "web", "surface": "web"})
        except Exception as exc:
            raise HTTPException(status_code=422, detail={
                "code": "document_conversion_failed", "message": str(exc)[:240]}) from exc

        result = {key: converted[key] for key in RESULT_KEYS if key in converted}
        result["status"] = str(converted.get("status") or "error")
        if result["status"] != "ok":
            result["error_code"] = str(converted.get("code") or "conversion_failed")
        result["filename"] = raw_name
        result["bytes"] = len(body)

        await service.emit(RuntimeEvent(type="document.converted", payload={
            "status": result["status"], "source_sha256": str(converted.get("source_sha256") or ""),
            "page_count": int(converted.get("page_count") or 0), "ocr_used": bool(converted.get("ocr_used")),
            "review_required": bool(converted.get("review_required")),
            "markdown_path": str(converted.get("markdown_path") or ""),
            "metadata_path": str(converted.get("metadata_path") or ""),
            "format": str(converted.get("format") or ""), "error_code": result.get("error_code"),
        }, trace_id=trace_id, source="gateway", client_id="web", surface="web").to_dict())
        return result
    finally:
        # 临时源文件用完就删：Markdown 与元数据已经落在数据目录里了
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["router"]
