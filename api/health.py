"""健康检查与装配状态（不含任何密钥）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])

CONTRACT_VERSION = "1.4.0"


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    service = request.app.state.service
    state: dict[str, Any] = service.health()
    state["contract_version"] = CONTRACT_VERSION
    state["action_bindings"] = service.dispatcher.binding_summary()
    return state