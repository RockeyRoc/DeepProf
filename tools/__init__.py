"""项目级原子工具（DESIGNv0.4 §5.3 / §9 目录树）。

Tools = 可执行且有结构化参数的原子操作（检索教材、保存错题、读取画像），
必须经过 schema 校验、权限检查与审计（§5.3）。

约定：
- 与 runtime 的分工：``runtime/tools`` 提供 Tool 基类、注册表与执行管线；
  本目录只放**本项目具体的工具实现**，通过 ``register_default_tools`` 装配；
- 权限默认最小：只声明真正需要的能力（见各工具的 ``permissions``），
  联网/写入/密钥权限由 Sandbox 默认拒绝（D-5 / §13.3）；
- 不编造引用：检索类工具在语料缺失时返回 insufficient_evidence（§12）。

本轮只实现 ``search_textbook``（诚实返回证据不足）；保存错题、读取画像
依赖数据组仓储与学情模型（§16.5 / §16.6），后续按同一模式补充。
"""
from __future__ import annotations

from runtime.tools.registry import ToolRegistry

from .retrieval import build_search_textbook_tool

__all__ = ["register_default_tools", "build_search_textbook_tool"]


def register_default_tools(registry: ToolRegistry) -> list[str]:
    """把项目级默认工具注册进 ToolRegistry，返回当前已注册的工具名。

    幂等：已注册的同名工具会被跳过，重复调用（多次 create_app）不会报错。
    """
    if not registry.has(build_search_textbook_tool().name):
        registry.register(build_search_textbook_tool())
    return registry.names()