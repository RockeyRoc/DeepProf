"""API 依赖注入与装配（DESIGNv0.4 §8 / §9.1 / §16.1：FastAPI 依赖注入与流式响应）。

RuntimeService 的提供方式：
1. 生产：``create_app()`` 构建一个进程内 RuntimeService 并放进 ``app.state``，
   所有请求复用同一 Runtime（同一事件总线与 SQLite 连接）；
2. 测试/嵌入：``create_app(runtime=my_service)`` 直接注入；
3. 单测还可以用 ``app.dependency_overrides[get_runtime_service] = lambda: svc``
   覆盖依赖——路由函数只依赖 ``get_runtime_service`` 这一个可替换入口。

本模块同时持有**装配函数** ``configure_runtime``：把 `runtime/` 上层的能力实现
（tools / skills）与教学动作绑定表注册进 Runtime。它放在这里而不是 `runtime/` 里，
是因为 `runtime/` 一旦 import 它们就构成反向依赖（§9.1，有守卫测试）；
放在这里也保证"进程内默认单例"与"create_app 构造的实例"用的是同一套装配——
否则单例会是一个零绑定的裸内核，跑到教学链路时才静默失败。

注意：API 层不接触 Provider 密钥，因此这里只暴露 RuntimeService 本身。
"""
from __future__ import annotations

from fastapi import Request

from runtime.service import RuntimeService, build_runtime_service

__all__ = [
    "configure_runtime",
    "get_runtime_service",
    "get_default_service",
    "set_default_service",
    "reset_default_service",
]

# 进程内单例：lazy 构建，避免 import 时就连接数据库 / 读取密钥
_default_service: RuntimeService | None = None


def configure_runtime(service: RuntimeService) -> RuntimeService:
    """给一个 RuntimeService 装上项目级能力与教学绑定表，返回同一个实例。

    幂等：能力名/绑定已存在就不重复装配（多次 create_app 不报错）。
    缺了绑定不会"静默降级"，而是图侧 execute 返回 status=no_binding——
    所以本函数是跑通教学链路的前提，`/health` 的 `action_bindings` 可自检。
    """
    # 延迟导入：避免把这些上层实现变成 api 模块导入期依赖
    from graph.education.bindings import ACTION_BINDINGS
    from skills import register_default_skills
    from tools import register_default_tools

    register_default_tools(service.tools)
    if not service.skills.names():
        register_default_skills(service.skills)
    if not service.action_bindings:
        service.set_action_bindings(ACTION_BINDINGS)
    return service


def get_default_service() -> RuntimeService:
    """返回（必要时构建并装配）进程内默认 RuntimeService。"""
    global _default_service
    if _default_service is None:
        _default_service = configure_runtime(build_runtime_service())
    return _default_service


def set_default_service(service: RuntimeService | None) -> None:
    """替换默认单例（供脚本与测试装配）。"""
    global _default_service
    _default_service = service


def reset_default_service() -> None:
    """清除默认单例，便于测试之间互不污染。"""
    set_default_service(None)


def get_runtime_service(request: Request) -> RuntimeService:
    """FastAPI 依赖：优先用 ``app.state.runtime``（create_app 注入），否则用单例。"""
    service = getattr(request.app.state, "runtime", None)
    return service or get_default_service()