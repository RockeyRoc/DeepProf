"""本机网页聊天前端。"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web"])
PAGE = Path(__file__).resolve().parents[1] / "apps" / "web" / "index.html"


@router.get("/web", response_class=HTMLResponse)
async def web_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))
