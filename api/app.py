"""FastAPI 应用工厂（DESIGNv0.4 §8：FastAPI + SSE）。

用法：
    # 生产
    uvicorn api.app:app --host 0.0.0.0 --port 8000
    # 测试 / 嵌入（注入 Runtime，不触网、不读真实密钥）
    app = create_app(runtime=service)

依赖规则（§9.1）：UI / API → Pedagogical Graph → Runtime Port → Runtime Services。

本模块是**装配入口**：create_app 调 `deps.configure_runtime` 把能力实现
（tools / skills）与教学动作绑定表注册进 Runtime。装配函数放在 `api/deps.py`
而不是这里，是为了让"进程内默认单例"与"create_app 构造的实例"共用同一套装配
（否则单例会漏掉绑定，跑到教学链路才失败）。`runtime/` 自身不反向依赖它们
（否则会破坏 §9.1 的依赖方向）。
路由函数不导入任何能力实现，也不持有与回传 Provider 密钥（§16.4）。
"""
from __future__ import annotations

from fastapi import FastAPI

from runtime.service import RuntimeService, build_runtime_service

from .deps import configure_runtime
from .routes import health, sessions

__all__ = ["create_app", "app"]


def create_app(runtime: RuntimeService | None = None) -> FastAPI:
    """构建 FastAPI 应用。

    Args:
        runtime: 注入的 RuntimeService；留空则用 ``build_runtime_service()``
            （按 .env 构造 Provider 与 SQLite），再由 ``configure_runtime`` 装配。
    """
    service = configure_runtime(runtime or build_runtime_service())

    application = FastAPI(
        title="DeepProf API",
        version="0.4.0",
        description=(
            "DeepProf 会话接入与流式输出层（DESIGNv0.4 §8）。"
            "流式使用 SSE，事件形状与 RuntimeEvent 一致（§18.2）。"
        ),
    )
    application.state.runtime = service
    application.include_router(health.router)
    application.include_router(sessions.router)
    return application


#: 供 uvicorn / 部署直接使用的实例
app = create_app()