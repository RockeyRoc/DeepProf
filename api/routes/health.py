"""健康检查路由（DESIGNv0.4 §8 / §12 / §16.4）。

安全要求：
- 渲染进程/客户端不持有模型密钥，API 也不回传密钥；
- 因此这里**不用** ``RuntimeService.describe()`` 的整份输出，而是白名单组装：
  只保留 provider 名称、能力声明、工具名、技能名、能力原语名、教学动作绑定名
  与 Sandbox 策略描述。
- 故意丢弃 describe() 里的 ``sqlite`` 路径等本机信息（前端不需要，且属内部细节）。
- ``action_bindings`` / ``capabilities`` 是**装配自检**用的：图侧 execute 返回
  ``no_binding`` 时，比对这两个清单即可确认是组合根漏装配还是绑定写错（DESIGNv0.4 §7.3）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from config import settings
from runtime.service import RuntimeService

from ..deps import get_runtime_service
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])

#: 允许对外暴露的 describe() 字段（白名单，新增字段必须显式加进来）
_PUBLIC_FIELDS = (
    "provider",
    "provider_capabilities",
    "tools",
    "skills",
    "capabilities",
    "action_bindings",
    "sandbox",
)


@router.get("/health", response_model=HealthResponse, summary="运行时健康检查")
def health(service: RuntimeService = Depends(get_runtime_service)) -> HealthResponse:
    """返回运行时状态；密钥、API key、本机路径一律不出现。"""
    snapshot = service.describe()
    runtime_info = {key: snapshot[key] for key in _PUBLIC_FIELDS if key in snapshot}
    return HealthResponse(
        status="ok",
        trace_id_header=settings.trace_id_header,
        runtime=runtime_info,
    )