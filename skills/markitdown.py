"""Local MarkItDown document conversion and offline OCR skill."""

from __future__ import annotations

from typing import Any


class MarkItDownSkill:
    name = "markitdown"
    description = "将沙箱内的本地文档转换为 Markdown 和逐页 JSON；扫描页使用本地 RapidOCR"
    uses_host = True

    async def invoke(self, input: dict[str, Any], ctx: dict[str, Any], host: Any) -> dict[str, Any]:
        result = await host.call_tool("convert_document", {
            "path": str(input.get("path") or ""),
            "first_page": input.get("first_page"),
            "last_page": input.get("last_page"),
            "force_ocr": bool(input.get("force_ocr", False)),
        }, ctx)
        return {"status": "ok" if result.get("status") == "ok" else "error",
                "source": "tool:convert_document", **result}


__all__ = ["MarkItDownSkill"]
