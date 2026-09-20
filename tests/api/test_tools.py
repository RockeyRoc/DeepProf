"""项目级工具测试（DESIGNv0.4 §5.3 / §16.2 / §12 不编造引用）。

覆盖：
- ``search_textbook`` 在索引未接入时诚实返回 insufficient_evidence，证据为空；
- 工具声明最小权限（只读），不需要审批；
- 执行过程写入审计事件（tool.requested / tool.completed）。

用 ``asyncio.run`` 在同步测试里驱动异步工具，不触网。
"""
from __future__ import annotations

import asyncio

from runtime.sandbox.policy import PERM_FS_READ
from runtime.tools.base import ToolContext
from tests.conftest import make_service
from tools.retrieval import STATUS_INSUFFICIENT, TOOL_NAME


def test_search_textbook_returns_insufficient_evidence():
    """没有索引就必须说没有证据，绝不返回编造的 document_id / page。"""

    async def _run():
        service = make_service(with_tools=True)
        result = await service.tools.execute(
            TOOL_NAME,
            {"query": "导数的定义", "concept_ids": ["calc.derivative"], "top_k": 3},
            ToolContext(session_id="sess-tool", learner_id="stu-001", sandbox=service.sandbox),
        )
        return service, result

    service, result = asyncio.run(_run())

    assert result.ok is True  # 无证据是事实，不是工具故障
    assert result.data["status"] == STATUS_INSUFFICIENT
    assert result.data["evidence"] == []  # 证据为空：不得编造来源
    assert "证据不足" in result.content  # 回填给模型的内容也明确说明证据不足
    assert result.data["missing"], "必须写明缺什么，便于交接"
    assert result.data["owner"], "必须写明谁来做"

    names = [schema["function"]["name"] for schema in service.tool_schemas()]
    assert TOOL_NAME in names  # 已注册并暴露给模型

    tool = service.tools.get(TOOL_NAME)
    assert tool.permissions == frozenset({PERM_FS_READ})  # 只读，默认最小权限
    assert tool.requires_approval is False


def test_search_textbook_writes_audit_events():
    """工具执行必须留下审计事件（§5.3 必须经过权限检查与审计）。"""

    async def _run():
        service = make_service(with_tools=True)
        await service.tools.execute(
            TOOL_NAME,
            {"query": "洛必达法则"},
            ToolContext(session_id="sess-audit", sandbox=service.sandbox),
        )
        return [str(event.type) for event in service.replay("sess-audit", 0)]

    types = asyncio.run(_run())

    assert "tool.requested" in types
    assert "tool.completed" in types